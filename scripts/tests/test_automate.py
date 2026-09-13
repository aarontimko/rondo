"""automate.py: dB<->gain, bar math, shape selection, name resolution, Lua.

Nothing here talks to Reaper. The FX and parameter lists are the ones a real
Surge XT reports (verified on Reaper 7.79), so the ambiguity rules are tested
against names that really are ambiguous.
"""

import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import automate  # noqa: E402
from rondo import reaper, tracks  # noqa: E402


def parse(*argv):
    return automate.build_parser().parse_args(list(argv))


# A slice of what Surge XT 1.3.4 really exposes (2858 parameters in all).
SURGE_PARAMS = [
    "M1: -", "M2: -", "Send FX 1 Return", "Global Volume", "Active Scene",
    "A Volume", "A Link Resonance", "A Osc 1 Volume", "A Noise Volume",
    "A Filter 1 Cutoff", "A Filter 1 Resonance",
    "A Filter 2 Cutoff", "A Filter 2 Resonance",
    "B Volume", "B Filter 1 Cutoff", "B Filter 1 Resonance",
]
SURGE_FX = [
    {"index": 0, "name": "VST3i: Surge XT (Surge Synth Team) (2->6ch)", "params": 2858},
    {"index": 1, "name": "VST3: ReaEQ (Cockos)", "params": 44},
]


class TestDbAndGain(unittest.TestCase):
    def test_unity(self):
        self.assertAlmostEqual(tracks.db_to_gain(0.0), 1.0)
        self.assertAlmostEqual(tracks.gain_to_db(1.0), 0.0)

    def test_minus_six_db_is_half_the_voltage(self):
        self.assertAlmostEqual(tracks.db_to_gain(-6.0), 0.5011872336)
        self.assertAlmostEqual(tracks.gain_to_db(0.5011872336), -6.0)

    def test_round_trip(self):
        for db in (-60.0, -18.0, -6.0, -0.5, 0.0, 6.0, 12.0):
            self.assertAlmostEqual(tracks.gain_to_db(tracks.db_to_gain(db)), db)

    def test_silence_is_minus_infinity(self):
        self.assertEqual(tracks.gain_to_db(0.0), float("-inf"))

    def test_db_limits_are_enforced(self):
        self.assertEqual(automate.check_db(-18.0, "--start-db"), -18.0)
        for bad in (-151.0, 25.0):
            with self.assertRaises(SystemExit):
                automate.check_db(bad, "--start-db")

    def test_fx_values_must_be_normalised(self):
        self.assertEqual(automate.check_unit(0.0, "--start"), 0.0)
        self.assertEqual(automate.check_unit(1.0, "--end"), 1.0)
        for bad in (-0.01, 1.5, 440.0):
            with self.assertRaises(SystemExit) as c:
                automate.check_unit(bad, "--start")
            self.assertIn("0..1", str(c.exception))


