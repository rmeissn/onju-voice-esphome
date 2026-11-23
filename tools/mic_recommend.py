#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "scipy"]
# ///
"""Recommend left, right or both microphones (average) from one (or more) captures of the manual mic measurement.

Usage: uv run tools/mic_recommend.py captures/mictest-1 [captures/mictest-2 ...]
       (add --right-first only if you recorded with rx_mic_slots: [right, left])

Runs tools/analyze_capture.py once per capture (in parallel, its long output is not shown), reads the numbers the
decision needs from its output and decides with the rule of docs/SETUP_GUIDE.md ("How to decide").

The decision rests on the speaker leak difference D: how much stronger the left mic picks up your own speaker than
the right one (dB at 1-3 kHz, "echo coupling" in the music-only part of the capture). It is a property of the unit,
measured over tens of seconds, and was the same within about 1 dB in every capture of the same unit. The average of both
mics hears the speaker like the quieter mic plus G(D) = 20 log10((10^(D/20) + 1) / 2) dB, so the quieter mic alone
gains G(D) dB against the speaker and loses about 3 dB of self-noise averaging:

  |D| < 6 dB     average (the default); G < 3.5 dB does not pay for the lost noise averaging
  6 - 10 dB      marginal: average stays fine, a single mic helps a little (optional)
  >= 10 dB       the quieter mic alone (G >= 6.4 dB), if the checks below pass

The steps are rules of thumb, not derived: 6 dB is about the break-even against the theoretical 3 dB noise cost (5.2),
10 dB is twice that. Only two units are measured (D = 0.2 and D = 22-23 dB).

Checks that turn a "single mic" into "average": (1) the quieter mic must not be a worse microphone: its speech-to-noise
ratio in the silent speech part (pooled `-- silence` block) must not be more than 5 dB below the average's (ideal for
two healthy mics: 3 dB), or, if there was no speech in the silent part, its self-noise must not be more than 3 dB
above the other mic's; (2) the speech result after an ideal echo canceller (`summary playback`) must not contradict
(quieter mic more than 3 dB WORSE than the average: wrong slot order / --right-first?); (3) the captures must agree
on D (spread <= 4 dB). That speech result is otherwise a confirmation only: the difference between the mics does not
depend on how loud you speak, but on how strong the music is at 1-3 kHz against the mic noise (with quiet music the
residual echo is below the noise on both mics: about 0 dB; +6 dB with real speech against loud music on a unit
with D = 23 dB), and a "speech" that is the device's own TTS reply gives about 0 dB as well.

A capture is invalid (not counted) if it is incomplete or lossy, the raw I2S data is corrupted, only one mic is in
raw_mic, there are less than 5 s of music without speech, or the echo of a mic is not measurable. With several
captures D is the median. The analysis is plain signal processing; the script only reads its output. It stops with
an error if the header line is missing; other missing numbers make the capture invalid with a message.

Raw data check at 16 bit: analyze_capture.py often flags "low bits" although the read is correct (a correct read can
bias bit 2 slightly), and then skips the whole analysis. This script therefore runs it with --lowbits-ok and judges
the raw data like tools/mic_check.sh: a period pattern = slipping read = invalid; flagged low bits are only
corruption if the DC level is also too high (--dc-threshold, default -23 dBFS, the correct read has about -25).
"""

import argparse
import math
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ANALYZER = Path(__file__).with_name("analyze_capture.py")
REPO = ANALYZER.parent.parent

LEAK_STRONG_DB = 10.0  # |D| from here: the quieter mic alone (echo 6.4 dB below the average's)
LEAK_MARGINAL_DB = 6.0  # |D| from here: a single mic helps a little (3.5 dB), below: average
MIN_MUSIC_S = 5.0  # music without speech needed (invalid below), 10 s recommended
GOOD_MUSIC_S = 10.0
SNR_GUARD_DB = 5.0  # single mic: speech SNR in silence at most this much below the average's (ideal for 2 healthy mics: 3)
CONTRADICTION_DB = 3.0  # single mic: speech result after the AEC this much WORSE than the average = contradiction
NOISE_DIFF_WARN_DB = 3.0  # self-noise difference between the mics worth a note
SPREAD_WARN_DB = 4.0  # D differs between captures by more than this: something changed
MID = 1  # band index: 300-1000, 1-3k, 3-7k Hz
DC_THRESHOLD_DBFS = -23.0  # correct read ~ -25 dBFS, read one BCLK late ~ -19.6 (tools/mic_check.sh, grey)

