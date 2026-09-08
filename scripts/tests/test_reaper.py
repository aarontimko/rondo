"""Pure-Python parts of rondo.reaper: bar math and Lua literal generation.

Nothing here talks to Reaper.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rondo import reaper  # noqa: E402


class TestBarMath(unittest.TestCase):
    def test_bar_one_is_zero(self):
        self.assertEqual(reaper.bars_to_qn(1), 0.0)
        self.assertEqual(reaper.bars_to_qn(1, 1), 0.0)

    def test_bars_and_beats(self):
        self.assertEqual(reaper.bars_to_qn(2), 4.0)
        self.assertEqual(reaper.bars_to_qn(9), 32.0)
        self.assertEqual(reaper.bars_to_qn(3, 3), 10.0)
        self.assertEqual(reaper.bars_to_qn(1, 2.5), 1.5)

    def test_inverse(self):
        for bar in range(1, 20):
            for beat in (1.0, 2.0, 3.5):
                qn = reaper.bars_to_qn(bar, beat)
                self.assertEqual(reaper.qn_to_bars(qn), (bar, beat))


class TestLuaLiterals(unittest.TestCase):
    def test_strings_are_escaped(self):
        self.assertEqual(reaper.lua_str("hi"), '"hi"')
        self.assertEqual(reaper.lua_str('a"b'), '"a\\"b"')
        self.assertEqual(reaper.lua_str("a\\b"), '"a\\\\b"')
        self.assertEqual(reaper.lua_str("a\nb"), '"a\\nb"')

    def test_paths_with_spaces(self):
        p = "/Library/Application Support/Surge XT/patches_factory/Pads/Bell Pad.fxp"
        self.assertEqual(reaper.lua_str(p), f'"{p}"')

    def test_non_ascii_passes_through(self):
        self.assertEqual(reaper.lua_str("café"), '"café"')

    def test_scalars(self):
        self.assertEqual(reaper.lua_value(None), "nil")
        self.assertEqual(reaper.lua_value(True), "true")
        self.assertEqual(reaper.lua_value(False), "false")
        self.assertEqual(reaper.lua_value(3), "3")

    def test_lists_and_tables(self):
        self.assertEqual(reaper.lua_value([1, 2]), "{1,2}")
        self.assertEqual(reaper.lua_value([[0, 1, 67]]), "{{0,1,67}}")
        self.assertEqual(reaper.lua_value({"a": 1}), '{["a"]=1}')

    def test_unsupported_type(self):
        with self.assertRaises(TypeError):
            reaper.lua_value(object())


class TestRunnerTemplate(unittest.TestCase):
    def test_template_formats_without_brace_errors(self):
        src = reaper._RUNNER.format(
            out="/tmp/o.txt", body="/tmp/b.lua",
            json=reaper.LUA_JSON, prelude=reaper.LUA_PRELUDE,
        )
        self.assertIn("/tmp/o.txt", src)
        self.assertIn("@@RONDO_DONE", src)
        self.assertIn("function jsonenc", src)
        self.assertIn("function find_track", src)
        self.assertIn("local t = {}", src)


if __name__ == "__main__":
    unittest.main()