class TestBarMath(unittest.TestCase):
    def test_ramp_ends_where_the_next_bar_starts(self):
        self.assertEqual(reaper.bar_span_qn(1, 4), (0.0, 16.0))
        self.assertEqual(reaper.bar_span_qn(1, 8), (0.0, 32.0))

    def test_swell_lands_on_the_bar_it_is_going_into(self):
        self.assertEqual(automate.swell_bars(9, 4), (5, 8))
        self.assertEqual(reaper.bar_span_qn(*automate.swell_bars(9, 4))[1],
                         reaper.bars_to_qn(9))
        self.assertEqual(automate.swell_bars(2, 1), (1, 1))

    def test_swell_cannot_start_before_bar_one(self):
        with self.assertRaises(SystemExit) as c:
            automate.swell_bars(3, 4)
        self.assertIn("bar 1", str(c.exception))

    def test_swell_needs_at_least_one_bar(self):
        with self.assertRaises(SystemExit):
            automate.swell_bars(9, 0)

    def test_qn_to_bar_snaps_to_the_bar_line(self):
        # 57.60s at 100 bpm is exactly bar 25, but the float arrives as
        # 95.99999999999999 -- the "bars 17-23" bug.
        self.assertEqual(reaper.qn_to_bar(95.99999999999999), 25)
        self.assertEqual(reaper.qn_to_bar(96.00000000000001), 25)
        self.assertEqual(reaper.qn_to_bar(96.0), 25)

    def test_qn_to_bar_does_not_snap_real_positions(self):
        self.assertEqual(reaper.qn_to_bar(0.0), 1)
        self.assertEqual(reaper.qn_to_bar(2.0), 1)       # bar 1, beat 3
        self.assertEqual(reaper.qn_to_bar(95.5), 24)
        self.assertEqual(reaper.qn_to_bar(95.0), 24)

    def test_qn_to_bar_agrees_with_bars_to_qn(self):
        for bar in range(1, 40):
            self.assertEqual(reaper.qn_to_bar(reaper.bars_to_qn(bar)), bar)

    def test_a_region_over_bars_17_to_24_reads_back_that_way(self):
        # what status.py prints: bars <bar>-<end_bar - 1>
        start, end = reaper.bar_span_qn(17, 24)
        end_float = end * (1 - 2e-16)                # Reaper's float round trip
        self.assertEqual(reaper.qn_to_bar(start), 17)
        self.assertEqual(reaper.qn_to_bar(end_float) - 1, 24)


class TestShapes(unittest.TestCase):
    def test_named_shapes_are_reapers_numbers(self):
        self.assertEqual(automate.shape_code("linear"), 0)
        self.assertEqual(automate.shape_code("fast-start"), 3)
        self.assertEqual(automate.shape_code("fast-end"), 4)

    def test_unknown_shape_is_refused(self):
        with self.assertRaises(SystemExit) as c:
            automate.shape_code("swoopy")
        self.assertIn("linear", str(c.exception))

    def test_parser_only_accepts_the_documented_shapes(self):
        self.assertEqual(parse("volume", "--track", "Pad", "--from", "1", "--to", "4",
                               "--start-db", "-18", "--end-db", "0").shape, "linear")
        with self.assertRaises(SystemExit):
            parse("volume", "--track", "Pad", "--from", "1", "--to", "4",
                  "--start-db", "-18", "--end-db", "0", "--shape", "swoopy")


class TestPick(unittest.TestCase):
    def test_exact_name_wins(self):
        self.assertEqual(automate.pick("A Filter 1 Cutoff", SURGE_PARAMS, "--param"), 9)

    def test_case_insensitive(self):
        self.assertEqual(automate.pick("a filter 1 cutoff", SURGE_PARAMS, "--param"), 9)

    def test_index(self):
        self.assertEqual(automate.pick("9", SURGE_PARAMS, "--param"), 9)

    def test_unique_substring(self):
        self.assertEqual(automate.pick("Noise", SURGE_PARAMS, "--param"), 8)
        self.assertEqual(automate.pick("surge", [f["name"] for f in SURGE_FX], "--fx"), 0)

    def test_ambiguous_substring_is_an_error_naming_the_candidates(self):
        with self.assertRaises(SystemExit) as c:
            automate.pick("cutoff", SURGE_PARAMS, "--param")
        msg = str(c.exception)
        self.assertIn("matches 3", msg)
        self.assertIn("A Filter 1 Cutoff", msg)
        self.assertIn("B Filter 1 Cutoff", msg)

    def test_ambiguous_even_with_most_of_the_name(self):
        with self.assertRaises(SystemExit):
            automate.pick("Filter 1 Cutoff", SURGE_PARAMS, "--param")

    def test_no_match(self):
        with self.assertRaises(SystemExit) as c:
            automate.pick("wobble", SURGE_PARAMS, "--param")
        self.assertIn("matches nothing", str(c.exception))

    def test_index_out_of_range(self):
        with self.assertRaises(SystemExit) as c:
            automate.pick("999", SURGE_PARAMS, "--param")
        self.assertIn("out of range", str(c.exception))

    def test_empty_spec(self):
        with self.assertRaises(SystemExit):
            automate.pick("  ", SURGE_PARAMS, "--param")

    def test_long_candidate_lists_are_truncated(self):
        names = [f"A Filter {i} Cutoff" for i in range(20)]
        with self.assertRaises(SystemExit) as c:
            automate.pick("cutoff", names, "--param")
        self.assertIn("and 12 more", str(c.exception))