NUM = r"(n/a|-?\d+\.\d+)"
RE_HEADER = re.compile(r"^=== .*\(complete=(\w+), gap frames=(\d+), dropped=(\d+)")
RE_RAW = re.compile(r"^raw ch(\d): ")
RE_DC = re.compile(r"^\s+raw ch(\d): DC\s+(-?\d+\.\d),")
RE_MUSIC = re.compile(r"^-- playback without speech \(music/TTS\): ([\d.]+)s")
RE_SUMMARY = re.compile(r"^\s+summary (silence|playback): (\d+) windows")
RE_DIFF = re.compile(rf"^\s+(right-left|right-avg|avg-left)\s+{NUM}\s+{NUM}\s+{NUM}\s+\[.*\]\s+(\w+) better in (\d+)/(\d+) windows")
RE_COUPLING = re.compile(rf"^\s+(left|right|avg)\s+echo coupling \(echo - reference\)\s+{NUM}\s+{NUM}\s+{NUM}")
RE_NOISE = re.compile(
    rf"^\s+(left|right|avg)\s+self-noise\s+{NUM}\s+{NUM}\s+{NUM}\s+\(broadband\s+{NUM}\)"
    rf"(?:\s+\|\s+speech\s+{NUM}\s+{NUM}\s+{NUM}\s+\|\s+SNR\s+{NUM}\s+{NUM}\s+{NUM})?"
)


def num(s: str) -> float:
    return math.nan if s == "n/a" else float(s)


def echo_gain(leak_db: float) -> float:
    """dB less speaker echo at the quieter mic than in the average of both, for a leak difference of leak_db."""
    return 20 * math.log10((10 ** (abs(leak_db) / 20) + 1) / 2)


@dataclass
class Run:
    name: str
    problems: list[str] = field(default_factory=list)  # make the run invalid
    notes: list[str] = field(default_factory=list)  # shown always
    info: list[str] = field(default_factory=list)  # shown with --verbose only
    raw_channels: dict[int, bool] = field(default_factory=dict)  # channel -> raw check passed
    lowbits: set[int] = field(default_factory=set)  # channels with the (ignored) low-bit flag
    dc: dict[int, float] = field(default_factory=dict)  # raw DC level per channel, dBFS
    music_s: float = 0.0  # playback without speech
    coupling: dict[str, float] = field(default_factory=dict)  # option -> echo coupling at 1-3 kHz
    noise: dict[str, float] = field(default_factory=dict)  # option -> self-noise at 1-3 kHz
    snr: dict[str, float] = field(default_factory=dict)  # option -> speech SNR in silence at 1-3 kHz
    speech_adv: dict[str, float] = field(default_factory=dict)  # right-avg / avg-left, median at 1-3 kHz

    @property
    def leak(self) -> float:
        """Left minus right echo coupling (1-3 kHz), dB: positive = the left mic hears the speaker more."""
        return self.coupling.get("left", math.nan) - self.coupling.get("right", math.nan)


