# Reaper notes: what is actually true

Everything here was verified on **Reaper 7.79, macOS arm64**, with Surge XT
1.3.4 and Dexed 1.0.1 installed via Homebrew casks. Where a claim is
second-hand or untested it says so. Prefer this file over anything you
remember about the ReaScript API.

## Two ways to reach Reaper

**Arbitrary ReaScript.** `open -a REAPER /abs/path/script.lua` runs the file
in the running instance. The script writes its own output file; the caller
polls for it. This is the workhorse. `rondo.reaper.run_lua` wraps it: it
generates a runner that provides `log()`, `jsonenc()` and a `pcall(dofile,
body)`, so a syntax error in your Lua comes back as an error instead of a
hang.

**The file mailbox ("bridge").** TwelveTake's `reaper_mcp_bridge.lua`, started
with `open -a REAPER "<Scripts>/reaper_mcp_bridge.lua"` (no action-list step
needed). Write `request_N.json`, read `response_N.json` in
`~/Library/Application Support/REAPER/Scripts/mcp_bridge_data`. Round trip is
22-25 ms. Limits: positional args only; the **track index**, not a pointer, is
the first argument for `TrackFX_*`; and any function with a pointer/out
argument (`GetTrackStateChunk`, `TrackFX_SetNamedConfigParm`,
`CountTrackMediaItems`, ...) does **not** work through it. rondo uses the
bridge only for the liveness check.

**Loading a project kills the bridge.** `Main_openProject` (any form,
`noprompt:` included) stops every deferred script, and the bridge is one, so
after it every `call()` times out. Restart it with ReaScript, which still
works:

```bash
python scripts/run_lua.py -e 'dofile(reaper.GetResourcePath() .. "/Scripts/reaper_mcp_bridge.lua")'
```

Re-running `open -a REAPER "<Scripts>/reaper_mcp_bridge.lua"` did **not**
bring it back in that state. `scripts/run_lua.py` deliberately skips the
`require_reaper()` liveness check for exactly this reason -- it is the tool you
need when the bridge is down.

The request file must be written **atomically**: the bridge polls fast enough
to read a half-written `request_N.json` and answer "malformed JSON". `call()`
writes to a dot-file in the same directory and `os.link()`s it into the slot,
which also gives it free `O_EXCL` slot allocation.

## Projects and tabs

* A `ReaProject` argument of `0` means **the active project tab**, not tab 0.
  Tab pointers come from `EnumProjects(index)`; `EnumProjects(-1)` is the
  active one. Every rondo script operates on the active tab.
* `Main_SaveProjectEx(0, path, 0)` writes a **copy**. It does not give the tab
  a filename and does not clear the dirty flag, so `GetSetProjectInfo_String(_,
  "PROJECT_NAME")` and `EnumProjects`' path stay empty and closing the tab
  still prompts.
* To close a scratch tab silently: `Main_openProject("noprompt:" .. path)`
  (adopts the file, clears dirty, stays in the same tab), then
  `Main_OnCommand(40860, 0)` (close current project tab). `40859` opens a new
  tab. This is the safe test harness: new tab, work, reopen-noprompt, close.
* `GetCursorPosition`, `GetPlayState` and `EnumProjectMarkers` are
  active-project-only. The `...Ex` / `...3` variants take a project.
* `Main_openProject("noprompt:<path>")` on a path that does **not** exist
  silently creates a blank project in the current tab instead of failing. Check
  the file exists first; getting this wrong once cost a scratch tab and the
  bridge.
* Once a tab has a filename, `Main_SaveProject(0, false)` does clear the dirty
  flag (unlike `Main_SaveProjectEx` to a new path). That is the other way to
  close a scratch tab without a prompt.
* On a tab with **no** filename, `Main_SaveProject` would open a Save As
  dialog, so `project.py save` refuses and asks for `--as PATH` instead. The
  full no-dialog recipe, verified end to end: `Main_SaveProjectEx(0, path, 0)`
  writes the file, `Main_openProject("noprompt:" .. path)` then adopts it (the
  path now exists, so the blank-the-tab trap above cannot fire), and from there
  a plain save is silent and `Main_OnCommand(40860, 0)` closes without a
  prompt.
