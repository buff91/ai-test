"""Game-scoped, asynchronous Laya training with explicit held-out promotion gates.

The console process never imports torch. Each job runs in a separate Python process;
only immutable dataset snapshots and checkpoint fingerprints cross that boundary.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid


BASES = ("english", "multilingual", "typed-decisions")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")
_REPO = Path(__file__).resolve().parent.parent


def _id(value):
    value = getattr(value, "id", value)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("게임/체크포인트 id가 올바르지 않습니다.")
    return value


def _context(game):
    questions = getattr(game, "questions", None)
    return {"game": _id(game), "version": getattr(game, "version", "default"),
            "schema_hash": hashlib.sha256(json.dumps(questions, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest() if questions is not None else None}


def _compatibility(game, checkpoint):
    context = _context(game)
    reasons = []
    if checkpoint.get("version", "default") != context["version"]:
        reasons.append("선택한 게임 버전과 체크포인트 버전이 다릅니다.")
    if context["schema_hash"] and checkpoint.get("schema_hash") != context["schema_hash"]:
        reasons.append("현재 질문/행동 스키마와 체크포인트 스키마가 다릅니다.")
    return reasons


def _now():
    return time.time()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(path, *, trusted_base=False):
    path = Path(path)
    if (path.is_symlink() and not trusted_base) or not path.is_file():
        raise ValueError("일반 파일만 사용할 수 있습니다: %s" % path.name)
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _inside(root, path):
    root, path = Path(root).resolve(), Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("학습 저장소 밖의 경로는 사용할 수 없습니다.")
    return path.resolve()


def checkpoint_fingerprint(path, *, trusted_base=False):
    """Hash the exact loadable artifact, excluding optional training intermediates."""
    path = Path(path)
    if (path.is_symlink() and not trusted_base) or not path.is_dir():
        raise ValueError("체크포인트 폴더가 없습니다.")
    files = [path / "rl_agent_config.json", path / "model.safetensors"]
    for directory in ("encoder", "tokenizer"):
        folder = path / directory
        if (folder.is_symlink() and not trusted_base) or not folder.is_dir():
            raise ValueError("체크포인트에 %s 폴더가 없습니다." % directory)
        children = sorted(folder.rglob("*"))
        if not trusted_base and any(p.is_symlink() for p in children):
            raise ValueError("체크포인트 심볼릭 링크는 사용할 수 없습니다.")
        files.extend(p for p in children if p.is_file())
        if not any(p.is_file() for p in children):
            raise ValueError("체크포인트 %s 폴더가 비었습니다." % directory)
    config = _read(files[0])
    if not isinstance(config, dict) or files[1].stat().st_size == 0:
        raise ValueError("체크포인트 설정/가중치가 올바르지 않습니다.")
    values = {p.relative_to(path).as_posix(): _digest(p, trusted_base=trusted_base) for p in files}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def dataset_fingerprints(snapshot):
    return {key: _digest(snapshot[key]) for key in ("train_path", "eval_path", "manifest_path")}


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError("학습 설정은 객체여야 합니다.")
    defaults = {"base": "multilingual", "epochs": 4, "encoder_lr": 2.5e-5,
                "head_lr": 1e-4, "freeze_encoder": False, "micro_batch": 2, "grad_accum": 8}
    if set(options) - set(defaults):
        raise ValueError("지원하지 않는 학습 설정: %s" % ", ".join(sorted(set(options) - set(defaults))))
    result = dict(defaults, **options)
    if result["base"] not in BASES:
        raise ValueError("base는 %s 중 하나여야 합니다." % ", ".join(BASES))
    for key, low, high in (("epochs", 1, 100), ("micro_batch", 1, 32), ("grad_accum", 1, 128)):
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError("%s는 %d..%d 정수여야 합니다." % (key, low, high))
    for key in ("encoder_lr", "head_lr"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1e-8 <= value <= .01:
            raise ValueError("%s는 0.00000001..0.01 사이 숫자여야 합니다." % key)
    if type(result["freeze_encoder"]) is not bool:
        raise ValueError("freeze_encoder는 true/false여야 합니다.")
    return result


class TrainingManager:
    def __init__(self, root, store=None, *, process_factory=subprocess.Popen):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if store is None:
            from gamelab.datasets import DatasetStore
            store = DatasetStore(self.root)
        self.store = store
        self._factory = process_factory
        self._lock = threading.RLock()
        self._registries = {}
        self._processes = {}
        self._closed = False
        self._hash_cache = {}
        for path in self.root.glob("*/training.json"):
            if _ID.fullmatch(path.parent.name):
                self._registry(path.parent.name)

    def _game_root(self, game):
        return _inside(self.root, self.root / _id(game))

    def _registry(self, game):
        game = _id(game)
        root = self._game_root(game)
        if game not in self._registries:
            path = root / "training.json"
            registry = _read(path) if path.exists() else {"jobs": [], "checkpoints": [], "active": None, "history": []}
            registry.setdefault("active_by_version", {})
            registry.setdefault("history_by_version", {})
            for job in registry["jobs"]:
                if job["status"] in ("queued", "running"):
                    job.update(status="failed", error="콘솔 재시작으로 작업이 중단되었습니다.", finished_at=_now(), stage="interrupted")
            self._registries[game] = registry
            self._save(game)
        return self._registries[game]

    def _save(self, game):
        game = _id(game)
        _write(self._game_root(game) / "training.json", self._registries[game])

    def _checkpoint(self, game, checkpoint_id):
        _id(checkpoint_id)
        checkpoint = next((c for c in self._registry(game)["checkpoints"] if c["id"] == checkpoint_id), None)
        if checkpoint is None:
            raise LookupError("이 게임의 체크포인트가 없습니다: %s" % checkpoint_id)
        return checkpoint

    def _check_paths(self, game, snapshot):
        for key in ("train_path", "eval_path", "manifest_path"):
            _inside(self._game_root(game), snapshot[key])

    def _refresh(self):
        for (game, job_id), process in list(self._processes.items()):
            registry = self._registry(game)
            job = next(j for j in registry["jobs"] if j["id"] == job_id)
            run = _inside(self._game_root(game), job["work_dir"])
            progress = run / "progress.json"
            if progress.exists():
                try:
                    info = _read(progress)
                    job["stage"] = str(info.get("stage", job["stage"]))[:200]
                    job["progress"] = min(1, max(0, float(info.get("progress", 0))))
                except (ValueError, OSError, TypeError):
                    pass
            log = run / "worker.log"
            if log.exists():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 16000))
                    job["log_tail"] = stream.read(16000).decode("utf-8", errors="replace").splitlines()[-80:]
                if job["kind"] == "train":
                    epochs = re.findall(r"epoch (\d+)/(\d+)", "\n".join(job["log_tail"]))
                    if epochs:
                        epoch, total = map(int, epochs[-1])
                        job["progress"] = max(job.get("progress", 0), min(.95, epoch / max(1, total) * .9))
            code = process.poll()
            if code is None:
                continue
            del self._processes[(game, job_id)]
            try:
                result = _read(run / "result.json")
                if code or not result.get("ok"):
                    raise ValueError(result.get("error") or "worker 종료 코드 %s" % code)
                if job["kind"] == "train":
                    checkpoint = result["checkpoint"]
                    if checkpoint["id"] != job["checkpoint_id"] or checkpoint["run_id"] != job["run_id"]:
                        raise ValueError("체크포인트 작업 식별자가 일치하지 않습니다.")
                    _inside(self._game_root(game), checkpoint["path"])
                    self._check_paths(game, checkpoint["snapshot"])
                    checkpoint.update(evaluation=None, activation_allowed=False, reasons=["독립 평가를 먼저 실행하세요."])
                    registry["checkpoints"].append(checkpoint)
                else:
                    checkpoint = self._checkpoint(game, job["checkpoint_id"])
                    evaluation = result["evaluation"]
                    if evaluation["artifact_hash"] != checkpoint["artifact_hash"] or evaluation["dataset_hash"] != checkpoint["dataset_hash"]:
                        raise ValueError("평가의 체크포인트/데이터 지문이 일치하지 않습니다.")
                    checkpoint.update(evaluation=evaluation,
                                      activation_allowed=evaluation["gate"]["allowed"], reasons=evaluation["gate"]["reasons"])
                job.update(status="completed", stage="completed", progress=1, finished_at=_now())
            except (ValueError, TypeError, KeyError, OSError) as exc:
                job.update(status="failed", stage="failed", error=str(exc)[:2000], finished_at=_now())
            self._save(game)

    def status(self, game):
        with self._lock:
            self._refresh()
            registry = self._registry(game)
            # JSON copy prevents callers from mutating the promotion registry.
            result = json.loads(json.dumps({key: registry[key] for key in ("jobs", "checkpoints")}))
            result["active"] = json.loads(json.dumps(registry["active_by_version"].get(_context(game)["version"])))
            for checkpoint in result["checkpoints"]:
                compatibility = _compatibility(game, checkpoint)
                checkpoint["compatible"] = not compatibility
                checkpoint["activation_allowed"] = checkpoint.get("activation_allowed", False) and not compatibility
                checkpoint["reasons"] = checkpoint.get("reasons", []) + compatibility
            return result

    def _start(self, game, kind, request, checkpoint_id, run_id, work_dir):
        if self._closed:
            raise RuntimeError("학습 관리자가 종료되었습니다.")
        self._refresh()
        if self._processes:
            raise ValueError("다른 학습/평가 작업이 실행 중입니다. 완료 후 다시 실행하세요.")
        registry = self._registry(game)
        work_dir = _inside(self._game_root(game), work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        job_id = kind + "-" + uuid.uuid4().hex[:16]
        request = dict(request, kind=kind, game=_id(game), context=_context(game), checkpoint_id=checkpoint_id, run_id=run_id, work_dir=str(work_dir))
        _write(work_dir / "request.json", request)
        job = {"id": job_id, "kind": kind, "status": "running", "stage": "starting", "progress": 0,
               "checkpoint_id": checkpoint_id, "run_id": run_id, "created_at": _now(),
               "finished_at": None, "error": None, "log_tail": [], "work_dir": str(work_dir)}
        registry["jobs"].append(job)
        self._save(game)
        # Explicit allowlist: inherited provider/cloud/API credentials never reach workers.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "HF_HOME", "HF_HUB_CACHE", "XDG_CACHE_HOME") if key in os.environ}
        env.update(PYTHONUNBUFFERED="1", TOKENIZERS_PARALLELISM="false", HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1")
        try:
            with (work_dir / "worker.log").open("wb") as log:
                process = self._factory([sys.executable, "-u", "-m", "gamelab.training", "--worker", str(work_dir / "request.json")],
                                        cwd=str(_REPO), env=env, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
            self._processes[(_id(game), job_id)] = process
        except Exception as exc:
            job.update(status="failed", stage="failed", error="worker 시작 실패: %s" % exc, finished_at=_now())
            self._save(game)
        return json.loads(json.dumps(job))

    def start_train(self, game, options):
        options = validate_options(options)
        with self._lock:
            self._registry(game)
            self._refresh()
            if self._closed or self._processes:
                raise ValueError("학습/평가 작업을 시작할 수 없습니다. 실행 중인 작업을 확인하세요.")
            run_id = "train-" + uuid.uuid4().hex[:16]
            checkpoint_id = "laya-" + uuid.uuid4().hex[:16]
            snapshot = {key: str(value) if isinstance(value, Path) else value
                        for key, value in self.store.freeze(game, run_id).items()}
            self._check_paths(game, snapshot)
            if snapshot["train_count"] < 1 or snapshot["eval_count"] < 1:
                raise ValueError("학습용과 독립 평가용 사례가 모두 필요합니다.")
            fingerprints = dataset_fingerprints(snapshot)
            return self._start(game, "train", {"options": options, "snapshot": snapshot, "dataset_fingerprints": fingerprints},
                               checkpoint_id, run_id, self._game_root(game) / "runs" / run_id / "training")

    def start_evaluate(self, game, checkpoint_id):
        with self._lock:
            self._refresh()
            checkpoint = self._checkpoint(game, checkpoint_id)
            self._verify_checkpoint(game, checkpoint)
            work = self._game_root(game) / "runs" / checkpoint["run_id"] / ("eval-" + uuid.uuid4().hex[:12])
            return self._start(game, "evaluate", {"checkpoint": checkpoint}, checkpoint_id, checkpoint["run_id"], work)

    def _model_hash(self, path, force=False, *, trusted_base=False):
        path = Path(path)
        relevant = [path / "rl_agent_config.json", path / "model.safetensors"]
        relevant.extend(p for name in ("encoder", "tokenizer") for p in sorted((path / name).rglob("*")))
        signature = tuple((str(p.relative_to(path)), p.lstat().st_mode, p.lstat().st_ino,
                           p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in relevant)
        cache_key = (str(path), trusted_base)
        cached = self._hash_cache.get(cache_key)
        if not force and cached and cached[0] == signature:
            return cached[1]
        value = checkpoint_fingerprint(path, trusted_base=trusted_base)
        self._hash_cache[cache_key] = (signature, value)
        return value

    def _verify_checkpoint(self, game, checkpoint, *, force=False):
        reasons = _compatibility(game, checkpoint)
        if reasons:
            raise ValueError(" ".join(reasons))
        _inside(self._game_root(game), checkpoint["path"])
        self._check_paths(game, checkpoint["snapshot"])
        if dataset_fingerprints(checkpoint["snapshot"]) != checkpoint["dataset_fingerprints"]:
            raise ValueError("고정한 데이터셋이 변경되었습니다. 새 학습을 실행하세요.")
        if self._model_hash(checkpoint["path"], force) != checkpoint["artifact_hash"]:
            raise ValueError("체크포인트 파일이 변경되었습니다. 다시 학습하세요.")
        if self._model_hash(checkpoint["base_path"], force, trusted_base=True) != checkpoint["base_hash"]:
            raise ValueError("원본 기본 체크포인트 파일이 변경되었습니다.")

    def activate(self, game, checkpoint_id):
        with self._lock:
            self._refresh()
            registry = self._registry(game)
            checkpoint = self._checkpoint(game, checkpoint_id)
            evaluation = checkpoint.get("evaluation")
            if not evaluation or not promotion_gate(evaluation)["allowed"]:
                raise ValueError("적용 조건을 통과한 독립 평가가 필요합니다.")
            self._verify_checkpoint(game, checkpoint, force=True)
            if evaluation["artifact_hash"] != checkpoint["artifact_hash"] or evaluation["dataset_hash"] != checkpoint["dataset_hash"]:
                raise ValueError("평가 지문이 변경되었습니다.")
            version = _context(game)["version"]
            current = registry["active_by_version"].get(version)
            if current and current["checkpoint_id"] == checkpoint_id:
                return json.loads(json.dumps(current))
            registry["history_by_version"].setdefault(version, []).append(current)
            active = {"checkpoint_id": checkpoint_id, "path": checkpoint["path"], "version": version, "activated_at": _now()}
            registry["active_by_version"][version] = active
            self._save(game)
            return json.loads(json.dumps(active))

    def rollback(self, game):
        with self._lock:
            self._refresh()
            registry = self._registry(game)
            version = _context(game)["version"]
            registry["active_by_version"][version] = None
            self._save(game)
            return {"active": None, "rolled_back": True}

    def active_checkpoint(self, game):
        with self._lock:
            active = self._registry(game)["active_by_version"].get(_context(game)["version"])
            if not active:
                return None
            checkpoint = self._checkpoint(game, active["checkpoint_id"])
            self._verify_checkpoint(game, checkpoint)
            return Path(checkpoint["path"])

    def close(self):
        with self._lock:
            self._closed = True
            for (game, job_id), process in list(self._processes.items()):
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                except ProcessLookupError:
                    pass
                job = next(j for j in self._registry(game)["jobs"] if j["id"] == job_id)
                job.update(status="failed", stage="interrupted", error="콘솔 종료로 작업이 중단되었습니다.", finished_at=_now())
                self._save(game)
            self._processes.clear()


def _rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _input_signatures(rows):
    return {json.dumps({"state": row["state"], "qid": qid, "question": question}, sort_keys=True, ensure_ascii=False)
            for row in rows for qid, question in row["questions"].items()}


def held_out_rows(snapshot):
    train, evaluation = _rows(snapshot["train_path"]), _rows(snapshot["eval_path"])
    if not train or not evaluation:
        raise ValueError("학습/평가 데이터셋이 비었습니다.")
    if _input_signatures(train) & _input_signatures(evaluation):
        raise ValueError("학습과 평가에 같은 상태/질문이 있습니다. 독립 평가가 아닙니다.")
    if len(train) != snapshot["train_count"] or len(evaluation) != snapshot["eval_count"]:
        raise ValueError("데이터셋 사례 수가 고정한 명세와 다릅니다.")
    return evaluation


def _target(row, qid, question):
    """One normalized probability target in the public Laya JSONL schema."""
    kind = question["type"]
    criteria = question.get("criteria", {})
    keys = list(criteria) if kind == "choice" else [str(i) for i in range(len(criteria))] if kind == "score" else ["false", "true"]
    if not keys:
        raise ValueError("빈 선택지입니다.")
    gold = row.get("gold", {}).get(qid)
    expected = row.get("expected", {}).get(qid)
    if gold is not None:
        raw = gold["probabilities"]
        values = [float(raw.get(key, raw.get(str(key), 0))) for key in keys]
    elif expected is not None:
        if kind == "noul":
            if type(expected) is bool:
                expected = "true" if expected else "false"
            elif expected in (0, 1):
                expected = "true" if expected else "false"
            else:
                raise ValueError("noul 정답은 boolean이어야 합니다.")
        if kind == "score" and isinstance(expected, (float, int)) and not isinstance(expected, bool):
            if not math.isfinite(expected) or not 0 <= expected <= len(keys) - 1:
                raise ValueError("점수 정답이 범위를 벗어났습니다.")
            values = [max(0, 1 - abs(i - expected)) for i in range(len(keys))]
        else:
            if str(expected) not in [str(k) for k in keys]:
                raise ValueError("정답 행동이 질문 선택지에 없습니다.")
            values = [float(str(key) == str(expected)) for key in keys]
    else:
        return None
    total = sum(values)
    if not all(math.isfinite(value) and value >= 0 for value in values) or total <= 0:
        raise ValueError("정답 확률이 올바르지 않습니다.")
    return keys, [value / total for value in values]


def _probabilities(answer, keys, question):
    if question["type"] == "noul":
        probability = float(answer["noul"])
        values = [1 - probability, probability]
    else:
        probabilities = answer["probabilities"]
        if set(map(str, probabilities)) != set(map(str, keys)):
            raise ValueError("예측 확률의 선택지가 질문과 다릅니다.")
        values = [float(probabilities.get(key, probabilities.get(str(key)))) for key in keys]
    total = sum(values)
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in values) or not .98 <= total <= 1.02:
        raise ValueError("예측 확률이 올바르지 않습니다.")
    return [value / total for value in values]


def _metrics(records):
    if not records:
        return {"n": 0, "accuracy": None, "brier": None, "ece": None}
    n = len(records)
    ece = 0
    for bucket in range(10):
        values = [r for r in records if min(9, int(r[1] * 10)) == bucket]
        if values:
            ece += len(values) / n * abs(sum(r[0] for r in values) / len(values) - sum(r[1] for r in values) / len(values))
    return {"n": n, "accuracy": round(sum(r[0] for r in records) / n, 6),
            "brier": round(sum(r[2] for r in records) / n, 6), "ece": round(ece, 6)}


def evaluate_agent(agent, rows, progress=None):
    """Run actual predict calls; expose pure metric logic for deterministic unit tests."""
    records, latencies = {}, []
    failures = invalid_labels = expected_actions = 0
    errors = []
    for index, row in enumerate(rows):
        labeled = {}
        for qid, question in row["questions"].items():
            target = _target(row, qid, question)
            if target is not None:
                labeled[qid] = (question, target)
        if "action" not in labeled or labeled["action"][0]["type"] != "choice":
            raise ValueError("모든 평가 사례에는 action 선택 정답이 필요합니다.")
        expected_actions += 1
        try:
            started = time.perf_counter()
            answers = agent.predict(row["state"], row["questions"])["answers"]
            latencies.append((time.perf_counter() - started) * 1000)
            for qid, (question, (keys, target)) in labeled.items():
                answer = answers[qid]
                probabilities = _probabilities(answer, keys, question)
                predicted = max(range(len(keys)), key=lambda i: probabilities[i])
                if question["type"] == "choice":
                    if answer.get("choice") not in keys:
                        invalid_labels += 1
                        raise ValueError("예측 행동이 질문 선택지에 없습니다.")
                    predicted = keys.index(answer["choice"])
                best = max(target)
                correct = target[predicted] == best
                brier = sum((value - gold) ** 2 for value, gold in zip(probabilities, target))
                records.setdefault(qid, []).append((correct, probabilities[predicted], brier))
        except Exception as exc:
            failures += 1
            if len(errors) < 10:
                errors.append("row %d: %s" % (index + 1, str(exc)[:300]))
        if progress:
            progress(index + 1, len(rows))
    flat = [record for group in records.values() for record in group]
    result = _metrics(flat)
    per_question = {qid: _metrics(group) for qid, group in records.items()}
    action = per_question.get("action", {"n": 0, "accuracy": None})
    latency = sorted(latencies)
    result.update(rows=len(rows), questions=len(flat), action_count=action["n"], expected_actions=expected_actions,
                  action_accuracy=action["accuracy"], per_question=per_question,
                  inference_failures=failures, invalid_labels=invalid_labels, errors=errors,
                  mean_latency_ms=round(sum(latency) / len(latency), 3) if latency else None,
                  p95_latency_ms=round(latency[max(0, math.ceil(len(latency) * .95) - 1)], 3) if latency else None)
    return result


def promotion_gate(evaluation):
    before, after = evaluation.get("before", {}), evaluation.get("after", {})
    reasons = []
    if not evaluation.get("held_out") or not evaluation.get("eval_count", 0):
        reasons.append("비어 있지 않은 독립 평가가 필요합니다.")
    for name, result in (("기본 모델", before), ("새 모델", after)):
        if result.get("inference_failures", 1) or result.get("invalid_labels", 1):
            reasons.append("%s의 추론 오류/잘못된 행동이 있습니다." % name)
        if not result.get("action_count") or result.get("action_count") != evaluation.get("eval_count"):
            reasons.append("%s가 모든 평가 행동을 답하지 못했습니다." % name)
    b, a = before.get("action_accuracy"), after.get("action_accuracy")
    if not isinstance(b, (int, float)) or not isinstance(a, (int, float)) or not math.isfinite(b) or not math.isfinite(a) or a < b:
        reasons.append("새 모델의 행동 정답률이 기본 모델 이상이어야 합니다.")
    return {"allowed": not reasons, "reasons": reasons}


def _progress(work, stage, progress):
    _write(Path(work) / "progress.json", {"stage": stage, "progress": progress})
    print(stage, flush=True)


def run_worker(request):
    work = Path(request["work_dir"])
    if request["kind"] == "train":
        from laya.train import TrainConfig, finetune, resolve_checkpoint_dir
        options = validate_options(request["options"])
        snapshot = request["snapshot"]
        held_out_rows(snapshot)
        if dataset_fingerprints(snapshot) != request["dataset_fingerprints"]:
            raise ValueError("고정 데이터셋이 변경되었습니다.")
        _progress(work, "기본 체크포인트 준비", .02)
        base_path = resolve_checkpoint_dir(options["base"])
        base_hash = checkpoint_fingerprint(base_path, trusted_base=True)
        output = work.parent / "checkpoint"
        _progress(work, "학습 중", .05)
        config = TrainConfig(**{key: value for key, value in options.items() if key != "base"},
                             loss="soft-ce", shuffle_options=("choice",), eval_data=snapshot["eval_path"], log_every=1)
        summary = finetune(snapshot["train_path"], base_path, str(output), config=config)
        if not summary.get("train_report", {}).get("is_held_out") or not summary.get("eval_items", 0):
            raise ValueError("토큰화 후 학습/평가 입력이 겹치거나 평가 사례를 사용할 수 없습니다.")
        artifact_hash = checkpoint_fingerprint(output)
        if checkpoint_fingerprint(base_path, trusted_base=True) != base_hash or dataset_fingerprints(snapshot) != request["dataset_fingerprints"]:
            raise ValueError("학습 중 기본 모델 또는 데이터셋이 변경되었습니다.")
        checkpoint = {"id": request["checkpoint_id"], "run_id": request["run_id"], "game": request["game"],
                      "path": str(output.resolve()), "version": request["context"]["version"], "schema_hash": request["context"]["schema_hash"],
                      "base": options["base"], "base_path": str(Path(base_path).resolve()),
                      "base_hash": base_hash, "artifact_hash": artifact_hash, "dataset_hash": snapshot["dataset_hash"],
                      "snapshot": snapshot, "dataset_fingerprints": request["dataset_fingerprints"],
                      "created_at": _now(), "options": options,
                      "training_summary": {key: summary.get(key) for key in ("train_items", "calibration_items", "skipped", "epoch_loss")}}
        _write(work.parent / "checkpoint-manifest.json", checkpoint)
        return {"ok": True, "checkpoint": checkpoint}
    if request["kind"] != "evaluate":
        raise ValueError("지원하지 않는 작업입니다.")
    import laya
    checkpoint = request["checkpoint"]
    snapshot = checkpoint["snapshot"]
    rows = held_out_rows(snapshot)
    if dataset_fingerprints(snapshot) != checkpoint["dataset_fingerprints"]:
        raise ValueError("데이터셋 지문이 변경되었습니다.")
    for path, fingerprint, trusted in ((checkpoint["path"], checkpoint["artifact_hash"], False), (checkpoint["base_path"], checkpoint["base_hash"], True)):
        if checkpoint_fingerprint(path, trusted_base=trusted) != fingerprint:
            raise ValueError("평가 대상 모델의 지문이 변경되었습니다.")
    results = []
    for offset, (name, path) in enumerate((("기본 모델 평가", checkpoint["base_path"]), ("새 모델 평가", checkpoint["path"]))):
        _progress(work, name + " 준비", offset * .5)
        agent = laya.load(path)
        results.append(evaluate_agent(agent, rows, lambda n, total: _progress(work, "%s %d/%d" % (name, n, total), offset * .5 + n / total * .5)))
        del agent
        gc.collect()
        # Release accelerator allocations before loading the second full model.
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    if dataset_fingerprints(snapshot) != checkpoint["dataset_fingerprints"]:
        raise ValueError("평가 중 데이터셋이 변경되었습니다.")
    if checkpoint_fingerprint(checkpoint["path"]) != checkpoint["artifact_hash"] or checkpoint_fingerprint(checkpoint["base_path"], trusted_base=True) != checkpoint["base_hash"]:
        raise ValueError("평가 중 모델이 변경되었습니다.")
    evaluation = {"before": results[0], "after": results[1], "held_out": True, "eval_count": len(rows),
                  "artifact_hash": checkpoint["artifact_hash"], "base_hash": checkpoint["base_hash"],
                  "dataset_hash": checkpoint["dataset_hash"], "evaluated_at": _now()}
    evaluation["gate"] = promotion_gate(evaluation)
    return {"ok": True, "evaluation": evaluation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path)
    arguments = parser.parse_args()
    request = _read(arguments.worker)
    work = Path(request["work_dir"])
    try:
        result = run_worker(request)
    except Exception as exc:
        print("%s: %s" % (type(exc).__name__, exc), flush=True)
        _write(work / "result.json", {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
        return 1
    _write(work / "result.json", result)
    _progress(work, "completed", 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
