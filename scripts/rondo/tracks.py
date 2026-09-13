"""Naming a track from the command line, and reading one track's whole state.

The Lua prelude's ``find_track(name)`` matches a name **exactly**. That is the
right rule for a script that will *create* the track it cannot find
(``add_instrument``, ``record``), and the wrong rule for a human or an LLM that
types ``mel`` or ``3`` and means the obvious track. The friendlier rule lives
here, in Python, so it can be tested without Reaper: the CLIs read the track
list once with ``snapshot()`` -- which they want anyway, for their own output --
and resolve against that list.

Resolution order for ``--track SPEC`` (documented so it can be relied on):

1. case-insensitive **exact** name match;
2. otherwise, a bare non-negative integer is a **track index** (0-based);
3. otherwise, case-insensitive **prefix** match.

A name always beats an index, so a track literally called ``3`` is still
reachable by name. More than one match at any step is an error naming the
candidates -- rondo never guesses which track you meant.

``project.py --project`` resolves a project *tab* with the same rule
(``resolve(spec, names, flag="--project", noun="tab")``), so the two flags
behave identically and are documented once.
"""

from __future__ import annotations

import math
import re

from . import reaper


class TrackError(SystemExit):
    """``--track`` did not name exactly one track. Exits 1 with the message."""


class NoSuchTrack(TrackError):
    """Nothing matched."""


class AmbiguousTrack(TrackError):
    """More than one track matched."""


# I_RECINPUT encodes the record input as one number:
#   audio mono input n     -> n            (0 = the first hardware input)
#   MIDI device d, chan c  -> 4096 + (d << 5) + c   (c = 0 means "all channels")
#   no input               -> -1
# Reaper's own Virtual MIDI Keyboard is device 62, so 4096 + (62 << 5) = 6080.
# record.py imports this; do not duplicate the numbers anywhere else.
REC_INPUTS = {"mic": 0, "keyboard": 6080, "none": -1}

MIDI_INPUT_BASE = 4096
VKB_DEVICE = 62


def rec_input_name(value: float) -> str:
    """Human-readable ``I_RECINPUT``: the inverse of ``REC_INPUTS``, plus the rest."""
    v = int(value)
    if v < 0:
        return "none"
    if v == REC_INPUTS["mic"]:
        return "mic (audio input 1)"
    if v < MIDI_INPUT_BASE:
        return f"audio input {v + 1}"
    if v == REC_INPUTS["keyboard"]:
        return "keyboard (virtual MIDI keyboard, all channels)"
    dev, chan = (v - MIDI_INPUT_BASE) >> 5, (v - MIDI_INPUT_BASE) & 31
    where = "all channels" if chan == 0 else f"channel {chan}"
    if dev == VKB_DEVICE:
        return f"virtual MIDI keyboard, {where}"
    return f"MIDI device {dev}, {where}"


MONITOR_NAMES = {0: "off", 1: "on", 2: "auto"}


def db_to_gain(db: float) -> float:
    """Decibels -> Reaper's linear ``D_VOL``. ``0 dB`` is unity, i.e. 1.0."""
    return 10.0 ** (db / 20.0)


def gain_to_db(gain: float) -> float:
    """Linear ``D_VOL`` -> decibels. A muted-to-zero fader is ``-inf``."""
    if gain <= 0:
        return float("-inf")
    return 20.0 * math.log10(gain)


def pan_name(pan: float) -> str:
    """``D_PAN`` (-1..1) as Reaper writes it: ``center``, ``30% L``, ``100% R``."""
    if abs(pan) < 5e-4:
        return "center"
    return f"{abs(pan) * 100:.0f}% {'L' if pan < 0 else 'R'}"


def _candidates(names: list[str], idxs: list[int]) -> str:
    return ", ".join(f"{i} {names[i]!r}" for i in idxs)


