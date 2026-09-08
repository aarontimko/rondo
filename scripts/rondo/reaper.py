"""Talk to a running Reaper: the bridge mailbox, and arbitrary ReaScript.

Two transports:

``call(func, *args)``
    TwelveTake file mailbox. Writes ``request_N.json``, waits for
    ``response_N.json``. Round trip ~25 ms. LIMITS: positional args only; the
    track *index* is the first arg for ``TrackFX_*``; handlers that take
    pointer/out arguments (``GetTrackStateChunk``, ``TrackFX_SetNamedConfigParm``,
    ``CountTrackMediaItems``, ...) do NOT work through the bridge -- use Lua.

``run_lua(source)``
    Writes ``source`` to a temp file, wraps it in a runner that provides
    ``log()`` / ``jsonenc()`` and a pcall, launches it with ``open -a REAPER``,
    polls for the output file, and returns everything ``log()`` wrote. Raises
    ``LuaError`` if the script raised.

Timing assumption: ``bars_to_qn`` and friends assume 4/4. Reaper's own
quarter-note timeline is authoritative for everything else, so a project in
another meter still renders correctly -- only the bar<->quarter-note
arithmetic here would be wrong. ``project_info()`` reports the actual time
signature so callers can refuse to guess.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

BRIDGE_DIR = Path(
    os.environ.get(
        "REAPER_BRIDGE_DIR",
        Path.home() / "Library/Application Support/REAPER/Scripts/mcp_bridge_data",
    )
)

REAPER_APP = os.environ.get("RONDO_REAPER_APP", "REAPER")

#: Scratch directory for generated Lua and its output.
SCRATCH = Path(os.environ.get("RONDO_SCRATCH", tempfile.gettempdir())) / "rondo-lua"


class BridgeError(RuntimeError):
    """The bridge answered, but Reaper reported a failure."""


class LuaError(RuntimeError):
    """A ReaScript raised. ``.output`` holds whatever it logged first."""

    def __init__(self, message: str, output: str = ""):
        super().__init__(message)
        self.output = output


# --------------------------------------------------------------------------
# bridge
# --------------------------------------------------------------------------


def call(func: str, *args, timeout: float = 10.0) -> dict:
    """Call ``reaper.<func>(*args)`` through the file mailbox.

    Returns the decoded response dict (``{"ok": bool, "ret": ..., "_ms": int}``).
    Raises ``TimeoutError`` if the bridge is not running.
    """
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    rid = uuid.uuid4().hex[:16]
    payload = json.dumps({"func": func, "args": list(args), "id": rid}).encode()

    # Write the whole payload to a scratch file first, then hard-link it into
    # the slot. Creating the slot empty and writing after (the obvious way) is
    # a race: the bridge polls hard enough to read a half-written request and
    # answer "malformed JSON".
    tmp = BRIDGE_DIR / f".rondo_{rid}.tmp"
    tmp.write_bytes(payload)
    slot = (os.getpid() + int(time.time() * 1000)) % 999 + 1
    req = None
    try:
        for _ in range(999):
            candidate = BRIDGE_DIR / f"request_{slot}.json"
            try:
                os.link(tmp, candidate)
            except FileExistsError:
                slot = slot % 999 + 1
                continue
            req = candidate
            break
    finally:
        tmp.unlink(missing_ok=True)
    if req is None:
        raise RuntimeError("bridge: no free request slot")

    resp = BRIDGE_DIR / f"response_{slot}.json"
    t0 = time.time()
    while time.time() - t0 < timeout:
        if resp.exists():
            try:
                data = json.loads(resp.read_text())
            except json.JSONDecodeError:
                time.sleep(0.02)
                continue
            if data.get("id") in (None, rid):
                resp.unlink(missing_ok=True)
                data["_ms"] = round((time.time() - t0) * 1000)
                return data
        time.sleep(0.02)
    req.unlink(missing_ok=True)
    raise TimeoutError(
        f"{func}: no response in {timeout}s. Is Reaper running with the bridge loaded? "
        f"(open -a REAPER '{BRIDGE_DIR.parent}/reaper_mcp_bridge.lua')"
    )


def ret(func: str, *args, timeout: float = 10.0):
    """``call`` but return the ``ret`` value, raising ``BridgeError`` on failure."""
    data = call(func, *args, timeout=timeout)
    if not data.get("ok"):
        raise BridgeError(f"{func}{args}: {data.get('error') or data}")
    return data.get("ret")


def is_running(timeout: float = 3.0) -> str | None:
    """Return Reaper's version string if the bridge answers, else ``None``."""
    try:
        return ret("GetAppVersion", timeout=timeout)
    except (TimeoutError, BridgeError, OSError):
        return None


# --------------------------------------------------------------------------
# Lua
# --------------------------------------------------------------------------

#: A tiny JSON encoder, injected as the global ``jsonenc`` for every script.
LUA_JSON = r"""
function jsonenc(v)
  local t = type(v)
  if v == nil then return "null" end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end
    return string.format("%.10g", v)
  end
  if t == "boolean" then return tostring(v) end
  if t == "string" then
    local s = v:gsub('[%c"\\]', function(c)
      local m = {['"']='\\"', ['\\']='\\\\', ['\n']='\\n', ['\r']='\\r', ['\t']='\\t'}
      return m[c] or string.format('\\u%04x', c:byte())
    end)
    return '"' .. s .. '"'
  end
  if t == "table" then
    local n = 0
    for _ in pairs(v) do n = n + 1 end
    if n == #v then
      local parts = {}
      for i = 1, #v do parts[i] = jsonenc(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for k in pairs(v) do keys[#keys+1] = k end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    local parts = {}
    -- keep the ORIGINAL key: `v[k] ~= nil and v[k] or ...` silently turns a
    -- stored `false` into null, which is how this bit was wrong the first time.
    for _, k in ipairs(keys) do
      parts[#parts+1] = jsonenc(tostring(k)) .. ":" .. jsonenc(v[k])
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return '"<' .. t .. '>"'
end
"""

