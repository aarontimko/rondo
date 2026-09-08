"""Bits every rondo CLI needs: import path, docstring header, path guards."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def bootstrap() -> None:
    """Make ``import rondo`` work when a script is run as ``python scripts/x.py``."""
    d = str(Path(__file__).resolve().parents[1])
    if d not in sys.path:
        sys.path.insert(0, d)


HEADER_KEYS = ("name", "summary", "needs", "usage")


def parse_header(text: str) -> dict:
    """Pull the ``name:/summary:/needs:/usage:`` block out of a module docstring."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(name|summary|needs|usage)\s*:\s*(.+?)\s*$", line)
        if m:
            out.setdefault(m.group(1), m.group(2))
    return out


def is_git_ignored(path: str | os.PathLike) -> bool:
    """True if git would ignore ``path`` (used to keep audio out of the repo)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", str(Path(path).resolve())],
            capture_output=True,
        )
    except OSError:
        return False
    return r.returncode == 0


#: The only place inside the repo that audio may be written.
AUDIO_DIR = "render"


def guard_output_path(path: str | os.PathLike) -> Path:
    """Refuse to write audio anywhere it could end up committed.

    Outside the repo: fine. Inside: only under ``render/``, and only if git
    actually ignores it. ``.gitignore`` has a blanket ``*.wav``, so the
    gitignore test alone would let a render land in ``docs/`` -- hence the
    directory rule as well.
    """
    p = Path(path).expanduser().resolve()
    try:
        rel = p.relative_to(REPO)
    except ValueError:
        return p  # outside the repo, nothing to police
    if rel.parts[:1] != (AUDIO_DIR,):
        raise SystemExit(
            f"refusing to write {p}: audio inside the repo may only go under "
            f"{REPO / AUDIO_DIR}/. Write to /private/tmp instead."
        )
    if not is_git_ignored(p):
        raise SystemExit(
            f"refusing to write {p}: {AUDIO_DIR}/ is not gitignored. "
            "Audio is never committed."
        )
    return p


def run(main) -> int:
    """Call a script's ``main`` and turn rondo's own errors into one clean line."""
    from rondo import reaper

    try:
        return main()
    except reaper.LuaError as e:
        if e.output.strip():
            print(e.output.rstrip(), file=sys.stderr)
        print(f"error (reascript): {e}", file=sys.stderr)
        return 1
    except (reaper.BridgeError, TimeoutError) as e:
        print(f"error (reaper): {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def require_reaper() -> str:
    """Exit with a clear message if the bridge is not answering."""
    from rondo import reaper

    v = reaper.is_running()
    if not v:
        raise SystemExit(
            "Reaper is not answering. Start Reaper, then load the bridge:\n"
            "  open -a REAPER "
            "~/'Library/Application Support/REAPER/Scripts/reaper_mcp_bridge.lua'"
        )
    return v
