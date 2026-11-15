#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "scipy"]
# ///
"""Synthetic esp_audio_capture runs with known ground truth to validate tools/analyze_capture.py.

Usage: uv run tools/make_synthetic_capture.py <dir>  ->  <dir>/synthetic-* (prints expected values)
Then:  uv run tools/analyze_capture.py <dir>/synthetic-*
       uv run tools/analyze_capture.py --compare <dir>/synthetic-music-mixed <dir>/synthetic-music-b

Runs:
- quiet:        speech in silence + short "TTS reply"
- music:        speech during music (ducked at 16-19 s)
- silent:       speech only, reference is digital silence (no playback at all)
- music-offset: like music, but raw_mic starts one 32 ms block later than the other streams (capture started
                between the raw_mic and processor_input taps of one audio cycle, seen in a real capture)
- music-gaps:   like music, with records dropped on the device (ring buffer full): 25 % of raw_mic, plus short
                gaps in reference and processor_output; the receiver zero-fills them like capture_audio.py does
- walk:         talker walking around (no playback): per segment the left/right speech level differs
- music-mixed:  music (fixed track) ducked 14-22 s with speech in the pause (silence phase) and speech during
                music; the device AEC attenuates speech during playback by 6 dB (double-talk suppression)
- music-b:      the same track 3 s further on, device AEC 5 dB better: --compare with music-mixed
- clip:         like quiet, plus a 0.3 s burst above full scale in both mics (clipping check)
- corrupt-*:    like music, the right mic (raw ch1) with the raw data errors of the open mic bug (docs/AUDIO_INVESTIGATION.md):
                phase3 (like 05/12: phase 1 high byte shifted by one bit, phase 2 lower 12 bits shifted, low bits
                stay random), lowbits (like 163032: lower 12 bits small and biased), halved (like 09: exactly -6 dB)
                Naming from before the cause was known: the base level (-20 dBFS) is the read one BCLK late (mic_check.sh:
                LATE), "halved" is the correct read (CORRECT), phase3/lowbits are SLIPPING. 32-bit cases:
                tools/make_synthetic_raw32.py.

The manifest contains capture protocol records (position, frames, timestamp_us with common per-cycle jitter
and the real per-point processing order), so the record-based checks of analyze_capture.py are exercised too.

All signals are white noise band-limited to 100-7500 Hz, so band power = variance * bandwidth / 7400 Hz,
and every ratio between two such signals is the same in every band.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np
from scipy import signal

FS48, FS16, DUR = 48000, 16000, 30.0
DELAY_S = 0.064
AEC_FRAME = 512
CYCLE_S = 0.032  # one audio cycle = one record per point (512 frames @ 16 kHz, 1536 @ 48 kHz)
POINTS = ("raw_mic", "reference", "processor_input", "processor_output", "output")
INTRA_US = {"raw_mic": 0, "processor_input": 1700, "reference": 1900, "processor_output": 4300, "output": 4500}
DEFAULT_SPEECH = ((6.0, 7.0, 0.0, 0.0), (9.0, 10.2, 0.0, 0.0))  # (start s, end s, left dB, right dB offset)
rng = np.random.default_rng(1)


def bl_noise(n: int, fs: int, rms_db: float, gen: np.random.Generator | None = None) -> np.ndarray:
    sos = signal.butter(8, [100, 7500], "bandpass", fs=fs, output="sos")
    x = signal.sosfilt(sos, (gen or rng).standard_normal(n))
    return x / np.std(x) * 10 ** (rms_db / 20)


def write(path: Path, x: np.ndarray, fs: int) -> None:
    x = np.atleast_2d(x.T).T if x.ndim == 1 else x
    data = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[1] if data.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(data.tobytes())


def records(streams: dict, raw_offset: int, gaps: dict) -> tuple[list, dict]:
    """Protocol records like esp_audio_capture sends them. A dropped record still advances the position."""
    n_cycles = int(DUR / CYCLE_S)
    # own generator: the signal noise stays identical to the runs without records
    jitter = np.random.default_rng(2).uniform(-15000, 15000, n_cycles + 1)  # delivery jitter, common per cycle
    recs, meta = [], {}
    for name, (x, fs) in streams.items():
        frames = int(round(CYCLE_S * fs))
        start = raw_offset if name == "raw_mic" else 0
        dropped_cycles = {c for a, d in gaps.get(name, ()) for c in range(int(a / CYCLE_S), int((a + d) / CYCLE_S))}
        drops = gap = 0
        for seq, c in enumerate(range(start, start + len(x) // frames)):
            if c in dropped_cycles:
                drops += 1
                gap += frames
                continue
            recs.append({"v": 1, "point": POINTS.index(name), "seq": seq, "position": (c - start) * frames,
                         "timestamp_us": int(c * CYCLE_S * 1e6 + jitter[c] + INTRA_US[name]), "generation": 0,
                         "rate": fs, "frames": frames, "channels": x.shape[1] if x.ndim == 2 else 1, "bits": 16,
                         "dropped": drops, "bytes": 0})
        meta[name] = {"rate": fs, "channels": x.shape[1] if x.ndim == 2 else 1, "bits": 16, "gap_frames": gap,
                      "dropped_cycles": dropped_cycles, "frames": frames, "start": start}
    recs.sort(key=lambda r: r["timestamp_us"])
    return recs, meta


def corrupt_raw(x: np.ndarray, kind: str) -> np.ndarray:
    """Apply a raw I2S data error of the open mic bug to one mic (float in, float out, on the int16 bits)."""
    v = np.clip(np.round(x * 32767), -32768, 32767).astype(np.int64)
    match kind:
        case "phase3":  # every 3rd sample its own lower bits, low bits stay random (05, 12, after-error-162742)
            v[1::3] = ((v[1::3] >> 9) << 8) | (v[1::3] & 0xFF)
            v[2::3] = (v[2::3] & ~0xFFF) | ((v[2::3] & 0xFFF) >> 1)
        case "lowbits":  # top nibble kept, lower 12 bits small and biased to 0 (after-error-reboot-163032)
            low = v & 0xFFF
            v = (v & ~0xFFF) | ((low & (low >> 1)) >> 4)
        case "halved":  # one-bit shift, signal intact (09)
            v = v >> 1
        case _:
            raise ValueError(f"unknown corruption {kind}")
    return v / 32767


def p(x_db: float) -> float:
    return 10 ** (x_db / 10)


def r(x: float) -> float:
    return 10 * np.log10(x)


def make(out: Path, music: bool, silent: bool = False, raw_offset: int = 0, gaps: dict | None = None,
         seed: int | None = None, music_offset_s: float = 0.0, dev_red_db: float = 15.0, dtd_db: float = 0.0,
         speech: tuple = DEFAULT_SPEECH, duck: tuple = (16.0, 19.0), clip: bool = False,
         corrupt: str | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    gaps = gaps or {}
    n48 = int(DUR * FS48)
    noise_db, speech_db, ref_db = -80.0, -60.0, -50.0
    g_left_db, g_right_db = -0.0, -20.0  # echo gain mic vs reference: left 20 dB more echo than right
    nl, nr = bl_noise(n48, FS48, noise_db), bl_noise(n48, FS48, noise_db)
    sp_l, sp_r = np.zeros(n48), np.zeros(n48)
    for a, b, ol, orr in speech:  # one source signal per segment, reaching the mics with different levels
        s = bl_noise(int((b - a) * FS48), FS48, speech_db)
        sp_l[int(a * FS48) : int(b * FS48)] = s * 10 ** (ol / 20)
        sp_r[int(a * FS48) : int(b * FS48)] = s * 10 ** (orr / 20)
    ref48 = np.zeros(n48)
    if music:
        if seed is None:
            ref48[:] = bl_noise(n48, FS48, ref_db)
        else:  # fixed "track": the same content in every run with this seed, started music_offset_s later
            off = int(music_offset_s * FS48)
            ref48[:] = bl_noise(n48 + off, FS48, ref_db, np.random.default_rng(seed))[off:]
        ref48[int(duck[0] * FS48) : int(duck[1] * FS48)] = 0  # ducked
    elif not silent:
        ref48[int(13 * FS48) : int(15.5 * FS48)] = bl_noise(int(2.5 * FS48), FS48, ref_db)  # "TTS reply"
    d = int(DELAY_S * FS48)
    ref_del = np.concatenate([np.zeros(d), ref48[:-d]])
    gl, gr = 10 ** (g_left_db / 20), 10 ** (g_right_db / 20)
    left = nl + sp_l + gl * ref_del + 0.1  # + DC like the SPH0645
    right = nr + sp_r + gr * ref_del - 0.1
    if clip:  # loud close talker: above full scale in both mics
        burst = bl_noise(int(0.3 * FS48), FS48, 0.0)
        left[int(24 * FS48) : int(24.3 * FS48)] += burst
        right[int(24 * FS48) : int(24.3 * FS48)] += burst
    if corrupt:
        right = corrupt_raw(right, corrupt)
    raw = np.stack([left, right], axis=1)[raw_offset * int(CYCLE_S * FS48) :]  # late start: first block(s) missing
    ref16 = signal.resample_poly(ref48, 1, 3)
    avg_noise, avg_speech, avg_echo = (nl + nr) / 2, (sp_l + sp_r) / 2, (gl + gr) / 2 * ref_del
    if clip:
        avg_speech = avg_speech.copy()
        avg_speech[int(24 * FS48) : int(24.3 * FS48)] += burst
    att = np.where(ref_del != 0, 10 ** (dtd_db / 20), 1.0)  # device AEC: speech attenuation while echo is present
    pin = signal.resample_poly(avg_noise + avg_speech + avg_echo, 1, 3)
    pout = signal.resample_poly(avg_noise + avg_speech * att + avg_echo * 10 ** (-dev_red_db / 20), 1, 3)
    pout = np.concatenate([np.zeros(AEC_FRAME), pout[:-AEC_FRAME]])  # AEC frame delay
    streams = {"raw_mic": (raw, FS48), "reference": (ref16, FS16), "processor_input": (pin, FS16),
               "processor_output": (pout, FS16), "output": (pout, FS16)}
    recs, meta = records(streams, raw_offset, gaps)
    manifest_streams = {}
    for name, (x, fs) in streams.items():
        m = meta[name]
        x = x.copy()
        for c in m["dropped_cycles"]:  # zero-filled like capture_audio.py (positions are relative to the stream start)
            a = (c - m["start"]) * m["frames"]
            x[max(a, 0) : a + m["frames"]] = 0
        write(out / f"{name}.wav", x, fs)
        manifest_streams[name] = {k: m[k] for k in ("rate", "channels", "bits", "gap_frames")}
    (out / "manifest.json").write_text(json.dumps(
        {"protocol": 1, "complete": True, "streams": manifest_streams, "records": recs, "generation": 0}))

    # ground truth (same in every band, see docstring); avg: noise -3 dB (uncorrelated), speech = mean amplitude
    e = {"left": p(ref_db + g_left_db), "right": p(ref_db + g_right_db), "avg": p(ref_db) * ((gl + gr) / 2) ** 2}
    nn = {"left": p(noise_db), "right": p(noise_db), "avg": p(noise_db) / 2}
    s = p(speech_db)
    res = e["avg"] * 10 ** (-dev_red_db / 10) + nn["avg"]
    playing = (f"{DUR - (duck[1] - duck[0]):.1f} s" if music else ("0.0 s" if silent else "2.5 s"))
    print(f"--- {out.name}: expected (dB, identical in all bands; raw DC +-0.1 -> -20.0 dBFS)")
    print(f"  speaker playing ~{playing}" + ("" if silent else f", echo delay {DELAY_S * 1000:.0f} ms")
          + (f", raw_mic start offset {raw_offset} block(s)" if raw_offset else "")
          + (f", gaps in {', '.join(gaps)}" if gaps else "")
          + (f", music track +{music_offset_s:.1f} s" if music_offset_s else ""))
    if clip:
        print("  clipping: raw ch0 and ch1 peak 0.0 dBFS with clipped samples > 0 (burst 24.0-24.3 s); output clipped")

    def in_music(a: float, b: float) -> bool:
        return music and not (duck[0] <= a and b <= duck[1])

    for a, b, ol, orr in speech:
        amp = {"left": 10 ** (ol / 20), "right": 10 ** (orr / 20)}
        amp["avg"] = (amp["left"] + amp["right"]) / 2
        if in_music(a, b):
            print(f"  speech {a:.1f}-{b:.1f}s during playback:"
                  + "".join(f" {k} {r(s * amp[k] ** 2 / nn[k]):5.1f}" for k in nn)
                  + " (= ideal speech/residual) | before AEC"
                  + "".join(f" {k} {r(s * amp[k] ** 2 / (e[k] + nn[k])):5.1f}" for k in nn)
                  + f" | device speech/residual {r(s * amp['avg'] ** 2 * p(dtd_db) / res):5.1f}"
                  + f", speech preservation {dtd_db:5.1f}")
        else:
            print(f"  speech {a:.1f}-{b:.1f}s in silence: SNR"
                  + "".join(f" {k} {r(s * amp[k] ** 2 / nn[k]):5.1f}" for k in nn))
    if music or not silent:
        print("  playback without speech: coupling" + "".join(f" {k} {r(e[k] / p(ref_db)):5.1f}" for k in e)
              + " | ideal reduction" + "".join(f" {k} {r((e[k] + nn[k]) / nn[k]):5.1f}" for k in e)
              + " | ideal residual - reference" + "".join(f" {k} {r(nn[k] / p(ref_db)):5.1f}" for k in e))
        print(f"  device: reduction {r((e['avg'] + nn['avg']) / res):5.1f}, residual - reference {r(res / p(ref_db)):5.1f}"
              f", vs ideal {r(nn['avg'] / res):5.1f}")
    print("  band level offset: band power = total * bw/7400 -> 300-1k: "
          f"{r(700 / 7400):.1f} dB, 1-3k: {r(2000 / 7400):.1f} dB, 3-7k: {r(4000 / 7400):.1f} dB")


if __name__ == "__main__":
    base = Path(sys.argv[1])
    make(base / "synthetic-quiet", music=False)
    make(base / "synthetic-music", music=True)
    make(base / "synthetic-silent", music=False, silent=True)
    make(base / "synthetic-music-offset", music=True, raw_offset=1)
    raw_gaps = [(0.5 + 2.0 * i, 0.5) for i in range(15)]  # 15 x 0.5 s = 7.5 s = 25 %, one hits speech at 6.5 s
    make(base / "synthetic-music-gaps", music=True,
         gaps={"raw_mic": raw_gaps, "reference": [(3.0, 0.3), (21.0, 0.3)], "processor_output": [(24.0, 0.2)]})
    make(base / "synthetic-walk", music=False, silent=True,
         speech=((3.0, 5.0, 0.0, 0.0), (8.0, 10.0, 6.0, -6.0), (13.0, 15.0, -6.0, 6.0), (18.0, 20.0, 0.0, 0.0)))
    make(base / "synthetic-music-mixed", music=True, seed=7, duck=(14.0, 22.0), dtd_db=-6.0,
         speech=((6.0, 7.0, 0.0, 0.0), (9.0, 10.2, 0.0, 0.0), (16.5, 18.5, 0.0, 0.0), (24.0, 25.5, 0.0, 0.0)))
    make(base / "synthetic-music-b", music=True, seed=7, music_offset_s=3.0, dev_red_db=20.0)
    make(base / "synthetic-clip", music=False, clip=True)
    for kind in ("phase3", "lowbits", "halved"):
        make(base / f"synthetic-corrupt-{kind}", music=True, corrupt=kind)
        print({"phase3": "  raw ch1: LOOKS CORRUPTED (period 3), ch0 ok",
               "lowbits": "  raw ch1: LOOKS CORRUPTED (low bits), ch0 ok",
               "halved": "  raw ch0/ch1 ok (bit and phase check pass), raw ch1 DC -26.0 dBFS (mic_check.sh: -6 dB)"}[kind])
