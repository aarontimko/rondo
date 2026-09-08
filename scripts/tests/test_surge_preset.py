"""The .fxp -> .vstpreset wrapper, checked against the byte layout, and the
patch finder (skipped when Surge XT is not installed)."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rondo import surge_preset as sp  # noqa: E402


def fake_fxp(payload: bytes) -> bytes:
    """A minimal .fxp: 60-byte VST2 header, then Surge's 'sub3' chunk."""
    return b"CcnK" + b"\x00" * 56 + b"sub3" + payload


class TestComponentState(unittest.TestCase):
    def test_strips_header_and_appends_juce_trailer(self):
        comp = sp.fxp_to_component_state(fake_fxp(b"hello"))
        self.assertEqual(comp, b"sub3hello" + b"\x00" * 16 + b"JUCEPrivateData")

    def test_rejects_non_fxp(self):
        with self.assertRaises(sp.SurgePresetError):
            sp.fxp_to_component_state(b"nope" + b"\x00" * 100)

    def test_rejects_non_surge_payload(self):
        bad = b"CcnK" + b"\x00" * 56 + b"XXXX" + b"data"
        with self.assertRaises(sp.SurgePresetError):
            sp.fxp_to_component_state(bad)

    def test_rejects_truncated(self):
        with self.assertRaises(sp.SurgePresetError):
            sp.fxp_to_component_state(b"CcnK" + b"\x00" * 20)


class TestBuild(unittest.TestCase):
    def test_layout(self):
        with tempfile.TemporaryDirectory() as d:
            fxp = Path(d) / "x.fxp"
            fxp.write_bytes(fake_fxp(b"P" * 100))
            out = sp.build_vstpreset(fxp, Path(d) / "x.vstpreset")
            blob = out.read_bytes()

        self.assertEqual(blob[:4], b"VST3")
        self.assertEqual(struct.unpack("<i", blob[4:8])[0], 1)
        self.assertEqual(blob[8:40], sp.CLASS_ID)
        list_offset = struct.unpack("<q", blob[40:48])[0]
        self.assertEqual(blob[list_offset:list_offset + 4], b"List")
        self.assertEqual(struct.unpack("<i", blob[list_offset + 4:list_offset + 8])[0], 1)
        cid = blob[list_offset + 8:list_offset + 12]
        off, size = struct.unpack("<qq", blob[list_offset + 12:list_offset + 28])
        self.assertEqual(cid, b"Comp")
        self.assertEqual(off, 48)
        self.assertEqual(size, list_offset - 48)
        self.assertEqual(blob[off:off + size][:4], b"sub3")
        self.assertTrue(blob[off:off + size].endswith(b"JUCEPrivateData"))
        self.assertEqual(len(blob), list_offset + 28)


@unittest.skipUnless(
    any((d / "patches_factory").is_dir() for d in sp.PATCH_DIRS),
    "Surge XT is not installed",
)
class TestFinder(unittest.TestCase):
    def test_finds_a_known_factory_patch(self):
        hits = sp.find_patches("Bell Pad")
        self.assertTrue(hits)
        self.assertEqual(hits[0].stem, "Bell Pad")

    def test_search_is_case_insensitive_and_matches_the_category(self):
        self.assertTrue(sp.find_patches("pads/bell"))

    def test_missing_patch_raises(self):
        with self.assertRaises(sp.SurgePresetError):
            sp.resolve_patch("no such patch at all zzzz")

    def test_real_patch_builds_a_well_formed_preset(self):
        fxp = sp.resolve_patch("Bell Pad")
        with tempfile.TemporaryDirectory() as d:
            out = sp.build_vstpreset(fxp, Path(d) / "bp.vstpreset")
            blob = out.read_bytes()
        # 60 header bytes dropped, 31 trailer bytes added, 48 header + 28 list.
        self.assertEqual(len(blob), fxp.stat().st_size - 60 + 31 + 48 + 28)


if __name__ == "__main__":
    unittest.main()
