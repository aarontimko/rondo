"""hum_to_grid without librosa: the slot merge, the CLI math, and the missing-deps message."""

import builtins
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hum_to_grid  # noqa: E402
from rondo import grid as gridmod  # noqa: E402


def row(slot, pitch):
    return (slot, pitch, 1.0 if pitch is not None else 0.0, 0.0)


class TestSlotsToNotes(unittest.TestCase):
    def test_adjacent_same_pitch_merges(self):
        rows = [row(0, 60), row(1, 60), row(2, 60)]
        self.assertEqual(hum_to_grid.slots_to_notes(rows, set()),
                         [gridmod.Note(0, 3, 60)])

    def test_pitch_change_starts_a_note(self):
        rows = [row(0, 60), row(1, 62)]
        self.assertEqual(hum_to_grid.slots_to_notes(rows, set()),
                         [gridmod.Note(0, 1, 60), gridmod.Note(1, 1, 62)])

    def test_onset_splits_a_repeated_pitch(self):
        rows = [row(0, 60), row(1, 60), row(2, 60)]
        self.assertEqual(hum_to_grid.slots_to_notes(rows, {2}),
                         [gridmod.Note(0, 2, 60), gridmod.Note(2, 1, 60)])

    def test_rest_breaks_the_merge(self):
        rows = [row(0, 60), row(1, None), row(2, 60)]
        self.assertEqual(hum_to_grid.slots_to_notes(rows, set()),
                         [gridmod.Note(0, 1, 60), gridmod.Note(2, 1, 60)])

    def test_all_rests_is_empty(self):
        self.assertEqual(hum_to_grid.slots_to_notes([row(0, None)], set()), [])


class TestCli(unittest.TestCase):
    def test_missing_file_is_a_clean_error(self):
        with self.assertRaises(SystemExit) as cm:
            hum_to_grid.main(["/nonexistent/take.wav"])
        self.assertIn("no such file", str(cm.exception))

    def test_start_and_duration_from_bars(self):
        seen = {}

        def fake(path, bpm, start, dur, **kw):
            seen.update(path=path, bpm=bpm, start=start, dur=dur, **kw)
            return [gridmod.Note(0, 2, 60)], []

        with mock.patch.object(hum_to_grid, "transcribe", fake), \
                mock.patch("os.path.exists", return_value=True):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = hum_to_grid.main(["take.wav", "--bpm", "120", "--start-bar", "3",
                                       "--bars", "2", "--transpose", "12"])
        self.assertEqual(rc, 0)
        # 120 bpm: 2 s per bar; bar 3 starts at 4 s; two bars last 4 s.
        self.assertAlmostEqual(seen["start"], 4.0)
        self.assertAlmostEqual(seen["dur"], 4.0)
        self.assertEqual(seen["transpose"], 12)
        self.assertEqual(seen["slots_per_bar"], 8)
        self.assertTrue(out.getvalue().startswith("# take.wav: 120 bpm, bars 3..4"))

    def test_nothing_voiced_is_a_clean_error(self):
        with mock.patch.object(hum_to_grid, "transcribe", return_value=([], [])), \
                mock.patch("os.path.exists", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                hum_to_grid.main(["take.wav"])
        self.assertIn("nothing voiced", str(cm.exception))


class TestMissingDeps(unittest.TestCase):
    def test_import_error_names_the_install_command(self):
        real_import = builtins.__import__

        def no_librosa(name, *a, **kw):
            if name in ("librosa", "numpy", "soundfile"):
                raise ImportError(name=name)
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", side_effect=no_librosa):
            with self.assertRaises(SystemExit) as cm:
                hum_to_grid._import_deps()
        msg = str(cm.exception)
        self.assertIn("[transcribe]", msg)
        self.assertIn("missing: librosa", msg)


if __name__ == "__main__":
    unittest.main()
