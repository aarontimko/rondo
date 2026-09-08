#!/usr/bin/env python3
"""
name: render
summary: Render a bar range or a named region to a 44.1k stereo wav, synchronously.
needs: reaper-running
usage: python scripts/render.py --from 1 --to 8 --out /private/tmp/take.wav | python scripts/render.py --region A2 --out /private/tmp/a2.wav
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper  # noqa: E402

# Action 42230 = "render project, using the most recent render settings,
# auto-close render dialog". It is SYNCHRONOUS and opens no dialog, which is
# the whole reason rondo can render in a loop. Never swap it for 41824.
LUA = r"""
local DIR, STEM = %(dir)s, %(stem)s
local REGION = %(region)s
local FROM_QN, TO_QN = %(from)s, %(to)s

local t0, t1
if REGION then
  local i = 0
  while true do
    local ok, isrgn, pos, rend, name = reaper.EnumProjectMarkers(i)
    if ok == 0 then break end
    if isrgn and name == REGION then t0, t1 = pos, rend break end
    i = i + 1
  end
  if not t0 then error("no region named " .. REGION) end
else
  t0 = reaper.TimeMap2_QNToTime(0, FROM_QN)
  t1 = reaper.TimeMap2_QNToTime(0, TO_QN)
end

reaper.GetSetProjectInfo_String(0, "RENDER_FILE", DIR, true)
reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", STEM, true)
reaper.GetSetProjectInfo_String(0, "RENDER_FORMAT", "evaw", true)   -- wav
reaper.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, true)             -- master mix
reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 0, true)           -- custom range
reaper.GetSetProjectInfo(0, "RENDER_STARTPOS", t0, true)
reaper.GetSetProjectInfo(0, "RENDER_ENDPOS", t1, true)
reaper.GetSetProjectInfo(0, "RENDER_SRATE", 44100, true)
reaper.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, true)
reaper.GetSetProjectInfo(0, "RENDER_ADDTOPROJ", 0, true)            -- do not import

local start = reaper.time_precise()
reaper.Main_OnCommand(42230, 0)
log(jsonenc({ seconds = reaper.time_precise() - start,
              start = t0, ["end"] = t1, span = t1 - t0,
              file = DIR .. "/" .. STEM .. ".wav" }))
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="--from/--to are inclusive 1-based bars (4/4 assumed). Output must "
               "be outside the repo, or on a gitignored path: audio is never "
               "committed.",
    )
    ap.add_argument("--from", dest="start", type=int)
    ap.add_argument("--to", dest="stop", type=int)
    ap.add_argument("--region", help="render a named region instead of a bar range")
    ap.add_argument("--out", required=True, help="output .wav path")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if bool(a.region) == bool(a.start or a.stop):
        ap.error("give either --region NAME or --from X --to Y")
    if not a.region and (a.start is None or a.stop is None):
        ap.error("--from and --to must both be given")

    out = _cli.guard_output_path(a.out)
    if out.suffix.lower() != ".wav":
        raise SystemExit(f"--out must end in .wav (got {out.name})")
    out.parent.mkdir(parents=True, exist_ok=True)

    _cli.require_reaper()
    r = reaper.run_lua_json(LUA % {
        "dir": reaper.lua_str(str(out.parent)),
        "stem": reaper.lua_str(out.stem),
        "region": reaper.lua_str(a.region) if a.region else "nil",
        "from": repr(reaper.bars_to_qn(a.start)) if a.start else "nil",
        "to": repr(reaper.bars_to_qn(a.stop + 1)) if a.stop else "nil",
    }, timeout=a.timeout)

    written = Path(r["file"])
    r["exists"] = written.exists()
    r["bytes"] = written.stat().st_size if r["exists"] else 0
    if r["exists"]:
        try:
            info = subprocess.run(["afinfo", str(written)], capture_output=True,
                                  text=True, timeout=20).stdout
            for line in info.splitlines():
                if "estimated duration" in line or "Data format" in line:
                    r.setdefault("afinfo", []).append(line.strip())
        except (OSError, subprocess.SubprocessError):
            pass

    if a.json:
        print(json.dumps(r, indent=2))
    else:
        where = f"region {a.region}" if a.region else f"bars {a.start}-{a.stop}"
        print(f"rendered {where} ({r['span']:.2f}s of audio) in {r['seconds']:.1f}s")
        print(f"  {written}  {r['bytes']} bytes")
        for line in r.get("afinfo", []):
            print(f"  {line}")
    return 0 if r["exists"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