class TestParser(unittest.TestCase):
    def test_every_documented_command_parses(self):
        self.assertEqual(parse("volume", "--track", "Pad", "--from", "1", "--to", "4",
                               "--start-db", "-18", "--end-db", "0").command, "volume")
        self.assertEqual(parse("swell", "--track", "Pad", "--into", "9", "--bars", "4",
                               "--from-db", "-18", "--to-db", "0").into, 9)
        a = parse("fx-param", "--track", "Sweep", "--fx", "surge", "--param", "cutoff",
                  "--from", "1", "--to", "8", "--start", "0.2", "--end", "0.9")
        self.assertEqual((a.start, a.stop, a.start_value, a.end_value),
                         (1, 8, 0.2, 0.9))
        self.assertEqual(parse("clear", "--track", "Sweep").envelope, None)
        self.assertEqual(parse("clear", "--track", "S", "--envelope", "volume",
                               "--envelope", "cutoff").envelope, ["volume", "cutoff"])
        self.assertIsNone(parse("show", "--track", "Sweep").start)

    def test_commands_list_matches_the_parser(self):
        sub = [x for x in automate.build_parser()._actions
               if x.choices and "show" in getattr(x, "choices", {})][0]
        self.assertEqual(sorted(sub.choices), sorted(automate.COMMANDS))

    def test_track_is_always_required(self):
        for cmd in automate.COMMANDS:
            with self.assertRaises(SystemExit):
                parse(cmd)

    def test_volume_needs_both_ends(self):
        with self.assertRaises(SystemExit):
            parse("volume", "--track", "Pad", "--from", "1", "--to", "4",
                  "--start-db", "-18")

    def test_fx_param_refuses_a_value_outside_0_to_1_before_reaper_is_touched(self):
        with self.assertRaises(SystemExit) as c:
            automate.main(["fx-param", "--track", "Sweep", "--fx", "0", "--param", "1",
                           "--from", "1", "--to", "8", "--start", "200", "--end", "0.9"])
        self.assertIn("0..1", str(c.exception))

    def test_volume_refuses_an_absurd_db_before_reaper_is_touched(self):
        with self.assertRaises(SystemExit):
            automate.main(["volume", "--track", "Pad", "--from", "1", "--to", "4",
                           "--start-db", "-18", "--end-db", "400"])

    def test_swell_is_refused_before_reaper_is_touched(self):
        with self.assertRaises(SystemExit):
            automate.main(["swell", "--track", "Pad", "--into", "3", "--bars", "8",
                           "--from-db", "-18", "--to-db", "0"])

    def test_fx_param_needs_an_fx(self):
        with self.assertRaises(SystemExit) as c:
            automate.main(["fx-param", "--track", "S", "--param", "1", "--from", "1",
                           "--to", "8", "--start", "0.2", "--end", "0.9"])
        self.assertIn("--fx", str(c.exception))