#: Helpers every rondo script gets. ``log`` writes a line to the output file.
LUA_PRELUDE = r"""
-- rondo helpers ------------------------------------------------------------
function bars_to_qn(bar, beat) return (bar - 1) * 4 + ((beat or 1) - 1) end

function find_track(name)
  for i = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, i)
    local _, n = reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)
    if n == name then return tr, i end
  end
  return nil, -1
end

function track_name(tr)
  local _, n = reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)
  return n
end
"""

_RUNNER = r"""
local OUT = [==[{out}]==]
local BODY = [==[{body}]==]
local __f = assert(io.open(OUT, "w"))
function log(...)
  local n = select('#', ...)
  local t = {{}}
  for i = 1, n do t[i] = tostring((select(i, ...))) end
  __f:write(table.concat(t, "\t") .. "\n")
  __f:flush()
end
{json}
{prelude}
local __ok, __err = pcall(dofile, BODY)
__f:write("\n@@RONDO_DONE " .. tostring(__ok))
if not __ok then __f:write(" " .. tostring(__err)) end
__f:write("\n")
__f:close()
"""

_DONE = "@@RONDO_DONE "


def lua_str(s: str) -> str:
    """Python string -> a Lua string literal, safe to paste into generated Lua."""
    return json.dumps(str(s), ensure_ascii=False)


def lua_value(v) -> str:
    """Python scalar/list/dict -> Lua literal (dicts become string-keyed tables)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return lua_str(v)
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(lua_value(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ",".join(f"[{lua_str(k)}]={lua_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"cannot express {type(v).__name__} as Lua")


def run_lua(source: str, timeout: float = 15.0, keep: bool = False) -> str:
    """Run ``source`` as a ReaScript in the running Reaper; return its log output.

    The script gets globals ``log(...)``, ``jsonenc(v)``, ``bars_to_qn``,
    ``find_track`` and ``track_name``. Anything the script raises becomes a
    ``LuaError`` here. A syntax error in ``source`` is caught too (the body is
    ``dofile``d inside a pcall), so you get a real message instead of a hang.
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:12]
    body = SCRATCH / f"body_{tag}.lua"
    runner = SCRATCH / f"run_{tag}.lua"
    out = SCRATCH / f"out_{tag}.txt"
    body.write_text(source)
    runner.write_text(
        _RUNNER.format(
            out=str(out), body=str(body), json=LUA_JSON, prelude=LUA_PRELUDE
        )
    )

    subprocess.run(["open", "-a", REAPER_APP, str(runner)], check=True)

    t0 = time.time()
    text = ""
    while time.time() - t0 < timeout:
        if out.exists():
            text = out.read_text()
            if _DONE in text:
                break
        time.sleep(0.05)
    else:
        raise TimeoutError(
            f"lua: no result in {timeout}s. Is Reaper running and in the foreground? "
            f"(script kept at {runner})"
        )

    head, _, tail = text.partition(_DONE)
    ok, _, err = tail.strip().partition(" ")
    if not keep:
        for p in (body, runner, out):
            p.unlink(missing_ok=True)
    if ok != "true":
        raise LuaError(err.strip() or "script failed", output=head)
    return head.strip("\n")


def run_lua_json(source: str, timeout: float = 15.0):
    """Run Lua that logs exactly one line of JSON; return the decoded value."""
    text = run_lua(source, timeout=timeout).strip()
    if not text:
        raise LuaError("script produced no JSON output")
    return json.loads(text.splitlines()[-1])


# --------------------------------------------------------------------------
# musical time
# --------------------------------------------------------------------------


def bars_to_qn(bar: int, beat: float = 1.0) -> float:
    """Bar/beat (both 1-based) -> quarter notes from project start. Assumes 4/4.

    Bar 1 beat 1 is 0.0 QN. In any other meter this is wrong; call
    ``project_info()`` and check ``timesig`` first if it matters.
    """
    return (bar - 1) * 4.0 + (beat - 1.0)


def bar_span_qn(first: int, last: int) -> tuple[float, float]:
    """Inclusive bar range -> the half-open quarter-note span that covers it.

    ``bar_span_qn(1, 8)`` is ``(0.0, 32.0)``: bars 1..8, ending where bar 9
    begins. Every rondo CLI's ``--from``/``--to`` is inclusive, so this is the
    one place the ``+ 1`` lives.
    """
    if last < first:
        raise ValueError(f"--to ({last}) is before --from ({first})")
    return bars_to_qn(first), bars_to_qn(last + 1)


def qn_to_bars(qn: float) -> tuple[int, float]:
    """Inverse of ``bars_to_qn``. Assumes 4/4. Returns (bar, beat), both 1-based."""
    bar = int(qn // 4) + 1
    return bar, qn - (bar - 1) * 4 + 1.0


def project_info(timeout: float = 15.0) -> dict:
    """Tempo, time signature, cursor and project name, read from Reaper."""
    return run_lua_json(
        r"""
        local _, _, bpm = reaper.TimeMap_GetTimeSigAtTime(0, 0)
        local num, den = reaper.TimeMap_GetTimeSigAtTime(0, 0)
        local _, path = reaper.EnumProjects(-1, "")
        log(jsonenc({
          bpm = bpm, timesig_num = num, timesig_den = den,
          cursor = reaper.GetCursorPosition(),
          project = path,
          tracks = reaper.CountTracks(0),
        }))
        """,
        timeout=timeout,
    )
