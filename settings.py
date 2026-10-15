"""Shared decision questions and provider defaults; restart apps after editing."""

import json
from pathlib import Path


CONFIG = Path(__file__).resolve().parent / "config"
QUESTIONS = json.loads((CONFIG / "questions.json").read_text(encoding="utf-8"))
PROVIDERS = json.loads((CONFIG / "providers.json").read_text(encoding="utf-8"))
OPTIONS = QUESTIONS["action"]["criteria"]

if QUESTIONS["action"]["type"] != "choice" or not isinstance(OPTIONS, dict) or not OPTIONS:
    raise ValueError("action must be a choice with named options")
if QUESTIONS["danger"]["type"] != "score" or not 2 <= len(QUESTIONS["danger"]["criteria"]) <= 10:
    raise ValueError("danger must be a score with 2 to 10 ordered levels")
if QUESTIONS["incoming_attack"]["type"] != "noul":
    raise ValueError("incoming_attack must be a noul question")