class TestLua(unittest.TestCase):
    def volume_src(self, shape=0, t0=0.0, t1=16.0, v0=0.125, v1=1.0):
        return automate.draw_source(2, automate.FIND_VOLUME, "volume",
                                    "rondo: volume envelope", t0, t1, v0, v1, shape)

    def fx_src(self, fx=0, param=319):
        return automate.draw_source(
            2, automate.FIND_FX % {"fx": fx, "param": param}, "fx",
            "rondo: fx parameter envelope", 0.0, 32.0, 0.2, 0.9, 0)

    def test_no_unsubstituted_placeholders(self):
        for src in (
            self.volume_src(),
            self.fx_src(),
            automate.FX_LUA % {"index": 2},
            automate.PARAMS_LUA % {"index": 2, "fx": 0},
            automate.ENVS_LUA % {"index": 2},
            automate.CLEAR_LUA % {"index": 2, "t0": "nil", "t1": "nil", "want": "nil"},
            automate.CLEAR_LUA % {"index": 2, "t0": "0.0", "t1": "32.0", "want": "{0,1}"},
            automate.SHOW_LUA % {"index": 2, "b0": "nil", "b1": "nil", "max": 64},
            automate.SHOW_LUA % {"index": 2, "b0": "1", "b1": "8", "max": 64},
        ):
            self.assertNotRegex(src, r"%\(\w+\)")
            self.assertIn("log(jsonenc(", src)

    def test_values_go_through_the_envelope_scaling(self):
        # A volume envelope is scaling mode 1: unity gain is stored as 716.2,
        # so a raw InsertEnvelopePoint(env, t, gain) would be silently wrong.
        src = self.volume_src()
        self.assertIn("GetEnvelopeScalingMode(env)", src)
        self.assertEqual(src.count("ScaleToEnvelopeMode(mode,"), 2)
        self.assertIn("ScaleFromEnvelopeMode(mode, v)", src)

    def test_the_guard_point_holds_the_previous_value(self):
        src = self.volume_src()
        # read before the delete, written back after it, and unscaled: the held
        # value never leaves envelope units, so it cannot be double-scaled.
        self.assertLess(src.index("Envelope_Evaluate(env, tg"),
                        src.index("DeleteEnvelopePointRange"))
        self.assertIn("InsertEnvelopePoint(env, tg, hold, 0, 0, false, true)", src)
        self.assertIn("if T0_QN - GUARD > 0 then", src)

    def test_existing_points_in_the_range_are_deleted_first(self):
        src = self.volume_src()
        self.assertIn("DeleteEnvelopePointRange(env, (tg or t0) - 1e-9, t1 + 1e-9)", src)
        self.assertLess(src.index("DeleteEnvelopePointRange"),
                        src.index("InsertEnvelopePoint(env, t0"))

    def test_points_are_sorted(self):
        self.assertIn("Envelope_SortPoints(env)", self.volume_src())

    def test_shape_lands_on_the_start_point_only(self):
        src = self.volume_src(shape=3)
        self.assertIn("local V0, V1, SHAPE = 0.125, 1.0, 3", src)
        self.assertIn("ScaleToEnvelopeMode(mode, V0), SHAPE", src)
        self.assertIn("ScaleToEnvelopeMode(mode, V1), 0", src)

    def test_volume_creates_the_envelope_with_the_verified_action(self):
        src = self.volume_src()
        self.assertIn('GetTrackEnvelopeByName(tr, "Volume")', src)
        self.assertIn("Main_OnCommand(40406, 0)", src)
        # and puts the user's track selection back
        self.assertIn("SetOnlyTrackSelected(tr)", src)
        self.assertIn("SetTrackSelected(reaper.GetTrack(0, i), sel[i])", src)

    def test_fx_param_asks_for_the_envelope_to_be_created(self):
        src = self.fx_src(fx=0, param=319)
        self.assertIn("local FX, PARAM = 0, 319", src)
        self.assertIn("GetFXEnvelope(tr, FX, PARAM, true)", src)

    def test_a_db_ramp_becomes_a_linear_gain_ramp(self):
        src = automate.draw_source(0, automate.FIND_VOLUME, "volume", "u", 0.0, 16.0,
                                   tracks.db_to_gain(-18.0), tracks.db_to_gain(0.0), 0)
        v0, v1 = re.search(r"local V0, V1, SHAPE = ([\d.eE+-]+), ([\d.eE+-]+), ",
                           src).groups()
        self.assertAlmostEqual(float(v0), 0.12589254117941673)
        self.assertAlmostEqual(float(v1), 1.0)

    def test_clear_without_a_range_clears_the_whole_envelope(self):
        src = automate.CLEAR_LUA % {"index": 2, "t0": "nil", "t1": "nil", "want": "nil"}
        self.assertIn("local T0_QN, T1_QN = nil, nil", src)
        self.assertIn("local lo, hi = -1.0, 1e12", src)
        self.assertIn("DeleteEnvelopePointRange(env, lo, hi)", src)
        self.assertIn("sweep(env, lo, hi)", src)

    def test_both_templates_sweep_the_point_range_delete_leaves_at_time_zero(self):
        # Reaper leaves a point at exactly time 0 behind; without the sweep a
        # full clear never reaches zero points and a redraw over bar 1 stacks
        # two points on the same time.
        for src in (automate.CLEAR_LUA, automate.DRAW_LUA):
            self.assertIn("DeleteEnvelopePointEx(env, -1, p)", src)
        draw = automate.draw_source(0, automate.FIND_VOLUME, "volume", "u",
                                    0.0, 16.0, 0.5, 1.0, 0)
        delete = draw.index("DeleteEnvelopePointRange(env,")
        self.assertLess(delete, draw.index("sweep(env, (tg or t0)"))
        self.assertLess(draw.index("sweep(env, (tg or t0)"),
                        draw.index("InsertEnvelopePoint(env, t0,"))

    def test_show_uses_the_snapping_bar_helper(self):
        src = automate.SHOW_LUA % {"index": 2, "b0": "nil", "b1": "nil", "max": 64}
        self.assertIn("qn_to_bar(last_qn)", src)
        self.assertIn("bars_to_qn(b)", src)


