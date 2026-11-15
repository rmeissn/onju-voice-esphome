#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "scipy"]
# ///
"""Analyze an esp_audio_capture run (raw_mic with both mic slots, reference, processor_input/output).

Usage: uv run tools/analyze_capture.py [--gain-factor 32] captures/<run> [captures/<run2> ...]
       uv run tools/analyze_capture.py --compare captures/<runA> captures/<runB> [...]
           (additionally compares the playback part of every run with the first one, music aligned via the
           reference: for AEC/mic variants recorded with the same track, started by hand a few seconds apart)
Validate the analysis itself: uv run tools/make_synthetic_capture.py <dir>, then analyze <dir>/synthetic-*
and compare with the expected values it prints.

Checks the raw mic data for I2S read corruption first (see docs/AUDIO_INVESTIGATION.md): with a working mic the
lowest bits are random (P(bit=1) ~0.5) because of the mic self-noise, and every 2nd/3rd/4th sample has the
same mean (a corrupted session can give each phase its own lower bits, e.g. capture 05, with random low bits).

Compares the mic options left only, right only and average(left, right) (= mic_mix: average) on the SAME
audio (both slots are in raw_mic), so no reproducible test signal is needed; with a single-mic config
(mic_channel) only the selected mic is in raw_mic. Sections:
- levels and clipping: raw mics (with DC), AEC input (input_gain headroom), output and output x gain_factor
- silence: self-noise, speech level and SNR per option (speech and background both from the silent phase);
  processing chain: SNR at raw / processor_input / processor_output / output of the device path
- playback without speech: reference level (fingerprint), echo coupling per option (echo - reference,
  self-noise subtracted), ideal linear AEC per option (upper bound), device AEC (reduction, residual,
  distance to the ideal)
- speech during playback (wake word situation; Assist ducks the music during the command): speech vs echo
  before / after an ideal linear AEC per option; device AEC speech/residual and speech preservation
  (speech in processor_output vs speech after the ideal AEC: negative = the AEC attenuates the talker)
- per speech window (<= 2 s, e.g. while walking around): the same ratios per option, median/IQR summary and
  paired differences (right - left etc.)
The device path (which option processor_input carries) is detected automatically by a least-squares match.

Method notes (lessons learned):
- All band powers use Welch with a fixed window DURATION (64 ms, 50% overlap) at every sample rate,
  so 48 kHz and 16 kHz signals are compared over the same time span.
- Everything is high-passed at 80 Hz (the SPH0645 raw data contains strong drift below 50 Hz).
- Mics are resampled 48k -> 16k (like the device) before comparing with the 16 kHz device streams.
- processor_output lags processor_input by one AEC frame (512 samples @ 16 kHz); it is shifted back.
- Speech is detected automatically on the echo-cancelled signal, not hand-picked.
- Playback needs an absolute floor too: a relative threshold alone marks digital silence (reference all
  zeros, no playback at all) as "playing" for the whole run.

Capture integrity (from the protocol records in manifest.json; the transport is TCP, so WiFi packet loss
does not create holes, but the device drops whole records when its ring buffer is full):
- Dropped records are zero-filled by capture_audio.py (alignment kept). Zeros would fake a "corrupted" raw
  mic (biased low bits), phantom speech and wrong echo/noise levels, so gaps (+ GAP_PAD_S) are excluded from
  every measurement; in raw_mic they are filled with the DC mean first (no step for the high-pass).
- Every stream starts at position 0, but the capture can start between two taps of one audio cycle; then a
  stream starts one 32 ms block later. Streams are aligned by the record timestamps (rounded to whole
  blocks; the taps of one cycle are only ~1-5 ms apart).
- Timestamps advancing faster than positions = audio lost before the capture point (e.g. I2S read).
- Speech and its background always come from the same phase (silence vs playback): mixing them biases the
  ratios (speech in the ducked pause has no residual echo, but the residual would be subtracted).
- The mic holds the echo of what played ~64 ms earlier. Where that part of the reference is missing (start of
  the capture, reference gaps), no canceller can remove the echo; these samples are excluded from all
  measurements once there is playback (echo_support). The echo path is estimated from valid pairs only, but
  applied to the full reference, so mic gaps do not cost extra samples.
"""

import argparse
import json
import warnings
from collections import defaultdict
import wave
from pathlib import Path

import numpy as np
from scipy import signal

FS = 16000
WIN_S = 0.064
BANDS = [(300, 1000), (1000, 3000), (3000, 7000)]
AEC_FRAME = 512  # processor_output delay vs processor_input in samples @ 16 kHz
FIR_N = 4096  # echo path model length (256 ms, centered -> +-128 ms delay range)
POINTS = ("raw_mic", "reference", "processor_input", "processor_output", "output")  # protocol point ids
PLAY_FLOOR_DB = -100  # reference frames below this are digital silence (1 LSB at 16 bit is ~-90 dBFS)
GAP_PAD_S = 0.1  # excluded around gaps (filter edges, echo delay ~64 ms)
GAP_WARN = 0.2  # warn if more than this fraction of the run is excluded
MIN_QUIET_S = 1.0  # silent background needed for noise/SNR (warning below 3 s)
MIN_PLAY_S = 1.5  # playback without speech needed for echo/residual levels
MIN_SPEECH_S = 2.0  # less speech per phase: warning (paired level differences: roughly +-1 dB with 2-5 s)
WIN_MAX_S = 2.0  # per-window comparison: speech segments are split into windows of at most this length
WIN_MIN_S = 0.3  # shorter windows / speech parts are not evaluated