* **`40860` closes the ACTIVE tab, not "your" tab.** Nothing about the action
  is scoped to the project your script opened; it acts on whatever
  `EnumProjects(-1, "")` returns at that instant, and that changes under you
  (the user clicks a tab, another agent opens one). Same for `40859`: the new
  tab becomes active, so anything you do afterwards lands wherever the active
  tab happens to be by then. The safe shape is one synchronous script that
  records the active project, `SelectProjectInstance`s its own tab, verifies
  `EnumProjects(-1, "")` by exact path, does the work, and hands the original
  tab back -- Reaper runs it on the main thread, so nothing can interleave.
  `Main_OnCommandEx(cmd, flag, proj)` takes a project pointer but most actions
  ignore it; `InsertTrackInProject`, `CountTracks(proj)` and the track-pointer
  APIs genuinely are project-explicit.
* `error(msg, 0)` raises without Lua's `body_xxx.lua:26:` prefix, so the
  message `LuaError` carries reads like a CLI error instead of a traceback.
  The new scripts use it; the older ones do not.

## Track state

`GetMediaTrackInfo_Value` / `SetMediaTrackInfo_Value` fields rondo relies on:

| field | meaning |
| --- | --- |
| `B_MUTE` | 0/1 |
| `I_SOLO` | 0 off, 1 solo, 2 solo in place |
| `I_RECARM` | 0/1 |
| `I_RECMON` | 0 off, 1 on, 2 auto |
| `I_RECINPUT` | see Recording below; **-1 is "no input"** |
| `D_VOL` | **linear** gain, not dB: unity is `1.0`, so `dB = 20*log10(vol)` |
| `D_PAN` | -1.0 hard left .. 0 center .. 1.0 hard right |

A track made with `InsertTrackAtIndex(n, true)` starts at `I_RECINPUT` 0 (the
first audio input) with `I_RECMON` 1 (**monitoring on**), not off.

Per-item detail: `TakeIsMIDI(GetActiveTake(item))` says whether an item is
MIDI, and `select(2, MIDI_CountEvts(take))` is its note count.

## Regions and markers

`EnumProjectMarkers(i)` returns `(retval, isrgn, pos, rgnend, name,
markrgnindexnumber)` and `retval == 0` ends the walk. `DeleteProjectMarker(0,
idx, isrgn)` wants that **6th value, the display index number** -- not the
enumeration position `i`. Deleting renumbers the enumeration, so restart the
walk from 0 after each delete (`project.py region/marker` replaces by name
this way).

`SetCurrentBPM(0, bpm, true)` sets the project tempo; the new value reads back
immediately from `select(3, TimeMap_GetTimeSigAtTime(0, 0))`.

`SetEditCurPos(t, true, false)` moves the **edit** cursor and scrolls to it.
The third argument is `seekplay`; keeping it false is what stops rondo from
nudging the transport.

## Instruments

`TrackFX_AddByName` accepts the full display string. Verified working:

| what | string |
| --- | --- |
| Surge XT | `VST3: Surge XT (Surge Synth Team)` |
| Dexed | `VST3: Dexed (Digital Suburban)` |
| Apple GM piano | `AU: DLSMusicDevice (Apple)` |

Bare `"Surge XT"`, `"VST3:Surge XT"` and `"CLAP: Surge XT"` also load.
**HAZARD:** `"AU: Surge XT"` fuzzy-matches the Surge XT *Effects* plugin. Use
the exact full names.

### Surge patches

Surge ships with `SURGE_EXPOSE_PRESETS` off, so it reports one program and
`TrackFX_SetPreset(tr, fx, "Bell Pad")` finds nothing. `TrackFX_SetPreset`
also accepts a **path to a `.vstpreset`**, and that works. Build one from the
factory `.fxp` (`rondo/surge_preset.py`):

