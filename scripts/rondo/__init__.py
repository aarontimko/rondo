"""rondo: a small toolkit for driving the Reaper DAW from an LLM.

Two ways in:

* ``rondo.reaper.call`` -- the TwelveTake file mailbox ("bridge"). ~25 ms per
  call, but only works for functions whose arguments and return value are
  plain scalars. Anything with a pointer/out argument does NOT work.
* ``rondo.reaper.run_lua`` -- writes a ReaScript to a temp file, launches it
  with ``open -a REAPER``, polls for its output. ~1-2 s per call, but can do
  anything ReaScript can do.

Rule of thumb: reads that fit the bridge go through the bridge; everything
structural goes through Lua.
"""

__all__ = ["reaper", "grid", "surge_preset"]