def load(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    dtype = {2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(data, dtype=dtype).astype(np.float64) / np.iinfo(dtype).max
    return x.reshape(-1, ch), rate


def stream_layout(manifest: dict) -> dict:
    """Per stream from the protocol records: leading samples to skip for a common start, gaps in stream samples
    (dropped records, zero-filled by the receiver) and timestamp-vs-position drift. Empty if records lack fields."""
    keys = ("point", "position", "frames", "timestamp_us", "rate")
    recs = defaultdict(list)
    for r in manifest.get("records", []):
        if all(k in r for k in keys):
            recs[POINTS[r["point"]]].append(r)
    if not recs:
        return {}
    for rs in recs.values():
        rs.sort(key=lambda r: r["position"])
    # time of sample 0 of each stream; the per-cycle delivery jitter is common to all points, the median removes it
    t0 = {p: np.median([r["timestamp_us"] / 1e6 - r["position"] / r["rate"] for r in rs]) for p, rs in recs.items()}
    cycles = {round(r["frames"] / r["rate"], 6) for rs in recs.values() for r in rs}
    cycle = max(cycles)
    if len(cycles) > 1:
        print(f"WARNING record durations differ ({sorted(cycles)} s), stream alignment may be wrong")
    latest = max(t0.values())
    layout = {}
    for p, rs in recs.items():
        rate = rs[0]["rate"]
        k = (latest - t0[p]) / cycle
        if abs(k - round(k)) > 0.25:
            print(f"WARNING {p}: start offset {k:.2f} blocks is ambiguous, alignment may be off by one block")
        gaps, end = [], 0
        for r in rs:
            if r["position"] > end:
                gaps.append((end, r["position"]))
            end = r["position"] + r["frames"]
        t = np.array([r["timestamp_us"] / 1e6 - r["position"] / rate for r in rs])
        m = min(50, len(t) // 2)
        drift = float(np.median(t[-m:]) - np.median(t[:m])) if m else 0.0
        layout[p] = {"rate": rate, "skip_blocks": round(k), "skip": round(round(k) * cycle * rate), "gaps": gaps,
                     "drift": drift, "cycle": cycle}
    return layout


def report_integrity(manifest: dict, layout: dict) -> None:
    if not manifest.get("complete", False):
        print(f"WARNING capture incomplete ({manifest.get('error', 'no error recorded')}): the end is missing")
    for p, info in layout.items():
        if info["skip_blocks"]:
            print(f"start alignment: {p} trimmed by {info['skip_blocks']} block(s) "
                  f"({info['skip_blocks'] * info['cycle'] * 1000:.0f} ms), it started earlier than the others")
        if abs(info["drift"]) > info["cycle"] / 2:
            print(f"WARNING {p}: timestamps drift {info['drift'] * 1000:+.0f} ms against the sample positions "
                  "(audio lost before the capture point, e.g. I2S read, or irregular cycles)")
        if gap := sum(b - a for a, b in info["gaps"]):
            print(f"gaps in {p}: {len(info['gaps'])} ({gap / info['rate']:.2f} s), excluded from all measurements")
    if not layout and sum(s.get("gap_frames", 0) for s in manifest.get("streams", {}).values()):
        print("WARNING gaps reported, but the records lack positions: gaps cannot be excluded, results unreliable")


def load_stream(run: Path, point: str, layout: dict) -> tuple[np.ndarray, int, np.ndarray]:
    """Samples aligned to the common start, gaps filled with the DC mean, plus the per-sample validity."""
    x, rate = load(run / f"{point}.wav")
    valid = np.ones(len(x), bool)
    if info := layout.get(point):
        for a, b in info["gaps"]:
            valid[a:b] = False
        x, valid = x[info["skip"] :], valid[info["skip"] :]
    if not valid.all():
        x[~valid] = x[valid].mean(axis=0) if valid.any() else 0
    return x, rate, valid


def to_fs(valid: np.ndarray, rate: int) -> np.ndarray:
    """Validity at FS: a FS sample is valid only if all source samples it covers are."""
    f = rate // FS
    return valid[: len(valid) // f * f].reshape(-1, f).all(axis=1)


RIGHT_FIRST = False  # --right-first: raw_mic is [right, left]
PHASE_PERIODS = (2, 3, 4)
PHASE_MIN_LSB = 5.0  # healthy captures: < 0.5 LSB; corrupted period-3 captures: 50..1900 LSB
PHASE_MIN_Z = 10.0


def phase_spread(v: np.ndarray, period: int) -> tuple[float, float]:
    """Spread (max - min) of the per-phase means for samples taken every `period` samples, in LSB, and its noise
    level. A periodic corruption (e.g. 05/12: every 3rd sample has other lower bits) gives each phase its own
    DC; healthy data has the same mean in every phase. The noise level uses the sample-to-sample differences
    WITHIN each phase, so neither the corruption itself nor the slow DC drift of the SPH0645 inflates it."""
    n = len(v) // period * period
    phases = v[:n].reshape(-1, period).astype(np.float64)
    means = phases.mean(axis=0)
    noise = float(np.mean([np.std(np.diff(phases[:, k])) for k in range(period)])) / np.sqrt(len(phases))
    return float(means.max() - means.min()), noise


def check_raw(raw: np.ndarray) -> bool:
    """Flag I2S read corruption: biased low bits (06, 12, 163032) or a periodic per-phase offset (05, 11, 12),
    see docs/AUDIO_INVESTIGATION.md. The correct read and the read one BCLK late (6 dB apart) both pass; mic_check.sh
    tells them apart by the DC (with 32-bit raw tools/r32_verdict.py decides exactly)."""
    any_corrupt = False
    for c in range(raw.shape[1]):
        v = np.round(raw[:, c] * 32767).astype(int)
        p_bits = [np.mean((v >> b) & 1) for b in range(3)]
        # healthy captures: 0.49..0.52 (synthetic up to 0.52); corrupted: 0.22..0.42 (06: 0.33/0.25/0.22, 11: 0.42)
        bits_bad = max(abs(p - 0.5) for p in p_bits) > 0.05
        spreads = {per: phase_spread(v, per) for per in PHASE_PERIODS}
        phase_bad = [per for per, (s, nz) in spreads.items() if s > max(PHASE_MIN_LSB, PHASE_MIN_Z * nz)]
        corrupt = bits_bad or bool(phase_bad)
        reasons = (["low bits"] if bits_bad else []) + [f"period {per}" for per in phase_bad]
        print(
            f"raw ch{c}: range {v.min()}..{v.max()}, std {v.std():.0f}, P(bit=1) bits 0-2 "
            + " ".join(f"{p:.2f}" for p in p_bits)
            + ", phase spread " + "/".join(f"{spreads[per][0]:.1f}" for per in PHASE_PERIODS)
            + f" LSB (period {'/'.join(map(str, PHASE_PERIODS))})"
            + (f"  <-- LOOKS CORRUPTED (I2S read: {', '.join(reasons)})" if corrupt else "  ok")
        )
        any_corrupt |= corrupt
    return any_corrupt


def highpass(x: np.ndarray, rate: int, fc: float = 80) -> np.ndarray:
    return signal.sosfiltfilt(signal.butter(4, fc, "highpass", fs=rate, output="sos"), x - x.mean())


def band_power(x: np.ndarray, rate: int) -> np.ndarray:
    n = int(WIN_S * rate)
    f, p = signal.welch(x, rate, nperseg=n, noverlap=n // 2)
    df = f[1] - f[0]
    return np.array([np.sum(p[(f >= lo) & (f < hi)]) * df for lo, hi in BANDS])


def db(p) -> np.ndarray:
    return 10 * np.log10(np.maximum(p, 1e-20))


def frame_db(x: np.ndarray, frame_s: float = 0.02) -> np.ndarray:
    n = int(frame_s * FS)
    k = len(x) // n
    return db(np.mean(x[: k * n].reshape(k, n) ** 2, axis=1))


def segments(mask: np.ndarray, frame_s: float = 0.02, gap_s: float = 0.3, min_s: float = 0.15) -> list:
    segs, start = [], None
    for i, m in enumerate(np.append(mask, False)):
        if m and start is None:
            start = i
        elif not m and start is not None:
            segs.append([start, i])
            start = None
    merged = []
    for s in segs:
        if merged and (s[0] - merged[-1][1]) * frame_s < gap_s:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(a * frame_s, b * frame_s) for a, b in merged if (b - a) * frame_s >= min_s]


def mask_from(segs: list, n: int, pad_s: float) -> np.ndarray:
    m = np.zeros(n, bool)
    for a, b in segs:
        m[max(0, int((a - pad_s) * FS)) : int((b + pad_s) * FS)] = True
    return m


def echo_delay(ref: np.ndarray, mic: np.ndarray, max_s: float = 0.25) -> int:
    """Bulk delay mic vs reference in samples (cross-correlation peak, 0..max_s)."""
    m = int(max_s * FS)
    c = signal.correlate(mic, ref, mode="full", method="fft")[len(ref) - 1 : len(ref) - 1 + m]
    return int(np.argmax(np.abs(c)))


def echo_estimate(ref: np.ndarray, mic: np.ndarray, delay: int, valid: np.ndarray | None = None) -> np.ndarray:
    """Linear echo of `ref` in `mic`: reference pre-aligned by the bulk delay (otherwise Welch spectra
    are biased by (1 - delay/window)), then Wiener FIR from cross/auto spectra (+-128 ms residual range).
    With `valid`, the FIR is estimated with invalid samples blanked in both signals (a blanked mic alone would
    bias it low) but applied to the full reference."""
    r = np.concatenate([np.zeros(delay), ref[: len(ref) - delay]])
    w = np.ones(len(r)) if valid is None else valid
    rw = np.concatenate([np.zeros(delay), (ref * w)[: len(ref) - delay]]) * w
    _, sxy = signal.csd(rw, mic * w, FS, nperseg=FIR_N, noverlap=FIR_N * 3 // 4)
    _, sxx = signal.welch(rw, FS, nperseg=FIR_N, noverlap=FIR_N * 3 // 4)
    h = np.roll(np.fft.irfft(sxy / np.maximum(sxx, 1e-20), FIR_N), FIR_N // 2)
    return signal.fftconvolve(r, h)[FIR_N // 2 : FIR_N // 2 + len(r)]


def echo_support(ref_valid: np.ndarray, delay: int) -> np.ndarray:
    """Mic samples whose echo model input (reference at t - delay +- FIR_N/2) is inside the capture and valid."""
    h = FIR_N // 2
    bad = np.concatenate([np.ones(h, bool), ~ref_valid, np.ones(h, bool)])  # before/after the capture: unknown
    c = np.concatenate([[0], np.cumsum(bad)])
    t = np.arange(len(ref_valid))
    lo, hi = np.clip(t - delay, 0, len(bad)), np.clip(t - delay + 2 * h, 0, len(bad))
    return c[hi] - c[lo] == 0


def fmt(v) -> str:
    return " ".join("   n/a" if np.isnan(x) else f"{x:6.1f}" for x in v)


def ratio_db(sig_plus_bg: np.ndarray, bg: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """10log10((sig_plus_bg - bg) / ref); n/a if the signal is not at least 1 dB above the background."""
    sig = sig_plus_bg - bg
    return np.where(sig_plus_bg > bg * 1.26, db(np.maximum(sig, 1e-20)) - db(ref), np.nan)


def peak_db(x: np.ndarray) -> float:
    return float(db(np.max(np.abs(x)) ** 2)) if len(x) else np.nan


def clipped(x: np.ndarray) -> int:
    return int(np.sum(np.abs(np.round(x * 32767)) >= 32767))


def iqr(a: np.ndarray) -> str:
    lo, hi = np.nanpercentile(a, 25, axis=0), np.nanpercentile(a, 75, axis=0)
    return " ".join("   n/a    " if np.isnan(x) else f"{x:5.1f}..{y:<4.1f}" for x, y in zip(lo, hi))


def detect_option(pin: np.ndarray, mics: dict, valid: np.ndarray) -> tuple:
    """Which mic option the device processes. Two mics: least-squares fit processor_input = a*left + b*right
    (a matching of single options fails: the left mic picks up the own speaker ~20 dB louder, so "left" and
    "avg" explain nearly the same energy). Lag search +-32 samples (the device resampler has its own delay).
    Returns (name or None, gain vs that option, residual dB, lag, (a, b))."""
    ref_sig = mics.get("avg", next(iter(mics.values())))
    c = signal.correlate(pin * valid, ref_sig * valid, mode="full", method="fft")
    mid = len(ref_sig) - 1
    lag = int(np.argmax(np.abs(c[mid - 32 : mid + 33]))) - 32
    pv = pin[valid]
    if "left" in mics and "right" in mics:
        x = np.stack([np.roll(mics["left"], lag)[valid], np.roll(mics["right"], lag)[valid]], axis=1)
        (a, b), *_ = np.linalg.lstsq(x, pv, rcond=None)
        res = float(db(np.sum((pv - x @ np.array([a, b])) ** 2) / max(np.sum(pv**2), 1e-30)))
        share = a / (a + b) if a + b > 0 else np.nan
        name = "avg" if 0.35 <= share <= 0.65 else ("left" if share > 0.85 else ("right" if share < 0.15 else None))
        g = np.nan
        if name:  # gain vs the detected option alone (the two weights are biased by resampler differences at HF)
            xs = np.roll(mics[name], lag)[valid]
            g = float(np.dot(pv, xs) / max(np.dot(xs, xs), 1e-30))
        return (name if res < -10 else None, float(g), res, lag, (float(a), float(b)))
    k, x = next(iter(mics.items()))
    xs = np.roll(x, lag)[valid]
    g = float(np.dot(pv, xs) / max(np.dot(xs, xs), 1e-30))
    res = float(db(np.sum((pv - g * xs) ** 2) / max(np.sum(pv**2), 1e-30)))
    return (k if res < -10 else None, g, res, lag, (g,))


def prepare(run: Path) -> dict:
    manifest = json.loads((run / "manifest.json").read_text())
    drops = max((r.get("dropped", 0) for r in manifest["records"]), default=0)
    gaps = sum(s.get("gap_frames", 0) for s in manifest["streams"].values())
    generations = sorted({r["generation"] for r in manifest["records"] if "generation" in r})
    print(f"\n=== {run.name}  (complete={manifest['complete']}, gap frames={gaps}, dropped={drops}, "
          f"generation {'/'.join(map(str, generations)) or '?'})")
    layout = stream_layout(manifest)
    report_integrity(manifest, layout)

    raw, rr, raw_valid = load_stream(run, "raw_mic", layout)
    corrupt = check_raw(raw[raw_valid])
    d = {"name": run.name, "raw": raw[raw_valid], "corrupt": corrupt}
    if corrupt:
        return d
    if raw.shape[1] == 2:  # rx_mic_slots: [left, right] -> both mics captured ([right, left] with --right-first)
        lc, rc = (1, 0) if RIGHT_FIRST else (0, 1)
        mics48 = {"left": raw[:, lc], "right": raw[:, rc], "avg": raw.mean(axis=1)}
    else:  # single mic config (mic_channel) -> only the selected mic is captured
        mics48 = {"mic": raw[:, 0]}
    mics = {k: highpass(signal.resample_poly(highpass(v, rr), 1, rr // FS), FS) for k, v in mics48.items()}
    st = {p: load_stream(run, p, layout) for p in ("reference", "processor_input", "processor_output", "output")}
    ref, ref_valid = highpass(st["reference"][0][:, 0], FS), st["reference"][2]
    pin_raw, pin_valid = st["processor_input"][0][:, 0], st["processor_input"][2]
    pin = highpass(pin_raw, FS)
    pout, pout_valid = highpass(st["processor_output"][0][:, 0], FS)[AEC_FRAME:], st["processor_output"][2][AEC_FRAME:]
    out_raw, out_valid = st["output"][0][AEC_FRAME:, 0], st["output"][2][AEC_FRAME:]  # output = processor_output x mic_gain
    out = highpass(out_raw, FS)
    valids = (to_fs(raw_valid, rr), ref_valid, pin_valid, pout_valid, out_valid)
    n = min(len(ref), len(pin), len(pout), len(out), *(len(v) for v in mics.values()), *(len(v) for v in valids))
    ref, pin, pout, out, pin_raw, out_raw = ref[:n], pin[:n], pout[:n], out[:n], pin_raw[:n], out_raw[:n]
    mics = {k: v[:n] for k, v in mics.items()}
    valid = np.logical_and.reduce([v[:n] for v in valids])
    if not valid.all():
        pad = int(GAP_PAD_S * FS)
        valid = np.convolve(~valid, np.ones(2 * pad + 1), "same") < 0.5
        lost = 1 - valid.mean()
        print(f"excluded (gaps of any stream + {GAP_PAD_S}s): {lost * 100:.1f} % of the run"
              + ("  <-- WARNING results less reliable" if lost > GAP_WARN else ""))
    ref_frames = frame_db(ref)
    playing = (ref_frames > np.max(ref_frames) - 50) & (ref_frames > PLAY_FLOOR_DB)
    play_segs = segments(playing, gap_s=0.5)
    print(f"speaker playing (reference active): {sum(b - a for a, b in play_segs):.1f}s of {n / FS:.1f}s")

    # ideal linear AEC per option; speech is detected on the best residual (right mic, least echo)
    resid = {}
    delay = echo_delay(ref * valid, next(iter(mics.values())) * valid) if play_segs else 0
    if play_segs:
        print(f"echo delay mic vs reference: {delay} samples = {delay / FS * 1000:.0f} ms")
        valid_fit = valid
        valid = valid & echo_support(ref_valid[:n], delay)
        print(f"echo not cancellable (reference history missing: capture start, reference gaps): "
              f"{(valid_fit & ~valid).sum() / FS:.2f}s excluded")
    for k, x in mics.items():
        resid[k] = x - echo_estimate(ref, x, delay, valid_fit) if play_segs else x
    fn = int(0.02 * FS)
    fvalid = valid[: n // fn * fn].reshape(-1, fn).all(axis=1)  # per 20 ms analysis frame
    det = frame_db(signal.sosfiltfilt(signal.butter(4, [300, 4000], "bandpass", fs=FS, output="sos"),
                                      resid.get("right", next(iter(resid.values())))))
    # separate baselines with and without playback: residual echo of a short TTS reply must not count as speech
    k = min(len(det), len(playing), len(fvalid))
    det, play_f, fv = det[:k], playing[:k], fvalid[:k]
    base = np.where(
        play_f,
        np.percentile(det[play_f & fv], 20) if (play_f & fv).any() else 0,
        np.percentile(det[~play_f & fv], 20) if (~play_f & fv).any() else 0,
    )
    speech_segs = segments((det > base + 10) & fv)
    print("speech segments (s): " + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in speech_segs))

    play, play_pad = mask_from(play_segs, n, 0.0), mask_from(play_segs, n, 0.3)
    speech_mask = mask_from(speech_segs, n, 0.3)
    speech_core = mask_from(speech_segs, n, 0.0) & valid  # speech levels: only the detected speech itself
    d.update(n=n, mics=mics, resid=resid, ref=ref, pin=pin, pout=pout, out=out, pin_raw=pin_raw, out_raw=out_raw,
             valid=valid, play=play, play_segs=play_segs, speech_segs=speech_segs, speech_core=speech_core,
             quiet=~speech_mask & ~play_pad & valid,  # silent background
             music=~speech_mask & play & valid,  # playback without speech
             sp_quiet=speech_core & ~play_pad, sp_play=speech_core & play)
    cfg = detect_option(pin, mics, valid)
    weights = " ".join(f"{w:.2f}" for w in cfg[4])
    if cfg[0]:
        print(f"device path: {cfg[0]} (processor_input = weights {weights} x mics, residual {cfg[2]:.1f} dB, "
              f"gain {20 * np.log10(abs(cfg[1])):+.1f} dB, lag {cfg[3]} samples)")
        d["cfg"] = cfg
    else:
        print(f"WARNING device path unknown (weights {weights}, residual {cfg[2]:.1f} dB): device comparisons skipped")
        d["cfg"] = None
    d["noise"] = ({k: band_power(x[d["quiet"]], FS) for k, x in resid.items()}
                  if d["quiet"].sum() >= MIN_QUIET_S * FS else None)
    d["r_play"] = ({k: band_power(x[d["music"]], FS) for k, x in resid.items()}
                   if play_segs and d["music"].sum() >= MIN_PLAY_S * FS else None)
    return d


def report_levels(d: dict, gain: float) -> None:
    print("-- levels and clipping (dBFS)")
    raw = d["raw"]
    for c in range(raw.shape[1]):
        x = raw[:, c]
        dc = x.mean()
        print(f"  raw ch{c}: DC {float(db(dc**2)):6.1f}, peak {peak_db(x):6.1f} (without DC {peak_db(x - dc):6.1f})"
              f", clipped samples {clipped(x)}")
    if d["corrupt"]:
        return
    v, sc = d["valid"], d["speech_core"]
    pin, o = d["pin_raw"][v], d["out_raw"]
    print(f"  processor_input (AEC input, after input_gain): peak {peak_db(pin):6.1f}, clipped samples {clipped(pin)}")
    g = 20 * np.log10(gain)
    sp_rms = float(db(np.mean(d["out"][sc] ** 2))) if sc.any() else np.nan
    sp_pk = peak_db(o[sc]) if sc.any() else np.nan
    run_pk = peak_db(o[v])
    print(f"  output (after mic_gain): peak {run_pk:6.1f}, clipped samples {clipped(o[v])}; "
          f"speech RMS {sp_rms:6.1f}, speech peak {sp_pk:6.1f}")
    head = -(run_pk + g)
    print(f"  output x gain_factor {gain:g} (+{g:.1f} dB) = what MWW/Assist get: speech RMS {sp_rms + g:6.1f}, "
          f"speech peak {sp_pk + g:6.1f}, run peak {run_pk + g:6.1f} -> headroom {head:.1f} dB"
          + ("  <-- WARNING clips" if head < 0 else ("  <-- less than 6 dB" if head < 6 else "")))
    if head < 0:  # where: own playback (residual echo, TTS, wake sound) or the talker
        clip = (np.abs(o) * gain >= 1.0) & v
        play = d["play"]
        print(f"    would clip at x{gain:g}: {int(clip.sum())} samples: {int((clip & sc).sum())} in speech, "
              f"{int((clip & play & ~sc).sum())} during own playback without speech, "
              f"{int((clip & ~play & ~sc).sum())} elsewhere; first at {np.argmax(clip) / FS:.2f}s")


def report_silence(d: dict) -> None:
    q, sq = d["quiet"], d["sp_quiet"]
    qs, ss = q.sum() / FS, sq.sum() / FS
    print(f"-- silence (no playback): background {qs:.1f}s, speech {ss:.1f}s")
    if d["noise"] is None:
        print(f"  WARNING less than {MIN_QUIET_S:.0f}s silent background: noise and SNR not measured")
        return
    if qs < 3:
        print("  WARNING less than 3s background: noise level uncertain")
    if WIN_MIN_S <= ss < MIN_SPEECH_S:
        print(f"  WARNING less than {MIN_SPEECH_S:.0f}s speech: SNR values uncertain")
    for k, x in d["resid"].items():
        nz = d["noise"][k]
        line = f"  {k:5s} self-noise {fmt(db(nz))} (broadband {float(db(np.mean(x[q] ** 2))):6.1f})"
        if ss >= WIN_MIN_S:
            sp = band_power(x[sq], FS)
            line += f" | speech {fmt(db(sp))} | SNR {fmt(ratio_db(sp, nz, nz))}"
        print(line)
    if d["cfg"] and ss >= WIN_MIN_S:
        k = d["cfg"][0]
        print(f"  processing chain of the device path ({k}): SNR per stage | loss vs the raw mic signal")
        base = None
        for name, x in ((f"{k} (raw)", d["mics"][k]), ("processor_input", d["pin"]),
                        ("processor_output", d["pout"]), ("output", d["out"])):
            nz, sp = band_power(x[q], FS), band_power(x[sq], FS)
            snr = ratio_db(sp, nz, nz)
            base = snr if base is None else base
            print(f"    {name:17s} {fmt(snr)} | {fmt(base - snr)}")


def playback_metrics(d: dict, m: np.ndarray) -> dict:
    """Echo/AEC levels on the samples `m` (playback without speech), relative to the reference level."""
    ref_p = band_power(d["ref"][m], FS)
    res = {"ref": ref_p, "opt": {}}
    for k, x in d["mics"].items():
        e, r = band_power(x[m], FS), band_power(d["resid"][k][m], FS)
        coup = ratio_db(e, d["noise"][k], ref_p) if d["noise"] else db(e) - db(ref_p)
        res["opt"][k] = {"coupling": coup, "ideal_red": db(e) - db(r), "ideal_res": db(r) - db(ref_p)}
    if d["cfg"]:
        k, g = d["cfg"][0], d["cfg"][1]
        e_i, r_o = band_power(d["pin"][m], FS), band_power(d["pout"][m], FS)
        red = db(e_i) - db(r_o)
        res["dev"] = {"red": red, "res": db(r_o) - db(g**2) - db(ref_p), "vs_ideal": red - res["opt"][k]["ideal_red"]}
    return res


def report_playback(d: dict) -> None:
    if not d["play_segs"]:
        return
    print(f"-- playback without speech (music/TTS): {d['music'].sum() / FS:.1f}s")
    if d["r_play"] is None:
        print(f"  WARNING less than {MIN_PLAY_S}s: echo levels not measured")
        return
    pm = playback_metrics(d, d["music"])
    print(f"  reference level {fmt(db(pm['ref']))}  (fingerprint: same track and volume -> same values)")
    for k, v in pm["opt"].items():
        print(f"  {k:5s} echo coupling (echo - reference) {fmt(v['coupling'])} | ideal lin. AEC: reduction "
              f"{fmt(v['ideal_red'])}, residual - reference {fmt(v['ideal_res'])}")
    if "dev" in pm:
        v = pm["dev"]
        print(f"  device AEC ({d['cfg'][0]}): reduction {fmt(v['red'])}, residual - reference {fmt(v['res'])}, "
              f"reduction vs ideal {fmt(v['vs_ideal'])}")
    if not d["noise"]:
        print("  (no silent background: echo coupling includes the self-noise)")


def report_speech_playback(d: dict) -> None:
    sp = d["sp_play"]
    ss = sp.sum() / FS
    if not d["play_segs"] or ss < WIN_MIN_S:
        return
    print(f"-- speech during playback: {ss:.1f}s (wake word situation)")
    if ss < MIN_SPEECH_S:
        print(f"  WARNING less than {MIN_SPEECH_S:.0f}s speech: values uncertain")
    if d["r_play"] is None:
        print("  no playback without speech as background: skipped")
        return
    m = d["music"]
    for k, x in d["mics"].items():
        e, r, s = band_power(x[m], FS), d["r_play"][k], band_power(d["resid"][k][sp], FS)
        print(f"  {k:5s} speech/echo before AEC {fmt(ratio_db(s, r, e))} | speech/residual after ideal lin. AEC "
              f"{fmt(ratio_db(s, r, r))}")
    if d["cfg"]:
        k, g = d["cfg"][0], d["cfg"][1]
        s_o, r_o = band_power(d["pout"][sp], FS), band_power(d["pout"][m], FS)
        s_c, r_c = band_power(d["resid"][k][sp], FS), d["r_play"][k]
        ideal_speech = np.where(s_c > r_c * 1.26, (s_c - r_c) * g**2, np.nan)
        print(f"  device AEC ({k}): speech/residual {fmt(ratio_db(s_o, r_o, r_o))} | speech preservation vs ideal "
              f"{fmt(ratio_db(s_o, r_o, ideal_speech))} (0 = intact, negative = AEC attenuates the talker)")


def speech_windows(d: dict) -> list:
    wins = []
    for a, b in d["speech_segs"]:
        k = max(1, int(np.ceil((b - a) / WIN_MAX_S - 1e-9)))
        for i in range(k):
            wa, wb = a + (b - a) * i / k, a + (b - a) * (i + 1) / k
            w = np.zeros(d["n"], bool)
            w[int(wa * FS) : int(wb * FS)] = True
            wq, wp = w & d["sp_quiet"], w & d["sp_play"]
            phase, m = ("silence", wq) if wq.sum() >= wp.sum() else ("playback", wp)
            bg = d["noise"] if phase == "silence" else d["r_play"]
            if m.sum() < WIN_MIN_S * FS or bg is None:
                continue
            wins.append((wa, wb, phase, {o: ratio_db(band_power(d["resid"][o][m], FS), bg[o], bg[o]) for o in d["mics"]}))
    return wins


def report_windows(d: dict) -> None:
    wins = speech_windows(d)
    if not wins:
        return
    keys = list(d["mics"])
    print("-- per speech window: SNR (silence) / speech-to-residual after ideal lin. AEC (playback), dB per band")
    print("  time (s)     phase    " + "".join(f"| {o:20s}" for o in keys))
    for wa, wb, ph, vals in wins:
        print(f"  {wa:5.1f}-{wb:5.1f}  {ph:8s} " + "".join(f"|{fmt(vals[o])} " for o in keys))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-n/a columns
        for ph in ("silence", "playback"):
            sel = [w[3] for w in wins if w[2] == ph]
            if not sel:
                continue
            print(f"  summary {ph}: {len(sel)} windows, median [25..75 %]"
                  + ("  <-- WARNING fewer than 3 windows" if len(sel) < 3 else ""))
            for o in keys:
                a = np.array([v[o] for v in sel])
                print(f"    {o:12s} {fmt(np.nanmedian(a, axis=0))}  [{iqr(a)}]")
            for x, y in (("right", "left"), ("right", "avg"), ("avg", "left")):
                if x in keys and y in keys:
                    a = np.array([v[x] - v[y] for v in sel])
                    ok = ~np.isnan(a[:, 1])
                    print(f"    {x + '-' + y:12s} {fmt(np.nanmedian(a, axis=0))}  [{iqr(a)}]  "
                          f"{x} better in {int(np.sum(a[ok, 1] > 0))}/{int(ok.sum())} windows (1-3 kHz)")


def report(d: dict, gain: float) -> None:
    report_levels(d, gain)
    if d["corrupt"]:
        print("raw data corrupted: mic comparison and AEC analysis skipped (they would be meaningless)")
        return
    report_silence(d)
    report_playback(d)
    report_speech_playback(d)
    report_windows(d)


def compare(a: dict, b: dict) -> None:
    """Playback part of two runs with the same track: aligned via the reference, evaluated on the same music."""
    print(f"\n=== compare playback: A = {a['name']}, B = {b['name']}")
    for d in (a, b):
        if d["corrupt"] or not d["play_segs"] or d["r_play"] is None:
            print(f"  {d['name']}: no usable playback without speech, comparison skipped")
            return
    ra, rb = a["ref"] * a["valid"], b["ref"] * b["valid"]
    c = signal.correlate(rb, ra, mode="full", method="fft")
    lag = int(np.argmax(c)) - (len(ra) - 1)  # rb[t + lag] ~ ra[t]
    ta = np.arange(max(0, -lag), min(len(ra), len(rb) - lag))
    both = ta[a["play"][ta] & b["play"][ta + lag]]  # ducking/pauses differ between runs: only where both play
    corr = float(np.corrcoef(ra[both], rb[both + lag])[0, 1]) if len(both) > FS else 0.0
    print(f"  music offset: B[t {lag / FS:+.2f}s] = A[t], correlation {corr:.2f}"
          + ("  <-- WARNING not the same music: comparison invalid" if corr < 0.9 else ""))
    common = a["music"][ta] & b["music"][ta + lag]
    print(f"  common playback without speech in both: {common.sum() / FS:.1f}s")
    if corr < 0.9 or common.sum() < MIN_PLAY_S * FS:
        return
    ma, mb = np.zeros(a["n"], bool), np.zeros(b["n"], bool)
    ma[ta], mb[ta + lag] = common, common
    pa, pb = playback_metrics(a, ma), playback_metrics(b, mb)

    def row(label: str, va: np.ndarray, vb: np.ndarray) -> None:
        print(f"  {label:34s} A {fmt(va)} | B {fmt(vb)} | B-A {fmt(vb - va)}")

    row("reference level (same volume?)", db(pa["ref"]), db(pb["ref"]))
    for k in pa["opt"]:
        if k in pb["opt"]:
            row(f"{k} echo coupling", pa["opt"][k]["coupling"], pb["opt"][k]["coupling"])
            row(f"{k} ideal lin. AEC reduction", pa["opt"][k]["ideal_red"], pb["opt"][k]["ideal_red"])
    if "dev" in pa and "dev" in pb:
        print(f"  device path: A {a['cfg'][0]}, B {b['cfg'][0]}")
        row("device AEC reduction", pa["dev"]["red"], pb["dev"]["red"])
        row("device AEC residual - reference", pa["dev"]["res"], pb["dev"]["res"])
        row("device AEC reduction vs ideal", pa["dev"]["vs_ideal"], pb["dev"]["vs_ideal"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--gain-factor", type=float, default=32,
                    help="gain_factor of micro_wake_word / voice_assistant for the level projection (default 32)")
    ap.add_argument("--compare", action="store_true",
                    help="compare the playback part of every run with the first run (music aligned via the reference)")
    ap.add_argument("--right-first", action="store_true",
                    help="raw_mic channel 0 is the right mic (rx_mic_slots: [right, left], diagnostics/both_slots_raw.yaml)")
    args = ap.parse_args()
    global RIGHT_FIRST
    RIGHT_FIRST = args.right_first
    print(f"bands {BANDS} Hz; levels in dBFS, ratios in dB")
    data = []
    for run in args.runs:
        d = prepare(run)
        report(d, args.gain_factor)
        if args.compare:
            data.append(d)
    for d in data[1:]:
        compare(data[0], d)


if __name__ == "__main__":
    main()
