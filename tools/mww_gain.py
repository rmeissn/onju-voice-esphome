#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pymicro-wakeword==2.5.0"]
# ///
"""Offline wake word check per gain_factor (docs/AUDIO_INVESTIGATION.md "Level tuning").

Usage: uv run tools/mww_gain.py captures/<run> [...] [--windows 1m=3-17,3m=17-35,loud=35-58] [--gains 8,16,24,32]

Feeds clip(output * gain_factor) (the `output` capture point = what micro_wake_word gets before its gain_factor)
through pymicro-wakeword's okay_nabu (same model file as grey, sha256 d89128c4..., same micro frontend, sliding mean
of 5, cutoff 0.85). Prints per window: detections (separate events: sliding mean > cutoff, events < 1.5 s apart
merged), max sliding mean probability, peak level after gain, clipped samples.
"""

import sys
import wave
from pathlib import Path

import numpy as np
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures, Model


def load(run: Path) -> np.ndarray:
    with wave.open(str(run / "output.wav"), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)


def probs(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mww = MicroWakeWord.from_builtin(Model.OKAY_NABU)
    feat = MicroWakeWordFeatures()
    b = y.astype("<i2").tobytes()
    t, p = [], []
    step = 160 * 2  # 10 ms chunks
    for i in range(0, len(b), step):
        for f in feat.process_streaming(b[i : i + step]):
            pr = mww.process_streaming_prob(f)
            if pr is not None:
                t.append((i + step) / 2 / 16000)
                p.append(pr)
    return np.array(t), np.array(p)


def main() -> None:
    wspec, gspec = "1m=3-17,3m=17-35,loud=35-58", "8,16,24,32"
    runs = []
    for a in sys.argv[1:]:
        if a.startswith("--windows="):
            wspec = a.split("=", 1)[1]
        elif a.startswith("--gains="):
            gspec = a.split("=", 1)[1]
        else:
            runs.append(Path(a))
    windows = {k: tuple(float(x) for x in v.split("-")) for k, v in (s.split("=") for s in wspec.split(","))}
    cutoff = MicroWakeWord.from_builtin(Model.OKAY_NABU).probability_cutoff
    for run in runs:
        x = load(run)
        print(f"=== {run.name} (cutoff {cutoff})")
        for gf in (int(g) for g in gspec.split(",")):
            y = np.clip(np.round(x * gf), -32768, 32767)
            t, p = probs(y)
            above = t[p > cutoff]
            events = [float(above[0])] if len(above) else []
            for tt in above[1:]:
                if tt - events[-1] > 1.5:
                    events.append(float(tt))
            parts = []
            for name, (a, b) in windows.items():
                sel = (t >= a) & (t < b)
                ev = [e for e in events if a <= e < b]
                seg = x[int(a * 16000) : int(b * 16000)] * gf
                pk = 20 * np.log10(max(np.abs(np.clip(seg, -32768, 32767)).max(), 1) / 32768)
                parts.append(f"{name}: {len(ev)} det, max p {p[sel].max() if sel.any() else 0:.2f}, pk {pk:5.1f}, "
                             f"clip {int((np.abs(seg) > 32767).sum())}")
            outside = [e for e in events if not any(a <= e < b for a, b in windows.values())]
            print(f"  {gf:3d}x | " + " | ".join(parts) + f" | outside windows: {len(outside)}")


if __name__ == "__main__":
    main()
