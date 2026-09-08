#!/usr/bin/env python3
"""
name: run-lua
summary: Run a ReaScript file or inline snippet inside the running Reaper and print its output.
needs: reaper-running
usage: python scripts/run_lua.py script.lua | python scripts/run_lua.py -e "log(reaper.CountTracks(0))"
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import reaper  # noqa: E402

EPILOG = """\
The script runs with these globals already defined:
  log(...)          write a tab-separated line to the output this command prints
  jsonenc(v)        encode a Lua value as JSON
  bars_to_qn(b, e)  bar/beat (1-based) -> quarter notes, 4/4
  find_track(name)  -> track, index  (nil, -1 if absent)
  track_name(tr)    -> string
Anything the script raises is reported here as an error, syntax errors included.

This is the only script that does not need the bridge, so it is also the
way to restart the bridge after something stopped it:
  python scripts/run_lua.py -e 'dofile(reaper.GetResourcePath() .. "/Scripts/reaper_mcp_bridge.lua")'
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", nargs="?", help="path to a .lua file")
    ap.add_argument("-e", "--eval", help="inline Lua source")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--keep", action="store_true",
                    help="keep the generated temp scripts for inspection")
    a = ap.parse_args(argv)

    if bool(a.file) == bool(a.eval):
        ap.error("give exactly one of FILE or -e/--eval")

    src = a.eval if a.eval else open(a.file).read()
    # Deliberately no require_reaper() here. That check goes through the bridge,
    # and this is the one script that must still work when the bridge is dead --
    # loading a project stops every deferred script, so `run_lua.py` is how you
    # restart the bridge. ReaScript needs only Reaper itself.
    try:
        out = reaper.run_lua(src, timeout=a.timeout, keep=a.keep)
    except reaper.LuaError as e:
        if e.output.strip():
            print(e.output.rstrip())
        print(f"lua error: {e}", file=sys.stderr)
        return 1
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
