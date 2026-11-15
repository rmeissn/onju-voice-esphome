#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
# ///
"""Synthetic 32-bit both-slot raw_mic captures with known ground truth, to validate tools/r32_verdict.py and the
32-bit path of tools/mic_check.sh (docs/AUDIO_INVESTIGATION.md, mic bug).

Usage: uv run tools/make_synthetic_raw32.py <dir>  ->  <dir>/syn32-<kind>/raw_mic.wav (prints the expected verdicts)
Then:  uv run tools/r32_verdict.py <dir>/syn32-*   and   bash tools/mic_check.sh --analyze <dir>/syn32-*

Model of the SPH0645 slot (datasheet frame, raw ch0 = right, ch1 = left like diagnostics/both_slots_raw.yaml):
18 data bits in bits 31..14 (two's complement, DC right -6853 / left -6439 in 18-bit units like grey, plus noise),
bits 13..0 zero (6 zero bits + tri-state read as 0).
Kinds:
- correct:     the datasheet frame                                          -> CORRECT
- late:        read one BCLK late: (word << 1) | first bit of the next slot (= its sign bit)  -> LATE
- slip-phase:  every 3rd frame read late, the others correct (period-3 slip) -> MIXED
- slip-dc:     low bits clean, but every value 2.4x the correct one (like rxnorm-225337/225343) -> MIXED
- correct-dev: correct frame of another device (DC right -5200 / left -7600): CORRECT only with --ref=auto or the
               matching --ref, MIXED with grey's default reference (DC off by > 10 %)
"""

import sys
import wave
from pathlib import Path

import numpy as np

FS = 48000
N = FS * 3


def mic(dc: float, rng: np.random.Generator) -> np.ndarray:
    """18-bit values: DC + noise + a 1 kHz tone (signal in the upper and the lower data bits)."""
    t = np.arange(N) / FS
    return np.round(dc + rng.normal(0, 25, N) + 400 * np.sin(2 * np.pi * 1000 * t)).astype(np.int64)


def frame32(v18: np.ndarray) -> np.ndarray:
    return (v18 << 14).astype(np.int64)


def late(words_r: np.ndarray, words_l: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One BCLK late: each slot shifted up by one bit, bit 0 = the next slot's MSB (sign)."""
    sign_l = (words_l < 0).astype(np.int64)  # next slot after right is left of the same frame
    sign_r_next = np.roll((words_r < 0).astype(np.int64), -1)  # next slot after left is right of the next frame
    return ((words_r << 1) & 0xFFFFFFFF) | sign_l, ((words_l << 1) & 0xFFFFFFFF) | sign_r_next


def to_i32(u: np.ndarray) -> np.ndarray:
    u = u & 0xFFFFFFFF
    return np.where(u >= 2**31, u - 2**32, u).astype(np.int32)


def write(path: Path, r: np.ndarray, l: np.ndarray) -> None:
    path.mkdir(parents=True, exist_ok=True)
    x = np.stack([to_i32(r), to_i32(l)], axis=1)
    with wave.open(str(path / "raw_mic.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)
        w.setframerate(FS)
        w.writeframes(x.astype("<i4").tobytes())


def main() -> None:
    out = Path(sys.argv[1])
    rng = np.random.default_rng(7)
    r, l = frame32(mic(-6853, rng)), frame32(mic(-6439, rng))
    write(out / "syn32-correct", r, l)
    lr, ll = late(r, l)
    write(out / "syn32-late", lr, ll)
    sr, sl = r.copy(), l.copy()
    sr[1::3], sl[1::3] = lr[1::3], ll[1::3]
    write(out / "syn32-slip-phase", sr, sl)
    write(out / "syn32-slip-dc", frame32(np.round((r >> 14) * 2.4).astype(np.int64)),
          frame32(np.round((l >> 14) * 2.4).astype(np.int64)))
    write(out / "syn32-correct-dev", frame32(mic(-5200, rng)), frame32(mic(-7600, rng)))
    print("expected: syn32-correct CORRECT, syn32-late LATE, syn32-slip-phase MIXED, syn32-slip-dc MIXED,")
    print("          syn32-correct-dev MIXED with the default reference, CORRECT with --ref=-5200,-7600")


if __name__ == "__main__":
    main()
