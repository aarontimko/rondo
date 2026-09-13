# rondo

A small toolkit that lets an LLM drive the Reaper DAW. One script per task,
stdlib only, no server to run. You talk; rondo writes notes, loads
instruments, copies sections and renders a wav you can listen to.

## Golden path

Reaper must be running with a project open, and the bridge loaded once per
Reaper launch:

```bash
open -a REAPER                                                    # if not running
open -a REAPER ~/"Library/Application Support/REAPER/Scripts/reaper_mcp_bridge.lua"
python scripts/status.py                                          # is it awake?
```

`status.py` is what you run first, every time. It prints the tempo, every
track with its FX and item count, the regions, and where the cursor is. Then
the loop is always the same:

```bash
python scripts/status.py                                    # look
python scripts/add_instrument.py --track Pad --instrument surge --patch "Bell Pad"
python scripts/write_notes.py --track Pad --bar 1 --grid pad.txt --replace
python scripts/render.py --from 1 --to 8 --out /private/tmp/take.wav   # listen
```

`track.py` and `project.py` are the small moves in between: `track.py show
--track Pad`, `track.py mute --track 3`, `project.py region --name A --from 1
--to 8`. **Every** `--track` / `--tracks` flag resolves the same way, in
Python, before any Lua runs: case-insensitive **exact name**, then exact
**role** (the name with its trailing ` (...)` suffix removed, so `--track Pad`
finds `Pad (Surge: MKS-70 Warm Pad)`), then **0-based index**, then **prefix**.
An ambiguous spec is an error listing the candidates, never a guess. The
scripts that *create* the track when they cannot find it --
`add_instrument.py`, `build_kit.py`, `record.py` -- stop before the prefix
step, so `--track Lead` creates `Lead` instead of landing on `Lead Harmony`.
The rule is `rondo.tracks.resolve`, documented in that module; `--project` and
`automate.py`'s `--fx` / `--param` are the same function with different knobs.

Track names teach the instrument. `add_instrument.py` renames the track it
loads onto to `Role (Instrument: Patch)` -- `Pad (Surge: MKS-70 Warm Pad)`,
`Bass (Dexed: E BASS 1)`, `Keys (GM Piano)` -- and `build_kit.py` names its
track `Drums (RS5K kit: <kit>)`, so Reaper's track list says what is making
each sound. Loading a new patch replaces the suffix rather than stacking
another one on; the role in front of it is never touched. Pass `--keep-name` to
skip the rename, and use `track.py rename --to` when you want a name rondo will
not touch -- a human rename is final.

Every script takes `--json` where machine output helps, and `--help` always
tells the truth. Scripts act on the **active project tab**, with one
exception: `project.py save --as PATH --project TAB` names the tab (file name
with or without `.rpp`, or the index from `project.py tabs`) and saves it by
pointer, so "save the prototype file as take2" is `project.py tabs` to see
what is open, then `project.py save --as ~/songs/take2.rpp --project
prototype-1`, whatever tab the human is looking at.

Unit tests: `python -m unittest discover -s scripts/tests`. Lint: `uvx ruff check scripts`.
CI runs both on Ubuntu plus `python scripts/index.py --check`.

## Tasks

<!-- rondo:tasks -->
| task | what it does | needs |
| --- | --- | --- |
| `scripts/add_instrument.py` | Find or create a track and load Surge XT, Dexed or the Apple GM piano on it, optionally with a patch. | reaper-running |
| `scripts/automate.py` | Draw a volume or FX-parameter automation envelope over a bar range, read the envelopes back, or clear them. | reaper-running |
| `scripts/build_kit.py` | Build a ReaSamplOmatic5000 drum kit on one track from a note-to-wav manifest. | reaper-running, samples |
| `scripts/copy_section.py` | Copy bars X..Y to bar Z on every track (or named tracks) and optionally name the result as a region. | reaper-running |
| `scripts/hum_to_grid.py` | Transcribe a hummed or sung wav into rondo grid text you can feed to write-notes. | librosa |
| `scripts/index.py` | List every rondo script from its docstring header as a markdown table; --check keeps AGENTS.md honest. | nothing |
| `scripts/install_samples.py` | Download the drum one-shots named in samples/kit.json (CC0, from VCSL) into samples/. | network |
| `scripts/project.py` | Project-wide moves: save, move the edit cursor, toggle the metronome, name a region or marker, set the tempo, list tabs. | reaper-running |
| `scripts/record.py` | Arm a track for the mic or the virtual keyboard, set monitoring and the metronome, or disarm everything. | reaper-running |
| `scripts/render.py` | Render a bar range or a named region to a 44.1k stereo wav, synchronously. | reaper-running |
| `scripts/run_lua.py` | Run a ReaScript file or inline snippet inside the running Reaper and print its output. | reaper-running |
| `scripts/status.py` | Read-only summary of the open Reaper project: tempo, tracks, FX, regions, cursor. | reaper-running |
| `scripts/track.py` | One track at a time: mute, solo, arm, rename, add, delete, clear items, set the input, set the volume, or show its full state. | reaper-running |
| `scripts/write_notes.py` | Write a melody from a grid text file (or JSON notes) onto a track at a bar. | reaper-running |
<!-- /rondo:tasks -->