```
offset 0   'VST3'
offset 4   int32 1
offset 8   char[32] "ABCDEF019182FAEB566D624153675854"   (Surge's class id)
offset 40  int64 offset of the chunk list
offset 48  component state = fxp[60:] + 16 zero bytes + "JUCEPrivateData"
...        'List', int32 1, 'Comp', int64 48, int64 len(component state)
```

Byte-for-byte identical to a preset Surge itself wrote. Factory patches live
in `/Library/Application Support/Surge XT/patches_factory/<Category>/*.fxp`
(641 of them, categorised by directory; the metadata tags are unreliable).

After a successful `SetPreset(path)`, `TrackFX_GetPresetIndex` returns `-1` and
`TrackFX_GetPreset` returns the **path**, not a patch name. That is expected.

The `vst_chunk` route via `TrackFX_SetNamedConfigParm` also works (Reaper's
VST3 chunk = int32 len, int32 1, component state, 8 zero bytes) but reads back
stale immediately after the set. Prefer the `.vstpreset` path.

### Dexed patches

Dexed exposes the 32 voices of the currently loaded cartridge.
`TrackFX_SetPresetByIndex` works; `TrackFX_SetPreset` by name does **not**,
because the names are space-padded (`"PHAROH    "`). To match by name, walk
`SetPresetByIndex` / `GetPreset` and compare against a stripped name --
`add_instrument.py` does this. `select(2, TrackFX_GetPresetIndex(tr, fx))` is
the program count.

## Drum kits (ReaSamplOmatic5000)

One RS5K instance holds one file list, so a GM kit is **N instances on one
track**, one per drum sound.

```lua
reaper.TrackFX_SetNamedConfigParm(tr, fx, "FILE0", path)
reaper.TrackFX_SetNamedConfigParm(tr, fx, "DONE", "")
reaper.TrackFX_SetParam(tr, fx, 3, note / 127)   -- note range start
reaper.TrackFX_SetParam(tr, fx, 4, note / 127)   -- note range end
reaper.TrackFX_SetParam(tr, fx, 11, 0)           -- obey note-offs: off
```

Params 17/18 are the velocity range, 5 is pitch `(semitones + 80) / 160`.
`TrackFX_GetFormattedParamValue` on 3/4 reads back the note **number**, not a
name. `TrackFX_Show(tr, fx, 2)` closes the floating window the add opens.

## MIDI

```lua
local item = reaper.CreateNewMIDIItemInProj(track, start_qn, end_qn, true)
local take = reaper.GetActiveTake(item)
local s = reaper.MIDI_GetPPQPosFromProjQN(take, abs_qn)
reaper.MIDI_InsertNote(take, false, false, s, e, chan, pitch, vel, true)
reaper.MIDI_Sort(take)
```

The 4th argument to `CreateNewMIDIItemInProj` (`qnInOptional`) makes the
positions quarter notes, so bar-aligned items need no time conversion. The
item must already span the range before notes go in.

## Copying a section

Reaper has **no** "copy items in a time range to a position" call. The
deterministic route:

```lua
local _, chunk = reaper.GetItemStateChunk(it, "", false)
local new = reaper.AddMediaItemToTrack(tr)
reaper.SetItemStateChunk(new, chunk, false)
reaper.SetMediaItemInfo_Value(new, "D_POSITION", p + delta)
```

Give the copy fresh GUIDs (`chunk:gsub("(\n%s*I?GUID )%b{}", ...)` with
`reaper.genGuid("")`) so it is not an alias of the original. rondo copies only
items fully inside the source range and reports anything that straddles a
boundary rather than guessing; the bar-aligned-section invariant makes that
rare.

Regions: `AddProjectMarker2(0, true, start, end, name, -1, 0)`. The
GUID-addressable `AddRegionOrMarker` family is preferred in 7.79 docs and
matters once sections start shifting, but `AddProjectMarker2` is what is
verified here.

