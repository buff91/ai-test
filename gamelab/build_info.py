"""Which build of a game is running: a built executable, or this repo's source.

  binary   path, size, modified time and a short sha256 of the executable, plus the
           interface's "sources" that changed after it was built (a stale build)
  source   "{python} -m <package>": the git commit and whether those sources have uncommitted edits

interface.json "launch.sources" lists the files a build is made from, e.g.
["game.py", "game_core.py", "game_ipc.py"]; for "-m <package>" it defaults to the package folder.
"""

import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def describe(command, sources=None):
    command = [sys.executable if part == "{python}" else part for part in command]
    if command[0] == sys.executable:
        module = command[2] if len(command) > 2 and command[1] == "-m" else None
        sources = sources or ([module.split(".")[0]] if module else [])
        return {"kind": "source", "python": sys.executable, "module": module, **_git(sources)}
    path = Path(command[0]) if Path(command[0]).is_absolute() else ROOT / command[0]
    return {"kind": "binary", **file_info(path), "stale_sources": _newer_sources(path, sources or []),
            "sources": sources or [], **_git(sources or [])}


def file_info(path):
    path = Path(path)
    if not path.exists():
        return {"path": _rel(path), "exists": False}
    stat = path.stat()
    target = path / "Contents" / "MacOS" if path.is_dir() and path.suffix == ".app" else path
    if target.is_dir():
        target = next((p for p in sorted(target.iterdir()) if p.is_file()), None) or path
    return {"path": _rel(path), "exists": True, "size": stat.st_size if path.is_file() else None,
            "built_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "sha256": _sha(target) if Path(target).is_file() else None}


def executable_of(pid):
    """The executable behind a process id (for a kbm game window), or None."""
    try:
        out = subprocess.run(["ps", "-o", "comm=", "-p", str(int(pid))], capture_output=True, text=True, timeout=2)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _newer_sources(binary, sources):
    if not binary.exists():
        return []
    built = binary.stat().st_mtime
    stale = []
    for source in sources:
        for path in _files(ROOT / source):
            if path.stat().st_mtime > built:
                stale.append(_rel(path))
    return stale


def _files(path):
    if path.is_file():
        return [path]
    if path.is_dir():
        return [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]
    return []


def _git(sources):
    def run(*args):
        try:
            out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.rstrip("\n") if out.returncode == 0 else None

    commit = run("rev-parse", "--short", "HEAD")
    if commit is None:
        return {"git": None}
    dirty = run("status", "--porcelain", "--", *sources) if sources else run("status", "--porcelain")
    return {"git": {"commit": commit, "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
                    "dirty": [line[3:] for line in (dirty or "").splitlines()]}}


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _rel(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