Regenerate this table with `python scripts/index.py --write`;
`python scripts/index.py --check` fails if it is stale.

### Verified Reaper API facts

`docs/reaper-notes.md` is the ground truth for how this machine's Reaper
actually behaves: the exact `TrackFX_AddByName` strings, the `.fxp` ->
`.vstpreset` byte layout that makes Surge patches loadable, the RS5K parameter
map, the synchronous render action, and the traps (`"AU: Surge XT"` loads the
wrong plugin; `Main_SaveProjectEx` without option `&8` does not clear the dirty
flag). Read it
before writing any new ReaScript, and trust it over your own recollection.

### The grid note format

`docs/grid-format.md` specifies the bar-per-line melody notation that
`write_notes.py` and `hum_to_grid.py` speak, with worked examples. Read it
when you need to write, correct, or generate a melody.

## Standing rules

* **Never commit audio.** No `.wav`, `.aif`, `.mp3`, no renders, no samples.
  `.gitignore` covers them; do not add exceptions. Renders go to
  `/private/tmp` or `render/` and nowhere else in the repo.
* **Never `git add -A` or `git add .`.** Stage explicit paths.
* **Samples arrive only through `samples/kit.json`** and
  `scripts/install_samples.py` (CC0 sources, pinned commit, size + sha256
  checked). Do not drop audio into the repo by hand.
* **Leave `~/Library/Application Support/REAPER` alone** apart from reading
  `reaper.ini` / the plugin caches and using the bridge mailbox directory.
* **Never work in the user's open project when testing.** Open a new tab
  (`Main_OnCommand(40859, 0)`), do everything there, then
  `Main_SaveProjectEx(0, "/private/tmp/<temp>.rpp", 8)` (the tab adopts the
  file and is clean; the bridge survives) and `Main_OnCommand(40860, 0)` to
  close it without a save prompt. Check `status.py`'s tab list before and
  after. If something goes wrong, stop and say so rather than improvising in
  the user's project.
* **Never close a tab without checking which one is active.** `40860` closes
  whatever tab is ACTIVE at that instant, not the tab your script opened, and
  the active tab changes under you -- the user clicks, another agent opens a
  tab. So: record the active project with `EnumProjects(-1, "")` before you
  open your scratch tab; give the scratch tab a filename of its own
  (`Main_SaveProjectEx` then `Main_openProject("noprompt:...")`) so you can
  recognise it by exact path; before closing, `SelectProjectInstance(<your
  scratch project>)` and verify with `EnumProjects(-1, "")` that the active
  tab's path really is your scratch file -- if it is not, do NOT close, stop
  and report; after closing, `SelectProjectInstance(<the project that was
  active>)` and verify by name. Same rule for writing: select your tab,
  write, and hand the user's tab back inside ONE script, so no tab switch can
  land between the select and the write.
* **No dialogs.** Renders stay on action `42230` with `RENDER_ADDTOPROJ 0`.
* **rondo never moves the transport.** It sets a track up; the human presses
  record and play.

## Assumptions worth knowing

* **4/4.** Every bar-to-quarter-note conversion assumes four beats to the bar.
  `status.py` reports the project's real time signature, so you can notice
  when the assumption is wrong.
* **Bars are 1-based and inclusive.** `--from 1 --to 8` is the first eight
  bars. So is a region reported as `bars 1-8`.
* Only `scripts/hum_to_grid.py` needs third-party packages
  (`pip install -e '.[transcribe]'`). Everything else is stdlib.
