"""macOS bundle entry point for the shared three-game teaching runtime."""
from __future__ import annotations
import sys
from pathlib import Path

# Allow direct source execution as well as PyInstaller's bundled import graph.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gamelab.desktop_game import main


if __name__ == "__main__":
    # Finder launches without arguments; show the turn-based lesson by default.
    if not any(arg == "--game" or arg.startswith("--game=") for arg in sys.argv[1:]):
        sys.argv.extend(("--game", "party"))
    main()
