"""Grid format: parsing, emitting, and the worked-example round trip."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rondo.grid import (  # noqa: E402
    GridError,
    Note,
    emit,
    note_name,
    parse,
    parse_notes,
    parse_token,
    to_qn,
)

EXAMPLE = """\
Example melody, four bars.
Ef = E flat, Cs = C sharp, / = rest, s = hold previous note (across bar lines too).

        1  &  2  &  3  &  4  &
bar 1:  C  D  E  s  G  s  /  E
bar 2:  D  s  s  s  C  s  s  s
bar 3:  s  s  E  F  G  s  Bf s
bar 4:  A  s  /  Cs D  s  s  s
"""

# Worked out by hand from the grid above.
EXAMPLE_NOTES = [
    (0, 1, 60), (1, 1, 62), (2, 2, 64), (4, 2, 67), (7, 1, 64),
    (8, 4, 62), (12, 6, 60),
    (18, 1, 64), (19, 1, 65), (20, 2, 67), (22, 2, 70),
    (24, 2, 69), (27, 1, 61), (28, 4, 62),
]


class TestTokens(unittest.TestCase):
    def test_default_octave(self):
        self.assertEqual(parse_token("C"), 60)
        self.assertEqual(parse_token("E"), 64)
        self.assertEqual(parse_token("B"), 71)

    def test_accidentals(self):
        self.assertEqual(parse_token("Ef"), 63)
        self.assertEqual(parse_token("Eb"), 63)
        self.assertEqual(parse_token("Cs"), 61)
        self.assertEqual(parse_token("C#"), 61)

    def test_octaves(self):
        self.assertEqual(parse_token("C3"), 48)
        self.assertEqual(parse_token("C5"), 72)
        self.assertEqual(parse_token("Ef3"), 51)
        self.assertEqual(parse_token("C-1"), 0)

    def test_lowercase(self):
        self.assertEqual(parse_token("g"), 67)
        self.assertEqual(parse_token("ef"), 63)

    def test_bad_tokens(self):
        for bad in ("H", "", "Cx", "5", "Cff"):
            with self.assertRaises(GridError):
                parse_token(bad)

    def test_out_of_range(self):
        with self.assertRaises(GridError):
            parse_token("C-5")

    def test_note_name_round_trip(self):
        for midi in range(0, 128):
            self.assertEqual(parse_token(note_name(midi)), midi)
            self.assertEqual(parse_token(note_name(midi, "sharp")), midi)

    def test_note_name_omits_default_octave(self):
        self.assertEqual(note_name(60), "C")
        self.assertEqual(note_name(63), "Ef")
        self.assertEqual(note_name(61, "sharp"), "Cs")
        self.assertEqual(note_name(48), "C3")


class TestParse(unittest.TestCase):
    def test_simple(self):
        notes, spb = parse("bar 1:  C  /  D  /  E  /  F  /")
        self.assertEqual(spb, 8)
        self.assertEqual([tuple(n) for n in notes],
                         [(0, 1, 60), (2, 1, 62), (4, 1, 64), (6, 1, 65)])

    def test_hold_within_bar(self):
        notes, _ = parse("bar 1:  C  s  s  s  /  /  /  /")
        self.assertEqual([tuple(n) for n in notes], [(0, 4, 60)])

    def test_hold_across_bars(self):
        notes, _ = parse(
            "bar 1:  /  /  /  /  /  /  /  C\n"
            "bar 2:  s  s  s  /  /  /  /  /\n"
        )
        self.assertEqual([tuple(n) for n in notes], [(7, 4, 60)])

    def test_hold_with_nothing_to_hold(self):
        with self.assertRaises(GridError):
            parse("bar 1:  s  /  /  /  /  /  /  /")

    def test_hold_after_rest_is_rejected(self):
        with self.assertRaises(GridError) as cm:
            parse("bar 1:  C  /  s  /  /  /  /  /")
        self.assertIn("does not follow", str(cm.exception))

    def test_sixteenths(self):
        text = "grid: 16\nbar 1: " + " ".join(["C"] * 16)
        notes, spb = parse(text)
        self.assertEqual(spb, 16)
        self.assertEqual(len(notes), 16)

    def test_wrong_column_count(self):
        with self.assertRaises(GridError) as cm:
            parse("bar 1:  C  D  E")
        self.assertIn("3 columns", str(cm.exception))

    def test_prose_and_header_ignored(self):
        notes, _ = parse(
            "some title\n\n        1  &  2  &  3  &  4  &\n"
            "bar 9:  C  /  /  /  /  /  /  /\n"
        )
        self.assertEqual([tuple(n) for n in notes], [(0, 1, 60)])

    def test_bar_labels_do_not_shift_slots(self):
        """Slot 0 is the first bar LINE, whatever number it carries."""
        a = parse_notes("bar 1:  C  /  /  /  /  /  /  /")
        b = parse_notes("bar 9:  C  /  /  /  /  /  /  /")
        self.assertEqual(a, b)

    def test_no_bars(self):
        with self.assertRaises(GridError):
            parse("just some prose\n")

    def test_grid_after_bar_is_rejected(self):
        with self.assertRaises(GridError):
            parse("bar 1: C / / / / / / /\ngrid: 16\n")


class TestEmit(unittest.TestCase):
    def test_round_trip_simple(self):
        notes = [Note(0, 2, 60), Note(4, 1, 67)]
        self.assertEqual(parse_notes(emit(notes)), notes)

    def test_round_trip_sixteenths(self):
        notes = [Note(0, 3, 60), Note(5, 11, 72)]
        text = emit(notes, 16)
        self.assertIn("grid: 16", text)
        got, spb = parse(text)
        self.assertEqual(spb, 16)
        self.assertEqual(got, notes)

    def test_emitted_bar_labels(self):
        text = emit([Note(0, 1, 60), Note(8, 1, 62)], first_bar=9)
        self.assertIn("bar 9:", text)
        self.assertIn("bar 10:", text)

    def test_octave_in_output(self):
        text = emit([Note(0, 1, 48)])
        self.assertIn("C3", text)
        self.assertEqual(parse_notes(text), [Note(0, 1, 48)])

    def test_overlap_rejected(self):
        with self.assertRaises(GridError):
            emit([Note(0, 4, 60), Note(2, 1, 64)])

    def test_empty(self):
        self.assertIn("bar 1:", emit([]))


class TestWorkedExample(unittest.TestCase):
    def test_parses_to_the_hand_worked_notes(self):
        notes, spb = parse(EXAMPLE)
        self.assertEqual(spb, 8)
        self.assertEqual([tuple(n) for n in notes], EXAMPLE_NOTES)

    def test_round_trips_through_emit(self):
        notes, spb = parse(EXAMPLE)
        again, spb2 = parse(emit(notes, spb))
        self.assertEqual(spb2, spb)
        self.assertEqual(again, notes)

    def test_emitted_text_matches_the_source_bar_lines(self):
        """Byte-identical apart from enharmonics: Cs (61) is emitted as Df."""
        notes, spb = parse(EXAMPLE)
        emitted = [l for l in emit(notes, spb).splitlines() if l.startswith("bar ")]
        source = [l for l in EXAMPLE.splitlines() if l.startswith("bar ")]
        self.assertEqual(len(emitted), len(source))
        for e, s in zip(emitted, source):
            self.assertEqual(e.rstrip(), s.replace("Cs", "Df").rstrip())

    def test_to_qn(self):
        notes, spb = parse(EXAMPLE)
        qn = to_qn(notes, spb)
        self.assertEqual(qn[0], (0.0, 0.5, 60))
        self.assertEqual(qn[5], (4.0, 2.0, 62))       # bar 2 beat 1, a half note
        self.assertAlmostEqual(qn[-1][0], 14.0)       # bar 4, beat 3


if __name__ == "__main__":
    unittest.main()
