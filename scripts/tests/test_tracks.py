"""The --track resolver, the I_RECINPUT decoder and the dB/gain conversions.

Nothing here talks to Reaper.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rondo import tracks  # noqa: E402

NAMES = [
    "Drums",
    "Bass",
    "Melody idea (record here)",
    "Melody draft (from hum)",
    "melody",
    "",
]


class TestResolveExact(unittest.TestCase):
    def test_exact_name(self):
        self.assertEqual(tracks.resolve("Drums", NAMES), 0)

    def test_exact_is_case_insensitive(self):
        self.assertEqual(tracks.resolve("bass", NAMES), 1)
        self.assertEqual(tracks.resolve("MELODY", NAMES), 4)

    def test_surrounding_space_is_ignored(self):
        self.assertEqual(tracks.resolve("  Drums ", NAMES), 0)

    def test_exact_beats_prefix(self):
        # "melody" is also a prefix of two other tracks; the exact match wins.
        self.assertEqual(tracks.resolve("melody", NAMES), 4)


class TestResolveIndex(unittest.TestCase):
    def test_index(self):
        self.assertEqual(tracks.resolve("0", NAMES), 0)
        self.assertEqual(tracks.resolve("3", NAMES), 3)

    def test_index_out_of_range(self):
        with self.assertRaises(tracks.NoSuchTrack) as c:
            tracks.resolve("9", NAMES)
        self.assertIn("6 track(s)", str(c.exception))

    def test_a_track_named_like_a_number_wins_over_the_index(self):
        names = ["a", "b", "3", "d"]
        self.assertEqual(tracks.resolve("3", names), 2)

    def test_negative_is_not_an_index(self):
        with self.assertRaises(tracks.NoSuchTrack):
            tracks.resolve("-1", NAMES)


class TestResolvePrefix(unittest.TestCase):
    def test_unique_prefix(self):
        self.assertEqual(tracks.resolve("dru", NAMES), 0)
        self.assertEqual(tracks.resolve("Melody i", NAMES), 2)

    def test_ambiguous_prefix_is_an_error(self):
        # NAMES[:4] has no track called exactly "melody", so nothing short-circuits.
        with self.assertRaises(tracks.AmbiguousTrack) as c:
            tracks.resolve("Melody", NAMES[:4])
        self.assertIn("2 tracks", str(c.exception))
        self.assertIn("'Melody idea (record here)'", str(c.exception))

    def test_substring_is_not_a_prefix(self):
        with self.assertRaises(tracks.NoSuchTrack):
            tracks.resolve("hum", NAMES)


class TestResolveErrors(unittest.TestCase):
    def test_ambiguous_exact_names(self):
        with self.assertRaises(tracks.AmbiguousTrack):
            tracks.resolve("dup", ["dup", "DUP"])

    def test_no_tracks(self):
        with self.assertRaises(tracks.NoSuchTrack):
            tracks.resolve("anything", [])

    def test_empty_spec(self):
        with self.assertRaises(tracks.NoSuchTrack):
            tracks.resolve("   ", NAMES)

    def test_errors_are_systemexit(self):
        # _cli.run lets SystemExit through, so these print one clean line.
        self.assertTrue(issubclass(tracks.TrackError, SystemExit))


class TestRecInput(unittest.TestCase):
    def test_round_trip_of_the_named_inputs(self):
        self.assertEqual(tracks.REC_INPUTS["keyboard"], 4096 + (62 << 5))
        self.assertIn("virtual MIDI keyboard",
                      tracks.rec_input_name(tracks.REC_INPUTS["keyboard"]))
        self.assertEqual(tracks.rec_input_name(tracks.REC_INPUTS["none"]), "none")
        self.assertIn("mic", tracks.rec_input_name(tracks.REC_INPUTS["mic"]))

    def test_audio_inputs_are_one_based_for_humans(self):
        self.assertEqual(tracks.rec_input_name(2), "audio input 3")

    def test_midi_device_and_channel(self):
        self.assertEqual(tracks.rec_input_name(4096 + (5 << 5) + 3),
                         "MIDI device 5, channel 3")
        self.assertEqual(tracks.rec_input_name(4096 + (5 << 5)),
                         "MIDI device 5, all channels")

    def test_floats_from_reaper_are_accepted(self):
        self.assertEqual(tracks.rec_input_name(6080.0),
                         tracks.rec_input_name(6080))


class TestVolume(unittest.TestCase):
    def test_unity(self):
        self.assertAlmostEqual(tracks.db_to_gain(0.0), 1.0)
        self.assertAlmostEqual(tracks.gain_to_db(1.0), 0.0)

    def test_known_points(self):
        self.assertAlmostEqual(tracks.db_to_gain(-6.0), 0.5011872336)
        self.assertAlmostEqual(tracks.db_to_gain(6.0), 1.9952623150)

    def test_round_trip(self):
        for db in (-60.0, -12.5, -3.0, 0.0, 4.2, 12.0):
            self.assertAlmostEqual(tracks.gain_to_db(tracks.db_to_gain(db)), db)

    def test_silence(self):
        self.assertEqual(tracks.gain_to_db(0.0), float("-inf"))


class TestPan(unittest.TestCase):
    def test_center(self):
        self.assertEqual(tracks.pan_name(0.0), "center")

    def test_sides(self):
        self.assertEqual(tracks.pan_name(-1.0), "100% L")
        self.assertEqual(tracks.pan_name(0.3), "30% R")


if __name__ == "__main__":
    unittest.main()
