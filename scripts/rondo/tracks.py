"""Naming a track from the command line, and reading one track's whole state.

The Lua prelude's ``find_track(name)`` matches a name **exactly**, and knows
nothing about indexes, prefixes or display suffixes. Rather than teach Lua the
friendly rules, every rondo CLI resolves ``--track`` here, in Python, against a
``snapshot()`` of the track list it wants anyway -- so the rule can be tested
without Reaper, and there is only one of it.

Resolution order for ``--track SPEC`` (documented so it can be relied on):

1. case-insensitive **exact** name match;
2. otherwise, case-insensitive **exact role** match, where a track's *role* is
   its name with the trailing ``" (...)"`` display suffix removed -- ``Pad``
   finds ``Pad (Surge: MKS-70 Warm Pad)``, and ``Lead`` finds ``Lead (Surge:
   Saw Octaves)`` without touching ``Lead Harmony (...)``;
3. otherwise, a bare non-negative integer is a **track index** (0-based);
4. otherwise, case-insensitive **prefix** match.

A name always beats an index, so a track literally called ``3`` is still
reachable by name. More than one match at any step is an error naming the
candidates -- rondo never guesses which track you meant.

Two knobs on the same rule, so there is still only one rule:

``prefix=False``
    for the CLIs that **create** the track when they cannot find it
    (``add_instrument``, ``build_kit``, ``record``). Prefix matching there
    would make ``--track Lead`` land on an existing ``Lead Harmony`` instead of
    creating ``Lead``, so they stop after the index step.

``role=False, prefix=False, substring=True``
    what ``automate.py`` needs for ``--fx`` / ``--param`` / ``--envelope``:
    plugin parameter names are long and there are thousands of them, so the
    last step is a unique substring instead of a prefix. Same steps, same
    ambiguity errors, same code.

``project.py --project`` resolves a project *tab* with the same rule
(``resolve(spec, names, flag="--project", noun="tab")``), and ``automate.py``'s
``--fx`` / ``--param`` with the substring variant, so every one of those flags
behaves the same way and is documented once, here.

Display suffixes are this module's business too: ``split_role`` takes one
apart and ``display_name`` puts one together, so ``Role (Instrument: Patch)``
is spelled in exactly one place.
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


#: A track name like ``Pad (Surge: MKS-70 Warm Pad)`` splits into the *role*
#: ("Pad" -- what the track is for) and the *display suffix* ("Surge: MKS-70
#: Warm Pad" -- what is making the sound). The suffix is the LAST parenthesised
#: group, and it may not itself contain parentheses.
_SUFFIX = re.compile(r"^(?P<role>.*?)\s*\((?P<suffix>[^()]*)\)$")


def split_role(name: str) -> tuple[str, str]:
    """``"Pad (Surge: Bell)"`` -> ``("Pad", "Surge: Bell")``.

    The suffix comes back WITHOUT its parentheses, and is ``""`` when the name
    has none. A name that is nothing but a suffix (``"(unnamed)"``) keeps its
    whole self as the role: stripping it would leave nothing to match on.
    """
    m = _SUFFIX.match(str(name).strip())
    if not m or not m.group("role").strip():
        return str(name).strip(), ""
    return m.group("role").strip(), m.group("suffix").strip()


def display_name(role: str, instrument: str, patch: str | None = None) -> str:
    """``("Pad", "Surge", "Bell")`` -> ``"Pad (Surge: Bell)"``; no patch -> ``"Pad (Surge)"``.

    The one place the display grammar is spelled. ``role`` is what
    ``split_role`` returned, so loading a second patch onto a track REPLACES
    the suffix instead of stacking another one on the end.
    """
    role = str(role).strip()
    patch = (patch or "").strip()
    inside = f"{instrument}: {patch}" if patch else str(instrument)
    return f"{role} ({inside})" if role else f"({inside})"


def _candidates(names: list[str], idxs, limit: int = 8) -> str:
    idxs = list(idxs)
    shown = ", ".join(f"{i} {names[i]!r}" for i in idxs[:limit])
    return shown + (f", ... and {len(idxs) - limit} more" if len(idxs) > limit else "")


def resolve(spec: str, names: list[str], *, flag: str = "--track",
            noun: str = "track", role: bool = True, prefix: bool = True,
            substring: bool = False) -> int:
    """``--track SPEC`` + the project's track names -> a 0-based index.

    Exact name, then exact role, then index, then prefix; see the module
    docstring for what each step means and for the ``prefix=False`` /
    ``substring=True`` variants. Raises ``NoSuchTrack`` or ``AmbiguousTrack``
    (both ``SystemExit``) with a message that lists what is actually there.
    """
    spec = str(spec).strip()
    if not spec:
        raise NoSuchTrack(f"{flag}: give a {noun} name or a 0-based index")
    if not names:
        raise NoSuchTrack(f"{flag} {spec!r}: there are no {noun}s")

    low = spec.lower()

    def step(hits: list[int], how: str, hint: str) -> int | None:
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise AmbiguousTrack(
                f"{flag} {spec!r} {how} {len(hits)} {noun}s: "
                f"{_candidates(names, hits)}. {hint}"
            )
        return None

    i = step([j for j, n in enumerate(names) if n.lower() == low],
             "names", "Use the index.")
    if i is not None:
        return i

    if role:
        i = step([j for j, n in enumerate(names) if split_role(n)[0].lower() == low],
                 "is the role of", "Use the full name or the index.")
        if i is not None:
            return i

    if re.fullmatch(r"\d+", spec):
        j = int(spec)
        if j < len(names):
            return j
        raise NoSuchTrack(
            f"{flag} {spec}: no {noun} at index {j}, which is out of range; "
            f"there are {len(names)} {noun}(s), 0..{len(names) - 1}"
        )

    if prefix or substring:
        hits = [j for j, n in enumerate(names)
                if (low in n.lower() if substring else n.lower().startswith(low))]
        i = step(hits, "matches" if substring else "is a prefix of",
                 "Use a longer name or the index.")
        if i is not None:
            return i

    raise NoSuchTrack(
        f"{flag} {spec!r} matches nothing. Have: "
        f"{_candidates(names, range(len(names)), limit=max(len(names), 1))}"
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
      bar = qn_to_bar(reaper.TimeMap2_timeToQN(0, p)),
      end_bar = qn_to_bar_end(reaper.TimeMap2_timeToQN(0, p + len)),
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


def find(spec: str, rows: list[dict] | None = None, *,
         prefix: bool = True) -> tuple[int, dict]:
    """``resolve`` against a live snapshot. Returns ``(index, that track's row)``."""
    if rows is None:
        rows = snapshot()
    i = resolve(spec, [r["name"] for r in rows], prefix=prefix)
    return i, rows[i]


def find_or_none(spec: str, rows: list[dict]) -> int | None:
    """For the CLIs that CREATE the track when it is missing.

    The same rule as ``resolve(..., prefix=False)`` -- exact name, exact role,
    index -- except that "nothing matched" comes back as ``None`` (go and
    create it) instead of an error. An AMBIGUOUS spec is still an error: two
    tracks could have been meant, and creating a third is not what was asked.
    """
    spec = str(spec).strip()
    if not spec:
        raise NoSuchTrack("--track: give a track name or a 0-based index")
    try:
        return resolve(spec, [r["name"] for r in rows], prefix=False)
    except AmbiguousTrack:
        raise
    except NoSuchTrack:
        if re.fullmatch(r"\d+", spec):
            raise      # a bare number asks for an INDEX; creating a track
        return None    # called "9" is never what was meant
