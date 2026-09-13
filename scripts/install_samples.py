#!/usr/bin/env python3
"""
name: install-samples
summary: Download the drum one-shots named in samples/kit.json (CC0, from VCSL) into samples/.
needs: network
usage: python scripts/install_samples.py [--manifest samples/kit.json] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli  # noqa: E402

DEFAULT_MANIFEST = _cli.REPO / "samples" / "kit.json"

UA = "rondo/0.1 (+https://github.com/aarontimko/rondo) python-urllib"


def download(url: str, dest: Path, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    manifest = Path(a.manifest).expanduser().resolve()
    data = json.loads(manifest.read_text())
    src = data["source"]
    out_dir = manifest.parent

    if not _cli.is_git_ignored(out_dir / "x.wav"):
        raise SystemExit(
            f"refusing to download: {out_dir}/*.wav is not gitignored. "
            "Add '*.wav' to .gitignore first -- audio is never committed."
        )

    results = []
    for note, entry in sorted(data["samples"].items(), key=lambda kv: int(kv[0])):
        dest = out_dir / entry["file"]
        url = src["url_template"].format(
            commit=src["commit"], path=urllib.parse.quote(entry["path"])
        )
        if dest.exists() and not a.force:
            results.append({"note": int(note), "file": str(dest),
                            "status": "present", "bytes": dest.stat().st_size})
            print(f"  have  {dest.name:<14} {dest.stat().st_size:>9} bytes")
            continue
        blob = download(url, dest)
        status = "ok"
        want = entry.get("bytes")
        if want and len(blob) != want:
            status = f"SIZE MISMATCH (expected {want})"
        want_sha = entry.get("sha256")
        if want_sha and hashlib.sha256(blob).hexdigest() != want_sha:
            status = "SHA256 MISMATCH"
        if status != "ok":
            dest.unlink(missing_ok=True)  # never leave a wrong file for build_kit to load
        results.append({"note": int(note), "file": str(dest), "status": status,
                        "bytes": len(blob),
                        "sha256": hashlib.sha256(blob).hexdigest()})
        print(f"  got   {dest.name:<14} {len(blob):>9} bytes  {status}")

    bad = [r for r in results if r["status"] not in ("ok", "present")]
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"{len(results)} sample(s) in {out_dir}"
              + (f"; {len(bad)} PROBLEM(S)" if bad else ""))
        print(f"license: {src['license']} ({src['repo']})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
