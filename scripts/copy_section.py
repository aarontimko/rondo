#!/usr/bin/env python3
"""
name: copy-section
summary: Copy bars X..Y to bar Z on every track (or named tracks) and optionally name the result as a region.
needs: reaper-running
usage: python scripts/copy_section.py --from 1 --to 8 --at 17 [--region A3] [--tracks "Drums,Bass"] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper, tracks  # noqa: E402

# Reaper has no "copy items in a time range to a position" call. The
# deterministic route is chunk-level: GetItemStateChunk -> AddMediaItemToTrack
# -> SetItemStateChunk, then move the copy. Fresh GUIDs so the copies are not
# aliases of the originals.
LUA = r"""
local FROM_QN, TO_QN, AT_QN = %(from)r, %(to)r, %(at)r
local REGION_END_QN = %(region_end)r
local TRACKS = %(tracks)s          -- nil = every track; else 0-based INDEXES
local REGION = %(region)s          -- nil = do not add a region

local t0 = reaper.TimeMap2_QNToTime(0, FROM_QN)
local t1 = reaper.TimeMap2_QNToTime(0, TO_QN)
local ta = reaper.TimeMap2_QNToTime(0, AT_QN)
local delta = ta - t0

-- --tracks is resolved to indexes in Python (rondo/tracks.py), so a name, a
-- role, an index or a prefix all arrive here as the same thing.
local wanted = nil
if TRACKS then
  wanted = {}
  for _, i in ipairs(TRACKS) do wanted[i] = true end
end

local function fresh_guids(chunk)
  -- %%b{} matches the balanced {...} that follows a GUID/IGUID key.
  return (chunk:gsub("(\n%%s*I?GUID )%%b{}", function(a) return a .. reaper.genGuid("") end))
end

reaper.Undo_BeginBlock()
local rows, total, skipped = {}, 0, 0
for t = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, t)
  local nm = track_name(tr)
  if (not wanted) or wanted[t] then
    local copied, partial = 0, 0
    local n = reaper.CountTrackMediaItems(tr)
    for i = 0, n - 1 do
      local it = reaper.GetTrackMediaItem(tr, i)
      local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
      local e = p + reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
      if p >= t0 - 1e-9 and e <= t1 + 1e-9 then
        local _, chunk = reaper.GetItemStateChunk(it, "", false)
        local new = reaper.AddMediaItemToTrack(tr)
        reaper.SetItemStateChunk(new, fresh_guids(chunk), false)
        reaper.SetMediaItemInfo_Value(new, "D_POSITION", p + delta)
        copied = copied + 1
      elseif p < t1 - 1e-9 and e > t0 + 1e-9 then
        partial = partial + 1     -- straddles the boundary: left alone on purpose
      end
    end
    total = total + copied
    skipped = skipped + partial
    rows[#rows+1] = { track = nm, index = t, copied = copied, straddling = partial }
  end
end

-- The region is measured in BARS, like everything else, not by adding the
-- source's duration to the destination time: same helper, same replace-by-name
-- rule as project.py region.
local region = nil
if REGION then
  local rend = reaper.TimeMap2_QNToTime(0, REGION_END_QN)
  local idx, replaced = add_marker_replacing(REGION, ta, rend, true)
  region = { name = REGION, index = idx, replaced = replaced,
             start = ta, ["end"] = rend }
end
reaper.Undo_EndBlock("rondo: copy section", -1)
reaper.UpdateArrange()

log(jsonenc({ copied = total, straddling = skipped, tracks = rows, region = region }))
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="Bars are 1-based and --from/--to are inclusive, so --from 1 --to 8 "
               "is the first eight bars. Items that straddle a boundary are NOT "
               "copied; they are reported instead. --region replaces a region of "
               "the same name rather than duplicating it, exactly as project.py "
               "region does. 4/4 is assumed.",
    )
    ap.add_argument("--from", dest="start", type=int, required=True)
    ap.add_argument("--to", dest="stop", type=int, required=True)
    ap.add_argument("--at", type=int, required=True, help="destination bar")
    ap.add_argument("--tracks",
                    help="comma-separated track names or indexes (default: all)")
    ap.add_argument("--region", help="name a region over the copy")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    try:
        from_qn, to_qn = reaper.bar_span_qn(a.start, a.stop)
    except ValueError:
        raise SystemExit("--to must be >= --from")
    span = a.stop - a.start + 1
    at_qn = reaper.bars_to_qn(a.at)
    region_end_qn = reaper.bars_to_qn(a.at + span)
    if from_qn <= at_qn < to_qn:
        raise SystemExit("--at lands inside the source range; that would copy onto itself")

    _cli.require_reaper()
    want = None
    if a.tracks:
        rows = tracks.snapshot()
        want = []
        for spec in a.tracks.split(","):
            i, _ = tracks.find(spec, rows)
            if i not in want:          # naming one track twice is not two copies
                want.append(i)
    r = reaper.run_lua_json(LUA % {
        "from": from_qn, "to": to_qn, "at": at_qn,
        "region_end": region_end_qn,
        "tracks": reaper.lua_value(want) if want else "nil",
        "region": reaper.lua_str(a.region) if a.region else "nil",
    }, timeout=45.0)

    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"copied {r['copied']} item(s) from bars {a.start}-{a.stop} "
              f"to bars {a.at}-{a.at + span - 1}")
        for row in r["tracks"]:
            if row["copied"] or row["straddling"]:
                extra = (f", {row['straddling']} straddling (skipped)"
                         if row["straddling"] else "")
                print(f"  track {row['index']:>2} {row['track']}: "
                      f"{row['copied']} item(s){extra}")
        if r.get("region"):
            reg = r["region"]
            print(f"  region {reg['name']!r} over bars {a.at}-{a.at + span - 1} "
                  f"({reg['start']:.2f}s..{reg['end']:.2f}s)"
                  + (f", replaced {reg['replaced']} of the same name"
                     if reg.get("replaced") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
