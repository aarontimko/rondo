<!-- The title is a conventional-commit subject, for example
     "fix(tracks): resolve --track before any Lua runs". Pull requests are
     squash merged, so that title becomes the commit on main. -->

## What



## Why

<!-- Link the issue. A feature needs one; a drive-by fix does not. -->

## How tested

- [ ] `python -m unittest discover -s scripts/tests`
- [ ] `python scripts/index.py --check`
- [ ] `ruff check scripts`
- [ ] By hand against a running Reaper. Say what you did, and on which Reaper and macOS version.

## Checklist

- [ ] Tests cover the behaviour change.
- [ ] Docs changed in this PR, not a follow-up.
- [ ] The title is a conventional-commit subject.
- [ ] No unrelated changes, and no audio files.