class TestShowText(unittest.TestCase):
    ROW = {
        "track": "Sweep", "index": 5, "from_bar": 1, "to_bar": 2,
        "envelopes": [
            {"index": 0, "name": "Volume", "kind": "VOLENV2", "points": 4,
             "scaling_mode": 1, "active": True, "visible": True, "armed": True,
             "boundaries": [
                 {"bar": 1, "time": 0.0, "value": 0.125, "formatted": "-18.06dB"},
                 {"bar": 2, "time": 2.4, "value": 0.5, "formatted": "-6.02dB"},
                 {"bar": 3, "time": 4.8, "value": 1.0, "formatted": "0.00dB"},
             ]},
            {"index": 1, "name": "A Filter 1 Cutoff / Surge XT / Filter 1",
             "kind": "PARMENV", "points": 0, "scaling_mode": 0, "active": True,
             "visible": False, "armed": False,
             "boundaries": [{"bar": 1, "time": 0.0, "value": 0.5, "formatted": ""}]},
        ],
    }

    def test_renders_every_envelope_and_boundary(self):
        text = automate.show_text(self.ROW)
        self.assertIn('track 5 "Sweep": 2 envelope(s), bars 1-2', text)
        self.assertIn("Volume  [VOLENV2]  4 point(s)  (active, visible, armed)", text)
        self.assertIn("-18.06dB", text)
        self.assertIn("bar 3", text)
        self.assertIn("A Filter 1 Cutoff", text)
        self.assertIn("0 point(s)", text)
        self.assertIn("hidden", text)
        self.assertIn("0.500", text)      # no formatted string: the raw value

    def test_a_bypassed_envelope_says_so(self):
        row = dict(self.ROW)
        row["envelopes"] = [dict(self.ROW["envelopes"][0], active=False)]
        self.assertIn("BYPASSED", automate.show_text(row))

    def test_a_track_with_no_envelopes(self):
        row = dict(self.ROW, envelopes=[])
        self.assertIn("no envelopes", automate.show_text(row))


if __name__ == "__main__":
    unittest.main()
