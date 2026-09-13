"""Display suffixes, and the --track resolver every CLI now shares.

Nothing here talks to Reaper: ``snapshot`` and ``run_lua_json`` are mocked, so
these tests check the arguments each CLI hands to Lua -- the track INDEX it
resolved, and the name it decided the track should carry.
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import add_instrument  # noqa: E402
import build_kit  # noqa: E402
import copy_section  # noqa: E402
import record  # noqa: E402
import write_notes  # noqa: E402
from rondo import tracks  # noqa: E402

# A project named the way rondo now names things: role first, then what is
# making the sound. "Lead" and "Lead Double" are the pair that broke the old
# exact-only find_track.
NAMED = [
    "Drums (RS5K kit: one-shots)",
    "Bass (Dexed: E BASS 1)",
    "Lead (Surge: Saw Octaves)",
    "Lead Double (Surge: Gliss Lead)",
    "Pad (Surge: MKS-70 Warm Pad)",
]


def rows(*names) -> list[dict]:
    return [{"name": n, "index": i} for i, n in enumerate(names)]


class TestSplitRole(unittest.TestCase):
    def test_a_suffix_comes_off(self):
        self.assertEqual(tracks.split_role("Pad (Surge: MKS-70 Warm Pad)"),
                         ("Pad", "Surge: MKS-70 Warm Pad"))

    def test_a_plain_name_is_all_role(self):
        self.assertEqual(tracks.split_role("Drums"), ("Drums", ""))

    def test_only_the_last_group_is_the_suffix(self):
        self.assertEqual(tracks.split_role("Melody (hum) (Surge: Bell)"),
                         ("Melody (hum)", "Surge: Bell"))

    def test_a_name_that_is_nothing_but_a_suffix_keeps_itself(self):
        # stripping "(unnamed)" down to "" would leave nothing to match on
        self.assertEqual(tracks.split_role("(unnamed)"), ("(unnamed)", ""))

    def test_whitespace_and_empty(self):
        self.assertEqual(tracks.split_role("  Pad (Surge: Bell)  "),
                         ("Pad", "Surge: Bell"))
        self.assertEqual(tracks.split_role(""), ("", ""))


class TestDisplayName(unittest.TestCase):
    def test_role_instrument_patch(self):
        self.assertEqual(tracks.display_name("Pad", "Surge", "MKS-70 Warm Pad"),
                         "Pad (Surge: MKS-70 Warm Pad)")

    def test_no_patch(self):
        self.assertEqual(tracks.display_name("Keys", "GM Piano"), "Keys (GM Piano)")
        self.assertEqual(tracks.display_name("Keys", "GM Piano", ""), "Keys (GM Piano)")

    def test_a_second_patch_replaces_the_suffix_instead_of_stacking(self):
        role, _ = tracks.split_role("Pad (Surge: MKS-70 Warm Pad)")
        self.assertEqual(tracks.display_name(role, "Surge", "Bell Pad"),
                         "Pad (Surge: Bell Pad)")

    def test_round_trip(self):
        name = tracks.display_name("Lead Double", "Surge", "Gliss Lead")
        self.assertEqual(tracks.split_role(name), ("Lead Double", "Surge: Gliss Lead"))


class TestResolveRole(unittest.TestCase):
    def test_role_finds_the_suffixed_track(self):
        self.assertEqual(tracks.resolve("Pad", NAMED), 4)
        self.assertEqual(tracks.resolve("drums", NAMED), 0)

    def test_role_is_exact_and_does_not_bleed_into_a_longer_role(self):
        self.assertEqual(tracks.resolve("Lead", NAMED), 2)
        self.assertEqual(tracks.resolve("Lead Double", NAMED), 3)

    def test_an_exact_name_still_wins_over_a_role(self):
        names = ["Pad", "Pad (Surge: Bell)"]
        self.assertEqual(tracks.resolve("Pad", names), 0)

    def test_role_beats_the_index(self):
        # a track whose ROLE is "3" is reachable by name, like an exact match
        names = ["a", "b", "c", "d", "3 (Surge: Bell)"]
        self.assertEqual(tracks.resolve("3", names), 4)

    def test_an_ambiguous_role_is_an_error_naming_the_candidates(self):
        names = ["Pad (Surge: Bell)", "Pad (Dexed: Glass)"]
        with self.assertRaises(tracks.AmbiguousTrack) as c:
            tracks.resolve("Pad", names)
        msg = str(c.exception)
        self.assertIn("is the role of 2 tracks", msg)
        self.assertIn("0 'Pad (Surge: Bell)'", msg)
        self.assertIn("1 'Pad (Dexed: Glass)'", msg)

    def test_the_four_steps_in_order(self):
        self.assertEqual(tracks.resolve("Lead (Surge: Saw Octaves)", NAMED), 2)  # exact
        self.assertEqual(tracks.resolve("Lead", NAMED), 2)                       # role
        self.assertEqual(tracks.resolve("1", NAMED), 1)                          # index
        self.assertEqual(tracks.resolve("Lead D", NAMED), 3)                     # prefix


class TestResolveWithoutPrefix(unittest.TestCase):
    """What add_instrument / build_kit / record use: a miss means 'create it'."""

    def test_exact_role_and_index_still_work(self):
        self.assertEqual(tracks.resolve("Pad (Surge: MKS-70 Warm Pad)", NAMED,
                                        prefix=False), 4)
        self.assertEqual(tracks.resolve("pad", NAMED, prefix=False), 4)
        self.assertEqual(tracks.resolve("2", NAMED, prefix=False), 2)

    def test_a_prefix_does_not_match(self):
        with self.assertRaises(tracks.NoSuchTrack):
            tracks.resolve("Lead D", NAMED, prefix=False)

    def test_find_or_none_says_create_it(self):
        self.assertIsNone(tracks.find_or_none("Strings", rows(*NAMED)))
        self.assertIsNone(tracks.find_or_none("Lead Do", rows(*NAMED)))
        self.assertEqual(tracks.find_or_none("lead", rows(*NAMED)), 2)

    def test_find_or_none_still_refuses_an_ambiguous_spec(self):
        with self.assertRaises(tracks.AmbiguousTrack):
            tracks.find_or_none("Pad", rows("Pad (Surge: Bell)", "Pad (Dexed: Glass)"))

    def test_find_or_none_refuses_an_index_that_is_out_of_range(self):
        # "--track 9" asks for a track that should exist; a track called "9" is
        # never what was meant.
        with self.assertRaises(tracks.NoSuchTrack) as c:
            tracks.find_or_none("9", rows(*NAMED))
        self.assertIn("out of range", str(c.exception))

    def test_find_or_none_refuses_an_empty_spec(self):
        with self.assertRaises(tracks.NoSuchTrack):
            tracks.find_or_none("  ", rows(*NAMED))


class CliCase(unittest.TestCase):
    """One mock for the snapshot, one for the Lua; the Lua source is the assertion."""

    module = None

    def run_cli(self, argv, result, names=NAMED):
        with mock.patch.object(self.module.tracks, "snapshot",
                               return_value=rows(*names)), \
             mock.patch.object(self.module.reaper, "run_lua_json",
                               return_value=result) as run, \
             mock.patch.object(self.module._cli, "require_reaper", return_value="7.79"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = self.module.main(argv)
        self.assertEqual(rc, 0, out.getvalue())
        return run.call_args.args[0], out.getvalue()


def instrument_result(**over):
    r = {"track": 4, "fx": 0, "created": False, "renamed": True,
         "name": "Pad (Surge: Nice Pluck 1)", "fx_name": "VST3i: Surge XT",
         "preset_index": 0, "preset": "Nice Pluck 1", "patch_file": "/tmp/x.vstpreset",
         "patch_ok": True}
    r.update(over)
    return r


class TestAddInstrumentHandoff(CliCase):
    module = add_instrument

    def setUp(self):
        # Surge patch resolution reads the real patch library; stub it out.
        self.fxp = mock.patch.object(
            add_instrument.surge_preset, "resolve_patch",
            side_effect=lambda name: Path(f"/patches/{name}.fxp"))
        self.build = mock.patch.object(add_instrument.surge_preset, "build_vstpreset",
                                       return_value=Path("/tmp/x.vstpreset"))
        self.fxp.start()
        self.build.start()
        self.addCleanup(self.fxp.stop)
        self.addCleanup(self.build.stop)

    def test_a_role_resolves_and_the_suffix_is_replaced(self):
        src, _ = self.run_cli(
            ["--track", "pad", "--instrument", "surge", "--patch", "Nice Pluck 1"],
            instrument_result())
        self.assertIn('local TI, NAME, NEW_NAME = 4, "Pad", "Pad (Surge: Nice Pluck 1)"',
                      src)

    def test_keep_name_leaves_the_name_alone(self):
        src, _ = self.run_cli(
            ["--track", "pad", "--instrument", "surge", "--patch", "Bell", "--keep-name"],
            instrument_result(renamed=False, name="Pad (Surge: MKS-70 Warm Pad)"))
        self.assertIn('local TI, NAME, NEW_NAME = 4, "Pad", nil', src)

    def test_a_missing_track_is_created_under_its_display_name(self):
        src, _ = self.run_cli(
            ["--track", "Strings", "--instrument", "surge", "--patch", "Bowed"],
            instrument_result(created=True, track=5, name="Strings (Surge: Bowed)"))
        self.assertIn('local TI, NAME, NEW_NAME = nil, "Strings", "Strings (Surge: Bowed)"',
                      src)
        self.assertIn('P_NAME", NEW_NAME or NAME, true)', src)

    def test_a_prefix_creates_rather_than_landing_on_the_longer_track(self):
        # "--track Lead" must not load onto "Lead Double"; it is its own track.
        src, _ = self.run_cli(
            ["--track", "Lead Dou", "--instrument", "dexed", "--patch", "E PIANO 1"],
            instrument_result(created=True, name="Lead Dou (Dexed: E PIANO 1)"))
        self.assertIn('local TI, NAME, NEW_NAME = nil, "Lead Dou", '
                      '"Lead Dou (Dexed: E PIANO 1)"', src)

    def test_an_instrument_with_no_patch_gets_a_one_word_suffix(self):
        src, _ = self.run_cli(
            ["--track", "Keys", "--instrument", "piano"],
            instrument_result(created=True, name="Keys (GM Piano)", preset=""))
        self.assertIn('local TI, NAME, NEW_NAME = nil, "Keys", "Keys (GM Piano)"', src)

    def test_the_label_names_the_patch_that_was_actually_found(self):
        # --patch is a substring; the .fxp that matched is what the track says.
        add_instrument.surge_preset.resolve_patch.side_effect = (
            lambda name: Path("/patches/MKS-70 Warm Pad.fxp"))
        src, _ = self.run_cli(
            ["--track", "pad", "--instrument", "surge", "--patch", "warm"],
            instrument_result())
        self.assertIn('"Pad (Surge: MKS-70 Warm Pad)"', src)

    def test_the_rename_happens_after_the_patch_loads(self):
        src, _ = self.run_cli(
            ["--track", "pad", "--instrument", "surge", "--patch", "Bell"],
            instrument_result())
        self.assertLess(src.index("TrackFX_SetPreset"),
                        src.index('P_NAME", NEW_NAME, true'))

    def test_every_instrument_has_a_label(self):
        self.assertEqual(sorted(add_instrument.LABELS),
                         sorted(add_instrument.INSTRUMENTS))


class TestBuildKitHandoff(CliCase):
    module = build_kit

    RESULT = {"track": "Drums (RS5K kit: test kit)", "track_index": 0,
              "created": False, "pads": []}

    def manifest(self, **extra) -> Path:
        d = Path(tempfile.mkdtemp())
        wav = d / "36_kick.wav"
        wav.write_bytes(b"RIFF")
        (d / "kit.json").write_text(json.dumps(
            dict({"samples": {"36": {"file": "36_kick.wav"}}}, **extra)))
        return d / "kit.json"

    def test_the_track_is_named_after_the_manifest(self):
        m = self.manifest(name="test kit")
        src, _ = self.run_cli(["--manifest", str(m)], self.RESULT)
        self.assertIn('local TI, NAME, NEW_NAME = 0, "Drums", '
                      '"Drums (RS5K kit: test kit)"', src)

    def test_keep_name(self):
        m = self.manifest(name="test kit")
        src, _ = self.run_cli(["--manifest", str(m), "--keep-name"], self.RESULT)
        self.assertIn('local TI, NAME, NEW_NAME = 0, "Drums", nil', src)

    def test_a_manifest_with_no_name_falls_back_to_its_file_name(self):
        m = self.manifest()
        self.assertEqual(build_kit.manifest_name(m), "kit")

    def test_the_shipped_manifest_has_a_name(self):
        name = build_kit.manifest_name(build_kit.DEFAULT_MANIFEST)
        self.assertTrue(name)
        self.assertEqual(
            tracks.display_name("Drums", build_kit.KIT_LABEL, name),
            f"Drums (RS5K kit: {name})")

    def test_a_named_track_resolves_by_role(self):
        m = self.manifest(name="test kit")
        src, _ = self.run_cli(["--manifest", str(m), "--track", "0"], self.RESULT)
        self.assertIn('local TI, NAME, NEW_NAME = 0, "Drums", ', src)


class TestRecordHandoff(CliCase):
    module = record

    RESULT = {"track": {"track": "Lead (Surge: Saw Octaves)", "index": 2,
                        "created": False, "rec_input": 6080, "monitor": 1,
                        "armed": True},
              "disarmed": 0, "metronome": False}

    def test_a_role_resolves_to_an_index(self):
        src, _ = self.run_cli(["--track", "lead", "--source", "keyboard"], self.RESULT)
        self.assertIn('local TI, NAME = 2, "lead"', src)

    def test_a_missing_track_is_still_created(self):
        src, _ = self.run_cli(["--track", "Vocal", "--source", "mic"],
                              dict(self.RESULT, track=dict(self.RESULT["track"],
                                                           created=True)))
        self.assertIn('local TI, NAME = nil, "Vocal"', src)
        self.assertIn('P_NAME", NAME, true)', src)

    def test_record_does_not_rename_anything(self):
        # naming is add_instrument's and build_kit's job: record only arms.
        self.assertNotIn("NEW_NAME", record.LUA)


class TestWriteNotesHandoff(CliCase):
    module = write_notes

    RESULT = {"track": "Pad (Surge: MKS-70 Warm Pad)", "index": 4, "notes": 1,
              "removed": 0, "midi_notes": 1, "item_length": 2.4}

    def test_a_role_resolves_to_an_index_and_the_exact_name(self):
        src, out = self.run_cli(
            ["--track", "pad", "--bar", "1", "--notes", "[[0,1,60]]"], self.RESULT)
        self.assertIn('local TI, NAME = 4, "Pad (Surge: MKS-70 Warm Pad)"', src)
        self.assertIn('track 4 "Pad (Surge: MKS-70 Warm Pad)"', out)

    def test_an_index_resolves(self):
        src, _ = self.run_cli(
            ["--track", "3", "--bar", "1", "--notes", "[[0,1,60]]"],
            dict(self.RESULT, index=3, track="Lead Double (Surge: Gliss Lead)"))
        self.assertIn('local TI, NAME = 3, "Lead Double (Surge: Gliss Lead)"', src)

    def test_a_prefix_resolves(self):
        src, _ = self.run_cli(
            ["--track", "Lead D", "--bar", "1", "--notes", "[[0,1,60]]"],
            dict(self.RESULT, index=3, track="Lead Double (Surge: Gliss Lead)"))
        self.assertIn("local TI, NAME = 3, ", src)

    def test_the_exact_only_lua_lookup_is_gone(self):
        self.assertNotIn("find_track", write_notes.LUA)
        self.assertIn("GetTrack(0, TI)", write_notes.LUA)

    def test_an_unknown_track_is_an_error_that_lists_the_project(self):
        with mock.patch.object(write_notes.tracks, "snapshot",
                               return_value=rows(*NAMED)), \
             mock.patch.object(write_notes.reaper, "run_lua_json") as run, \
             mock.patch.object(write_notes._cli, "require_reaper", return_value="7.79"), \
             self.assertRaises(SystemExit) as c:
            write_notes.main(["--track", "Choir", "--bar", "1", "--notes", "[[0,1,60]]"])
        self.assertIn("Pad (Surge: MKS-70 Warm Pad)", str(c.exception))
        run.assert_not_called()


class TestCopySectionHandoff(CliCase):
    module = copy_section

    RESULT = {"copied": 2, "straddling": 0, "region": None,
              "tracks": [{"track": "Drums (RS5K kit: one-shots)", "index": 0,
                          "copied": 2, "straddling": 0}]}

    def test_named_tracks_become_indexes(self):
        src, _ = self.run_cli(
            ["--from", "1", "--to", "8", "--at", "17", "--tracks", "drums,Lead"],
            self.RESULT)
        self.assertIn("local TRACKS = {0,2}", src)
        self.assertIn("wanted[t]", src)

    def test_an_index_and_a_prefix_work_too(self):
        src, _ = self.run_cli(
            ["--from", "1", "--to", "8", "--at", "17", "--tracks", "1,Lead D"],
            self.RESULT)
        self.assertIn("local TRACKS = {1,3}", src)

    def test_naming_one_track_twice_is_not_two_copies(self):
        src, _ = self.run_cli(
            ["--from", "1", "--to", "8", "--at", "17", "--tracks", "Pad,4"],
            self.RESULT)
        self.assertIn("local TRACKS = {4}", src)

    def test_no_tracks_flag_still_means_every_track(self):
        src, _ = self.run_cli(["--from", "1", "--to", "8", "--at", "17"], self.RESULT)
        self.assertIn("local TRACKS = nil", src)


class TestRenameIsFinal(unittest.TestCase):
    def test_track_py_rename_writes_exactly_what_it_was_given(self):
        import track  # noqa: E402

        body, _ = track.build("rename", mock.Mock(to="Pad (my own words)"))
        self.assertIn('"P_NAME", "Pad (my own words)", true)', body)
        # no display grammar anywhere near it
        self.assertNotIn("display_name", body)


if __name__ == "__main__":
    unittest.main()
