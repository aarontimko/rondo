"""Load a Surge XT factory patch by turning its ``.fxp`` into a ``.vstpreset``.

Why this exists: Surge XT ships with ``SURGE_EXPOSE_PRESETS`` off, so the
plugin reports exactly one program and ``TrackFX_SetPreset(track, fx, "Bell
Pad")`` cannot find anything. ``TrackFX_SetPreset`` also accepts a *path* to a
``.vstpreset``, and that route works. So: read the factory ``.fxp``, wrap its
payload as a VST3 preset, hand Reaper the path.

Byte layout (verified against ``z_ignore/spike/bellpad.vstpreset``)::

    offset 0   'VST3'
    offset 4   int32  1                       (version)
    offset 8   char[32] class id, ASCII hex    (Surge XT: see CLASS_ID)
    offset 40  int64  offset of the chunk list
    offset 48  component state
    ...        'List', int32 chunk count,
               then per chunk: char[4] id, int64 offset, int64 size

The component state is ``fxp[60:] + 16 zero bytes + b"JUCEPrivateData"``:
the first 60 bytes of an ``.fxp`` are the VST2 ``fxp`` header, the rest is
Surge's own ``sub3`` chunk, and JUCE appends an empty private-data block.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

#: Surge XT's VST3 processor class id, as ASCII hex.
CLASS_ID = b"ABCDEF019182FAEB566D624153675854"

#: JUCE writes this trailer after the plugin's own state.
_JUCE_TRAILER = b"\x00" * 16 + b"JUCEPrivateData"

#: Where the Surge installer puts patches. First existing wins.
PATCH_DIRS = [
    Path("/Library/Application Support/Surge XT"),
    Path.home() / "Library/Application Support/Surge XT",
]


class SurgePresetError(RuntimeError):
    pass


def fxp_to_component_state(fxp: bytes) -> bytes:
    """Strip the 60-byte fxp header, append JUCE's empty private-data block."""
    if fxp[:4] != b"CcnK":
        raise SurgePresetError("not an .fxp file (missing 'CcnK' magic)")
    if len(fxp) <= 60:
        raise SurgePresetError(f".fxp is only {len(fxp)} bytes")
    body = fxp[60:]
    if body[:4] != b"sub3":
        raise SurgePresetError(
            f"expected Surge 'sub3' chunk at offset 60, found {body[:4]!r}"
        )
    return body + _JUCE_TRAILER


def build_vstpreset(fxp_path: str | os.PathLike, out_path: str | os.PathLike,
                    class_id: bytes = CLASS_ID) -> Path:
    """Write a ``.vstpreset`` next to wherever you point it. Returns the path."""
    fxp = Path(fxp_path).read_bytes()
    comp = fxp_to_component_state(fxp)

    header = b"VST3" + struct.pack("<i", 1) + class_id
    list_offset = len(header) + 8 + len(comp)
    blob = (
        header
        + struct.pack("<q", list_offset)
        + comp
        + b"List"
        + struct.pack("<i", 1)
        + b"Comp"
        + struct.pack("<qq", len(header) + 8, len(comp))
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return out


# --------------------------------------------------------------------------
# finding patches
# --------------------------------------------------------------------------


def patch_root() -> Path:
    for d in PATCH_DIRS:
        if (d / "patches_factory").is_dir():
            return d
    raise SurgePresetError(
        "Surge XT patch directory not found. Looked in: "
        + ", ".join(str(d) for d in PATCH_DIRS)
    )


def find_patches(substring: str = "", limit: int | None = None) -> list[Path]:
    """Factory + 3rd-party ``.fxp`` paths whose name contains ``substring``.

    Case-insensitive; matches on the ``Category/Patch`` tail, so
    ``find_patches("pads/bell")`` works. Sorted so exact-ish matches float up.
    """
    root = patch_root()
    needle = substring.lower()
    hits = []
    for sub in ("patches_factory", "patches_3rdparty"):
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.fxp")):
            rel = p.relative_to(d).as_posix()
            if needle in rel.lower():
                hits.append(p)
    hits.sort(key=lambda p: (p.stem.lower() != needle,
                             not p.stem.lower().startswith(needle),
                             len(p.stem), str(p)))
    return hits[:limit] if limit else hits


def resolve_patch(name: str) -> Path:
    """One patch, or a helpful error listing near misses."""
    hits = find_patches(name)
    if not hits:
        raise SurgePresetError(f"no Surge patch matches {name!r} under {patch_root()}")
    return hits[0]
