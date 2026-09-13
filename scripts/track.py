#!/usr/bin/env python3
"""
name: track
summary: One track at a time: mute, solo, arm, rename, add, delete, clear items, set the input, set the volume, or show its full state.
needs: reaper-running
usage: python scripts/track.py show --track Pad | python scripts/track.py mute --track 3 | python scripts/track.py add --name Bass --at 1 | python scripts/track.py clear-items --track Pad --from 9 --to 16
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper, tracks  # noqa: E402

# Every mutation runs through this one wrapper so they all get an undo block,
# a redraw and the same JSON shape. The track is addressed by INDEX: track.py
# resolves --track in Python (rondo/tracks.py) against a snapshot it has
# already read, rather than teaching Lua the matching rules twice.
OP_LUA = r"""
local TI = %(index)d
local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI) end

reaper.Undo_BeginBlock()
local result = { index = TI, name = track_name(tr) }
%(body)s
reaper.Undo_EndBlock(%(undo)s, -1)
reaper.TrackList_AdjustWindows(false)
reaper.UpdateArrange()
log(jsonenc(result))
"""

# I_SOLO: 0 = off, 1 = solo, 2 = solo in place. rondo uses plain solo.
# (field, value, verb, past tense) -- the last two are only for the help text
# and the one-line confirmation.
FLAG_OPS = {
    "mute":    ("B_MUTE", 1, "mute", "muted"),
    "unmute":  ("B_MUTE", 0, "unmute", "unmuted"),
    "solo":    ("I_SOLO", 1, "solo", "soloed"),
    "unsolo":  ("I_SOLO", 0, "unsolo", "unsoloed"),
    "arm":     ("I_RECARM", 1, "arm for recording", "armed"),
    "disarm":  ("I_RECARM", 0, "disarm", "disarmed"),
}

ADD_LUA = r"""
local NAME, AT = %(name)s, %(at)s
if AT == nil then AT = reaper.CountTracks(0) end
if AT > reaper.CountTracks(0) then AT = reaper.CountTracks(0) end
reaper.Undo_BeginBlock()
reaper.InsertTrackAtIndex(AT, true)
local tr = reaper.GetTrack(0, AT)
reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", NAME, true)
reaper.Undo_EndBlock("rondo: add track " .. NAME, -1)
reaper.TrackList_AdjustWindows(false)
log(jsonenc({ index = AT, name = track_name(tr), tracks = reaper.CountTracks(0) }))
"""


def flag_body(field: str, value: int) -> str:
    return (
        f'reaper.SetMediaTrackInfo_Value(tr, "{field}", {value})\n'
        f'result.value = reaper.GetMediaTrackInfo_Value(tr, "{field}")'
    )


def rename_body(new: str) -> str:
    return (
        f"result.old_name = result.name\n"
        f"reaper.GetSetMediaTrackInfo_String(tr, \"P_NAME\", {reaper.lua_str(new)}, true)\n"
        f"result.name = track_name(tr)"
    )


DELETE_BODY = r"""
result.items = reaper.CountTrackMediaItems(tr)
result.fx = reaper.TrackFX_GetCount(tr)
reaper.DeleteTrack(tr)
result.deleted = true
result.tracks = reaper.CountTracks(0)
"""

CLEAR_BODY = r"""
local FROM_QN, TO_QN = %(from)s, %(to)s
local t0, t1
if FROM_QN then
  t0 = reaper.TimeMap2_QNToTime(0, FROM_QN)
  t1 = reaper.TimeMap2_QNToTime(0, TO_QN)
end
local removed = 0
for i = reaper.CountTrackMediaItems(tr) - 1, 0, -1 do
  local it = reaper.GetTrackMediaItem(tr, i)
  local hit = true
  if t0 then
    local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local e = p + reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    hit = p < t1 - 1e-9 and e > t0 + 1e-9        -- any overlap, same rule as --replace
  end
  if hit then reaper.DeleteTrackMediaItem(tr, it); removed = removed + 1 end
