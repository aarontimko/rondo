#!/usr/bin/env python3
"""
name: automate
summary: Draw a volume or FX-parameter automation envelope over a bar range, read the envelopes back, or clear them.
needs: reaper-running
usage: python scripts/automate.py volume --track Pad --from 1 --to 4 --start-db -18 --end-db 0 | python scripts/automate.py swell --track Pad --into 9 --bars 4 --from-db -18 --to-db 0 | python scripts/automate.py fx-param --track Sweep --fx surge --param "A Filter 1 Cutoff" --from 1 --to 8 --start 0.2 --end 0.9 | python scripts/automate.py show --track Sweep | python scripts/automate.py clear --track Sweep
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper, tracks  # noqa: E402

# Reaper's envelope point shapes. 2 (slow start/end), 5 (bezier) and 6 are not
# exposed: fast-start / fast-end are real shapes here, so no bezier tension is
# needed to get them.
SHAPES = {"linear": 0, "fast-start": 3, "fast-end": 4}

#: How far before ``--from`` the guard point goes, in quarter notes. 0.01 QN is
#: 6 ms at 100 bpm -- short enough to be inaudible, long enough that Reaper
#: keeps it as its own point.
GUARD_QN = 0.01

#: dB limits for the volume subcommands. Reaper stores a volume envelope point
#: faithfully past the +12 dB fader range (+18 dB was verified to round-trip),
#: but nothing above this is a musical request.
DB_MIN, DB_MAX = -150.0, 24.0

#: `show` never prints more than this many bar boundaries when it is picking
#: the range itself.
MAX_SHOW_BARS = 64


class PickError(SystemExit):
    """A ``--fx`` / ``--param`` / ``--envelope`` spec named no single thing."""


def pick(spec: str, names: list[str], what: str) -> int:
    """``SPEC`` + the real names -> a 0-based index.

    The same spirit as ``rondo.tracks.resolve``, one step looser at the end
    because plugin parameter names are long and there are thousands of them:

    1. case-insensitive **exact** name match;
    2. otherwise, a bare non-negative integer is an **index**;
    3. otherwise, a unique case-insensitive **substring** match.

    More than one match is an error listing the candidates -- rondo never
    guesses which parameter you meant.
    """
    spec = str(spec).strip()
    if not spec:
        raise PickError(f"{what}: give a name or a 0-based index")
    if not names:
        raise PickError(f"{what} {spec!r}: there are none to choose from")

    low = spec.lower()
    exact = [i for i, n in enumerate(names) if n.lower() == low]
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise PickError(f"{what} {spec!r} names {len(exact)}: {_candidates(names, exact)}. "
                        "Use the index.")

    if spec.isdigit():
        i = int(spec)
        if i < len(names):
            return i
        raise PickError(f"{what} {spec}: out of range; there are {len(names)} "
                        f"(0..{len(names) - 1})")

    hits = [i for i, n in enumerate(names) if low in n.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise PickError(
            f"{what} {spec!r} matches {len(hits)}: {_candidates(names, hits)}. "
            "Use a longer name or the index."
        )
    raise PickError(f"{what} {spec!r} matches nothing. Have: "
                    f"{_candidates(names, list(range(len(names))))}")


def _candidates(names: list[str], idxs: list[int], limit: int = 8) -> str:
    shown = ", ".join(f"{i} {names[i]!r}" for i in idxs[:limit])
    return shown + (f", ... and {len(idxs) - limit} more" if len(idxs) > limit else "")


def shape_code(name: str) -> int:
    """``--shape`` -> Reaper's point shape number."""
    try:
        return SHAPES[name]
    except KeyError:
        raise PickError(f"--shape {name!r}: pick one of {', '.join(SHAPES)}")


def check_db(value: float, flag: str) -> float:
    if not DB_MIN <= value <= DB_MAX:
        raise SystemExit(f"{flag} {value:g} is outside {DB_MIN:g}..{DB_MAX:g} dB")
    return value