def analyze(capture: Path, gain_factor: float, right_first: bool, dc_threshold: float) -> Run:
    """Run analyze_capture.py on one capture and parse what the decision needs."""
    run = Run(capture.name)
    if not capture.is_dir():
        run.problems.append("not a directory")
        return run
    cmd = [sys.executable, "-I", str(ANALYZER), "--lowbits-ok", "--gain-factor", str(gain_factor)]
    if right_first:
        cmd.append("--right-first")
    proc = subprocess.run([*cmd, str(capture)], cwd=REPO, capture_output=True, text=True)
    if proc.returncode != 0:
        run.problems.append(f"analyze_capture.py failed: {proc.stderr.strip().splitlines()[-1:] or proc.returncode}")
        return run
    phase = None
    seen_header = False
    for line in proc.stdout.splitlines():
        if m := RE_HEADER.match(line):
            seen_header = True
            if m[1] != "True":
                run.problems.append("capture incomplete")
            if int(m[3]):
                run.info.append(f"{m[3]} dropped records (excluded by the analysis)")
        elif m := RE_RAW.match(line):
            ch = int(m[1])
            run.raw_channels[ch] = "LOOKS CORRUPTED" not in line
            if "low bits flagged" in line:
                run.lowbits.add(ch)
            if not run.raw_channels[ch]:  # with --lowbits-ok only a period pattern (slipping read) is left
                run.problems.append(f"raw ch{ch}: read slips (period pattern), reboot the device and record again")
        elif m := RE_DC.match(line):
            run.dc[int(m[1])] = float(m[2])
        elif m := RE_MUSIC.match(line):
            run.music_s = float(m[1])
        elif "results less reliable" in line:
            run.problems.append("more than 20 % of the run lost (gaps): results less reliable")
        elif line.startswith("raw data corrupted") and not run.problems:
            run.problems.append("raw I2S data corrupted (mic read bug), reboot the device and record again")
        elif m := RE_SUMMARY.match(line):
            phase = m[1]
        elif phase == "playback" and (m := RE_DIFF.match(line)):
            if m[1] in ("right-avg", "avg-left"):
                run.speech_adv[m[1]] = num(m[3])
        elif m := RE_COUPLING.match(line):
            run.coupling[m[1]] = num(m[3])
        elif m := RE_NOISE.match(line):
            run.noise[m[1]] = num(m[3])
            if m[9] is not None:  # groups 6-8 speech level, 9-11 SNR (300-1000, 1-3k, 3-7k Hz)
                run.snr[m[1]] = num(m[10])
        elif re.match(r"^\s+WARNING less than .*(not measured|uncertain)", line):
            run.info.append(line.strip())
    if not seen_header:
        raise SystemExit(f"{capture}: no header line in the analyzer output, did analyze_capture.py change?")
    for ch in sorted(run.lowbits):  # flagged low bits only count together with a DC level that is too high
        if run.dc.get(ch, 0.0) > dc_threshold and not run.problems:
            run.problems.append(f"raw ch{ch}: low bits biased and DC {run.dc.get(ch, 0.0):.1f} dBFS > {dc_threshold:.0f}: faulty read, reboot and record again")
    if not run.problems and any(dc > dc_threshold for dc in run.dc.values()):
        run.notes.append(f"raw DC {max(run.dc.values()):.1f} dBFS > {dc_threshold:.0f}: read one clock late (mic fix not active?) or a unit with a higher DC (--dc-threshold)")
    if not run.problems:
        if set(run.raw_channels) != {0, 1}:
            run.problems.append("only one microphone in raw_mic (single-mic configuration): nothing to compare")
        elif run.music_s < MIN_MUSIC_S:
            run.problems.append(f"only {run.music_s:.0f} s of music without speech (need {MIN_MUSIC_S:.0f} s, better {GOOD_MUSIC_S:.0f} s): stay silent while the music plays")
        elif math.isnan(run.leak):
            run.problems.append("the echo of at least one microphone is not measurable (below its noise floor): a dead or covered microphone, or the speaker too quiet; the analysis printed no echo-coupling numbers")
        elif run.music_s < GOOD_MUSIC_S:
            run.notes.append(f"only {run.music_s:.0f} s of music without speech ({GOOD_MUSIC_S:.0f} s recommended)")
    return run


@dataclass
class Decision:
    choice: str  # right | left | average
    tier: str  # strong | marginal | none
    leak: float  # median left - right, dB
    quiet: str  # the mic that hears the speaker less
    gain: float  # echo_gain(leak)
    guard_kind: str  # "snr" | "noise" | "none"
    guard: float  # SNR of the quiet mic minus the average's / self-noise of the quiet mic minus the other's (median), dB
    blocked: str | None  # why the result is "average" although the leak says otherwise
    spread: float  # max - min of D over the valid runs
    speech: float  # median speech advantage of the quiet mic over the average (confirmation), nan = n/a


