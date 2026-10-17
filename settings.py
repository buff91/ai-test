"""Shared provider defaults; restart apps after editing."""

import json
from pathlib import Path


CONFIG = Path(__file__).resolve().parent / "config"
PROVIDERS = json.loads((CONFIG / "providers.json").read_text(encoding="utf-8"))
