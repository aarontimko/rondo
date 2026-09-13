"""Argument parsing and Lua generation for track.py, project.py, copy_section.py.

Everything here stops short of ``run_lua``: it checks that the parsers accept
what the docs promise, refuse what they should, and that the generated Lua is
well formed (no leftover ``%(...)s``, the right API call, the right numbers).
"""

import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import copy_section  # noqa: E402
import project  # noqa: E402
import track  # noqa: E402
from rondo import reaper  # noqa: E402


def parse_track(*argv):
    return track.build_parser().parse_args(list(argv))


def parse_project(*argv):
    return project.build_parser().parse_args(list(argv))


class TestBarSpan(unittest.TestCase):
    def test_inclusive_span(self):
        self.assertEqual(reaper.bar_span_qn(1, 8), (0.0, 32.0))
        self.assertEqual(reaper.bar_span_qn(9, 16), (32.0, 64.0))

    def test_single_bar(self):
        self.assertEqual(reaper.bar_span_qn(5, 5), (16.0, 20.0))

    def test_backwards_is_an_error(self):
        with self.assertRaises(ValueError):
            reaper.bar_span_qn(8, 1)


class TestTrackParser(unittest.TestCase):
    def test_every_documented_command_parses(self):
        for cmd in track.FLAG_OPS:
            self.assertEqual(parse_track(cmd, "--track", "Pad").command, cmd)
        self.assertEqual(parse_track("show", "--track", "3").track, "3")
        self.assertEqual(parse_track("rename", "--track", "a", "--to", "b").to, "b")
        self.assertEqual(parse_track("add", "--name", "Bass", "--at", "1").at, 1)
        self.assertTrue(parse_track("delete", "--track", "a", "--yes").yes)
        self.assertEqual(parse_track("set-input", "--track", "a", "keyboard").source,
                         "keyboard")
        self.assertEqual(parse_track("volume", "--track", "a", "--db", "-6").db, -6.0)

    def test_commands_list_matches_the_parser(self):
        sub = [a for a in track.build_parser()._actions if a.choices
               and "show" in getattr(a, "choices", {})][0]
        self.assertEqual(sorted(sub.choices), sorted(track.COMMANDS))

    def test_track_is_required_except_for_add(self):
        with self.assertRaises(SystemExit):
            parse_track("mute")
        self.assertEqual(parse_track("add", "--name", "x").name, "x")

    def test_add_does_not_take_track(self):
        with self.assertRaises(SystemExit):
            parse_track("add", "--name", "x", "--track", "y")

    def test_set_input_rejects_unknown_sources(self):
        with self.assertRaises(SystemExit):
            parse_track("set-input", "--track", "a", "line-in")

    def test_delete_needs_yes(self):
        with self.assertRaises(SystemExit) as c:
            track.main(["delete", "--track", "a"])
        self.assertIn("--yes", str(c.exception))

    def test_negative_insert_position_is_refused(self):
        with self.assertRaises(SystemExit) as c:
            track.main(["add", "--name", "x", "--at", "-1"])
        self.assertIn("--at", str(c.exception))


