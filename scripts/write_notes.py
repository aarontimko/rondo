#!/usr/bin/env python3
"""
name: write-notes
summary: Write a melody from a grid text file (or JSON notes) onto a track at a bar.
needs: reaper-running
usage: python scripts/write_notes.py --track "Melody" --bar 9 --grid melody.txt [--velocity 100] [--replace] [--transpose -12]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, grid as gridmod, reaper  # noqa: E402

LUA = r"""
local NAME = %(name)s
local START_QN, END_QN = %(start)r, %(end)r
local NOTES = %(notes)s
local REPLACE = %(replace)s

local tr = find_track(NAME)
if not tr then error("no track named " .. NAME) end

reaper.Undo_BeginBlock()
local removed = 0
if REPLACE then
  local t0 = reaper.TimeMap2_QNToTime(0, START_QN)
  local t1 = reaper.TimeMap2_QNToTime(0, END_QN)
  for i = reaper.CountTrackMediaItems(tr) - 1, 0, -1 do
    local it = reaper.GetTrackMediaItem(tr, i)
    local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local e = p + reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    if p < t1 - 1e-9 and e > t0 + 1e-9 then
      reaper.DeleteTrackMediaItem(tr, it); removed = removed + 1
    end
  end
end

local item = reaper.CreateNewMIDIItemInProj(tr, START_QN, END_QN, true)
local take = reaper.GetActiveTake(item)
for _, n in ipairs(NOTES) do
  local s = reaper.MIDI_GetPPQPosFromProjQN(take, START_QN + n[1])
  local e = reaper.MIDI_GetPPQPosFromProjQN(take, START_QN + n[1] + n[2])
  reaper.MIDI_InsertNote(take, false, false, s, e, 0, n[3], n[4], true)
end
reaper.MIDI_Sort(take)
reaper.Undo_EndBlock("rondo: write notes on " .. NAME, -1)
reaper.UpdateArrange()

log(jsonenc({
  track = NAME, notes = #NOTES, removed = removed,
  start_qn = START_QN, end_qn = END_QN,
  item_position = reaper.GetMediaItemInfo_Value(item, "D_POSITION"),
  item_length = reaper.GetMediaItemInfo_Value(item, "D_LENGTH"),
  midi_notes = select(2, reaper.MIDI_CountEvts(take)),
}))
"""


def notes_from_grid(text: str, velocity: int, transpose: int
                    ) -> tuple[list[list], int]:
    notes, spb = gridmod.parse(text)
    triples = gridmod.to_qn(notes, spb)
    total_slots = max((n.slot + n.length for n in notes), default=0)
    bars = max(1, -(-total_slots // spb))
    return [[s, l, m + transpose, velocity] for s, l, m in triples], bars


def notes_from_json(text: str, velocity: int, transpose: int) -> tuple[list[list], int]:
    raw = json.loads(text)
    out = []
    for n in raw:
        if isinstance(n, dict):
            s, l, m = n["start"], n["length"], n["midi"]
            v = n.get("velocity", velocity)
        else:
            s, l, m = n[0], n[1], n[2]
            v = n[3] if len(n) > 3 else velocity
        out.append([float(s), float(l), int(m) + transpose, int(v)])
    end_qn = max((s + l for s, l, _, _ in out), default=0.0)
    return out, max(1, int(-(-end_qn // 4)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="--notes takes JSON [[start_qn, length_qn, midi, velocity?], ...] "
               "with start_qn measured from --bar. 4/4 is assumed for bar math.",
    )
    ap.add_argument("--track", required=True)
    ap.add_argument("--bar", type=int, required=True, help="1-based bar to start at")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--grid", help="path to a grid text file, or - for stdin")
    src.add_argument("--notes", help="inline JSON note list, or @file.json")
    ap.add_argument("--velocity", type=int, default=100)
    ap.add_argument("--transpose", type=int, default=0, help="semitones")
    ap.add_argument("--bars", type=int, help="force the item length in bars")
    ap.add_argument("--replace", action="store_true",
                    help="delete items overlapping the target bars first")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.grid:
        text = sys.stdin.read() if a.grid == "-" else open(a.grid).read()
        notes, bars = notes_from_grid(text, a.velocity, a.transpose)
    else:
        text = open(a.notes[1:]).read() if a.notes.startswith("@") else a.notes
        notes, bars = notes_from_json(text, a.velocity, a.transpose)

    if not notes:
        raise SystemExit("nothing to write: the grid has no notes")
    bad = [n for n in notes if not 0 <= n[2] <= 127]
    if bad:
        raise SystemExit(f"transposed out of MIDI range: {bad[:3]}")

    bars = a.bars or bars
    start = reaper.bars_to_qn(a.bar)
    end = start + bars * 4

    _cli.require_reaper()
    r = reaper.run_lua_json(LUA % {
        "name": reaper.lua_str(a.track),
        "start": start,
        "end": end,
        "notes": reaper.lua_value(notes),
        "replace": "true" if a.replace else "false",
    }, timeout=30.0)

    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"wrote {r['midi_notes']} note(s) to \"{r['track']}\" at bar {a.bar} "
              f"({bars} bar item, {r['item_length']:.2f}s)"
              + (f", removed {r['removed']} existing item(s)" if a.replace else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