`AddProjectMarker2` happily creates a **second** region with a name that is
already taken, so naming the same section twice leaves duplicates behind.
`project.py region` and `copy_section.py --region` both go through the
`add_marker_replacing(name, t0, t1, is_region)` prelude helper, which deletes
every same-named region first (`DeleteProjectMarker(0, idx, isrgn)` -- note
that is the **display index** from `EnumProjectMarkers`' sixth return value,
not the enumeration index) and then adds one.

## Automation envelopes

Verified against a Surge XT track in a scratch tab.

* **Track volume envelope**: `GetTrackEnvelopeByName(tr, "Volume")`. It does
  not exist until something creates it -- `Main_OnCommand(40406, 0)` ("Toggle
  track volume envelope visible") creates *and* shows it for the **selected**
  tracks, so save and restore the selection around it.
  `GetTrackEnvelopeByChunkName(tr, "VOLENV2")` does **not** find it here even
  though `GetEnvelopeName` reports the chunk name as `VOLENV2`.
* **Envelope values are in the envelope's own units, not yours.**
  `GetEnvelopeScalingMode(env)` returns 1 for a track volume envelope, where
  unity gain is stored as **716.2178503**, not 1.0. Always go through
  `ScaleToEnvelopeMode(mode, v)` on the way in and `ScaleFromEnvelopeMode(mode,
  v)` on the way out; the scaled-out value is then plain linear gain, the same
  units as `D_VOL`, so rondo's `db_to_gain` / `gain_to_db` apply unchanged.
  Round trip checked at -18, 0 and +18 dB. This is a normal (post-FX) volume
  envelope; the pre-FX one is a separate envelope named `Volume (Pre-FX)`.
* **FX parameter envelopes**: `GetFXEnvelope(tr, fx, param, true)` creates the
  envelope if it is missing (there is no "did you create it" return, so count
  `CountTrackEnvelopes` before and after). Scaling mode is 0 and the stored
  value is the plugin's normalised **0..1**, so no conversion is needed. The
  envelope's name is `"<param> / <fx> / <group>"`, e.g.
  `A Filter 1 Cutoff / Surge XT / A Filters`.
* `Envelope_FormatValue(env, value, "")` returns **one** string (not the usual
  `ok, str` pair) and wants the **raw** value, before `ScaleFromEnvelopeMode`.
  It gives `-18.0dB` for a volume envelope and `61.74 Hz` for Surge's cutoff,
  which is what makes `automate.py show` readable.
* Point shapes for `InsertEnvelopePoint(env, time, value, shape, tension,
  selected, noSort)`: 0 linear, 1 square, 2 slow start/end, 3 fast start, 4
  fast end, 5 bezier. The shape belongs to the point the segment starts from,
  so only the **first** point of a ramp needs it.
* **`DeleteEnvelopePointRange` leaves a point sitting at exactly time 0
  behind**, whatever range you pass (`-1.0, 1e12` included). So a "clear
  everything" reports one point left, and redrawing over bar 1 stacks a second
  point on the same time. `DeleteEnvelopePointEx(env, -1, index)` does delete
  it, so `automate.py` sweeps the range by index afterwards and really does
  reach zero points.
* With zero points an envelope is not "off": it evaluates to the underlying
  value (the fader, or the plugin's own parameter), which is why a cleared
  cutoff envelope reads back as the patch's own cutoff.
* `Envelope_Evaluate(env, time, samplerate, 0)` returns `retval, value, ...`;
  read the value **before** deleting anything if you want to restate it. That
  is how `automate.py` writes its guard point just before `--from`: without a
  point holding the previous value, a ramp drags every earlier bar with it.
* `Envelope_SortPoints(env)` after inserting with `noSort = true`, then
  `UpdateArrange()`.
* Useful Surge XT parameter indices on this machine (2858 parameters, so
  resolve by name): 319 `A Filter 1 Cutoff`, 320 `A Filter 1 Resonance`, 237
  `A Volume`, 12 `Global Volume`, 592 `B Filter 1 Cutoff`, 510 `B Volume`.
  `cutoff` alone matches four parameters -- filter 1 and 2 of both scenes --
  so name matching has to refuse ambiguity rather than pick.

## Rendering

```lua
reaper.GetSetProjectInfo_String(0, "RENDER_FILE", dir, true)
reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", stem, true)
reaper.GetSetProjectInfo_String(0, "RENDER_FORMAT", "evaw", true)  -- wav
reaper.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, true)            -- master mix
reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 0, true)          -- custom range
reaper.GetSetProjectInfo(0, "RENDER_STARTPOS", t0, true)
reaper.GetSetProjectInfo(0, "RENDER_ENDPOS", t1, true)
reaper.GetSetProjectInfo(0, "RENDER_SRATE", 44100, true)
reaper.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, true)
reaper.GetSetProjectInfo(0, "RENDER_ADDTOPROJ", 0, true)
reaper.Main_OnCommand(42230, 0)
```

`42230` ("render using the most recent settings, auto-close render dialog") is
**synchronous** and opens no dialog. Measured: 8 s of audio in 1.0 s, 16 s in
2.0 s, output 24-bit 44.1 kHz stereo. Do not substitute `41824` (it opens the
dialog). Disarm record-armed tracks before rendering or live input bleeds in.

Other bounds flags: 1 whole project, 2 time selection, 3 all regions, 5
selected regions. `RENDER_SETTINGS & 8` enables the region render matrix
(`SetRegionRenderMatrix` / `EnumRegionRenderMatrix`); not used yet.

## Recording

`I_RECINPUT` encodes the input: an audio mono input is just its index (`0` =
first hardware input); a MIDI input is `4096 + (device << 5) + channel`, with
channel `0` meaning "all". Reaper's own Virtual MIDI Keyboard is device 62, so
`6080`. `I_RECMON` is 0 off / 1 on / 2 auto. `I_RECARM` is 0/1.

The virtual keyboard's octave is a **"Center note" control in its window**.
There is no action for it -- a human sets it by hand.

`Main_OnCommand(40364, 0)` toggles the metronome;
`GetToggleCommandState(40364)` reads it.

**Correction, verified 2026-09:** this note used to say the metronome is a
global option, not project state. It is **per project tab**. Turning it on in
the active tab, opening a new tab (`40859`) and reading `40364` there gives 0;
closing that tab and reading again in the original gives 1. So `record.py`'s
and `project.py metronome`'s reading is about the tab you are in, and a scratch
tab cannot leave the user's metronome flipped.

## Humming

Transcribing a hum: gate on **pyin's voicing probability**, not on level. A
steady hum recorded through AirPods sits 30-39 dB below peak because the
headset's voice processing ducks it, while pyin is 100% confident about the
pitch. A `-30 dB` level gate throws the whole take away. Segment with
spectral flux over a log-compressed spectrum so a soft re-articulation at the
same pitch still reads as a new note, then merge same-pitch contiguous slots.

AirPods used as an input drop to a 24 kHz phone profile. Use a real mic.

## Still unverified

* CLAP plugin naming and the CLAP cache filename beyond
  `reaper-clap-macos-aarch64.ini` existing.
* `reaper -renderproject` with VSTis, and any headless mode on macOS.
* Whether `AddRegionOrMarker` behaves the same as `AddProjectMarker2` here.
* Envelope shapes beyond 0/3/4: rondo exposes linear, fast-start and fast-end
  only, and bezier tension is never set.
* Whether a **pre-FX** volume envelope uses the same scaling mode as the
  post-FX one; only the post-FX envelope has been exercised.
* Tempo changes inside an automated range: every time is computed with
  `TimeMap2_QNToTime`, so it should follow the tempo map, but it is untested.
* Time signatures other than 4/4: every bar<->quarter-note conversion in rondo
  assumes 4/4. `status.py` reports the real time signature so you can notice.
