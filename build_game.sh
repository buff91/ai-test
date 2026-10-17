#!/bin/zsh
# Optional native bundle for all three teaching games. Source app profiles still
# launch Python; this bundle gives macOS a stable TeachingLab process identity.
set -euo pipefail
cd "${0:A:h}"
export UV_CACHE_DIR="${PWD}/.uv-cache"
export PYINSTALLER_CONFIG_DIR="${UV_CACHE_DIR}/pyinstaller"
# Set UV_OFFLINE=true to use only already cached build dependencies.
uv run --with pyinstaller python -m PyInstaller \
  --noconfirm --clean --windowed --onedir \
  --name TeachingLab \
  --osx-bundle-identifier local.jevlayalab.teachinglab \
  --paths "${PWD}" \
  --hidden-import tkinter \
  --collect-submodules sims \
  --exclude-module examples --exclude-module laya --exclude-module laya_mlx \
  --exclude-module torch --exclude-module mlx \
  launchers/teaching_game.py
print "\nTeachingLab: ${PWD}/dist/TeachingLab.app"
print "실행 예: open -n dist/TeachingLab.app --args --game moba --socket /tmp/teaching-moba.sock"
print "게임 선택: party | racer | moba (Finder 기본: party)"