end
result.removed = removed
result.items = reaper.CountTrackMediaItems(tr)
"""

INPUT_BODY = r"""
reaper.SetMediaTrackInfo_Value(tr, "I_RECINPUT", %(value)d)
result.rec_input = reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT")
"""

VOLUME_BODY = r"""
reaper.SetMediaTrackInfo_Value(tr, "D_VOL", %(gain)r)
result.volume = reaper.GetMediaTrackInfo_Value(tr, "D_VOL")
"""


def build(command: str, a: argparse.Namespace) -> tuple[str, str]:
    """Command + parsed args -> (Lua body, undo label). No Reaper needed."""
    if command in FLAG_OPS:
        field, value, verb, _ = FLAG_OPS[command]
        return flag_body(field, value), f"rondo: {verb} track"
    if command == "rename":
        return rename_body(a.to), "rondo: rename track"
    if command == "delete":
        return DELETE_BODY, "rondo: delete track"
    if command == "clear-items":
        if (a.start is None) != (a.stop is None):
            raise SystemExit("--from and --to must be given together")
        if a.start is None:
            span = ("nil", "nil")
        else:
            try:
                span = tuple(repr(q) for q in reaper.bar_span_qn(a.start, a.stop))
            except ValueError as e:
                raise SystemExit(str(e))
        return CLEAR_BODY % {"from": span[0], "to": span[1]}, "rondo: clear items"
    if command == "set-input":
        return (INPUT_BODY % {"value": tracks.REC_INPUTS[a.source]},
                "rondo: set track input")
    if command == "volume":
        return VOLUME_BODY % {"gain": tracks.db_to_gain(a.db)}, "rondo: set track volume"
    raise SystemExit(f"unknown command {command}")


def show_text(row: dict) -> str:
    out = [f"track {row['index']} \"{row['name'] or '(unnamed)'}\""]
    out.append(
        f"  mute {'ON' if row['mute'] else 'off'}   "
        f"solo {'ON' if row['solo'] else 'off'}   "
        f"armed {'YES' if row['armed'] else 'no'}"
    )
    out.append(
        f"  input {tracks.rec_input_name(row['rec_input'])}   "
        f"monitor {tracks.MONITOR_NAMES.get(int(row['monitor']), row['monitor'])}"
    )
    db = tracks.gain_to_db(row["volume"])
    out.append(
        f"  volume {'-inf' if db == float('-inf') else format(db, '+.1f')} dB   "
        f"pan {tracks.pan_name(row['pan'])}"
    )
    fx = row["fx"]
    out.append(f"  fx: {', '.join(fx) if fx else '(none)'}"
               + (f"   [instrument in slot {row['instrument_fx']}]"
                  if row["instrument_fx"] >= 0 else ""))
    items = row["items"]
    out.append(f"  {len(items)} item(s):" if items else "  no items")
    for it in items:
        span = (f"bar {it['bar']}" if it["bar"] == it["end_bar"]
                else f"bars {it['bar']}-{it['end_bar']}")
        notes = f"  {it['midi_notes']} MIDI note(s)" if it["midi_notes"] is not None else ""
        out.append(f"    {span:<14} {it['position']:.2f}s +{it['length']:.2f}s{notes}")
    return "\n".join(out)


COMMANDS = list(FLAG_OPS) + [
    "rename", "add", "delete", "clear-items", "set-input", "volume", "show",
]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="--track takes a name or a 0-based index. Names match "
               "case-insensitively: exact name first, then the track's role "
               "(its name without the trailing ' (...)' suffix), then the "
               "index, then a prefix. An ambiguous spec is an error, never a "
               "guess. Bars are 1-based and --from/--to are inclusive.",
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, help_: str, needs_track: bool = True):
        p = sub.add_parser(name, help=help_, description=help_)
        if needs_track:
            p.add_argument("--track", required=True, help="track name or 0-based index")
        p.add_argument("--json", action="store_true")
        return p

    for name, (_, _, verb, _past) in FLAG_OPS.items():
        add(name, f"{verb[0].upper()}{verb[1:]} the track.")
    add("rename", "Rename the track.").add_argument(
        "--to", required=True, help="the new name")
    p = add("add", "Create a new track.", needs_track=False)
    p.add_argument("--name", required=True)
    p.add_argument("--at", type=int, help="0-based insert position (default: last)")
    add("delete", "Delete the track and everything on it.").add_argument(
        "--yes", action="store_true",
        help="required: the track's items and FX go with it (Reaper's own undo "
             "still has them, rondo has no undo of its own)")
    p = add("clear-items", "Delete the track's items (all of them, or a bar range).")
    p.add_argument("--from", dest="start", type=int, help="first bar (inclusive)")
    p.add_argument("--to", dest="stop", type=int, help="last bar (inclusive)")
    add("set-input", "Set what the track records from.").add_argument(
        "source", choices=sorted(tracks.REC_INPUTS))
    add("volume", "Set the track fader.").add_argument(
        "--db", type=float, required=True, help="0 is unity gain")
    add("show", "Print one track's full state.")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)

    if a.command == "delete" and not a.yes:
        raise SystemExit(
            "refusing to delete a track without --yes. Items and FX go with it."
        )
    if a.command == "add" and a.at is not None and a.at < 0:
        raise SystemExit("--at must be 0 or more (0 puts the new track first)")

    _cli.require_reaper()

    if a.command == "add":
        r = reaper.run_lua_json(ADD_LUA % {
            "name": reaper.lua_str(a.name),
            "at": "nil" if a.at is None else str(a.at),
        }, timeout=20.0)
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            print(f"added track {r['index']} \"{r['name']}\" "
                  f"({r['tracks']} track(s) now)")
        return 0

    rows = tracks.snapshot()
    index, row = tracks.find(a.track, rows)

    if a.command == "show":
        print(json.dumps(row, indent=2) if a.json else show_text(row))
        return 0

    body, undo = build(a.command, a)
    r = reaper.run_lua_json(
        OP_LUA % {"index": index, "body": body, "undo": reaper.lua_str(undo)},
        timeout=30.0,
    )

    if a.json:
        print(json.dumps(r, indent=2))
    else:
        who = f"track {r['index']} \"{r['name'] or '(unnamed)'}\""
        if a.command in FLAG_OPS:
            print(f"{who}: {FLAG_OPS[a.command][3]}")
        elif a.command == "rename":
            print(f"track {r['index']}: \"{r['old_name']}\" -> \"{r['name']}\"")
        elif a.command == "delete":
            print(f"deleted {who} ({r['items']} item(s), {r['fx']} fx); "
                  f"{r['tracks']} track(s) left")
        elif a.command == "clear-items":
            where = (f"bars {a.start}-{a.stop}" if a.start is not None else "the track")
            print(f"{who}: removed {r['removed']} item(s) from {where}, "
                  f"{r['items']} left")
        elif a.command == "set-input":
            print(f"{who}: input {tracks.rec_input_name(r['rec_input'])}")
        elif a.command == "volume":
            print(f"{who}: volume {tracks.gain_to_db(r['volume']):+.1f} dB")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
