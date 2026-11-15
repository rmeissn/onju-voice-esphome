#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
# ///
"""Bit view of 32-bit both-slot raw_mic captures (mic bug, docs/AUDIO_INVESTIGATION.md; device: diagnostics/raw32.yaml +
diagnostics/both_slots_raw.yaml, so raw ch0 = RIGHT, ch1 = LEFT).

Usage: uv run tools/raw32_bits.py [--phases] [--seconds] captures/<run> [...]
- default:    P(bit=1) per bit, one digit per bit from bit 31 (left) to bit 0: 0 = never, 9 = always, '-' = < 5 %,
              '+' = > 95 %, 5 = random; plus the DC (16-bit scale) per mic.
- --phases:   the same per phase (frame index mod 3), shows "corrupted" sessions (the read slips inside the slot).
- --seconds:  per second: share of samples with any of bits 13..0 set, share with bit 0 set, AC level (dBFS).

Reading (SPH0645 datasheet: 18 data bits, 6 zero bits, then tri-state): correct alignment = bits 13..0 never set
(right mic `99999009 0+144455 55000000 00000000`); one BCLK late (the former "healthy") = bit 0 always set, data in
31..15 (`99990090 +1444555 50000000 00000009`). Not validated with synthetic data yet (simple bit counting only).
"""

import sys
import wave
from pathlib import Path

import numpy as np

NAMES = ((0, "R"), (1, "L"))


def code(p: float) -> str:
    if p == 0:
        return "0"
    if p == 1:
        return "9"
    if p < 0.05:
        return "-"
    if p > 0.95:
        return "+"
    return str(min(8, max(1, int(round(p * 10)))))


def bitmap(u: np.ndarray) -> str:
    s = "".join(code(((u >> b) & 1).mean()) for b in range(31, -1, -1))
    return f"{s[:8]} {s[8:16]} {s[16:24]} {s[24:]}"


def load(run: Path) -> np.ndarray:
    with wave.open(str(run / "raw_mic.wav"), "rb") as w:
        if w.getsampwidth() != 4 or w.getnchannels() != 2:
            raise SystemExit(f"{run}: needs 32-bit raw_mic with 2 channels")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i4").reshape(-1, 2)


def main() -> None:
    args = sys.argv[1:]
    phases, seconds = "--phases" in args, "--seconds" in args
    for run in (Path(a) for a in args if not a.startswith("--")):
        x = load(run)
        print(run.name)
        if seconds:
            print("   s | right: low14 bit0 level | left: low14 bit0 level")
            for s in range(len(x) // 48000):
                blk = x[s * 48000:(s + 1) * 48000]
                cols = []
                for c, _ in NAMES:
                    u = blk[:, c].view(np.uint32)
                    v = blk[:, c].astype(float) / 65536
                    lvl = 20 * np.log10(np.std(v - v.mean()) / 32768 + 1e-12)
                    cols.append(f"{np.mean((u & 0x3FFF) != 0):5.3f} {np.mean(u & 1):5.3f} {lvl:6.1f}")
                print(f"  {s:2d} | " + " | ".join(cols))
        elif phases:
            n = len(x) // 3 * 3
            for k in range(3):
                cols = [f"{name} {bitmap(x[k:n:3, c].view(np.uint32))} dc {x[k:n:3, c].mean() / 65536:6.0f}"
                        for c, name in NAMES]
                print(f"  phase {k}: " + " | ".join(cols))
        else:
            cols = [f"{name} {bitmap(x[:, c].view(np.uint32))} dc {x[:, c].mean() / 65536:6.0f}" for c, name in NAMES]
            print("  " + " | ".join(cols))


if __name__ == "__main__":
    main()
