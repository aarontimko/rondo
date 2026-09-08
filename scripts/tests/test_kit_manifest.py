"""The drum-kit manifest: shape, GM note numbers, and URL construction."""

import json
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rondo import _cli  # noqa: E402

MANIFEST = _cli.REPO / "samples" / "kit.json"


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(MANIFEST.read_text())

    def test_source_is_pinned_and_cc0(self):
        src = self.data["source"]
        self.assertEqual(src["license"], "CC0-1.0")
        self.assertEqual(len(src["commit"]), 40)
        self.assertIn("{commit}", src["url_template"])
        self.assertIn("{path}", src["url_template"])

    def test_entries_are_complete(self):
        for note, e in self.data["samples"].items():
            self.assertTrue(note.isdigit(), note)
            self.assertTrue(0 <= int(note) <= 127)
            for key in ("name", "file", "path", "sha256", "bytes"):
                self.assertIn(key, e, f"note {note} missing {key}")
            self.assertEqual(len(e["sha256"]), 64, f"note {note}")
            self.assertGreater(e["bytes"], 0)
            self.assertTrue(e["file"].endswith(".wav"))
            self.assertNotIn("/", e["file"], "files land directly in samples/")

    def test_gm_notes(self):
        # The GM drum map: these are the notes build_kit wires up.
        self.assertEqual(
            sorted(int(n) for n in self.data["samples"]), [36, 38, 39, 42, 46, 49]
        )

    def test_urls_percent_encode_spaces_and_commas(self):
        src = self.data["source"]
        e = self.data["samples"]["38"]
        url = src["url_template"].format(
            commit=src["commit"], path=urllib.parse.quote(e["path"])
        )
        self.assertNotIn(" ", url)
        self.assertIn("%20", url)
        self.assertTrue(url.startswith("https://raw.githubusercontent.com/sgossner/VCSL/"))

    def test_build_kit_resolves_paths_against_the_manifest_dir(self):
        sys.path.insert(0, str(_cli.REPO / "scripts"))
        import build_kit  # noqa: E402

        kit = build_kit.load_manifest(MANIFEST)
        self.assertEqual([n for n, _ in kit], [36, 38, 39, 42, 46, 49])
        for _, f in kit:
            self.assertTrue(Path(f).is_absolute())
            self.assertEqual(Path(f).parent, MANIFEST.parent)


if __name__ == "__main__":
    unittest.main()
