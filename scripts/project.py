#!/usr/bin/env python3
"""
name: project
summary: Project-wide moves: save, move the edit cursor, toggle the metronome, name a region or marker, set the tempo, list tabs.
needs: reaper-running
usage: python scripts/project.py tabs | python scripts/project.py region --name B --from 9 --to 16 | python scripts/project.py tempo --bpm 96 | python scripts/project.py cursor --bar 9
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import _cli, reaper  # noqa: E402

# Main_SaveProject(0, false) saves in place and CLEARS the dirty flag, but only
# once the tab has a filename; on an unnamed tab it would open a Save As
# dialog, which rondo never does. Main_SaveProjectEx(0, path, 8) is Save As
# without the dialog: option &8 ("set as the new project filename for this
# ReaProject", verified on 7.79) makes the tab adopt the path and clears the
# dirty flag, and nothing is reloaded, so the bridge survives and the undo
# history stays. Options 0 would only write a copy. Both are reported honestly.
SAVE_AS_OPTIONS = 8

SAVE_LUA = r"""
local AS = %(as)s
local _, before = reaper.EnumProjects(-1, "")
local method, target
if AS then
  reaper.Main_SaveProjectEx(0, AS, %(options)d)
  method, target = "Main_SaveProjectEx", AS
else
  if before == nil or before == "" then
    error("this tab has no filename yet, so a plain save would open a Save As "
          .. "dialog. Give --as PATH (the tab adopts it, no dialog) or save it once by hand.", 0)
  end
  reaper.Main_SaveProject(0, false)
  method, target = "Main_SaveProject", before
end
local _, after = reaper.EnumProjects(-1, "")
local adopted = after ~= "" and after == target
local dirty = reaper.IsProjectDirty(0) == 1
log(jsonenc({ method = method, target = target,
              path_before = before, path_after = after,
              written = reaper.file_exists(target),
              adopted = adopted, dirty = dirty,
              ok = adopted and not dirty }))
"""

# seekplay = false: this moves the EDIT cursor only. rondo never touches the
# transport -- the human presses play.
CURSOR_LUA = r"""
local QN = %(qn)r
reaper.SetEditCurPos(reaper.TimeMap2_QNToTime(0, QN), true, false)
local pos = reaper.GetCursorPosition()
log(jsonenc({ position = pos, qn = QN,
              bar = math.floor(reaper.TimeMap2_timeToQN(0, pos) / 4) + 1,
              play_state = reaper.GetPlayState() }))
"""

METRONOME_LUA = r"""
local WANT = %(want)s
local on = reaper.GetToggleCommandState(40364) == 1
local changed = false
if WANT ~= nil and WANT ~= on then
  reaper.Main_OnCommand(40364, 0)          -- Options: Toggle metronome
  on = reaper.GetToggleCommandState(40364) == 1
  changed = true
end
log(jsonenc({ metronome = on, changed = changed }))
"""

# EnumProjectMarkers' 6th return is the DISPLAY index number, which is what
# DeleteProjectMarker wants -- not the enumeration position. Deleting shifts
# the enumeration, so the walk restarts after each hit.
MARK_LUA = r"""
local NAME, T0, T1, IS_REGION = %(name)s, %(t0)r, %(t1)r, %(region)s

reaper.Undo_BeginBlock()
local replaced = 0
local i = 0
while true do
  local ok, isrgn, _, _, name, idx = reaper.EnumProjectMarkers(i)
  if ok == 0 then break end
  if isrgn == IS_REGION and name == NAME then
    reaper.DeleteProjectMarker(0, idx, isrgn)
    replaced = replaced + 1
    i = 0
  else
    i = i + 1
  end
end
local t0 = reaper.TimeMap2_QNToTime(0, T0)
local t1 = reaper.TimeMap2_QNToTime(0, T1)
local idx = reaper.AddProjectMarker2(0, IS_REGION, t0, t1, NAME, -1, 0)
reaper.Undo_EndBlock("rondo: " .. (IS_REGION and "region " or "marker ") .. NAME, -1)
reaper.UpdateArrange()

