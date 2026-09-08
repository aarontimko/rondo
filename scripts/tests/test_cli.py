"""Docstring headers, the script index, and the audio path guard."""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rondo import _cli  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1]


class TestHeaders(unittest.TestCase):
    def test_parse_header(self):
        h = _cli.parse_header(
            "\nname: write-notes\nsummary: Write a melody.\n"
            "needs: reaper-running\nusage: python scripts/write_notes.py\n"
        )
        self.assertEqual(h["name"], "write-notes")
        self.assertEqual(h["summary"], "Write a melody.")
        self.assertEqual(h["needs"], "reaper-running")
        self.assertTrue(h["usage"].startswith("python "))

    def test_every_script_has_a_complete_header(self):
        missing = []
        for p in sorted(SCRIPTS.glob("*.py")):
            doc = ast.get_docstring(ast.parse(p.read_text())) or ""
            h = _cli.parse_header(doc)
            for key in _cli.HEADER_KEYS:
                if key not in h:
                    missing.append(f"{p.name}: {key}")
        self.assertEqual(missing, [], f"incomplete script headers: {missing}")

    def test_names_are_unique(self):
        names = []
        for p in sorted(SCRIPTS.glob("*.py")):
            doc = ast.get_docstring(ast.parse(p.read_text())) or ""
            h = _cli.parse_header(doc)
            if "name" in h:
                names.append(h["name"])
        self.assertEqual(len(names), len(set(names)))


class TestAgentsTable(unittest.TestCase):
    def test_agents_md_table_is_current(self):
        sys.path.insert(0, str(SCRIPTS))
        import index  # noqa: E402

        self.assertEqual(
            index.current_block(), index.table(index.headers()),
            "AGENTS.md task table is stale -- run: python scripts/index.py --write",
        )


class TestPathGuard(unittest.TestCase):
    def test_outside_the_repo_is_allowed(self):
        self.assertEqual(_cli.guard_output_path("/private/tmp/x.wav").name, "x.wav")

    def test_repo_docs_is_refused(self):
        with self.assertRaises(SystemExit):
            _cli.guard_output_path(_cli.REPO / "docs" / "x.wav")

    def test_repo_root_is_refused(self):
        with self.assertRaises(SystemExit):
            _cli.guard_output_path(_cli.REPO / "x.wav")

    def test_render_dir_is_allowed(self):
        p = _cli.guard_output_path(_cli.REPO / "render" / "x.wav")
        self.assertEqual(p, (_cli.REPO / "render" / "x.wav").resolve())

    def test_render_dir_is_gitignored(self):
        self.assertTrue(_cli.is_git_ignored(_cli.REPO / "render" / "x.wav"))

    def test_samples_wavs_are_gitignored(self):
        self.assertTrue(_cli.is_git_ignored(_cli.REPO / "samples" / "kick.wav"))

    def test_source_files_are_not_gitignored(self):
        self.assertFalse(_cli.is_git_ignored(_cli.REPO / "samples" / "kit.json"))
        self.assertFalse(_cli.is_git_ignored(_cli.REPO / "AGENTS.md"))


if __name__ == "__main__":
    unittest.main()
