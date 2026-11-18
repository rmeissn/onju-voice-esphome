# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "scipy"]
# ///
"""Echo delay and device AEC reduction per capture, independent of the stream alignment (docs/ECHO_CANCELLATION.md).

Per run: echo delay as the AEC sees it (reference vs processor_input: both are tapped in the same audio cycle with
the very buffers the AEC gets), the audio-loop cadence from the record timestamps (steady 32 ms or alternating
21/42 ms; it predicts the delay range), the device AEC reduction as band-power ratio processor_input /
processor_output over the playback frames (reference active, first second skipped) and the reference level.
Meant for music-only captures (e.g. the automated delay test): speech during playback lowers the reduction.
Validate after changes: uv run tools/make_synthetic_capture.py <dir>; synthetic-quiet (playback without speech)
must give delay 64 ms and device reduction 14.8 dB in every band (the runs with speech during playback come out
lower, as expected).

Usage: uv run tools/aec_delay.py captures/<run> [...]
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np
from scipy import signal

FS = 16000
BANDS = [(300, 1000), (1000, 3000), (3000, 7000)]
FRAME = 512  # 32 ms


def read_wav(p: Path) -> np.ndarray:
    w = wave.open(str(p))
    return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float)


def cadence(run: Path) -> str:
    recs = json.loads((run / "manifest.json").read_text()).get("records", [])
    rs = sorted([r for r in recs if r.get("point") == 0], key=lambda r: r["position"])
    if len(rs) < 10:
        return "n/a"
    d = np.diff([r["timestamp_us"] / 1000 for r in rs])
    if np.mean(((d > 18) & (d < 25)) | ((d > 38) & (d < 46))) > 0.6:
        return "21/42 ms"
    return "32 ms" if np.mean(np.abs(d - 32) < 4) > 0.6 else "mixed"


def analyze(run: Path) -> str:
    ref, pin, pout = (read_wav(run / f"{n}.wav") for n in ("reference", "processor_input", "processor_output"))
    n = min(len(ref), len(pin), len(pout)) // FRAME * FRAME
    ref, pin, pout = ref[:n], pin[:n], pout[:n]
    hp = signal.butter(4, 80, "highpass", fs=FS, output="sos")
    r, m = signal.sosfilt(hp, ref), signal.sosfilt(hp, pin)
    c = signal.correlate(m, r, mode="full", method="fft")[n - 1 : n - 1 + int(0.25 * FS)]
    delay = np.argmax(np.abs(c)) / FS * 1000
    lvl = 10 * np.log10(np.mean(ref.reshape(-1, FRAME) ** 2, axis=1) / 32768**2 + 1e-12)
    play = (lvl > lvl.max() - 30) & (lvl > -70)
    play[: FS // FRAME] = False  # first second: reference history / convergence
    mask = np.repeat(play, FRAME)
    if mask.sum() < FS:
        return f"{run.name:20s} {delay:5.0f}  {cadence(run):9s}  less than 1 s playback"
    red = []
    for lo, hi in BANDS:
        bp = signal.butter(4, [lo, hi], "bandpass", fs=FS, output="sos")
        a, b = signal.sosfilt(bp, pin)[mask], signal.sosfilt(bp, pout)[mask]
        red.append(10 * np.log10(np.mean(a**2) / max(np.mean(b**2), 1e-12)))
    return (f"{run.name:20s} {delay:5.0f}  {cadence(run):9s}  " + " / ".join(f"{x:5.1f}" for x in red)
            + f"   {np.mean(lvl[play]):6.1f}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    print(f"{'run':20s} delay  cadence    device reduction dB (300-1k / 1-3k / 3-7k)  ref dBFS")
    for run in map(Path, sys.argv[1:]):
        if (run / "reference.wav").exists():
            print(analyze(run))


if __name__ == "__main__":
    main()
