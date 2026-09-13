"""Every script answers --help with exit 0 and without touching Reaper."""

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


class TestHelp(unittest.TestCase):
    def test_every_script_prints_help(self):
        failures = []
        for p in sorted(SCRIPTS.glob("*.py")):
            r = subprocess.run([sys.executable, str(p), "--help"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or "usage:" not in r.stdout:
                failures.append(f"{p.name}: rc={r.returncode} {r.stderr.strip()[:200]}")
        self.assertEqual(failures, [])

    def test_index_check_passes(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "index.py"), "--check"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
