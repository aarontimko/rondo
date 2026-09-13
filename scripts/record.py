#!/usr/bin/env python3
"""
name: record
summary: Arm a track for the mic or the virtual keyboard, set monitoring and the metronome, or disarm everything.
needs: reaper-running
usage: python scripts/record.py --track "Melody (keys)" --source keyboard --monitor on --metronome on | python scripts/record.py --disarm-all
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper, tracks  # noqa: E402

# The I_RECINPUT encoding lives in rondo/tracks.py. "none" is a real input
# setting (track.py set-input none), but arming a track for nothing is not a
# recording setup, so it is not offered here.
# The virtual keyboard's octave is the "Center note" control in its window;
# there is no action for it, so a human sets that by hand.
SOURCES = {k: v for k, v in tracks.REC_INPUTS.items() if v >= 0}

# TI is the index rondo/tracks.py resolved --track to, or nil for "not there,
# create it under NAME". Prefix matching is off for the same reason as
# add_instrument: a miss here creates a track.
LUA = r"""
local TI, NAME = %(index)s, %(name)s
local SOURCE, MONITOR, ARM = %(source)s, %(monitor)s, %(arm)s
local METRONOME, DISARM_ALL = %(metronome)s, %(disarm_all)s

reaper.Undo_BeginBlock()
local disarmed = 0
if DISARM_ALL then
  for t = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, t)
    if reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") == 1 then
      reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", 0); disarmed = disarmed + 1
    end
  end
end

local row = nil
if NAME then
  local tr, ti, created = nil, TI, false
  if TI then
    tr = reaper.GetTrack(0, TI)
    if not tr then error("no track at index " .. TI, 0) end
  else
    local at = reaper.CountTracks(0)
    reaper.InsertTrackAtIndex(at, true)
    tr = reaper.GetTrack(0, at); ti = at; created = true
    reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", NAME, true)
  end
  if SOURCE then reaper.SetMediaTrackInfo_Value(tr, "I_RECINPUT", SOURCE) end
  if MONITOR then reaper.SetMediaTrackInfo_Value(tr, "I_RECMON", MONITOR) end
  if ARM ~= nil then reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", ARM and 1 or 0) end
  row = { track = track_name(tr), index = ti, created = created,
          rec_input = reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT"),
          monitor = reaper.GetMediaTrackInfo_Value(tr, "I_RECMON"),
          armed = reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") == 1 }
end

local metro = reaper.GetToggleCommandState(40364) == 1
if METRONOME ~= nil and METRONOME ~= metro then
  reaper.Main_OnCommand(40364, 0)      -- Options: Toggle metronome
  metro = reaper.GetToggleCommandState(40364) == 1
end
reaper.Undo_EndBlock("rondo: record setup", -1)
reaper.TrackList_AdjustWindows(false)

log(jsonenc({ track = row, disarmed = disarmed, metronome = metro }))
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="This only sets a track up. Press record in Reaper yourself -- "
               "rondo never starts or stops the transport for you.",
    )
    ap.add_argument("--track", help="track name (found, or created)")
    ap.add_argument("--source", choices=sorted(SOURCES),
                    help="mic = first mono hardware input; keyboard = Reaper's virtual MIDI keyboard")
    ap.add_argument("--monitor", choices=["on", "off", "auto"])
    ap.add_argument("--metronome", choices=["on", "off"])
    ap.add_argument("--arm", choices=["on", "off"], default="on")
    ap.add_argument("--disarm-all", action="store_true",
                    help="disarm every track (do this before rendering)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if not a.track and not a.disarm_all and not a.metronome:
        ap.error("nothing to do: give --track, --metronome or --disarm-all")

    mon = {"off": 0, "on": 1, "auto": 2}.get(a.monitor)
    _cli.require_reaper()
    ti = None
    if a.track:
        ti = tracks.find_or_none(a.track, tracks.snapshot())
    r = reaper.run_lua_json(LUA % {
        "index": "nil" if ti is None else str(ti),
        "name": reaper.lua_str(a.track.strip()) if a.track else "nil",
        "source": SOURCES[a.source] if a.source else "nil",
        "monitor": mon if mon is not None else "nil",
        "arm": ("true" if a.arm == "on" else "false") if a.track else "nil",
        "metronome": {"on": "true", "off": "false"}.get(a.metronome, "nil"),
        "disarm_all": "true" if a.disarm_all else "false",
    }, timeout=30.0)

    if a.json:
        print(json.dumps(r, indent=2))
    else:
        if r["disarmed"]:
            print(f"disarmed {r['disarmed']} track(s)")
        t = r.get("track")   # absent when no --track was given
        if t:
            print(f"track {t['index']} \"{t['track']}\""
                  f"{' (created)' if t['created'] else ''}: "
                  f"input {t['rec_input']:g}, monitor {t['monitor']:g}, "
                  f"{'ARMED' if t['armed'] else 'not armed'}")
            if a.source == "keyboard":
                print("  set the virtual keyboard's octave with its \"Center note\" "
                      "control (View > Virtual MIDI keyboard)")
        print(f"metronome {'on' if r['metronome'] else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
