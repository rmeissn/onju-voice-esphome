#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pyspeex-noise==1.0.2", "pymicro-vad==1.0.1"]
# ///
"""Offline level tuning for Assist (docs/AUDIO_INVESTIGATION.md "Level tuning").

Usage: uv run tools/level_tune.py captures/<quiet> captures/<music> [--windows 1m=3-20,3m=21-42,loud=42-58]

Input: the `output` point of esp_audio_capture (= what micro_wake_word and voice_assistant get, before their
gain_factor), 16 kHz mono. Simulates the chain to HA's STT: clip(output * gain_factor) -> volume_multiplier 1.0 ->
HA's microVAD (on the audio BEFORE speex, as in assist_pipeline/audio_enhancer.py) -> speex auto gain / noise
suppression (HA: auto_gain * 300, noise_suppression * -15; same library versions as HA's manifest).

Windows: time ranges of the guided capture (1 m, 3 m, loud at 30-50 cm). Speech frames = 10 ms frames where HA's
microVAD (at gain_factor 32; its decisions do not depend on the gain, checked 8x..64x) says p > 0.5, pause frames
p < 0.05. An energy mask was tried first and caught footsteps/moving noise as speech (2026-11-15).
"""

import sys
import wave
from pathlib import Path

import numpy as np
from pymicro_vad import MicroVad
from pyspeex_noise import AudioProcessor

FS = 16000
N = 160  # 10 ms


def load(run: Path) -> np.ndarray:
    with wave.open(str(run / "output.wav"), "rb") as w:
        if w.getframerate() != FS or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{run}: expected 16 kHz mono 16 bit output.wav")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
    return x[: len(x) // N * N]


def db(v: float) -> float:
    return 20 * np.log10(max(v, 1e-9) / 32768)


def frames(x: np.ndarray) -> np.ndarray:
    return x.reshape(-1, N)


def frame_rms(x: np.ndarray) -> np.ndarray:
    f = frames(x)
    return np.sqrt((f**2).mean(axis=1))


def gain(x: np.ndarray, g: float) -> tuple[np.ndarray, int]:
    y = x * g
    clipped = int((np.abs(y) > 32767).sum())
    return np.clip(np.round(y), -32768, 32767), clipped


def vad_probs(x: np.ndarray) -> np.ndarray:
    v = MicroVad()
    b = x.astype("<i2").tobytes()
    return np.array([v.Process10ms(b[i : i + 2 * N]) for i in range(0, len(b), 2 * N)])


def speex(x: np.ndarray, auto_gain: int, ns: int) -> np.ndarray:
    if auto_gain == 0 and ns == 0:
        return x
    p = AudioProcessor(auto_gain * 300, ns * -15)
    b = x.astype("<i2").tobytes()
    out = b"".join(p.Process10ms(b[i : i + 2 * N]).audio for i in range(0, len(b), 2 * N))
    return np.frombuffer(out, dtype="<i2").astype(np.float64)


def masks(x: np.ndarray, windows: dict[str, tuple[float, float]]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    p = vad_probs(gain(x, 32)[0])
    out = {}
    for name, (a, b) in windows.items():
        idx = np.arange(int(a * 100), min(int(b * 100), len(p)))
        out[name] = (idx[p[idx] > 0.5], idx[p[idx] < 0.05])
    return out


def onsets(sp: np.ndarray) -> list[int]:
    """First frame of each speech burst (gap >= 300 ms before it)."""
    if len(sp) == 0:
        return []
    res = [int(sp[0])]
    for prev, cur in zip(sp[:-1], sp[1:]):
        if cur - prev >= 30:
            res.append(int(cur))
    return res


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wspec = "1m=3-20,3m=21-42,loud=42-58"
    for a in sys.argv[1:]:
        if a.startswith("--windows="):
            wspec = a.split("=", 1)[1]
    windows = {k: tuple(float(t) for t in v.split("-")) for k, v in (p.split("=") for p in wspec.split(","))}
    for run in (Path(a) for a in args):
        x = load(run)
        m = masks(x, windows)
        print(f"\n=== {run.name} ({len(x) / FS:.0f} s)")
        for name, (sp, pa) in m.items():
            r = frame_rms(x)
            print(f"  window {name}: speech {len(sp) / 100:.1f} s at {db(np.sqrt((r[sp]**2).mean())):.1f} dBFS, "
                  f"pause {len(pa) / 100:.1f} s at {db(np.sqrt((r[pa]**2).mean())):.1f} dBFS (before gain_factor), "
                  f"bursts {len(onsets(sp))}")
        print("  gain_factor: peak (dBFS) and clipped samples per window, VAD speech seconds (p > 0.5)")
        for gf in (8, 16, 24, 32, 48, 64):
            y, clipped = gain(x, gf)
            p = vad_probs(y)
            parts = []
            for name in windows:
                idx = np.arange(int(windows[name][0] * 100), min(int(windows[name][1] * 100), len(p)))
                pk = db(np.abs(frames(y)[idx]).max())
                cl = int((np.abs(frames(x * gf)[idx]) > 32767).sum())
                parts.append(f"{name}: pk {pk:5.1f} clip {cl:4d} vad {(p[idx] > 0.5).sum() / 100:4.1f}s")
            print(f"    {gf:3d}x | " + " | ".join(parts))
        print("  speex (gain_factor 32): speech / pause RMS after processing (dBFS), SNR gain vs. unprocessed (dB),")
        print("                          onset loss = first-100ms gain minus burst gain (dB, negative = onsets damped)")
        y, _ = gain(x, 32)
        ry = frame_rms(y)
        for ag in (0, 10, 31):
            for ns in (0, 1, 2, 3, 4):
                z = speex(y, ag, ns)
                rz = frame_rms(z)
                parts = []
                for name, (sp, pa) in m.items():
                    s_db = db(np.sqrt((rz[sp]**2).mean()))
                    p_db = db(np.sqrt((rz[pa]**2).mean()))
                    snr_in = db(np.sqrt((ry[sp]**2).mean())) - db(np.sqrt((ry[pa]**2).mean()))
                    loss = []
                    for o in onsets(sp):
                        burst = sp[(sp >= o) & (sp < o + 100)]
                        first = sp[(sp >= o) & (sp < o + 10)]
                        if len(first) and len(burst):
                            g_first = db(np.sqrt((rz[first]**2).mean())) - db(np.sqrt((ry[first]**2).mean()))
                            g_burst = db(np.sqrt((rz[burst]**2).mean())) - db(np.sqrt((ry[burst]**2).mean()))
                            loss.append(g_first - g_burst)
                    parts.append(f"{name}: {s_db:5.1f}/{p_db:5.1f} snr+{(s_db - p_db) - snr_in:4.1f} on {np.mean(loss) if loss else 0:5.1f}")
                print(f"    ag {ag:2d} ns {ns} | " + " | ".join(parts))


if __name__ == "__main__":
    main()
