"""The rondo grid: a bar-per-line, one-column-per-slot melody notation.

    grid: 8
            1  &  2  &  3  &  4  &
    bar 1:  G  E  /  E  Ef E  Ef E
    bar 2:  D  s  C  s  s  s  Ef E

Rules
-----
* Only lines matching ``bar <n>:`` carry notes. Everything else is prose --
  a header row, a title, a comment -- and is ignored, except a ``grid: <n>``
  line, which sets slots per bar (8 = eighths, the default; 16 = sixteenths).
* A token is one of:

  ``/``      rest
  ``s``      hold: extend the previous note by one slot (works across bars)
  a note     letter ``A``-``G``, optional accidental (``f``/``b`` flat,
             ``s``/``#`` sharp), optional octave number. ``Ef`` = E flat,
             ``Cs`` = C sharp, ``C3`` = C in octave 3. Default octave is 4,
             so ``E`` is E4 = MIDI 64 and middle C (``C``) is MIDI 60.

* Bar numbers are labels for humans. Slots are counted from the FIRST bar
  line in the file, so ``bar 9:`` as the first line still starts at slot 0.
  Where that block lands on the timeline is ``write_notes.py --bar``'s job.

Parsing yields ``[Note(slot, length, midi), ...]`` with slot and length
measured in grid slots.
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

DEFAULT_OCTAVE = 4
DEFAULT_SLOTS_PER_BAR = 8

#: MIDI number of C in ``DEFAULT_OCTAVE``. C4 = 60 (Yamaha/Reaper convention).
_C0 = 12  # MIDI number of C0, i.e. octave -1 is 0..11

_LETTER = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

#: How ``notes_to_grid`` spells the black keys. Matches the flats used in the
#: hand-written examples (``Ef``); ``Cs`` in a hand-written grid is the same pitch as
#: ``Df``, so text round-trips are enharmonic, not byte-identical.
_SPELL_FLAT = {0: "C", 1: "Df", 2: "D", 3: "Ef", 4: "E", 5: "F",
               6: "Gf", 7: "G", 8: "Af", 9: "A", 10: "Bf", 11: "B"}
_SPELL_SHARP = {0: "C", 1: "Cs", 2: "D", 3: "Ds", 4: "E", 5: "F",
                6: "Fs", 7: "G", 8: "Gs", 9: "A", 10: "As", 11: "B"}

_BAR_RE = re.compile(r"^\s*bar\s+(-?\d+)\s*:(.*)$", re.IGNORECASE)
_GRID_RE = re.compile(r"^\s*grid\s*:\s*(\d+)\s*$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^([A-Ga-g])([fFbBsS#]?)(-?\d+)?$")

REST = "/"
HOLD = "s"


class Note(NamedTuple):
    slot: int
    length: int
    midi: int


class GridError(ValueError):
    """Malformed grid text."""


def parse_token(tok: str, default_octave: int = DEFAULT_OCTAVE) -> int:
    """``'Ef'`` -> 63, ``'C3'`` -> 48. Raises ``GridError`` on anything else."""
    m = _NOTE_RE.match(tok)
    if not m:
        raise GridError(f"not a note: {tok!r}")
    letter, acc, octave = m.groups()
    semitone = _LETTER[letter.upper()]
    if acc in ("f", "F", "b", "B"):
        semitone -= 1
    elif acc in ("s", "S", "#"):
        semitone += 1
    octave_num = int(octave) if octave is not None else default_octave
    midi = _C0 + (octave_num * 12) + semitone
    if not 0 <= midi <= 127:
        raise GridError(f"{tok!r} is MIDI {midi}, outside 0..127")
    return midi


def note_name(midi: int, prefer: str = "flat", default_octave: int = DEFAULT_OCTAVE) -> str:
    """Inverse of ``parse_token``. Omits the octave when it is the default."""
    table = _SPELL_SHARP if prefer == "sharp" else _SPELL_FLAT
    octave, semitone = divmod(midi - _C0, 12)
    name = table[semitone]
    return name if octave == default_octave else f"{name}{octave}"


def _tokenize(text: str) -> tuple[list[str], int]:
    """Return (tokens in order, slots_per_bar). Non-``bar N:`` lines are prose."""
    slots_per_bar = DEFAULT_SLOTS_PER_BAR
    tokens: list[str] = []
    seen_bar = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0] if raw.lstrip().startswith("#") else raw
        g = _GRID_RE.match(line)
        if g:
            if seen_bar:
                raise GridError("'grid:' must come before the first bar line")
            slots_per_bar = int(g.group(1))
            if slots_per_bar < 1:
                raise GridError(f"grid: {slots_per_bar} is not a usable slot count")
            continue
        m = _BAR_RE.match(line)
        if not m:
            continue
        seen_bar = True
        cells = m.group(2).split()
        if len(cells) != slots_per_bar:
            raise GridError(
                f"bar {m.group(1)}: has {len(cells)} columns, expected {slots_per_bar}"
                f" (set 'grid: {len(cells)}' if that is deliberate)"
            )
        tokens.extend(cells)
    if not tokens:
        raise GridError("no 'bar N:' lines found")
    return tokens, slots_per_bar


def parse(text: str, default_octave: int = DEFAULT_OCTAVE) -> tuple[list[Note], int]:
    """Grid text -> (notes, slots_per_bar)."""
    tokens, slots_per_bar = _tokenize(text)
    notes: list[Note] = []
    for i, tok in enumerate(tokens):
        if tok == REST:
            continue
        if tok == HOLD:
            if not notes:
                raise GridError(f"slot {i}: hold 's' with nothing to hold")
            prev = notes[-1]
            if prev.slot + prev.length != i:
                raise GridError(
                    f"slot {i}: hold 's' does not follow the previous note "
                    f"(which ends at slot {prev.slot + prev.length})"
                )
            notes[-1] = Note(prev.slot, prev.length + 1, prev.midi)
            continue
        notes.append(Note(i, 1, parse_token(tok, default_octave)))
    return notes, slots_per_bar


def parse_notes(text: str, default_octave: int = DEFAULT_OCTAVE) -> list[Note]:
    """``parse`` when you only want the notes."""
    return parse(text, default_octave)[0]


def emit(
    notes: Iterable[Note],
    slots_per_bar: int = DEFAULT_SLOTS_PER_BAR,
    first_bar: int = 1,
    prefer: str = "flat",
    header: bool = True,
    default_octave: int = DEFAULT_OCTAVE,
) -> str:
    """Notes -> grid text. Round-trips through ``parse`` to the same notes."""
    notes = sorted(notes)
    for a, b in zip(notes, notes[1:]):
        if a.slot + a.length > b.slot:
            raise GridError(
                f"overlapping notes at slots {a.slot} and {b.slot}: the grid is "
                "monophonic"
            )
    total = max((n.slot + n.length for n in notes), default=0)
    bars = max(1, -(-total // slots_per_bar))

    cells = [REST] * (bars * slots_per_bar)
    for n in notes:
        if n.slot < 0:
            raise GridError(f"negative slot {n.slot}")
        cells[n.slot] = note_name(n.midi, prefer, default_octave)
        for k in range(n.slot + 1, n.slot + n.length):
            cells[k] = HOLD

    width = max(2, max((len(c) for c in cells), default=2))
    label_w = len(f"bar {first_bar + bars - 1}:")
    lines: list[str] = []
    if slots_per_bar != DEFAULT_SLOTS_PER_BAR:
        lines.append(f"grid: {slots_per_bar}")
    if header:
        lines.append(" " * (label_w + 2) + " ".join(
            _beat_label(i, slots_per_bar).ljust(width) for i in range(slots_per_bar)
        ).rstrip())
    for b in range(bars):
        row = cells[b * slots_per_bar:(b + 1) * slots_per_bar]
        label = f"bar {first_bar + b}:".ljust(label_w)
        lines.append(f"{label}  " + " ".join(c.ljust(width) for c in row).rstrip())
    return "\n".join(lines) + "\n"


def _beat_label(i: int, slots_per_bar: int) -> str:
    """Column headings: 1 & 2 & ... for eighths, 1 e & a ... for sixteenths."""
    if slots_per_bar % 4:
        return str(i + 1)
    per_beat = slots_per_bar // 4
    beat, sub = divmod(i, per_beat)
    if sub == 0:
        return str(beat + 1)
    if per_beat == 2:
        return "&"
    if per_beat == 4:
        return ("", "e", "&", "a")[sub]
    return "."


def to_qn(notes: Iterable[Note], slots_per_bar: int = DEFAULT_SLOTS_PER_BAR
          ) -> list[tuple[float, float, int]]:
    """Notes -> ``[(start_qn, length_qn, midi)]`` relative to the grid's bar 1.

    Assumes 4/4: one bar is 4 quarter notes.
    """
    qn_per_slot = 4.0 / slots_per_bar
    return [(n.slot * qn_per_slot, n.length * qn_per_slot, n.midi) for n in notes]
