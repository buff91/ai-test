#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
export UV_CACHE_DIR="${PWD}/.uv-cache"
uv run --with pyinstaller python -m PyInstaller \
  --noconfirm --clean --onedir \
  --name ArenaGame game.py
print "\n게임 실행 파일: ${PWD}/dist/ArenaGame/ArenaGame"