def check_unit(value: float, flag: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise SystemExit(
            f"{flag} {value:g} is outside 0..1. FX parameter envelopes are in the "
            "plugin's NORMALISED range, not its display units."
        )
    return value


# --------------------------------------------------------------------------
# Lua
# --------------------------------------------------------------------------

# Every draw goes through this. %(find)s is the snippet that puts the envelope
# in `env` (and may set `created`); everything else is shared, so a volume ramp
# and an FX sweep cannot drift apart.
# Reaper 7.79: DeleteEnvelopePointRange leaves a point sitting at EXACTLY
# time 0 behind, however wide the range is -- so a full clear reports one
# point left, and redrawing over bar 1 would stack a second point on top of
# the old one. Deleting by index does work, so both templates finish the job
# by hand.
SWEEP_LUA = r"""
local function sweep(env, a, b)
  local n = 0
  for p = reaper.CountEnvelopePoints(env) - 1, 0, -1 do
    local ok, t = reaper.GetEnvelopePoint(env, p)
    if ok and t >= a and t <= b then
      if reaper.DeleteEnvelopePointEx(env, -1, p) then n = n + 1 end
    end
  end
  return n
end
"""

DRAW_LUA = SWEEP_LUA + r"""
local TI = %(index)d
local T0_QN, T1_QN, GUARD = %(t0)r, %(t1)r, %(guard)r
local V0, V1, SHAPE = %(v0)r, %(v1)r, %(shape)d

local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI, 0) end
local created = false
%(find)s
if not env then error(%(what)s .. ": no envelope", 0) end

-- Envelope points are stored in the envelope's OWN units. A track volume
-- envelope is scaling mode 1 (VOLTYPE 1): unity gain is stored as 716.2, not
-- 1.0. ScaleToEnvelopeMode / ScaleFromEnvelopeMode is the only safe way in and
-- out. FX parameter envelopes are mode 0, where the stored value is already
-- the plugin's normalised 0..1.
local mode = reaper.GetEnvelopeScalingMode(env)
local t0 = reaper.TimeMap2_QNToTime(0, T0_QN)
local t1 = reaper.TimeMap2_QNToTime(0, T1_QN)
local tg = nil
if T0_QN - GUARD > 0 then tg = reaper.TimeMap2_QNToTime(0, T0_QN - GUARD) end

reaper.Undo_BeginBlock()
-- Read what the envelope held just BEFORE the range, before anything is
-- deleted. The guard point restates it, so the new ramp starts at --from
-- instead of dragging every earlier bar with it.
local hold = nil
if tg then hold = select(2, reaper.Envelope_Evaluate(env, tg, 44100, 0)) end
local before = reaper.CountEnvelopePoints(env)
reaper.DeleteEnvelopePointRange(env, (tg or t0) - 1e-9, t1 + 1e-9)
sweep(env, (tg or t0) - 1e-9, t1 + 1e-9)
if tg then reaper.InsertEnvelopePoint(env, tg, hold, 0, 0, false, true) end
reaper.InsertEnvelopePoint(env, t0, reaper.ScaleToEnvelopeMode(mode, V0), SHAPE, 0, false, true)
reaper.InsertEnvelopePoint(env, t1, reaper.ScaleToEnvelopeMode(mode, V1), 0, 0, false, true)
reaper.Envelope_SortPoints(env)
reaper.Undo_EndBlock(%(undo)s, -1)
reaper.UpdateArrange()

-- Envelope_FormatValue wants the RAW (scaled) value and returns ONE string.
local function at(t)
  local v = select(2, reaper.Envelope_Evaluate(env, t, 44100, 0))
  return { value = reaper.ScaleFromEnvelopeMode(mode, v),
           formatted = reaper.Envelope_FormatValue(env, v, "") }
end
log(jsonenc({
  track = track_name(tr), index = TI,
  envelope = select(2, reaper.GetEnvelopeName(env, "")),
  created = created, scaling_mode = mode,
  points = reaper.CountEnvelopePoints(env), points_before = before,
  start_time = t0, end_time = t1, guard_time = tg,
  at_start = at(t0), at_end = at(t1),
  before_start = tg and at(tg) or nil,
}))
"""

# Action 40406 = "Track: Toggle track volume envelope visible". It is only ever
# reached when the track has NO volume envelope, so the toggle can only create
# one, never hide one. GetTrackEnvelopeByName finds a hidden envelope too.
# NOTE: this is the normal (post-FX) track volume envelope, chunk VOLENV2 --
# not "Volume (Pre-FX)", which is a separate envelope rondo does not touch.
FIND_VOLUME = r"""
local env = reaper.GetTrackEnvelopeByName(tr, "Volume")
if not env then
  local sel = {}
  for i = 0, reaper.CountTracks(0) - 1 do
    sel[i] = reaper.IsTrackSelected(reaper.GetTrack(0, i))
  end
  reaper.SetOnlyTrackSelected(tr)
  reaper.Main_OnCommand(40406, 0)
  for i = 0, reaper.CountTracks(0) - 1 do
    reaper.SetTrackSelected(reaper.GetTrack(0, i), sel[i])
  end
  env = reaper.GetTrackEnvelopeByName(tr, "Volume")
  created = true
end
"""

# GetFXEnvelope(_, _, _, true) creates the parameter envelope if it is missing.
FIND_FX = r"""
local FX, PARAM = %(fx)d, %(param)d
if FX >= reaper.TrackFX_GetCount(tr) then
  error("no fx " .. FX .. " on that track", 0)
end
local n0 = reaper.CountTrackEnvelopes(tr)
local env = reaper.GetFXEnvelope(tr, FX, PARAM, true)
created = reaper.CountTrackEnvelopes(tr) > n0
"""

FX_LUA = r"""
local TI = %(index)d
local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI, 0) end
local rows = {}
for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
  rows[#rows+1] = { index = f,
                    name = select(2, reaper.TrackFX_GetFXName(tr, f, "")),
                    params = reaper.TrackFX_GetNumParams(tr, f) }
end
log(jsonenc(rows))
"""

PARAMS_LUA = r"""
local TI, FX = %(index)d, %(fx)d
local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI, 0) end
if FX >= reaper.TrackFX_GetCount(tr) then error("no fx " .. FX .. " on that track", 0) end
local names = {}
for p = 0, reaper.TrackFX_GetNumParams(tr, FX) - 1 do
  names[#names+1] = select(2, reaper.TrackFX_GetParamName(tr, FX, p, ""))
end
log(jsonenc(names))
"""

ENVS_LUA = r"""
local TI = %(index)d
local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI, 0) end
local names = {}
for e = 0, reaper.CountTrackEnvelopes(tr) - 1 do
  names[#names+1] = select(2, reaper.GetEnvelopeName(reaper.GetTrackEnvelope(tr, e), ""))
end
log(jsonenc(names))
"""

CLEAR_LUA = SWEEP_LUA + r"""
local TI = %(index)d
local T0_QN, T1_QN = %(t0)s, %(t1)s      -- nil, nil = the whole envelope
local WANT = %(want)s                    -- nil = every envelope on the track

local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI, 0) end
local want = nil
if WANT then
  want = {}
  for _, i in ipairs(WANT) do want[i] = true end
end

reaper.Undo_BeginBlock()
local rows, total = {}, 0
for e = 0, reaper.CountTrackEnvelopes(tr) - 1 do
  if (not want) or want[e] then
    local env = reaper.GetTrackEnvelope(tr, e)
    local before = reaper.CountEnvelopePoints(env)
    local lo, hi = -1.0, 1e12
    if T0_QN then
      lo = reaper.TimeMap2_QNToTime(0, T0_QN) - 1e-9
      hi = reaper.TimeMap2_QNToTime(0, T1_QN) + 1e-9
    end
    reaper.DeleteEnvelopePointRange(env, lo, hi)
    sweep(env, lo, hi)
    reaper.Envelope_SortPoints(env)
    local after = reaper.CountEnvelopePoints(env)
    total = total + (before - after)
    rows[#rows+1] = { index = e, name = select(2, reaper.GetEnvelopeName(env, "")),
                      removed = before - after, points = after }
  end
end
reaper.Undo_EndBlock("rondo: clear automation", -1)
reaper.UpdateArrange()
log(jsonenc({ track = track_name(tr), index = TI, removed = total, envelopes = rows }))
"""

SHOW_LUA = r"""
local TI = %(index)d
local B0, B1, MAX = %(b0)s, %(b1)s, %(max)d

local tr = reaper.GetTrack(0, TI)
if not tr then error("no track at index " .. TI, 0) end

-- With no --from/--to, show as far as there is anything to show: the last item
-- anywhere in the project, or this track's last envelope point.
local last_qn = 0
for t = 0, reaper.CountTracks(0) - 1 do
  local x = reaper.GetTrack(0, t)
  for m = 0, reaper.CountTrackMediaItems(x) - 1 do
    local it = reaper.GetTrackMediaItem(x, m)
    local e = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
            + reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    local q = reaper.TimeMap2_timeToQN(0, e)
    if q > last_qn then last_qn = q end
  end
end
for e = 0, reaper.CountTrackEnvelopes(tr) - 1 do
  local env = reaper.GetTrackEnvelope(tr, e)
  local n = reaper.CountEnvelopePoints(env)
  if n > 0 then
    local _, t = reaper.GetEnvelopePoint(env, n - 1)
    local q = reaper.TimeMap2_timeToQN(0, t)
    if q > last_qn then last_qn = q end
  end
end

local first = B0 or 1
local last = B1 or math.max(first, qn_to_bar(last_qn) - 1)
if last < first then last = first end
if last - first + 1 > MAX then last = first + MAX - 1 end

local rows = {}
for e = 0, reaper.CountTrackEnvelopes(tr) - 1 do
  local env = reaper.GetTrackEnvelope(tr, e)
  local mode = reaper.GetEnvelopeScalingMode(env)
  local _, chunk = reaper.GetEnvelopeStateChunk(env, "", false)
  local kind = chunk:match("^%%s*<(%%S+)") or "?"
  local bars = {}
  for b = first, last + 1 do
    local t = reaper.TimeMap2_QNToTime(0, bars_to_qn(b))
    local v = select(2, reaper.Envelope_Evaluate(env, t, 44100, 0))
    bars[#bars+1] = { bar = b, time = t,
                      value = reaper.ScaleFromEnvelopeMode(mode, v),
                      formatted = reaper.Envelope_FormatValue(env, v, "") }
  end
  rows[#rows+1] = {
    index = e,
    name = select(2, reaper.GetEnvelopeName(env, "")),
    kind = kind,
    points = reaper.CountEnvelopePoints(env),
    scaling_mode = mode,
    active = chunk:match("\nACT (%%d)") == "1",
    visible = chunk:match("\nVIS (%%d)") == "1",
    armed = chunk:match("\nARM (%%d)") == "1",
    boundaries = bars,
  }
end
log(jsonenc({ track = track_name(tr), index = TI,
              from_bar = first, to_bar = last, envelopes = rows }))
"""


# --------------------------------------------------------------------------
# building the calls
# --------------------------------------------------------------------------


def draw_source(index: int, find: str, what: str, undo: str, t0: float, t1: float,
                v0: float, v1: float, shape: int) -> str:
    """The full Lua for one ramp. Pure: no Reaper needed to build or test it."""
    return DRAW_LUA % {
        "index": index, "find": find, "what": reaper.lua_str(what),
        "undo": reaper.lua_str(undo), "t0": t0, "t1": t1,
        "guard": GUARD_QN, "v0": v0, "v1": v1, "shape": shape,
    }


def swell_bars(into: int, bars: int) -> tuple[int, int]:
    """``--into BAR --bars N`` -> the inclusive bar range the ramp covers.

    The ramp ENDS on the bar line at ``--into``: ``--into 9 --bars 4`` is bars
    5..8, whose end is exactly where bar 9 starts.
    """
    if bars < 1:
        raise SystemExit("--bars must be 1 or more")
    first = into - bars
    if first < 1:
        raise SystemExit(
            f"--into {into} --bars {bars} would start at bar {first}; "
            "the project starts at bar 1"
        )
    return first, into - 1


def span(start: int, stop: int) -> tuple[float, float]:
    try:
        return reaper.bar_span_qn(start, stop)
    except ValueError as e:
        raise SystemExit(str(e))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


COMMANDS = ["volume", "swell", "fx-param", "clear", "show"]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="--track takes a name or a 0-based index, resolved the same way as "
               "track.py. Bars are 1-based and --from/--to are inclusive, so "
               "--from 1 --to 4 ramps from the start of bar 1 to the start of bar 5. "
               "A point just before --from holds the old value, so a ramp never "
               "drags the bars in front of it. FX parameter values are the plugin's "
               "normalised 0..1, not its display units.",
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, help_: str):
        p = sub.add_parser(name, help=help_, description=help_)
        p.add_argument("--track", required=True, help="track name or 0-based index")
        p.add_argument("--json", action="store_true")
        return p

    def add_range(p, required=True):
        p.add_argument("--from", dest="start", type=int, required=required,
                       help="first bar (inclusive)")
        p.add_argument("--to", dest="stop", type=int, required=required,
                       help="last bar (inclusive); the ramp ends where the next bar starts")

    def add_shape(p):
        p.add_argument("--shape", default="linear", choices=sorted(SHAPES),
                       help="curve of the ramp (default: linear)")

    p = add("volume", "Ramp the track volume envelope over a bar range.")
    add_range(p)
    p.add_argument("--start-db", dest="start_db", type=float, required=True,
                   help="0 is unity gain")
    p.add_argument("--end-db", dest="end_db", type=float, required=True)
    add_shape(p)

    p = add("swell", "A volume ramp that ENDS on a bar line -- a pad swelling into a section.")
    p.add_argument("--into", type=int, required=True,
                   help="the bar the swell arrives on")
    p.add_argument("--bars", type=int, required=True, help="how many bars it takes")
    p.add_argument("--from-db", dest="from_db", type=float, required=True)
    p.add_argument("--to-db", dest="to_db", type=float, required=True)
    add_shape(p)

    p = add("fx-param", "Ramp one FX parameter over a bar range.")
    p.add_argument("--fx", help="fx name or 0-based slot")
    p.add_argument("--param", help="parameter name or 0-based index")
    add_range(p, required=False)
    # dest is start_value/end_value: --from already owns `start` here, as it
    # does in every other rondo CLI.
    p.add_argument("--start", dest="start_value", type=float,
                   help="value at --from, normalised 0..1")
    p.add_argument("--end", dest="end_value", type=float,
                   help="value at the end of --to, normalised 0..1")
    add_shape(p)
    p.add_argument("--list-params", action="store_true",
                   help="print the fx's parameters (filtered by --param) and stop")

    p = add("clear", "Delete envelope points, in a bar range or everywhere.")
    add_range(p, required=False)
    p.add_argument("--envelope", action="append", metavar="NAME",
                   help="envelope name or index; repeatable (default: all of them)")

    p = add("show", "List the track's envelopes and their value at each bar line.")
    add_range(p, required=False)
    return ap


