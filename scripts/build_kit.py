#!/usr/bin/env python3
"""
name: build-kit
summary: Build a ReaSamplOmatic5000 drum kit on one track from a note-to-wav manifest.
needs: reaper-running, samples
usage: python scripts/build_kit.py [--track Drums] [--manifest samples/kit.json] [--replace] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper, tracks  # noqa: E402

DEFAULT_MANIFEST = _cli.REPO / "samples" / "kit.json"

#: The instrument label for the track's display suffix, so the Reaper track
#: list reads "Drums (RS5K kit: VCSL one-shots)" and says where the sound comes
#: from. ``--keep-name`` skips the rename.
KIT_LABEL = "RS5K kit"

# One RS5K instance per drum sound: an instance holds ONE file list, so a GM
# kit is N instances on ONE track, each narrowed to a single note.
#   param 3  = note range start   (value = note / 127)
#   param 4  = note range end
#   param 11 = obey note-offs     (0 = one-shot, let the sample ring out)
# TI is the track index rondo/tracks.py resolved --track to, or nil for "not
# there, create it". NEW_NAME = nil means --keep-name.
LUA = r"""
local TI, NAME, NEW_NAME = %(index)s, %(name)s, %(new_name)s
local KIT, REPLACE = %(kit)s, %(replace)s

reaper.Undo_BeginBlock()
local tr, ti, created = nil, TI, false
if TI then
  tr = reaper.GetTrack(0, TI)
  if not tr then error("no track at index " .. TI, 0) end
else
  local at = reaper.CountTracks(0)
  reaper.InsertTrackAtIndex(at, true)
  tr = reaper.GetTrack(0, at); ti = at; created = true
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", NEW_NAME or NAME, true)
end
if NEW_NAME and track_name(tr) ~= NEW_NAME then
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", NEW_NAME, true)
end

if REPLACE then
  for f = reaper.TrackFX_GetCount(tr) - 1, 0, -1 do
    local _, n = reaper.TrackFX_GetFXName(tr, f, "")
    if n:find("RS5K", 1, true) or n:find("ReaSamplOmatic", 1, true) then
      reaper.TrackFX_Delete(tr, f)
    end
  end
end

local rows = {}
for _, entry in ipairs(KIT) do
  local note, path = entry[1], entry[2]
  local fx = reaper.TrackFX_AddByName(tr, "ReaSamplOmatic5000", false, -1)
  if fx < 0 then error("could not add ReaSamplOmatic5000") end
  reaper.TrackFX_SetNamedConfigParm(tr, fx, "FILE0", path)
  reaper.TrackFX_SetNamedConfigParm(tr, fx, "DONE", "")
  reaper.TrackFX_SetParam(tr, fx, 3, note / 127)
  reaper.TrackFX_SetParam(tr, fx, 4, note / 127)
  reaper.TrackFX_SetParam(tr, fx, 11, 0)
  reaper.TrackFX_Show(tr, fx, 2)
  -- read back, do not trust the write
  local _, lo = reaper.TrackFX_GetFormattedParamValue(tr, fx, 3, "")
  local _, hi = reaper.TrackFX_GetFormattedParamValue(tr, fx, 4, "")
  local okf, file = reaper.TrackFX_GetNamedConfigParm(tr, fx, "FILE0")
  rows[#rows+1] = { note = note, fx = fx, lo = lo, hi = hi,
                    file = okf and file or "", wanted = path,
                    ok = okf and file == path }
end
reaper.Undo_EndBlock("rondo: build drum kit on " .. NAME, -1)
reaper.TrackList_AdjustWindows(false)

log(jsonenc({ track = track_name(tr), track_index = ti,
              created = created, pads = rows }))
"""


def manifest_name(path: Path) -> str:
    """The kit's short name, for the track's display suffix.

    ``samples/kit.json`` carries a ``name``; a hand-written manifest that has
    none falls back to its own file name, which is at least something the human
    chose.
    """
    data = json.loads(path.read_text())
    return str(data.get("name") or path.stem).strip() or path.stem


def load_manifest(path: Path) -> list[tuple[int, str]]:
    data = json.loads(path.read_text())
    if "samples" in data and isinstance(data["samples"], dict):
        data = data["samples"]
    kit = []
    for note, entry in sorted(data.items(), key=lambda kv: int(kv[0])):
        wav = entry["file"] if isinstance(entry, dict) else entry
        p = Path(wav)
        if not p.is_absolute():
            p = (path.parent / p).resolve()
        kit.append((int(note), str(p)))
    return kit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    ap.add_argument("--track", default="Drums")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--replace", action="store_true",
                    help="remove existing RS5K instances from the track first")
    ap.add_argument("--keep-name", action="store_true",
                    help="do not rename the track to \"Role (RS5K kit: <kit>)\"")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    manifest = Path(a.manifest).expanduser().resolve()
    if not manifest.exists():
        raise SystemExit(f"no manifest at {manifest}")
    kit = load_manifest(manifest)
    missing = [f for _, f in kit if not Path(f).exists()]
    if missing:
        raise SystemExit(
            "these samples are not on disk:\n  "
            + "\n  ".join(missing)
            + "\nRun: python scripts/install_samples.py"
        )

    _cli.require_reaper()
    rows = tracks.snapshot()
    ti = tracks.find_or_none(a.track, rows)
    role = tracks.split_role(rows[ti]["name"] if ti is not None else a.track)[0]
    new_name = (None if a.keep_name
                else tracks.display_name(role, KIT_LABEL, manifest_name(manifest)))
    r = reaper.run_lua_json(LUA % {
        "index": "nil" if ti is None else str(ti),
        "name": reaper.lua_str(role or a.track),
        "new_name": reaper.lua_str(new_name) if new_name else "nil",
        "kit": reaper.lua_value([[n, f] for n, f in kit]),
        "replace": "true" if a.replace else "false",
    }, timeout=60.0)

    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"track {r['track_index']} \"{r['track']}\""
              f"{' (created)' if r['created'] else ''}: {len(r['pads'])} pad(s)")
        for p in r["pads"]:
            mark = "ok " if p["ok"] else "BAD"
            print(f"  {mark} note {p['note']:>3} fx {p['fx']}  range {p['lo']}..{p['hi']}"
                  f"  {os.path.basename(p['file'])}")
    return 0 if all(p["ok"] for p in r["pads"]) else 1


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