def median_of(values: list[float]) -> float:
    values = [v for v in values if not math.isnan(v)]
    return statistics.median(values) if values else math.nan


def decide(valid: list[Run]) -> Decision:
    leaks = [r.leak for r in valid]
    leak = statistics.median(leaks)
    quiet, other = ("right", "left") if leak > 0 else ("left", "right")  # leak > 0: the left mic hears the speaker more
    gain = echo_gain(leak)
    nan = math.nan
    snr = median_of([r.snr.get(quiet, nan) - r.snr.get("avg", nan) for r in valid])  # speech SNR in silence vs average
    noise = median_of([r.noise.get(quiet, nan) - r.noise.get(other, nan) for r in valid])  # self-noise vs the other mic
    guard_kind, guard = ("snr", snr) if not math.isnan(snr) else ("noise", noise) if not math.isnan(noise) else ("none", nan)
    adv = [r.speech_adv.get("right-avg", nan) if quiet == "right" else -r.speech_adv.get("avg-left", nan) for r in valid]
    speech = median_of(adv)
    tier = "strong" if abs(leak) >= LEAK_STRONG_DB else "marginal" if abs(leak) >= LEAK_MARGINAL_DB else "none"
    spread = max(leaks) - min(leaks)
    blocked = None
    if spread > SPREAD_WARN_DB and max(abs(x) for x in leaks) >= LEAK_MARGINAL_DB:
        # D is stable within about 1 dB per unit: a spread means a changed setup (slot order, --right-first)
        blocked = f"the captures disagree (D differs by {spread:.1f} dB): same slot order and --right-first in all of them? Record again"
    elif tier == "strong" and guard_kind == "snr" and guard < -SNR_GUARD_DB:
        blocked = f"the {quiet} mic leaks less but its speech-to-noise ratio is {-guard:.1f} dB below the average's (less sensitive or damaged?)"
    elif tier == "strong" and guard_kind == "noise" and guard > NOISE_DIFF_WARN_DB:
        blocked = f"the {quiet} mic leaks less but its self-noise is {guard:.1f} dB above the other mic's (damaged?)"
    elif tier == "strong" and not math.isnan(speech) and speech <= -CONTRADICTION_DB:
        blocked = f"the speech check contradicts: with the {quiet} mic alone speech vs music residual is {-speech:.1f} dB worse than with the average (wrong slot order: did you need or forget --right-first?)"
    choice = quiet if tier == "strong" and blocked is None else "average"
    return Decision(choice, tier, leak, quiet, gain, guard_kind, guard, blocked, spread, speech)


SNIPPET = "esp_audio_stack:\n  mic_channel: {side}\n  rx_mic_slots: !remove\n  mic_mix: !remove\n"


def fmt(x: float) -> str:
    return "  n/a" if math.isnan(x) else f"{x:+5.1f}"


