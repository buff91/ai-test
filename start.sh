#!/bin/zsh
# Common entry point. The console selects the game, version and execution mode.
set -euo pipefail
cd "${0:A:h}"
export UV_CACHE_DIR="${PWD}/.uv-cache"
exec uv run python web_console.py "$@"
