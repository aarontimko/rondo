#!/usr/bin/env python3
"""
name: status
summary: Read-only summary of the open Reaper project: tempo, tracks, FX, regions, cursor.
needs: reaper-running
usage: python scripts/status.py [--json] [--project N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper  # noqa: E402

LUA = r"""
-- ReaProject 0 means "the ACTIVE project tab", NOT "tab 0". Tab pointers come
-- from EnumProjects(index); EnumProjects(-1) is the active one. Every other
-- rondo script works on the active tab, so that is the default here too.
local IDX = %d
local P = 0
if IDX >= 0 then
  P = reaper.EnumProjects(IDX, "")
  if not P then error("no project tab " .. IDX) end
end
local active = reaper.EnumProjects(-1, "")

local tabs = {}
local i = 0
while true do
  local proj, path = reaper.EnumProjects(i, "")
  if not proj then break end
  -- NOTE: a project saved only via Main_SaveProjectEx(_, path, 0) keeps an
  -- EMPTY name/path here -- Reaper wrote a copy, it did not adopt the file.
  local _, name = reaper.GetSetProjectInfo_String(proj, "PROJECT_NAME", "", false)
  tabs[#tabs+1] = { index = i, name = name, path = path,
                    active = proj == active,
                    dirty = reaper.IsProjectDirty(proj) == 1 }
  i = i + 1
end

local num, den, bpm = reaper.TimeMap_GetTimeSigAtTime(P, 0)
local tracks = {}
for t = 0, reaper.CountTracks(P) - 1 do
  local tr = reaper.GetTrack(P, t)
  local fx = {}
  for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
    local _, n = reaper.TrackFX_GetFXName(tr, f, "")
    fx[#fx+1] = n
  end
  tracks[#tracks+1] = {
    index = t,
    name = track_name(tr),
    fx = fx,
    instrument_fx = reaper.TrackFX_GetInstrument(tr),
    items = reaper.CountTrackMediaItems(tr),
    mute = reaper.GetMediaTrackInfo_Value(tr, "B_MUTE") == 1,
    solo = reaper.GetMediaTrackInfo_Value(tr, "I_SOLO") ~= 0,
    armed = reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") == 1,
    monitor = reaper.GetMediaTrackInfo_Value(tr, "I_RECMON"),
    rec_input = reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT"),
  }
end

local regions, markers = {}, {}
local k = 0
while true do
  local ok, isrgn, pos, rend, name, idx = reaper.EnumProjectMarkers3(P, k)
  if ok == 0 then break end
  -- qn_to_bar, not math.floor: a region ending exactly on the bar 25 line
  -- converts to 95.999999999 quarter notes, and flooring that reports the
  -- region a whole bar short.
  local row = { name = name, index = idx, position = pos,
                bar = qn_to_bar(reaper.TimeMap2_timeToQN(P, pos)) }
  if isrgn then
    row["end"] = rend
    row.end_bar = qn_to_bar(reaper.TimeMap2_timeToQN(P, rend))
    regions[#regions+1] = row
  else
    markers[#markers+1] = row
  end
  k = k + 1
end

local cursor = reaper.GetCursorPositionEx(P)
local last = 0
for t = 0, reaper.CountTracks(P) - 1 do
  local tr = reaper.GetTrack(P, t)
  for m = 0, reaper.CountTrackMediaItems(tr) - 1 do
    local it = reaper.GetTrackMediaItem(tr, m)
    local e = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
            + reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    if e > last then last = e end
  end
end

log(jsonenc({
  reaper_version = reaper.GetAppVersion(),
  project_index = IDX,
  project_tabs = tabs,
  bpm = bpm, timesig = { num, den },
  cursor = cursor,
  cursor_bar = qn_to_bar(reaper.TimeMap2_timeToQN(P, cursor)),
  length_seconds = last,
  length_bars = math.floor(reaper.TimeMap2_timeToQN(P, last) / 4 + 0.5),
  tracks = tracks,
  regions = regions,
  markers = markers,
  play_state = reaper.GetPlayStateEx(P),
}))
"""


def collect(project: int = -1) -> dict:
    return reaper.run_lua_json(LUA % project)


def render_text(s: dict) -> str:
    out = []
    tabs = s["project_tabs"]
    names = ", ".join(
        f"[{t['index']}]{'<' if t['active'] else ' '}"
        f"{t['name'] or t['path'] or '(unsaved)'}"
        f"{'*' if t['dirty'] else ''}"
        for t in tabs
    )
    out.append(f"Reaper {s['reaper_version']}  |  {len(tabs)} tab(s): {names}")
    out.append(
        f"active project: {s['bpm']:g} bpm, "
        f"{s['timesig'][0]}/{s['timesig'][1]}, "
        f"{s['length_bars']} bars ({s['length_seconds']:.1f}s), "
        f"cursor bar {s['cursor_bar']} ({s['cursor']:.2f}s), "
        f"play_state {s['play_state']}"
    )
    out.append("")
    out.append(f"{len(s['tracks'])} track(s):")
    for t in s["tracks"]:
        flags = "".join(
            [
                "M" if t["mute"] else "-",
                "S" if t["solo"] else "-",
                "R" if t["armed"] else "-",
            ]
        )
        fx = ", ".join(t["fx"]) or "(no fx)"
        out.append(
            f"  {t['index']:>2}  {flags}  {t['name'] or '(unnamed)':<34} "
            f"{t['items']:>3} item(s)  {fx}"
        )
    out.append("")
    if s["regions"]:
        out.append(f"{len(s['regions'])} region(s):")
        for r in s["regions"]:
            # end_bar is the bar the region ENDS ON (exclusive boundary), so a
            # region covering bars 1-8 reports bar 1, end_bar 9.
            out.append(
                f"  {r['name']:<20} bars {r['bar']}-{max(r['bar'], r['end_bar'] - 1)}  "
                f"({r['position']:.2f}s..{r['end']:.2f}s)"
            )
    else:
        out.append("no regions")
    if s["markers"]:
        out.append(f"{len(s['markers'])} marker(s): "
                   + ", ".join(f"{m['name']}@bar{m['bar']}" for m in s["markers"]))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--project", type=int, default=-1,
                    help="project TAB index (default: whichever tab is active)")
    a = ap.parse_args(argv)

    _cli.require_reaper()
    s = collect(a.project)
    print(json.dumps(s, indent=2) if a.json else render_text(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