def report(runs: list[Run], verbose: bool) -> int:
    print("\nPer capture (1-3 kHz; echo = how strongly your own speaker is picked up, in dB, lower = hears less):")
    print(f"  {'capture':28s} {'music':>6s} {'left':>6s} {'right':>6s} {'leak L-R':>9s}")
    for r in runs:
        if r.problems:
            print(f"  {r.name[:28]:28s} {'invalid':>6s}")
        else:
            print(f"  {r.name[:28]:28s} {r.music_s:5.0f}s {fmt(r.coupling.get('left', math.nan)):>6s} {fmt(r.coupling.get('right', math.nan)):>6s} {fmt(r.leak):>9s}")
        for line in [*r.problems, *r.notes, *(r.info if verbose else [])]:
            print(f"      - {line}")
    valid = [r for r in runs if not r.problems]
    if not valid:
        print("\nNo valid capture. Fix the problems above and record again (docs/SETUP_GUIDE.md, section 4).")
        return 2

    d = decide(valid)
    louder = "left" if d.leak > 0 else "right"
    print(f"\nLeak difference D (median of {len(valid)} capture(s)): the {louder} mic picks up your own speaker {abs(d.leak):.1f} dB more than the {d.quiet} mic.")
    print(f"The average of both hears it {d.gain:.1f} dB more than the {d.quiet} mic alone (the single mic gives up the noise averaging of two mics, up to 3 dB).")
    if d.spread > SPREAD_WARN_DB and len(valid) > 1:
        print(f"Note: D differs by {d.spread:.1f} dB between the captures: did the setup change (slot order, --right-first)?")
    for r in valid:
        if r.noise.get("left") is not None and r.noise.get("right") is not None and abs(r.noise["left"] - r.noise["right"]) > NOISE_DIFF_WARN_DB:
            print(f"Note: {r.name}: self-noise left-right differs by {r.noise['left'] - r.noise['right']:+.1f} dB at 1-3 kHz: one microphone may be damaged.")
    print("Checks for a single microphone:")
    match d.guard_kind:
        case "snr":
            print(f"  speech vs noise in silence, {d.quiet} mic minus average: {fmt(d.guard).strip()} dB (must be above -{SNR_GUARD_DB:.0f})")
        case "noise":
            print(f"  no speech in the silent part: self-noise of the {d.quiet} mic minus the other mic: {fmt(d.guard).strip()} dB (must be below +{NOISE_DIFF_WARN_DB:.0f})")
        case _:
            print("  not checked (no speech and no usable background noise in the silent part)")
    print("  speech vs music residual, " + (f"{d.quiet} mic minus average: {fmt(d.speech).strip()} dB (must be above -{CONTRADICTION_DB:.0f}; about 0 is normal)" if not math.isnan(d.speech) else "not available"))

    print()
    if d.blocked:
        print(f"RECOMMENDATION: keep BOTH microphones averaged (the default): {d.blocked}.")
        return 0
    match d.tier:
        case "strong":
            side = d.choice
            print(f"RECOMMENDATION: use the {side.upper()} microphone only (clear: |D| >= {LEAK_STRONG_DB:.0f} dB).")
            if d.guard_kind == "none":
                print("  CAUTION: the check for a worse microphone could not be made (speak for a few seconds in the silent part, or leave 3 s of quiet). Verify carefully.")
            print("  Add this to your device file (top level, not in packages):\n")
            print("  " + SNIPPET.format(side=side).replace("\n", "\n  ").rstrip())
            print("\n  Then flash and verify with one more capture and the wake word in practice (docs/SETUP_GUIDE.md, 'Apply the result').")
        case "marginal":
            print(f"RECOMMENDATION: keep BOTH microphones averaged (the default). Marginal case ({LEAK_MARGINAL_DB:.0f} to {LEAK_STRONG_DB:.0f} dB):")
            print(f"  the {d.quiet} mic alone would hear your speaker {d.gain:.1f} dB less. Try it only if the wake word during music is unreliable:\n")
            print("  " + SNIPPET.format(side=d.quiet).replace("\n", "\n  ").rstrip())
        case _:
            print(f"RECOMMENDATION: keep BOTH microphones averaged (the default). Nothing to change (|D| < {LEAK_MARGINAL_DB:.0f} dB).")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("captures", nargs="+", type=Path, help="capture directories (one is enough)")
    ap.add_argument("--gain-factor", type=float, default=16, help="gain_factor of the configuration (default 16)")
    ap.add_argument("--dc-threshold", type=float, default=DC_THRESHOLD_DBFS, help="raw DC level (dBFS) above which a read counts as late/faulty (default -23)")
    ap.add_argument("--verbose", action="store_true", help="also list remarks that do not change the verdict")
    ap.add_argument("--right-first", action="store_true", help="raw_mic channel 0 is the right mic (rx_mic_slots: [right, left])")
    args = ap.parse_args()
    with ThreadPoolExecutor(max_workers=min(4, len(args.captures))) as pool:
        runs = list(pool.map(lambda c: analyze(c, args.gain_factor, args.right_first, args.dc_threshold), args.captures))
    raise SystemExit(report(runs, args.verbose))


if __name__ == "__main__":
    main()
