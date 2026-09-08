# The rondo grid

A melody written as one line per bar, one column per slot. It is meant to be
easy for a person to read out loud, easy to correct by hand, and unambiguous
enough for a script to write straight into Reaper.

```
        1  &  2  &  3  &  4  &
bar 1:  G  E  /  E  Ef E  Ef E
bar 2:  D  s  C  s  s  s  Ef E
bar 3:  D  s  C  s  s  s  s  D
bar 4:  /  C  s  s  s  s  /  /
```

Read bar 1 as: G, E, rest, E, E-flat, E, E-flat, E -- all eighth notes.
Read bar 2 as: D held for two eighths, then C held for four, then E-flat, E.

## Lines

* Only a line matching `bar <n>:` carries notes. Anything else -- a title, a
  comment, the `1 & 2 &` header row -- is ignored, so you can annotate freely.
* `grid: <n>` sets the slots per bar. The default is 8 (eighth notes); 16
  gives sixteenths. It must appear before the first `bar` line.
* Every bar line must have exactly `slots_per_bar` columns. A mismatched count
  is an error naming the bar, not a silent shift.
* The bar **numbers are labels for humans**. Slots are counted from the first
  bar line in the file, so a block that starts at `bar 9:` still starts at slot
  0. Where it lands on the timeline is `write_notes.py --bar`'s job.

## Tokens

| token | meaning |
| --- | --- |
| `/` | rest |
| `s` | hold: extend the previous note by one slot. Works across bar lines. |
| `C` `D` ... `B` | a note in the default octave (4) |
| `Ef` `Eb` | E flat. `f` or `b` after the letter lowers it. |
| `Cs` `C#` | C sharp. `s` or `#` after the letter raises it. |
| `C3` `Ef3` `Cs5` | an explicit octave |

Middle C is `C` = C4 = MIDI 60, so `E` is MIDI 64. Lower case works (`ef` is
E flat). A bare `s` is always a hold, never a note.

A hold with nothing before it, or a hold that does not directly follow the
note it would extend (a rest in between), is an error.

## Both directions

`rondo.grid.parse(text)` returns `([Note(slot, length, midi), ...],
slots_per_bar)`. `rondo.grid.emit(notes, slots_per_bar, first_bar=...)` writes
the text back out.

The round trip preserves the notes exactly. It does not always preserve the
spelling: 61 comes back as `Df` even if you wrote `Cs`, because a pitch has no
memory of how you spelled it. Pass `prefer="sharp"` for the other convention.
`emit` refuses overlapping notes -- the grid is monophonic by construction.

## Where it goes

```
python scripts/write_notes.py --track "Melody" --bar 9 --grid melody.txt --replace
python scripts/hum_to_grid.py take.wav --bpm 120 --start-bar 9 --bars 8 \
  | python scripts/write_notes.py --track "Melody" --bar 9 --grid - --replace
```

`write_notes.py` also takes `--notes` with JSON `[[start_qn, length_qn, midi,
velocity?], ...]` measured from `--bar`, which is the way to write chords and
drum patterns -- anything the monophonic grid cannot express.

## Worked example

This is Aaron's melody as written on 2026-09-07 (`melody_v2`), eight bars over
the second A section:

```
        1  &  2  &  3  &  4  &
bar 1:  G  E  /  E  Ef E  Ef E
bar 2:  D  s  C  s  s  s  Ef E
bar 3:  D  s  C  s  s  s  s  D
bar 4:  /  C  s  s  s  s  /  /
bar 5:  /  Cs D  s  s  s  s  s
bar 6:  s  s  s  s  Ef E  /  F
bar 7:  s  D  s  s  s  s  s  s
bar 8:  s  s  s  s  E  s  F  s
```

It parses to 23 notes. The long ones are holds running across bar lines: the D
that starts in bar 5 slot 34 runs ten slots into bar 6, and the D in bar 7 runs
eleven slots into bar 8. `scripts/tests/test_grid.py` pins this example against
the note list that was actually written into Reaper.
