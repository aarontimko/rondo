# Operations

For whoever cuts the next release. Maintainer only; users need none of this.

## What ships

A tagged source tree. There are no binaries, packages or images. `pyproject.toml`
carries the version; `CHANGELOG.md` carries one dated section per version.

## Cutting a release

1. A PR that moves the `[Unreleased]` entries under `## <version> - YYYY-MM-DD`
   and bumps `version` in `pyproject.toml`.
2. After the merge, on `main`: `git tag -a v<version> -m "rondo <version>"` and
   push the tag. The Releases page picks the tag up; paste the changelog section
   as the release notes.
3. Check the `release` badge on the README resolves to the new tag.

## What runs on its own

| job | when | if red |
|---|---|---|
| `ci` (lint, unit 3.11, unit 3.12) | every PR and push to `main` | fix on a branch; these are the required checks and `main` cannot merge without them |
| `scans` (CodeQL, dependency review) | every PR, Mondays 06:00 UTC | read the alert under Security; dependency review only fails on a known-vulnerable or disallowed dependency in the diff |
| dependabot (GitHub Actions only) | Mondays 06:00 UTC | merge if green; an action major bump has never needed a workflow change so far |

## What must not drift

- The three required check contexts in branch protection match the `ci` job names
  exactly: `lint`, `unit (3.11)`, `unit (3.12)`. Renaming a job means updating
  the protection or `main` locks.
- `python scripts/index.py --check` keeps the task table in `AGENTS.md` honest;
  it is part of the unit suite.
- No audio is ever committed; `.gitignore` covers it and CI does not check it.

## Deferred

- macOS in the CI matrix. Trigger: a Reaper-facing regression that the mocked
  suite did not catch.
- A `--version` flag on the scripts. Trigger: the first bug report where the
  commit is ambiguous.
