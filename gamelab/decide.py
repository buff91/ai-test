"""Ask Jev, Laya, or Laya-MLX with a game case's own questions."""

import json
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy

from settings import PROVIDERS


_ROUTERS = {}
_ROUTER_LOCK = threading.Lock()


def call_jev(state, key, model, timeout, questions):
    payload = json.dumps(
        {"model": model, "state": state, "questions": questions},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        PROVIDERS["jev"]["endpoint"],
        data=payload,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def make_laya_router():
    try:
        from laya import Router
    except ImportError as exc:
        raise RuntimeError("Laya is not installed. Run 'uv sync' in this folder.") from exc
    return Router()


def make_laya_mlx_router():
    try:
        from laya_mlx import Router
    except ImportError as exc:
        raise RuntimeError("Laya-MLX is not installed. Run 'uv sync' in this folder.") from exc
    return Router()


def _router(provider):
    """Reuse each runtime's router and its lazily loaded checkpoint cache."""
    with _ROUTER_LOCK:
        cached = provider in _ROUTERS
        if not cached:
            _ROUTERS[provider] = {"laya": make_laya_router, "laya-mlx": make_laya_mlx_router}[provider]()
        return _ROUTERS[provider], cached


def for_game(provider, game, key=None):
    """Decider using the model the game's type (or game.json) assigns to this provider."""
    model = game.providers["models"].get(provider)
    if provider == "jev":
        return Decider(provider, game.questions, key=key, jev_model=model)
    return Decider(provider, game.questions, laya_model=model)


class Decider:
    def __init__(self, provider, questions, key=None, jev_model=None, laya_model=None, timeout=None):
        started = time.perf_counter()
        if provider not in ("jev", "laya", "laya-mlx"):
            raise ValueError("알 수 없는 provider입니다: %s" % provider)
        if provider == "jev" and not key:
            raise ValueError("JEV_API_KEY가 설정되지 않았습니다.")
        self.provider = provider
        self.questions = questions
        self.options = questions["action"]["criteria"]
        self.key = key
        self.jev_model = jev_model or PROVIDERS["jev"]["model"]
        self.laya_model = laya_model or PROVIDERS.get(provider, {}).get("model")
        self.timeout = timeout or PROVIDERS["jev"]["timeout"]
        self.router, self.router_cached = (None, None) if provider == "jev" else _router(provider)
        # Router() is lazy: this measures initialization, not checkpoint loading.
        self.preparation_ms = round((time.perf_counter() - started) * 1000, 1)

    def decide(self, state):
        started = time.perf_counter()
        loaded_before = list(getattr(self.router, "loaded", ())) if self.router is not None else None
        try:
            if self.provider == "jev":
                result = call_jev(state, self.key, self.jev_model, self.timeout, self.questions)
            else:
                kwargs = {} if self.laya_model == "auto" else {"model": self.laya_model}
                result = self.router.predict(state, self.questions, **kwargs)
            row = summarize(result, self.options)
            row["checkpoint_cached"] = (row["checkpoint"] in loaded_before
                                        if loaded_before is not None and row.get("checkpoint") else None)
        except urllib.error.HTTPError as exc:
            # Never include headers or request data: they contain the API key.
            row = {"status": "error", "error": "Jev HTTP %s" % exc.code}
        except Exception as exc:
            message = "%s: %s" % (type(exc).__name__, exc)
            row = {"status": "error", "error": message.replace(self.key, "[redacted]") if self.key else message}
        row["provider"] = self.provider
        row["model_ms"] = round((time.perf_counter() - started) * 1000, 1)
        row["model_ms_scope"] = "api_round_trip" if self.provider == "jev" else "predict_including_lazy_load"
        return row


def summarize(result, options):
    answer = result["answers"]["action"]
    action = answer["choice"]
    if action not in options:
        raise ValueError("모델이 알 수 없는 행동을 골랐습니다: %s" % action)
    probabilities = answer.get("probabilities", {})
    return {
        "status": "ok",
        "action": action,
        "probability": probabilities.get(action),
        "confidence": answer.get("confidence"),
        "probabilities": probabilities,
        "answers": result["answers"],
        "model": result.get("model") or (result.get("routing") or {}).get("model"),
        "routing": deepcopy(result.get("routing")),
        "checkpoint": (result.get("routing") or {}).get("model"),
        "usage": result.get("usage"),
    }
