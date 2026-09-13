# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
rondo follows [semantic versioning](https://semver.org/) from its first release.

## Unreleased

Everything below is what `main` carries today. There is no release yet.

### Talking to Reaper

`rondo.reaper` reaches the running Reaper two ways: the file mailbox for the
liveness check, and generated ReaScript through `open -a REAPER` for everything
else, with a runner that turns a Lua error into a Python exception instead of a
hang. `run_lua.py` exposes that directly, and deliberately skips the liveness
check so it still works when the bridge is down. `status.py` is the read-only
summary every session starts with: tempo, tracks, FX, items, regions, cursor.

### Writing music

`write_notes.py` writes a melody onto a track from the bar-per-line grid format
or from JSON notes, and `rondo.grid` is the parser both it and `hum_to_grid.py`
speak. `add_instrument.py` finds or creates a track and loads Surge XT, Dexed or
the Apple GM piano on it, converting a Surge factory `.fxp` into a loadable
`.vstpreset` when you name a patch. `build_kit.py` builds a drum kit as one
ReaSamplOmatic5000 per note on a single track, from the manifest
`install_samples.py` fills with CC0 VCSL one-shots.

### Arranging and shaping

`copy_section.py` copies a bar range to a new bar across tracks and can name the
result as a region. `automate.py` draws volume and FX-parameter envelopes over a
bar range, reads them back, and clears them. `project.py` carries the
project-wide moves: save, save-as by tab pointer, tempo, regions and markers,
the metronome, the edit cursor, and the tab list. `track.py` carries the
one-track moves: mute, solo, arm, rename, add, delete, clear, input, volume and
show.

### Playing and listening

`record.py` arms a track for the mic or the virtual keyboard and sets monitoring
and the click; rondo never touches the transport. `render.py` renders a bar
range or a named region to a 44.1k stereo wav synchronously, with no dialog.
`hum_to_grid.py` transcribes a hummed or sung wav back into grid text.

### Names and resolution

Every `--track` flag resolves in Python before any Lua runs: exact name, exact
role, index, then prefix, with an ambiguous spec an error that lists the
candidates. Scripts that create a track when they cannot find one stop before
the prefix step. `add_instrument.py` and `build_kit.py` rename the track they
load onto to `Role (Instrument: Patch)`, replacing the suffix rather than
stacking, and a human rename through `track.py rename` is final.

### Docs and checks

`AGENTS.md` is the golden path and the task table, which `index.py --check`
keeps honest against the script docstrings. `docs/reaper-notes.md` records the
verified Reaper behaviour and the traps, `docs/grid-format.md` specifies the
note format, and `docs/tutorial.md` walks one jam from a recorded take to
versioned clones. CI runs the unit suite on Python 3.11 and 3.12, the index
check, and `ruff`.
