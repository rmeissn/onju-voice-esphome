#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "soundfile"]
# ///
"""Shorter variants of the wake sound (user's wish 2026-11-15, docs/AUDIO_INVESTIGATION.md "Assist test with the final
settings": users start talking when the sound ends, so a shorter sound lets the VA listen earlier).

Usage: uv run tools/make_wake_variants.py sounds/wakeup.flac sounds/
Source: "New Notification #7" by UNIVERSFIELD (freesound 736267), 48 kHz: sweep 225 -> 650 Hz (0-160 ms), tone until
~220 ms, then a slow decay (about 8 dB per 100 ms). Writes wake_v1..v5 FLAC files next to it. `sounds/wakeup_v2.flac` is variant 5 (wake_v5_faster_higher_367ms), renamed.
"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def fade_out(x: np.ndarray, fs: int, start_ms: float, end_ms: float) -> np.ndarray:
    """Keep x until start_ms, cosine fade to 0 at end_ms, cut there."""
    b = min(int(fs * end_ms / 1000), len(x))
    a = min(int(fs * start_ms / 1000), b)
    y = x[:b].copy()
    n = b - a
    y[a:b] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, n)))
    return y


def faster_decay(x: np.ndarray, fs: int, from_ms: float, db_per_100ms: float, end_ms: float) -> np.ndarray:
    """Extra exponential attenuation after from_ms (steeper decay, sounds like a damped version), short fade at end."""
    a, b = int(fs * from_ms / 1000), int(fs * end_ms / 1000)
    y = x[:b].copy()
    t = np.arange(b - a) / fs
    y[a:b] *= 10 ** (-db_per_100ms * t * 10 / 20)
    return fade_out(y, fs, end_ms - 15, end_ms)


def wsola(x: np.ndarray, fs: int, rate: float, win_ms: float = 30, tol_ms: float = 8) -> np.ndarray:
    """Time compression without pitch change (WSOLA): output = len(x) / rate."""
    n = int(fs * win_ms / 1000)
    hop_out = n // 2
    hop_in = int(hop_out * rate)
    tol = int(fs * tol_ms / 1000)
    w = np.hanning(n)
    out_len = int(len(x) / rate) + n
    y = np.zeros(out_len)
    norm = np.zeros(out_len)
    pos_in, pos_out = 0, 0
    prev = x[:n]
    while pos_in + n + tol < len(x) and pos_out + n < out_len:
        # find the offset around pos_in that best continues the previous frame's natural successor
        target = x[min(pos_in_prev + hop_out, len(x) - n): min(pos_in_prev + hop_out, len(x) - n) + n] if pos_out else prev
        best, best_k = -np.inf, 0
        for k in range(-tol, tol + 1):
            s = pos_in + k
            if s < 0 or s + n > len(x):
                continue
            c = np.dot(x[s : s + n], target)
            if c > best:
                best, best_k = c, k
        s = pos_in + best_k
        y[pos_out : pos_out + n] += x[s : s + n] * w
        norm[pos_out : pos_out + n] += w
        pos_in_prev = s
        pos_in += hop_in
        pos_out += hop_out
    norm[norm < 1e-3] = 1
    return (y / norm)[: int(len(x) / rate)]


def resample_speed(x: np.ndarray, factor: float) -> np.ndarray:
    """Play faster by `factor` (pitch rises by 12*log2(factor) semitones)."""
    idx = np.arange(0, len(x) - 1, factor)
    return np.interp(idx, np.arange(len(x)), x)


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    x, fs = sf.read(src)
    x = x if x.ndim == 1 else x.mean(axis=1)
    variants = {
        # 1: cut after the tone, gentle fade (keeps sweep and tone unchanged)
        "wake_v1_trim_350ms": fade_out(x, fs, 240, 350),
        # 2: shortest cut: sweep + short tone, quick fade
        "wake_v2_trim_250ms": fade_out(x, fs, 180, 250),
        # 3: same sound, decay damped (sounds like a shorter ring-out), ends at 320 ms
        "wake_v3_damped_320ms": faster_decay(x, fs, 200, 22, 320),
        # 4: whole 550 ms version 1.6x faster, same pitch (WSOLA), ~330 ms
        "wake_v4_faster_samepitch_330ms": fade_out(wsola(x[: int(fs * 0.55)], fs, 1.6), fs, 280, 330),
        # 5: whole 550 ms version 1.5x faster and a fifth higher (simple speed-up), ~367 ms
        "wake_v5_faster_higher_367ms": fade_out(resample_speed(x[: int(fs * 0.55)], 1.5), fs, 330, 366),
    }
    for name, y in variants.items():
        y = y * (10 ** (-9 / 20) / max(np.abs(y).max(), 1e-9))  # same peak as the original (-9 dBFS)
        sf.write(out / f"{name}.flac", y.astype(np.float32), fs, subtype="PCM_16")
        print(f"{name}: {len(y) / fs * 1000:.0f} ms, peak {20 * np.log10(np.abs(y).max()):.1f} dBFS")


if __name__ == "__main__":
    main()
