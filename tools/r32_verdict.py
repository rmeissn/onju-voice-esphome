# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
# ///
"""Verdict per 32-bit both-slot capture (raw ch0 = right, ch1 = left), per mic:
CORRECT = bits 13..0 never set, phase spread (frame mod 3) < 2 % and DC within 10 % of the correct-read reference
          (rxfast-* captures 2026-11-14: right -6853, left -6439 in 18-bit units);
LATE    = one BCLK late: bit 0 set in >= 99 %, bits 13..1 never set, spread < 2 %, DC within 10 % of 2x reference;
MIXED   = anything else (slipping / corrupted).
Validated against the verdicts of rxnorm-* (mic_check.sh + bit analysis) and rxfast-* (all correct).
Usage: uv run tools/r32_verdict.py [--ref=R,L | --ref=auto] captures/<run> [...]
  --ref=R,L: correct-read DC per mic for another device; --ref=auto: median DC of the given runs (use it on a new
  device with >= 5 sessions that are all clean in the low bits; a wrong session then stands out by its DC)."""
import sys
import wave
from contextlib import suppress
from pathlib import Path

import numpy as np

REF = (-6853.0, -6439.0)


def verdict(raw: np.ndarray, ref: float) -> tuple[str, str]:
    u = raw.view("<u4")
    v = (raw >> 14).astype(float)
    dc = v.mean()
    ph = [v[p::3].mean() for p in range(3)]
    spread = (max(ph) - min(ph)) / max(abs(dc), 1.0)
    low14 = float(((u & 0x3FFF) != 0).mean())
    mid = float(((u & 0x3FFE) != 0).mean())
    bit0 = float((u & 1).mean())
    info = f"dc {dc:7.0f} spread {spread:.3f} low14 {low14:.3f}"
    if low14 == 0 and spread < 0.02 and abs(dc / ref - 1) < 0.1:
        return "CORRECT", info
    if bit0 >= 0.99 and mid == 0 and spread < 0.02 and abs(dc / (2 * ref) - 1) < 0.1:
        return "LATE", info
    return "MIXED", info


args = [a for a in sys.argv[1:] if not a.startswith("--ref=")]
for a in sys.argv[1:]:
    if a.startswith("--ref="):
        v = a.split("=", 1)[1]
        if v == "auto":
            dcs = []
            for b in args:
                with suppress(Exception):
                    with wave.open(str(Path(b) / "raw_mic.wav"), "rb") as w:
                        if w.getsampwidth() == 4 and w.getnchannels() == 2:
                            y = np.frombuffer(w.readframes(w.getnframes()), dtype="<i4").reshape(-1, 2)
                            dcs.append([(y[:, 0] >> 14).mean(), (y[:, 1] >> 14).mean()])
            if dcs:
                REF = tuple(float(v) for v in np.median(np.array(dcs), axis=0))
                print(f"reference DC (median of {len(dcs)} runs): right {REF[0]:.0f}, left {REF[1]:.0f}")
        else:
            REF = tuple(float(v) for v in v.split(","))

for a in args:
    run = Path(a)
    try:
        with wave.open(str(run / "raw_mic.wav"), "rb") as w:
            if w.getsampwidth() != 4 or w.getnchannels() != 2:
                print(f"{run.name}: NOT-32BIT")
                continue
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i4").reshape(-1, 2)
    except (FileNotFoundError, NotADirectoryError, EOFError, wave.Error):
        print(f"{run.name}: NO-CAPTURE")
        continue
    r, ri = verdict(x[:, 0], REF[0])
    l, li = verdict(x[:, 1], REF[1])
    overall = r if r == l else f"{r}/{l}"
    print(f"{run.name}: {overall}  (R {ri} | L {li})")
