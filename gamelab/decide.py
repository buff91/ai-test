"""Ask Jev, Laya, or Laya-MLX with a game case's own questions."""

import time
import urllib.error

from compare import call_jev, make_laya_mlx_router, make_laya_router
from settings import PROVIDERS


_ROUTERS = {}


def _router(provider):
    """One router per runtime for the whole process, so a matrix run loads each model once."""
    if provider not in _ROUTERS:
        _ROUTERS[provider] = {"laya": make_laya_router, "laya-mlx": make_laya_mlx_router}[provider]()
    return _ROUTERS[provider]


def for_game(provider, game, key=None):
    """Decider using the model the game's type (or game.json) assigns to this provider."""
    model = game.providers["models"].get(provider)
    if provider == "jev":
        return Decider(provider, game.questions, key=key, jev_model=model)
    return Decider(provider, game.questions, laya_model=model)


class Decider:
    def __init__(self, provider, questions, key=None, jev_model=None, laya_model=None, timeout=None):
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
        self.router = None if provider == "jev" else _router(provider)

    def decide(self, state):
        started = time.perf_counter()
        try:
            if self.provider == "jev":
                result = call_jev(state, self.key, self.jev_model, self.timeout, self.questions)
            else:
                kwargs = {} if self.laya_model == "auto" else {"model": self.laya_model}
                result = self.router.predict(state, self.questions, **kwargs)
            row = summarize(result, self.options)
        except urllib.error.HTTPError as exc:
            # Never include headers or request data: they contain the API key.
            row = {"status": "error", "error": "Jev HTTP %s" % exc.code}
        except Exception as exc:
            row = {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
        row["provider"] = self.provider
        row["model_ms"] = round((time.perf_counter() - started) * 1000, 1)
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
        "model": result.get("model") or result.get("routing", {}).get("model"),
        "usage": result.get("usage"),
    }