def resolve(spec: str, names: list[str], *, flag: str = "--track",
            noun: str = "track") -> int:
    """``--track SPEC`` + the project's track names -> a 0-based track index.

    Raises ``NoSuchTrack`` or ``AmbiguousTrack`` (both ``SystemExit``) with a
    message that lists what is actually there. ``flag``/``noun`` only change
    the wording, so ``--project`` can share the rule for tabs.
    """
    spec = str(spec).strip()
    if not spec:
        raise NoSuchTrack(f"{flag}: give a {noun} name or a 0-based index")
    if not names:
        raise NoSuchTrack(f"{flag} {spec!r}: there are no {noun}s")

    low = spec.lower()
    exact = [i for i, n in enumerate(names) if n.lower() == low]
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise AmbiguousTrack(
            f"{flag} {spec!r} names {len(exact)} {noun}s: {_candidates(names, exact)}. "
            "Use the index."
        )

    if re.fullmatch(r"\d+", spec):
        i = int(spec)
        if i < len(names):
            return i
        raise NoSuchTrack(
            f"{flag} {spec}: no {noun} at index {i}; there are "
            f"{len(names)} {noun}(s), 0..{len(names) - 1}"
        )

    pre = [i for i, n in enumerate(names) if n.lower().startswith(low)]
    if len(pre) == 1:
        return pre[0]
    if pre:
        raise AmbiguousTrack(
            f"{flag} {spec!r} is a prefix of {len(pre)} {noun}s: "
            f"{_candidates(names, pre)}. Use a longer name or the index."
        )

    raise NoSuchTrack(
        f"{flag} {spec!r} matches no {noun}. Have: "
        f"{_candidates(names, list(range(len(names))))}"
    )


# One read of every track, with enough detail for `track.py show`. Bars are
# 1-based; an item's end_bar is the bar it ENDS ON, so a one-bar item at bar 1
# reports bar 1, end_bar 1 (status.py's regions use the exclusive convention --
# these do not, because an item is a thing you can point at).
SNAPSHOT_LUA = r"""
local rows = {}
for t = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, t)
  local fx = {}
  for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
    local _, n = reaper.TrackFX_GetFXName(tr, f, "")
    fx[#fx+1] = n
  end
  local items = {}
  for m = 0, reaper.CountTrackMediaItems(tr) - 1 do
    local it = reaper.GetTrackMediaItem(tr, m)
    local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
    local len = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
    local take = reaper.GetActiveTake(it)
    local notes = nil
    if take and reaper.TakeIsMIDI(take) then
      notes = select(2, reaper.MIDI_CountEvts(take))
    end
    items[#items+1] = {
      position = p, length = len, midi_notes = notes,
      bar = math.floor(reaper.TimeMap2_timeToQN(0, p) / 4) + 1,
      end_bar = math.ceil(reaper.TimeMap2_timeToQN(0, p + len) / 4),
    }
  end
  rows[#rows+1] = {
    index = t,
    name = track_name(tr),
    fx = fx,
    instrument_fx = reaper.TrackFX_GetInstrument(tr),
    items = items,
    mute = reaper.GetMediaTrackInfo_Value(tr, "B_MUTE") == 1,
    solo = reaper.GetMediaTrackInfo_Value(tr, "I_SOLO") ~= 0,
    armed = reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") == 1,
    monitor = reaper.GetMediaTrackInfo_Value(tr, "I_RECMON"),
    rec_input = reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT"),
    volume = reaper.GetMediaTrackInfo_Value(tr, "D_VOL"),
    pan = reaper.GetMediaTrackInfo_Value(tr, "D_PAN"),
  }
end
log(jsonenc(rows))
"""


def snapshot(timeout: float = 20.0) -> list[dict]:
    """Every track in the active project tab, with the state ``show`` prints."""
    return reaper.run_lua_json(SNAPSHOT_LUA, timeout=timeout)


def find(spec: str, rows: list[dict] | None = None) -> tuple[int, dict]:
    """``resolve`` against a live snapshot. Returns ``(index, that track's row)``."""
    if rows is None:
        rows = snapshot()
    i = resolve(spec, [r["name"] for r in rows])
    return i, rows[i]