log(jsonenc({ name = NAME, index = idx, replaced = replaced,
              is_region = IS_REGION, start = t0, ["end"] = t1 }))
"""

TEMPO_LUA = r"""
local BPM = %(bpm)r
local before = select(3, reaper.TimeMap_GetTimeSigAtTime(0, 0))
reaper.SetCurrentBPM(0, BPM, true)
log(jsonenc({ bpm = select(3, reaper.TimeMap_GetTimeSigAtTime(0, 0)),
              bpm_before = before }))
"""

TABS_LUA = r"""
local active = reaper.EnumProjects(-1, "")
local tabs, i = {}, 0
while true do
  local proj, path = reaper.EnumProjects(i, "")
  if not proj then break end
  -- A tab saved via Main_SaveProjectEx WITHOUT option &8 keeps an EMPTY name
  -- and path here: Reaper wrote a copy, it did not adopt the file.
  local _, name = reaper.GetSetProjectInfo_String(proj, "PROJECT_NAME", "", false)
  tabs[#tabs+1] = { index = i, name = name, path = path,
                    active = proj == active,
                    dirty = reaper.IsProjectDirty(proj) == 1,
                    tracks = reaper.CountTracks(proj) }
  i = i + 1
end
log(jsonenc(tabs))
"""


def guard_project_path(path: str) -> Path:
    """``--as`` must end in .rpp and must not land inside the repo."""
    p = Path(path).expanduser().resolve()
    if p.suffix.lower() != ".rpp":
        raise SystemExit(f"--as must end in .rpp (got {p.name})")
    try:
        p.relative_to(_cli.REPO)
    except ValueError:
        return p
    raise SystemExit(
        f"refusing to save a project inside the repo ({p}). "
        "Projects are not tracked here -- write to /private/tmp or your own folder."
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="Everything acts on the ACTIVE project tab. Bars are 1-based and "
               "--from/--to are inclusive, so --from 9 --to 16 is eight bars. "
               "A region or marker with the same name is replaced, not duplicated. "
               "cursor moves the edit cursor only; rondo never starts the transport.",
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, help_: str):
        p = sub.add_parser(name, help=help_, description=help_)
        p.add_argument("--json", action="store_true")
        return p

    add("save", "Save the project in place, or Save As with --as.").add_argument(
        "--as", dest="as_path", metavar="PATH",
        help="save as PATH (must end in .rpp): the tab adopts that filename, no dialog. "
             "On a tab that already has a filename this is a rename, and the output says so.")
    p = add("cursor", "Move the edit cursor to a bar.")
    p.add_argument("--bar", type=int, required=True)
    p.add_argument("--beat", type=float, default=1.0, help="1-based, so 1 is the downbeat")
    add("metronome", "Turn the metronome on or off, or read it.").add_argument(
        "state", choices=["on", "off", "status"])
    p = add("region", "Name a bar range as a region (replacing one of the same name).")
    p.add_argument("--name", required=True)
    p.add_argument("--from", dest="start", type=int, required=True)
    p.add_argument("--to", dest="stop", type=int, required=True)
    p = add("marker", "Put a named marker at a bar (replacing one of the same name).")
    p.add_argument("--name", required=True)
    p.add_argument("--bar", type=int, required=True)
    add("tempo", "Set the project tempo.").add_argument(
        "--bpm", type=float, required=True)
    add("tabs", "List the open project tabs.")
    return ap


def render_save(r: dict) -> str:
    """One line saying what the save did, and whether the tab now owns the file.

    Success means the tab's path is the target AND the dirty flag is clear;
    anything else is described from the fields Reaper reported, so the text
    never claims a write that did not happen.
    """
    tab = (f"the tab is {r['path_after'] or '(unsaved)'} and "
           f"{'has' if r['dirty'] else 'has no'} unsaved changes")
    if r["method"] == "Main_SaveProject":
        if r["ok"]:
            return f"saved in place: {r['target']}"
        return f"save in place of {r['target']} did not stick: {tab}"
    if r["ok"]:
        renamed = r["path_before"] and r["path_before"] != r["target"]
        return (f"saved as {r['target']}; the tab now has that name and no unsaved changes"
                + (f" (it was {r['path_before']})" if renamed else ""))
    wrote = "wrote" if r["written"] else "did NOT write"
    return f"{wrote} {r['target']}, and {tab}"


def render_tabs(tabs: list[dict]) -> str:
    out = [f"{len(tabs)} tab(s):"]
    for t in tabs:
        out.append(
            f"  [{t['index']}]{' <- active' if t['active'] else '         '}  "
            f"{t['name'] or t['path'] or '(unsaved)'}"
            f"{' *unsaved changes*' if t['dirty'] else ''}"
            f"   {t['tracks']} track(s)"
        )
    return "\n".join(out)


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)

    as_path = None
    if a.command == "save" and a.as_path:
        as_path = guard_project_path(a.as_path)
        # Reaper's own behaviour on an unwritable directory is unverified (it
        # may show an error box), so refuse here and never find out.
        try:
            as_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SystemExit(f"cannot create {as_path.parent}: {e.strerror or e}")
        if not os.access(as_path.parent, os.W_OK):
            raise SystemExit(f"{as_path.parent} is not writable, so Reaper could not "
                             "save there")
    if a.command == "region":
        # raises ValueError if --to is before --from
        try:
            t0, t1 = reaper.bar_span_qn(a.start, a.stop)
        except ValueError as e:
            raise SystemExit(str(e))
    if a.command == "tempo" and not 1.0 <= a.bpm <= 960.0:
        raise SystemExit(f"--bpm {a.bpm:g} is outside Reaper's 1..960 range")

    _cli.require_reaper()

    if a.command == "save":
        r = reaper.run_lua_json(
            SAVE_LUA % {"as": reaper.lua_str(str(as_path)) if as_path else "nil",
                        "options": SAVE_AS_OPTIONS},
            timeout=60.0)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r["ok"] else 1
        if not r["ok"]:
            raise SystemExit(render_save(r))     # stderr, exit 1, like every other refusal
        print(render_save(r))
        return 0

    if a.command == "cursor":
        r = reaper.run_lua_json(
            CURSOR_LUA % {"qn": reaper.bars_to_qn(a.bar, a.beat)}, timeout=20.0)
        print(json.dumps(r, indent=2) if a.json else
              f"edit cursor at bar {r['bar']} ({r['position']:.2f}s); "
              f"transport untouched (play_state {r['play_state']})")
        return 0

    if a.command == "metronome":
        r = reaper.run_lua_json(METRONOME_LUA % {
            "want": {"on": "true", "off": "false"}.get(a.state, "nil")}, timeout=20.0)
        print(json.dumps(r, indent=2) if a.json else
              f"metronome {'on' if r['metronome'] else 'off'}"
              + (" (changed)" if r["changed"] else ""))
        return 0

    if a.command in ("region", "marker"):
        is_region = a.command == "region"
        if not is_region:
            t0 = t1 = reaper.bars_to_qn(a.bar)
        r = reaper.run_lua_json(MARK_LUA % {
            "name": reaper.lua_str(a.name), "t0": t0, "t1": t1,
            "region": "true" if is_region else "false",
        }, timeout=20.0)
        if a.json:
            print(json.dumps(r, indent=2))
        elif is_region:
            print(f"region {r['name']!r} over bars {a.start}-{a.stop} "
                  f"({r['start']:.2f}s..{r['end']:.2f}s)"
                  + (f", replaced {r['replaced']} of the same name" if r["replaced"] else ""))
        else:
            print(f"marker {r['name']!r} at bar {a.bar} ({r['start']:.2f}s)"
                  + (f", replaced {r['replaced']} of the same name" if r["replaced"] else ""))
        return 0

    if a.command == "tempo":
        r = reaper.run_lua_json(TEMPO_LUA % {"bpm": float(a.bpm)}, timeout=20.0)
        print(json.dumps(r, indent=2) if a.json else
              f"tempo {r['bpm_before']:g} -> {r['bpm']:g} bpm")
        return 0

    tabs = reaper.run_lua_json(TABS_LUA, timeout=20.0)
    print(json.dumps(tabs, indent=2) if a.json else render_tabs(tabs))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli.run(main))
