"""Reviewed, game-scoped examples and immutable Laya training snapshots.

Capturing a failure only creates a pending example. An explicit human review is
required before an example can enter either side of a training/evaluation split.
"""

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_LOCKS = {}
_LOCKS_LOCK = threading.Lock()


def _identifier(value, name):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or ".." in value:
        raise ValueError("%s must be a safe identifier" % name)
    return value


def _json(value, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=sort_keys,
                      separators=(",", ":"))


def _hash(value, sort_keys=False):
    return hashlib.sha256(_json(value, sort_keys).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _evidence(value):
    """Keep diagnostic records JSON-safe without altering the model request."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _evidence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_evidence(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _valid_label(question, value):
    kind = question.get("type")
    if kind == "choice":
        return isinstance(value, str) and value in (question.get("criteria") or {})
    if kind == "noul":
        return isinstance(value, bool)
    if kind == "score":
        return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                and 0 <= value <= len(question.get("criteria") or []) - 1)
    return False


def _labels(expected, questions, require_action=False):
    if not isinstance(expected, dict):
        raise ValueError("expected must be an object keyed by question id")
    for key, value in expected.items():
        if key not in questions or not _valid_label(questions[key], value):
            raise ValueError("invalid expected label for %s" % key)
    if require_action and ("action" not in expected or questions.get("action", {}).get("type") != "choice"):
        raise ValueError("approval requires an expected action label")
    return deepcopy(expected)


def _suggest(truth, questions):
    result = {}
    for key, value in (truth.items() if isinstance(truth, dict) else ()):
        question = questions.get(key)
        if question is None:
            continue
        if question.get("type") == "choice" and isinstance(value, list):
            value = next((item for item in value if _valid_label(question, item)), None)
        if _valid_label(question, value):
            result[key] = value
    return result


def _action_options(truth, questions):
    value = truth.get("action") if isinstance(truth, dict) else None
    values = value if isinstance(value, list) else [value]
    return [item for item in values if _valid_label(questions["action"], item)]


def _failure(decision, graded, truth, questions):
    if not isinstance(decision, dict):
        return None
    status = decision.get("status")
    if status and status not in ("ok", "running"):
        return "model_%s" % status
    action_grade = (graded or {}).get("action") if isinstance(graded, dict) else None
    if isinstance(action_grade, dict) and action_grade.get("correct") is False:
        return "action_mismatch"
    allowed = _action_options(truth, questions)
    answers = decision.get("answers")
    answer = answers.get("action") if isinstance(answers, dict) else None
    action = decision.get("action", answer.get("choice") if isinstance(answer, dict) else None)
    if status == "ok" and allowed and action not in allowed:
        return "action_mismatch"
    return None


def _version(case):
    return ((case.get("source") or {}).get("target") or {}).get("version")


class DatasetStore:
    """Durable example review storage; independent of any model runtime."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        with _LOCKS_LOCK:
            self.lock = _LOCKS.setdefault(str(self.root), threading.RLock())

    def _directory(self, game):
        name = _identifier(game.id, "game id")
        path = self.root / name
        self._contained(path)
        return path

    def _contained(self, path):
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("dataset path escapes its root")
        # Do not write through a user-replaced symlink, even inside the root.
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError("dataset paths must not contain symlinks")

    def _read(self, game):
        path = self._directory(game) / "cases.json"
        self._contained(path)
        if not path.exists():
            return []
        saved = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or saved.get("schema_version") != 1
                or saved.get("game_id") != game.id or not isinstance(saved.get("cases"), list)):
            raise ValueError("invalid game dataset")
        return saved["cases"]

    def _write(self, game, cases):
        directory = self._directory(game)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "cases.json"
        self._contained(path)
        content = _json({"schema_version": 1, "game_id": game.id, "cases": cases}) + "\n"
        self._atomic(path, content)

    def _atomic(self, path, content):
        self._contained(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".%s-" % path.name, dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def status(self, game):
        with self.lock:
            cases = [case for case in self._read(game) if _version(case) == game.version]
            for case in cases:
                try:
                    self._current(game, case)
                    case["compatible"] = True
                except ValueError as exc:
                    case.update(compatible=False, incompatible_reason=str(exc))
            cases.sort(key=lambda case: (case.get("updated_at", ""), case.get("id", "")), reverse=True)
            counts = {"total": len(cases), "pending": 0, "approved": 0, "train": 0, "eval": 0, "excluded": 0}
            for case in cases:
                if case.get("review") in ("pending", "approved", "excluded"):
                    counts[case["review"]] += 1
                if case.get("review") == "approved" and case.get("split") in ("train", "eval"):
                    counts[case["split"]] += 1
            return {"cases": deepcopy(cases), "counts": counts}

    def capture(self, game, row, force=False):
        self._directory(game)
        if not isinstance(row, dict):
            raise ValueError("capture row must be an object")
        request = row.get("request")
        if not isinstance(request, dict) or "state" not in request or not isinstance(request.get("questions"), dict):
            raise ValueError("capture requires the exact state/questions request snapshot")
        state, questions = deepcopy(request["state"]), deepcopy(request["questions"])
        if not isinstance(state, (str, dict, list)) or (isinstance(state, str) and not state.strip()):
            raise ValueError("request state must be nonempty text or structured state")
        _json(state)
        if _json(questions) != _json(game.questions):
            raise ValueError("request questions do not match the selected game schema")
        target = deepcopy(row.get("target") if row.get("target") is not None else game.target)
        if not isinstance(target, dict) or target.get("game") != game.id or target.get("version") != game.version:
            raise ValueError("request target does not match the selected game/version")
        reasons = []
        failed = _failure(row.get("decision"), row.get("grade"), row.get("truth"), questions)
        if failed:
            reasons.append("live:%s" % failed)
        for shadow in row.get("shadow") or []:
            if not isinstance(shadow, dict) or shadow.get("current_observation") is False:
                continue
            if any(shadow.get(source, row.get(current)) != row.get(current)
                   for source, current in (("source_step", "step"), ("source_episode", "episode"))):
                continue
            failed = _failure(shadow, shadow.get("grade"), row.get("truth"), questions)
            if failed:
                reasons.append("shadow:%s:%s" % (shadow.get("provider", "unknown"), failed))
        if not reasons and not force:
            return None
        source = {key: _evidence(row.get(key)) for key in
                  ("step", "episode", "truth", "grade", "decision", "shadow", "reference_kind", "game_build", "frame")}
        source["target"] = _evidence(target)
        fingerprint = _hash({"state": _hash(state, sort_keys=True), "questions": questions,
                             "game": game.id, "version": game.version})
        timestamp = _now()
        with self.lock:
            cases = self._read(game)
            existing = next((case for case in cases if case.get("fingerprint") == fingerprint), None)
            if existing:
                existing["occurrences"] = existing.get("occurrences", 1) + 1
                existing["updated_at"] = timestamp
                existing["last_source"] = source
                self._write(game, cases)
                return deepcopy(existing)
            case = {"id": "case-" + fingerprint[:24], "game_id": game.id, "version": game.version, "state": state,
                    "questions": questions, "fingerprint": fingerprint,
                    "suggested_expected": _suggest(row.get("truth"), questions), "expected": {},
                    "review": "pending", "split": "train", "reason": ", ".join(reasons) or "bookmark",
                    "source": source, "occurrences": 1, "created_at": timestamp, "updated_at": timestamp}
            cases.append(case)
            self._write(game, cases)
            return deepcopy(case)

    def review(self, game, case_id, expected=None, split="train", review="approved"):
        _identifier(case_id, "case id")
        if split not in ("train", "eval") or review not in ("pending", "approved", "excluded"):
            raise ValueError("invalid dataset review or split")
        with self.lock:
            cases = self._read(game)
            case = next((item for item in cases if item.get("id") == case_id), None)
            if case is None:
                raise LookupError("dataset case not found: %s" % case_id)
            if review == "approved":
                self._current(game, case)
            elif case.get("game_id") != game.id:
                raise ValueError("dataset case belongs to another game")
            labels = _labels(case.get("expected", {}) if expected is None else expected,
                             case["questions"], require_action=review == "approved")
            case.update(expected=labels, review=review, split=split, updated_at=_now())
            self._write(game, cases)
            return deepcopy(case)

    def _current(self, game, case):
        source = case.get("source") or {}
        target = source.get("target") or {}
        if case.get("game_id") != game.id or target.get("game") != game.id or target.get("version") != game.version:
            raise ValueError("dataset case belongs to another game/version")
        build = source.get("game_build")
        if isinstance(build, dict) and ((build.get("game") and build["game"] != game.id)
                                       or (build.get("version") and build["version"] != game.version)):
            raise ValueError("dataset case was observed in a different game/version")
        if _json(case.get("questions")) != _json(game.questions):
            raise ValueError("dataset case uses a different question schema")

    def freeze(self, game, run_id):
        _identifier(run_id, "run id")
        with self.lock:
            directory = self._directory(game) / "runs" / run_id
            self._contained(directory)
            if directory.exists():
                raise ValueError("training run already exists; frozen datasets cannot be overwritten")
            selected = {"train": [], "eval": []}
            seen_states = {}
            approved = [case for case in self._read(game)
                        if case.get("review") == "approved" and _version(case) == game.version]
            # Check state overlap independently of question schema. A changed prompt
            # must not allow the same observation to leak into the held-out split.
            for case in approved:
                split = case.get("split")
                if split not in selected:
                    raise ValueError("approved case has an invalid split")
                state_hash = _hash(case["state"], sort_keys=True)
                if state_hash in seen_states and seen_states[state_hash] != split:
                    raise ValueError("the same state appears in both train and eval (dataset leakage)")
                seen_states[state_hash] = split
            for case in approved:
                split = case["split"]
                self._current(game, case)
                _labels(case.get("expected"), case["questions"], require_action=True)
                selected[split].append(case)
            if not selected["train"] or not selected["eval"]:
                raise ValueError("training requires nonempty approved train and eval datasets")
            content = {}
            for split, cases in selected.items():
                content[split] = "".join(_json({key: case[key] for key in ("state", "questions", "expected")}) + "\n"
                                         for case in cases)
            hashes = {split: hashlib.sha256(value.encode("utf-8")).hexdigest() for split, value in content.items()}
            manifest = {"schema_version": 1, "game_id": game.id, "version": game.version,
                        "target": deepcopy(game.target), "schema_hash": _hash(game.questions),
                        "run_id": run_id, "created_at": _now(), "questions_hash": _hash(game.questions),
                        "counts": {split: len(cases) for split, cases in selected.items()},
                        "case_ids": {split: [case["id"] for case in cases] for split, cases in selected.items()},
                        "data_hashes": hashes,
                        "sources": {case["id"]: case["source"] for cases in selected.values() for case in cases}}
            manifest["dataset_hash"] = _hash({"game_id": game.id, "version": game.version, "data_hashes": hashes})
            directory.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".dataset-", dir=directory.parent))
            try:
                for split in selected:
                    self._atomic(temporary / (split + ".jsonl"), content[split])
                self._atomic(temporary / "dataset.json", _json(manifest) + "\n")
                # Rename publishes all three completed files together. Never reuse a run id.
                if directory.exists():
                    raise ValueError("training run already exists")
                os.rename(temporary, directory)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            return {"train_path": str(directory / "train.jsonl"), "eval_path": str(directory / "eval.jsonl"),
                    "manifest_path": str(directory / "dataset.json"), "train_count": len(selected["train"]),
                    "eval_count": len(selected["eval"]), "dataset_hash": manifest["dataset_hash"],
                    "version": game.version, "schema_hash": manifest["schema_hash"],
                    "target": manifest["target"], "data_hashes": hashes, "case_ids": manifest["case_ids"]}
