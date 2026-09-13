"""install_samples with the network mocked: statuses, checksums, the gitignore guard."""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install_samples  # noqa: E402

BLOB = b"RIFF fake wav bytes"


def manifest(tmp: Path, **overrides) -> Path:
    entry = {"name": "kick", "file": "36_kick.wav", "path": "Kicks/kick one.wav",
             "sha256": hashlib.sha256(BLOB).hexdigest(), "bytes": len(BLOB)}
    entry.update(overrides)
    data = {
        "source": {"repo": "https://example.com/repo", "commit": "c" * 40,
                   "license": "CC0-1.0",
                   "url_template": "https://example.com/{commit}/{path}"},
        "samples": {"36": entry},
    }
    p = tmp / "kit.json"
    p.write_text(json.dumps(data))
    return p


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = install_samples.main(argv)
    return rc, out.getvalue()


class TestInstallSamples(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ignored = mock.patch.object(install_samples._cli, "is_git_ignored",
                                         return_value=True)
        self.ignored.start()
        self.addCleanup(self.ignored.stop)

    def fake_download(self, blob=BLOB):
        calls = []

        def download(url, dest, timeout=0):
            calls.append(url)
            dest.write_bytes(blob)
            return blob
        return calls, mock.patch.object(install_samples, "download", download)

    def test_downloads_to_the_manifest_dir_with_quoted_url(self):
        m = manifest(self.tmp)
        calls, patch = self.fake_download()
        with patch:
            rc, out = run(["--manifest", str(m), "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["https://example.com/" + "c" * 40 + "/Kicks/kick%20one.wav"])
        self.assertEqual((self.tmp / "36_kick.wav").read_bytes(), BLOB)
        results = json.loads(out[out.index("["):])
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["note"], 36)

    def test_existing_file_is_kept_unless_forced(self):
        m = manifest(self.tmp)
        (self.tmp / "36_kick.wav").write_bytes(b"old")
        calls, patch = self.fake_download()
        with patch:
            rc, out = run(["--manifest", str(m)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("have", out)
        with patch:
            rc, out = run(["--manifest", str(m), "--force"])
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.tmp / "36_kick.wav").read_bytes(), BLOB)

    def test_sha_mismatch_fails(self):
        m = manifest(self.tmp, sha256="0" * 64)
        _, patch = self.fake_download()
        with patch:
            rc, out = run(["--manifest", str(m)])
        self.assertEqual(rc, 1)
        self.assertIn("SHA256 MISMATCH", out)
        self.assertFalse((self.tmp / "36_kick.wav").exists())
        self.assertIn("1 PROBLEM", out)

    def test_size_mismatch_fails(self):
        m = manifest(self.tmp, bytes=1)
        _, patch = self.fake_download()
        with patch:
            rc, out = run(["--manifest", str(m)])
        self.assertEqual(rc, 1)
        self.assertIn("SIZE MISMATCH", out)

    def test_refuses_when_wavs_are_not_gitignored(self):
        m = manifest(self.tmp)
        with mock.patch.object(install_samples._cli, "is_git_ignored", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                run(["--manifest", str(m)])
        self.assertIn("not gitignored", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
