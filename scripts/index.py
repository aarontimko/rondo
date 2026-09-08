#!/usr/bin/env python3
"""
name: index
summary: List every rondo script from its docstring header as a markdown table; --check keeps AGENTS.md honest.
needs: nothing
usage: python scripts/index.py [--check] [--write]
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
AGENTS = _cli.REPO / "AGENTS.md"
BEGIN = "<!-- rondo:tasks -->"
END = "<!-- /rondo:tasks -->"


def headers() -> list[dict]:
    rows = []
    for p in sorted(SCRIPTS.glob("*.py")):
        try:
            doc = ast.get_docstring(ast.parse(p.read_text())) or ""
        except SyntaxError:
            continue
        h = _cli.parse_header(doc)
        if "name" in h and "summary" in h:
            h["file"] = f"scripts/{p.name}"
            rows.append(h)
    rows.sort(key=lambda r: r["name"])
    return rows


def table(rows: list[dict]) -> str:
    out = ["| task | what it does | needs |", "| --- | --- | --- |"]
    for r in rows:
        out.append(f"| `{r['file']}` | {r['summary']} | {r.get('needs', '')} |")
    return "\n".join(out)


def current_block() -> str | None:
    if not AGENTS.exists():
        return None
    text = AGENTS.read_text()
    if BEGIN not in text or END not in text:
        return None
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if AGENTS.md's table is stale")
    ap.add_argument("--write", action="store_true",
                    help="rewrite the table inside AGENTS.md")
    ap.add_argument("--usage", action="store_true", help="print usage lines too")
    a = ap.parse_args(argv)

    rows = headers()
    want = table(rows)

    if a.write:
        text = AGENTS.read_text()
        if BEGIN not in text or END not in text:
            raise SystemExit(f"AGENTS.md has no {BEGIN} / {END} markers")
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        AGENTS.write_text(f"{head}{BEGIN}\n{want}\n{END}{tail}")
        print(f"updated {AGENTS}")
        return 0

    if a.check:
        have = current_block()
        if have is None:
            print(f"AGENTS.md is missing the {BEGIN} block", file=sys.stderr)
            return 1
        if have != want:
            print("AGENTS.md task table is stale. Run: python scripts/index.py --write",
                  file=sys.stderr)
            return 1
        print("AGENTS.md task table is up to date")
        return 0

    print(want)
    if a.usage:
        print()
        for r in rows:
            print(f"### {r['name']}\n{r.get('usage', '')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