def boundary_text(row: dict, per_line: int = 4) -> list[str]:
    cells = []
    for b in row["boundaries"]:
        v = b["formatted"] or f"{b['value']:.3f}"
        cells.append(f"bar {b['bar']:<3} {v}")
    # One column width for the whole envelope, always wide enough to leave a
    # gap: Envelope_FormatValue strings vary in length ("0.00dB", "11839.82 Hz").
    width = max([len(c) for c in cells] + [16]) + 2
    return ["    " + "".join(c.ljust(width) for c in cells[i:i + per_line]).rstrip()
            for i in range(0, len(cells), per_line)]


def show_text(r: dict) -> str:
    out = [f"track {r['index']} \"{r['track'] or '(unnamed)'}\": "
           f"{len(r['envelopes'])} envelope(s), bars {r['from_bar']}-{r['to_bar']}"]
    if not r["envelopes"]:
        out.append("  no envelopes on this track")
    for e in r["envelopes"]:
        flags = ", ".join(
            [("active" if e["active"] else "BYPASSED"),
             ("visible" if e["visible"] else "hidden"),
             ("armed" if e["armed"] else "not armed")]
        )
        out.append(f"  {e['name']}  [{e['kind']}]  {e['points']} point(s)  ({flags})")
        out.extend(boundary_text(e))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)

    # Everything that can be refused without Reaper is refused here.
    shape = shape_code(a.shape) if hasattr(a, "shape") else 0
    if a.command == "swell":
        start, stop = swell_bars(a.into, a.bars)
    else:
        start, stop = a.start, a.stop

    if a.command == "volume":
        check_db(a.start_db, "--start-db")
        check_db(a.end_db, "--end-db")
    elif a.command == "swell":
        check_db(a.from_db, "--from-db")
        check_db(a.to_db, "--to-db")
    elif a.command == "fx-param":
        if not a.fx:
            raise SystemExit("--fx is required")
        if not a.list_params:
            if a.param is None:
                raise SystemExit("--param is required")
            if start is None or stop is None:
                raise SystemExit("--from and --to are required")
            if a.start_value is None or a.end_value is None:
                raise SystemExit("--start and --end are required")
            check_unit(a.start_value, "--start")
            check_unit(a.end_value, "--end")

    _cli.require_reaper()
    rows = tracks.snapshot()
    index, _row = tracks.find(a.track, rows)

    if a.command in ("volume", "swell"):
        if start is None or stop is None:
            raise SystemExit("--from and --to are required")
        t0, t1 = span(start, stop)
        db0 = a.start_db if a.command == "volume" else a.from_db
        db1 = a.end_db if a.command == "volume" else a.to_db
        src = draw_source(index, FIND_VOLUME, "volume", "rondo: volume envelope",
                          t0, t1, tracks.db_to_gain(db0), tracks.db_to_gain(db1), shape)
        r = reaper.run_lua_json(src, timeout=30.0)
        r["from_bar"], r["to_bar"] = start, stop
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            what = ("swell into bar %d" % a.into) if a.command == "swell" else "volume"
            print(f"track {r['index']} \"{r['track']}\" {what}: bars {start}-{stop}, "
                  f"{db0:+.1f} -> {db1:+.1f} dB ({a.shape})"
                  + (", envelope created" if r["created"] else ""))
            print(f"  reads back {r['at_start']['formatted']} at bar {start} and "
                  f"{r['at_end']['formatted']} at bar {stop + 1}"
                  f"   ({r['points']} point(s) on \"{r['envelope']}\")")
        return 0

    if a.command == "fx-param":
        fxs = reaper.run_lua_json(FX_LUA % {"index": index}, timeout=20.0)
        if not fxs:
            raise SystemExit(f"track {index} has no FX")
        fx = pick(a.fx, [f["name"] for f in fxs], "--fx")
        names = reaper.run_lua_json(PARAMS_LUA % {"index": index, "fx": fx},
                                    timeout=30.0)
        if a.list_params:
            low = (a.param or "").lower()
            hits = [(i, n) for i, n in enumerate(names) if low in n.lower()]
            print(f"fx {fx} {fxs[fx]['name']}: {len(names)} parameter(s)"
                  + (f", {len(hits)} matching {a.param!r}" if a.param else ""))
            for i, n in hits:
                print(f"  {i:>5}  {n}")
            return 0
        param = pick(a.param, names, "--param")
        t0, t1 = span(start, stop)
        src = draw_source(
            index, FIND_FX % {"fx": fx, "param": param}, f"fx {fx} param {param}",
            "rondo: fx parameter envelope", t0, t1, a.start_value, a.end_value, shape,
        )
        r = reaper.run_lua_json(src, timeout=30.0)
        r.update(fx=fx, fx_name=fxs[fx]["name"], param=param, param_name=names[param],
                 from_bar=start, to_bar=stop)
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            print(f"track {r['index']} \"{r['track']}\" fx {fx} \"{r['fx_name']}\" "
                  f"param {param} \"{r['param_name']}\": bars {start}-{stop}, "
                  f"{a.start_value:g} -> {a.end_value:g} ({a.shape})"
                  + (", envelope created" if r["created"] else ""))
            print(f"  reads back {r['at_start']['value']:.3f} at bar {start} and "
                  f"{r['at_end']['value']:.3f} at bar {stop + 1}"
                  f"   ({r['points']} point(s) on \"{r['envelope']}\")")
        return 0

    if a.command == "clear":
        if (start is None) != (stop is None):
            raise SystemExit("--from and --to must be given together")
        want = "nil"
        if a.envelope:
            names = reaper.run_lua_json(ENVS_LUA % {"index": index}, timeout=20.0)
            want = reaper.lua_value([pick(s, names, "--envelope") for s in a.envelope])
        t0 = t1 = "nil"
        if start is not None:
            t0, t1 = (repr(q) for q in span(start, stop))
        r = reaper.run_lua_json(
            CLEAR_LUA % {"index": index, "t0": t0, "t1": t1, "want": want},
            timeout=30.0)
        if a.json:
            print(json.dumps(r, indent=2))
        else:
            where = f"bars {start}-{stop}" if start is not None else "the whole track"
            print(f"track {r['index']} \"{r['track']}\": removed {r['removed']} "
                  f"point(s) from {where}")
            for e in r["envelopes"]:
                print(f"  {e['name']}: -{e['removed']}, {e['points']} left")
        return 0

    r = reaper.run_lua_json(SHOW_LUA % {
        "index": index,
        "b0": "nil" if start is None else str(start),
        "b1": "nil" if stop is None else str(stop),
        "max": MAX_SHOW_BARS,
    }, timeout=30.0)
    print(json.dumps(r, indent=2) if a.json else show_text(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
