# Contributing to rondo

rondo has one maintainer, [@aarontimko](https://github.com/aarontimko). The
project is at 0.x, so the surface still moves between minor versions. Bug
reports and small fixes are welcome now; a feature needs an issue first so we
can agree on the shape before you write it.

## Setup

You need three things:

- Python 3.11 or newer. The scripts are stdlib only, so there is no environment
  to build and nothing to install for the tests.
- [`ruff`](https://docs.astral.sh/ruff/) for the lint check. `uvx ruff` works
  without installing it.
- `git`.

```sh
git clone https://github.com/aarontimko/rondo.git
cd rondo
python -m unittest discover -s scripts/tests
```

Only `scripts/hum_to_grid.py` needs third-party packages, and only if you touch
it: `uv pip install -e '.[transcribe]'`.

Reaper is not needed for the tests. Every test mocks the bridge, so the suite
runs on a machine with no DAW on it. A change to how a script talks to Reaper
still wants a run against a real Reaper on a Mac before you open the PR; say in
the PR what you ran.

## Tests

The three checks `.github/workflows/ci.yml` runs, as commands:

| command | what it runs |
|---|---|
| `python -m unittest discover -s scripts/tests` | the whole suite, on Python 3.11 and 3.12 in CI |
| `python scripts/index.py --check` | fails if the task table in `AGENTS.md` is stale against the script docstrings |
| `ruff check scripts` | the linter over every script and the `rondo` package |

Run all three before opening a PR. All three must be green; the PR template
asks for them.

If your change adds or renames a script, regenerate the table with
`python scripts/index.py --write` and commit it, or the check fails.

## Hooks

There are none. rondo ships no git hooks and no task runner, so nothing runs on
your machine unless you run it. CI is the check: the three commands above, on
every pull request.

## Commits

[Conventional Commits](https://www.conventionalcommits.org): `feat:`, `fix:`,
`docs:`, `test:`, `ci:`, `refactor:`, `chore:`. Add a scope in parentheses when
it helps. The subject line says what changed and why; use the body when the why
is not obvious from the diff.

```
fix(tracks): resolve --track in Python, not Lua's exact-only find_track

Lua matched the full track name, so --track Pad missed
"Pad (Surge: MKS-70 Warm Pad)" and created a second track.
```

## Pull requests

- Small. One thing per PR. Two unrelated fixes are two PRs.
- Behaviour changes come with tests.
- Docs change in the same PR as the code, not a follow-up.
- CI green.
- One maintainer review before merge.
- The PR title is a conventional-commit subject. Pull requests are squash
  merged, so that title becomes the commit on `main` and the history stays
  linear.

Drive-by fixes (a typo, a broken link, a dead comment) need no issue: just open
the PR. Features do: open an issue describing the problem first.

Never commit audio. No `.wav`, no `.aif`, no `.mp3`, no renders and no samples;
`.gitignore` covers them and new exceptions are not accepted. Renders belong in
`/private/tmp`, or in the gitignored `render/` directory.

## Triage

New issues get `needs-triage`. Within a week each one is labelled, asked for a
reproduction, or closed with a reason. A closed issue always says why it was
closed.

Never report a security problem in a public issue. See [SECURITY.md](SECURITY.md).

## Where things live

- [`AGENTS.md`](AGENTS.md): the golden path, the task table, and the rules about
  track names and project tabs. Read it before your first change.
- [`docs/reaper-notes.md`](docs/reaper-notes.md): the verified Reaper API facts.
  Trust it over your recollection, and add to it when you verify something new.
- [`docs/grid-format.md`](docs/grid-format.md): the note format specification.
  Changing it changes both `write_notes.py` and `hum_to_grid.py`.
- [`docs/tutorial.md`](docs/tutorial.md): the user-facing walk through a jam.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