class TestTrackLua(unittest.TestCase):
    def wrap(self, cmd, *argv):
        a = parse_track(cmd, *argv)
        body, undo = track.build(cmd, a)
        return track.OP_LUA % {"index": 2, "body": body,
                               "undo": reaper.lua_str(undo)}

    def test_no_unsubstituted_placeholders(self):
        for src in (
            self.wrap("mute", "--track", "a"),
            self.wrap("unsolo", "--track", "a"),
            self.wrap("rename", "--track", "a", "--to", "New"),
            self.wrap("delete", "--track", "a", "--yes"),
            self.wrap("clear-items", "--track", "a"),
            self.wrap("clear-items", "--track", "a", "--from", "9", "--to", "16"),
            self.wrap("set-input", "--track", "a", "keyboard"),
            self.wrap("volume", "--track", "a", "--db", "-6"),
            track.ADD_LUA % {"name": '"Bass"', "at": "1"},
        ):
            self.assertNotRegex(src, r"%\(\w+\)")
            self.assertIn("log(jsonenc(", src)

    def test_flag_ops_set_the_right_field(self):
        self.assertIn('"B_MUTE", 1', self.wrap("mute", "--track", "a"))
        self.assertIn('"B_MUTE", 0', self.wrap("unmute", "--track", "a"))
        self.assertIn('"I_SOLO", 1', self.wrap("solo", "--track", "a"))
        self.assertIn('"I_RECARM", 1', self.wrap("arm", "--track", "a"))
        self.assertIn('"I_RECARM", 0', self.wrap("disarm", "--track", "a"))

    def test_rename_quotes_awkward_names(self):
        src = self.wrap("rename", "--track", "a", "--to", 'He said "hi"')
        self.assertIn(r'"He said \"hi\""', src)

    def test_clear_items_bar_range_becomes_quarter_notes(self):
        src = self.wrap("clear-items", "--track", "a", "--from", "9", "--to", "16")
        self.assertIn("local FROM_QN, TO_QN = 32.0, 64.0", src)

    def test_clear_items_without_a_range_clears_everything(self):
        src = self.wrap("clear-items", "--track", "a")
        self.assertIn("local FROM_QN, TO_QN = nil, nil", src)

    def test_clear_items_needs_both_ends(self):
        with self.assertRaises(SystemExit):
            track.build("clear-items", parse_track("clear-items", "--track", "a",
                                                   "--from", "9"))

    def test_clear_items_rejects_a_backwards_range(self):
        with self.assertRaises(SystemExit):
            track.build("clear-items", parse_track("clear-items", "--track", "a",
                                                   "--from", "16", "--to", "9"))

    def test_set_input_uses_the_shared_encoding(self):
        self.assertIn('"I_RECINPUT", 6080',
                      self.wrap("set-input", "--track", "a", "keyboard"))
        self.assertIn('"I_RECINPUT", -1',
                      self.wrap("set-input", "--track", "a", "none"))

    def test_volume_is_a_linear_gain(self):
        src = self.wrap("volume", "--track", "a", "--db", "0")
        gain = float(re.search(r'"D_VOL", ([\d.eE+-]+)\)', src).group(1))
        self.assertAlmostEqual(gain, 1.0)


class TestShowText(unittest.TestCase):
    ROW = {
        "index": 4, "name": "Pad", "fx": ["VST3i: Surge XT (Surge Synth Team)"],
        "instrument_fx": 0, "mute": True, "solo": False, "armed": False,
        "monitor": 1.0, "rec_input": 6080.0, "volume": 0.5011872336, "pan": 0.0,
        "items": [{"position": 0.0, "length": 16.0, "midi_notes": 12,
                   "bar": 1, "end_bar": 8}],
    }

    def test_renders_every_field(self):
        text = track.show_text(self.ROW)
        self.assertIn('track 4 "Pad"', text)
        self.assertIn("mute ON", text)
        self.assertIn("armed no", text)
        self.assertIn("virtual MIDI keyboard", text)
        self.assertIn("monitor on", text)
        self.assertIn("-6.0 dB", text)
        self.assertIn("pan center", text)
        self.assertIn("Surge XT", text)
        self.assertIn("bars 1-8", text)
        self.assertIn("12 MIDI note(s)", text)

    def test_empty_track(self):
        row = dict(self.ROW, fx=[], items=[], instrument_fx=-1, volume=0.0)
        text = track.show_text(row)
        self.assertIn("fx: (none)", text)
        self.assertIn("no items", text)
        self.assertIn("-inf dB", text)


class TestProjectParser(unittest.TestCase):
    def test_every_documented_command_parses(self):
        self.assertEqual(parse_project("tabs").command, "tabs")
        self.assertIsNone(parse_project("save").as_path)
        self.assertEqual(parse_project("save", "--as", "/tmp/x.rpp").as_path,
                         "/tmp/x.rpp")
        self.assertEqual(parse_project("cursor", "--bar", "9").beat, 1.0)
        self.assertEqual(parse_project("metronome", "off").state, "off")
        self.assertEqual(parse_project("region", "--name", "B", "--from", "9",
                                       "--to", "16").stop, 16)
        self.assertEqual(parse_project("marker", "--name", "drop", "--bar", "17").bar, 17)
        self.assertEqual(parse_project("tempo", "--bpm", "96").bpm, 96.0)

    def test_metronome_rejects_nonsense(self):
        with self.assertRaises(SystemExit):
            parse_project("metronome", "louder")

    def test_region_needs_both_ends(self):
        with self.assertRaises(SystemExit):
            parse_project("region", "--name", "B", "--from", "9")

    def test_backwards_region_is_refused_before_reaper_is_touched(self):
        with self.assertRaises(SystemExit):
            project.main(["region", "--name", "B", "--from", "16", "--to", "9"])

    def test_absurd_tempo_is_refused_before_reaper_is_touched(self):
        with self.assertRaises(SystemExit):
            project.main(["tempo", "--bpm", "0"])


