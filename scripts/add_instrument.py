#!/usr/bin/env python3
"""
name: add-instrument
summary: Find or create a track and load Surge XT, Dexed or the Apple GM piano on it, optionally with a patch.
needs: reaper-running
usage: python scripts/add_instrument.py --track "Pad" --instrument surge [--patch "Bell Pad"] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper, surge_preset  # noqa: E402

# Exact TrackFX_AddByName strings. Verified on Reaper 7.79 / macOS-arm64.
# HAZARD: "AU: Surge XT" fuzzy-matches the Surge XT *Effects* plugin -- never
# use a short name for Surge.
INSTRUMENTS = {
    "surge": "VST3: Surge XT (Surge Synth Team)",
    "dexed": "VST3: Dexed (Digital Suburban)",
    "piano": "AU: DLSMusicDevice (Apple)",
}

LUA = r"""
local NAME, FX_NAME, INDEX = %(name)s, %(fx)s, %(index)s
local SHORT, VENDOR = %(short)s, %(vendor)s
local tr, ti = find_track(NAME)
local created = false
if not tr then
  local at = INDEX
  if at == nil then at = reaper.CountTracks(0) end
  reaper.InsertTrackAtIndex(at, true)
  tr = reaper.GetTrack(0, at)
  ti = at
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", NAME, true)
  created = true
end

-- reuse an instrument slot if this exact plugin is already on the track
local fx = -1
for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
  local _, n = reaper.TrackFX_GetFXName(tr, f, "")
  if n:find(SHORT, 1, true) and n:find(VENDOR, 1, true) then fx = f break end
end
if fx < 0 then
  fx = reaper.TrackFX_AddByName(tr, FX_NAME, false, -1)
end
if fx < 0 then error("TrackFX_AddByName failed for " .. FX_NAME) end

local result = { track = ti, fx = fx, created = created }
%(patch)s

local _, loaded = reaper.TrackFX_GetFXName(tr, fx, "")
result.fx_name = loaded
result.preset_index = reaper.TrackFX_GetPresetIndex(tr, fx)
local _, pname = reaper.TrackFX_GetPreset(tr, fx, "")
result.preset = pname
reaper.TrackFX_Show(tr, fx, 2)   -- make sure no floating window is left open
log(jsonenc(result))
"""

SURGE_PATCH = r"""
do
  local ok = reaper.TrackFX_SetPreset(tr, fx, %(preset)s)
  result.patch_file = %(preset)s
  result.patch_ok = ok
  if not ok then error("TrackFX_SetPreset refused " .. %(preset)s) end
end
"""

DEXED_INDEX = r"""
do
  reaper.TrackFX_SetPresetByIndex(tr, fx, %(idx)d)
  result.patch_index = %(idx)d
end
"""

DEXED_NAME = r"""
do
  -- Dexed exposes the 32 voices of the loaded cartridge. Names are SPACE
  -- PADDED ("PHAROH    "), which is why SetPreset by name fails; walk the
  -- indexes instead.
  local want = (%(want)s):lower()
  local _, n = reaper.TrackFX_GetPresetIndex(tr, fx)
  if n == nil or n < 1 then n = 32 end
  local found, names = -1, {}
  for i = 0, n - 1 do
    reaper.TrackFX_SetPresetByIndex(tr, fx, i)
    local _, nm = reaper.TrackFX_GetPreset(tr, fx, "")
    nm = nm:gsub("%%s+$", "")
    names[#names+1] = nm
    if found < 0 and nm:lower():find(want, 1, true) then found = i end
  end
  result.programs = names
  if found < 0 then
    reaper.TrackFX_SetPresetByIndex(tr, fx, 0)
    error("no Dexed program matches " .. %(want)s .. " -- have: " .. table.concat(names, ", "))
  end
  reaper.TrackFX_SetPresetByIndex(tr, fx, found)
  result.patch_index = found
end
"""


def add_instrument(track: str, instrument: str, patch: str | None = None,
                   index: int | None = None) -> dict:
    fx_name = INSTRUMENTS[instrument]
    patch_lua = ""
    if patch:
        if instrument == "surge":
            fxp = surge_preset.resolve_patch(patch)
            out = reaper.SCRATCH / "presets" / (fxp.stem + ".vstpreset")
            surge_preset.build_vstpreset(fxp, out)
            patch_lua = SURGE_PATCH % {"preset": reaper.lua_str(str(out))}
        elif instrument == "dexed":
            if patch.strip().lstrip("-").isdigit():
                patch_lua = DEXED_INDEX % {"idx": int(patch)}
            else:
                patch_lua = DEXED_NAME % {"want": reaper.lua_str(patch)}
        else:
            raise SystemExit(
                f"--patch is not supported for --instrument {instrument} "
                "(the Apple GM piano exposes no presets)"
            )

    short, _, vendor = fx_name.partition(":")[2].strip().partition(" (")
    src = LUA % {
        "name": reaper.lua_str(track),
        "fx": reaper.lua_str(fx_name),
        "short": reaper.lua_str(short),
        "vendor": reaper.lua_str(vendor.rstrip(")")),
        "index": "nil" if index is None else str(index),
        "patch": patch_lua,
    }
    return reaper.run_lua_json(src, timeout=45.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    ap.add_argument("--track", required=True, help="track name (found, or created)")
    ap.add_argument("--instrument", required=True, choices=sorted(INSTRUMENTS))
    ap.add_argument("--patch", help="Surge: patch name substring. Dexed: program name or index.")
    ap.add_argument("--index", type=int, help="insert position for a new track")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-patches", action="store_true",
                    help="just list matching Surge patches and exit")
    a = ap.parse_args(argv)

    if a.list_patches:
        if a.instrument != "surge":
            raise SystemExit("--list-patches only applies to --instrument surge")
        root = surge_preset.patch_root()
        for p in surge_preset.find_patches(a.patch or ""):
            print(p.relative_to(root))
        return 0

    _cli.require_reaper()
    r = add_instrument(a.track, a.instrument, a.patch, a.index)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        verb = "created" if r["created"] else "reused"
        print(f"{verb} track {r['track']} \"{a.track}\": fx {r['fx']} = {r['fx_name']}")
        if a.patch:
            print(f"  patch: {r.get('patch_file') or r.get('patch_index')} "
                  f"-> preset {r['preset']!r} (index {r['preset_index']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
