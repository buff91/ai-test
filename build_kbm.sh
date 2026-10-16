#!/bin/zsh
# Build KbmAgent.app: the only process that captures the screen and sends keyboard/mouse input.
# macOS ties Screen Recording and Accessibility permissions to this app, so grant them once
# (System Settings > Privacy & Security). An ad-hoc signed rebuild may need them granted again.
set -euo pipefail
cd "${0:A:h}"
export UV_CACHE_DIR="${PWD}/.uv-cache"
uv run --with pyinstaller python -m PyInstaller \
  --noconfirm --clean --windowed --onedir \
  --name KbmAgent \
  --osx-bundle-identifier local.jevlayalab.kbmagent \
  --hidden-import PIL.Image \
  --collect-submodules kbm --collect-submodules gamelab \
  --exclude-module laya --exclude-module laya_mlx --exclude-module torch --exclude-module mlx \
  kbm_agent.py
print "\nKbmAgent: ${PWD}/dist/KbmAgent.app"
print "게임 케이스는 빌드에 넣지 않고 실행할 때 games/ 폴더에서 읽습니다."
