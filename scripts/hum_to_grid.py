#!/usr/bin/env python3
"""
name: hum-to-grid
summary: Transcribe a hummed or sung wav into rondo grid text you can feed to write-notes.
needs: librosa
usage: python scripts/hum_to_grid.py take.wav --bpm 120 --start-bar 9 --bars 8 [--grid 8] [--transpose 12]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rondo import grid as gridmod  # noqa: E402

MISSING = """\
hum_to_grid needs librosa and soundfile, which rondo does not require by default.

    uv pip install 'librosa>=0.11' soundfile
    # or, with the project's optional group:
    uv pip install -e '.[transcribe]'
"""


def _import_deps():
    try:
        import librosa  # noqa: F401
        import numpy  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError as e:
        raise SystemExit(f"{MISSING}\n(missing: {e.name})")
    import librosa
    import numpy as np
    import soundfile as sf
    return librosa, np, sf


def transcribe(path, bpm, start_sec, dur_sec, slots_per_bar=8, voicing=0.3,
               coverage=0.35, fmin=65.0, fmax=500.0, onset_strength=1.6,
               transpose=0):
    """Return (notes, debug rows). Slots are counted from ``start_sec``."""
    librosa, np, sf = _import_deps()

    y, sr = sf.read(path, dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    a = int(round(start_sec * sr))
    b = len(y) if dur_sec is None else min(len(y), a + int(round(dur_sec * sr)))
    y = y[a:b]
    if len(y) < sr // 10:
        raise SystemExit("selection is shorter than 0.1s -- check --start-bar/--bars")

    # pyin is expensive; 22.05k is plenty for a hum.
    target_sr = 22050
    if sr != target_sr:
        y = librosa.resample(y, orig_sr=sr, target_sr=target_sr)
        sr = target_sr
    hop = 256

    f0, voiced, prob = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr, hop_length=hop, frame_length=2048
    )
    # LESSON FROM THE SPIKE: do NOT gate on level. AirPods used as an input
    # duck a steady hum by ~30 dB, so a -30 dBFS gate throws away material that
    # pyin is 100% confident about. Gate on the voicing probability instead.
    ok = np.asarray(voiced) & (np.asarray(prob) > voicing) & ~np.isnan(f0)
    midi = np.full(len(f0), np.nan)
    midi[ok] = librosa.hz_to_midi(np.asarray(f0)[ok])

    # onsets: spectral flux over a log-compressed magnitude spectrum, so a soft
    # re-articulation at the same pitch still shows up as a boundary.
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop))
    flux = librosa.onset.onset_strength(S=np.log1p(1000 * S), sr=sr, hop_length=hop)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=flux, sr=sr, hop_length=hop, backtrack=False,
        delta=onset_strength * np.median(flux[flux > 0]) if np.any(flux > 0) else 0.1,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)

    slot_sec = (60.0 / bpm) * (4.0 / slots_per_bar)
    n_slots = int(np.ceil(len(y) / sr / slot_sec))
    frames_per_slot = slot_sec * sr / hop

    rows = []
    for s in range(n_slots):
        lo = int(round(s * frames_per_slot))
        hi = int(round((s + 1) * frames_per_slot))
        seg = midi[lo:hi]
        seg = seg[~np.isnan(seg)]
        cov = len(seg) / max(1, hi - lo)
        if cov < coverage:
            rows.append((s, None, cov, 0.0))
            continue
        pitch = float(np.median(seg))
        rows.append((s, int(round(pitch)) + transpose, cov, pitch - round(pitch)))

    onset_slots = {int(t // slot_sec) for t in onset_times}

    notes: list[gridmod.Note] = []
    for s, pitch, _cov, _cents in rows:
        if pitch is None:
            continue
        if (notes and notes[-1].midi == pitch
                and notes[-1].slot + notes[-1].length == s
                and s not in onset_slots):
            prev = notes[-1]
            notes[-1] = gridmod.Note(prev.slot, prev.length + 1, pitch)
        else:
            notes.append(gridmod.Note(s, 1, pitch))
    return notes, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        epilog="Output is grid text; pipe it straight into write_notes.py --grid -.",
    )
    ap.add_argument("wav")
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--grid", type=int, default=8, help="slots per bar (8 = eighths)")
    ap.add_argument("--start-bar", type=int, default=1,
                    help="bar of the RECORDING the transcription starts at (1-based)")
    ap.add_argument("--bars", type=int, help="how many bars to transcribe")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="extra seconds to skip before --start-bar")
    ap.add_argument("--label-from", type=int,
                    help="bar number to label the first output line with "
                         "(default: --start-bar)")
    ap.add_argument("--transpose", type=int, default=0, help="semitones")
    ap.add_argument("--voicing", type=float, default=0.3,
                    help="pyin voiced-probability gate (NOT a level gate)")
    ap.add_argument("--coverage", type=float, default=0.35,
                    help="fraction of a slot that must be voiced to count")
    ap.add_argument("--fmin", type=float, default=65.0)
    ap.add_argument("--fmax", type=float, default=500.0)
    ap.add_argument("--debug", action="store_true", help="per-slot table on stderr")
    a = ap.parse_args(argv)

    if not os.path.exists(a.wav):
        raise SystemExit(f"no such file: {a.wav}")

    sec_per_bar = 60.0 / a.bpm * 4
    start = (a.start_bar - 1) * sec_per_bar + a.offset
    dur = a.bars * sec_per_bar if a.bars else None

    notes, rows = transcribe(
        a.wav, a.bpm, start, dur, slots_per_bar=a.grid, voicing=a.voicing,
        coverage=a.coverage, fmin=a.fmin, fmax=a.fmax, transpose=a.transpose,
    )

    if a.debug:
        print("slot  bar beat  midi  note   cov   cents", file=sys.stderr)
        for s, pitch, cov, cents in rows:
            bar = s // a.grid + (a.label_from or a.start_bar)
            beat = (s % a.grid) / (a.grid / 4) + 1
            name = gridmod.note_name(pitch) if pitch is not None else "-"
            print(f"{s:4d} {bar:4d} {beat:4.2f}  "
                  f"{pitch if pitch is not None else 0:4d}  {name:5s} "
                  f"{cov:5.2f} {cents*100:+6.0f}", file=sys.stderr)

    if not notes:
        raise SystemExit("nothing voiced above the gate -- try --voicing 0.2 "
                         "or --coverage 0.2")
    print(f"# {os.path.basename(a.wav)}: {a.bpm:g} bpm, bars "
          f"{a.start_bar}..{a.start_bar + (a.bars - 1) if a.bars else '?'}, "
          f"transpose {a.transpose:+d}")
    print(gridmod.emit(notes, a.grid, first_bar=a.label_from or a.start_bar), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