class TestProjectPathGuard(unittest.TestCase):
    def test_outside_the_repo_is_allowed(self):
        self.assertEqual(project.guard_project_path("/private/tmp/x.rpp").name,
                         "x.rpp")

    def test_suffix_is_enforced(self):
        with self.assertRaises(SystemExit):
            project.guard_project_path("/private/tmp/x.wav")

    def test_inside_the_repo_is_refused(self):
        from rondo import _cli
        with self.assertRaises(SystemExit):
            project.guard_project_path(str(_cli.REPO / "scratch.rpp"))


class TestProjectLua(unittest.TestCase):
    def test_no_unsubstituted_placeholders(self):
        for src in (
            project.SAVE_LUA % {"as": "nil"},
            project.SAVE_LUA % {"as": '"/tmp/x.rpp"'},
            project.CURSOR_LUA % {"qn": 32.0},
            project.METRONOME_LUA % {"want": "true"},
            project.MARK_LUA % {"name": '"B"', "t0": 32.0, "t1": 64.0,
                                "region": "true"},
            project.TEMPO_LUA % {"bpm": 96.0},
            project.TABS_LUA,
        ):
            self.assertNotRegex(src, r"%\(\w+\)")
            self.assertIn("log(jsonenc(", src)

    def test_metronome_uses_the_verified_action(self):
        self.assertIn("40364", project.METRONOME_LUA)

    def test_cursor_does_not_seek_the_transport(self):
        # SetEditCurPos(time, moveview, seekplay) -- seekplay must stay false.
        self.assertIn("SetEditCurPos(reaper.TimeMap2_QNToTime(0, QN), true, false)",
                      project.CURSOR_LUA)

    def test_save_picks_the_right_call_for_each_case(self):
        self.assertIn("Main_SaveProjectEx", project.SAVE_LUA)
        self.assertIn("Main_SaveProject(0, false)", project.SAVE_LUA)

    def test_marker_deletes_by_display_index(self):
        # the walk lives in the Lua prelude now, so copy_section.py --region
        # replaces a same-named region exactly the way project.py region does.
        self.assertIn("add_marker_replacing(NAME, t0, t1, IS_REGION)", project.MARK_LUA)
        self.assertIn("DeleteProjectMarker(0, idx, isrgn)", reaper.LUA_PRELUDE)
        self.assertIn("AddProjectMarker2(0, is_region, t0, t1, name, -1, 0)",
                      reaper.LUA_PRELUDE)


class TestCopySectionRegion(unittest.TestCase):
    def src(self, start=1, stop=8, at=17, region='"A3"'):
        span = stop - start + 1
        t0, t1 = reaper.bar_span_qn(start, stop)
        return copy_section.LUA % {
            "from": t0, "to": t1, "at": reaper.bars_to_qn(at),
            "region_end": reaper.bars_to_qn(at + span),
            "tracks": "nil", "region": region,
        }

    def test_no_unsubstituted_placeholders(self):
        for src in (self.src(), self.src(region="nil")):
            self.assertNotRegex(src, r"%\(\w+\)")
            self.assertIn("log(jsonenc(", src)

    def test_the_region_is_measured_in_bars_not_by_adding_a_duration(self):
        src = self.src(start=1, stop=8, at=17)
        self.assertIn("local FROM_QN, TO_QN, AT_QN = 0.0, 32.0, 64.0", src)
        self.assertIn("local REGION_END_QN = 96.0", src)     # bars 17-24
        self.assertIn("TimeMap2_QNToTime(0, REGION_END_QN)", src)

    def test_it_replaces_a_same_named_region_like_project_py_does(self):
        self.assertIn("add_marker_replacing(REGION, ta, rend, true)", self.src())
        self.assertNotIn("AddProjectMarker2", copy_section.LUA)

    def test_a_backwards_range_is_refused_before_reaper_is_touched(self):
        with self.assertRaises(SystemExit):
            copy_section.main(["--from", "8", "--to", "1", "--at", "17"])

    def test_copying_onto_itself_is_refused(self):
        with self.assertRaises(SystemExit) as c:
            copy_section.main(["--from", "1", "--to", "8", "--at", "4"])
        self.assertIn("inside the source range", str(c.exception))


if __name__ == "__main__":
    unittest.main()
