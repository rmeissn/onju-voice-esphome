# Audio investigation (duplex config)

Measurements of the Onju microphones, AEC, levels and Assist behaviour with `onju_duplex_i2s.yaml` (Audio Stack, `esp_aec`). CPU and RAM: `RESOURCE_INVESTIGATION.md`.

> **Unless a second unit is mentioned, everything here was measured on a single device.** The mechanisms (mic read bug, AEC, HA processing) apply to every Onju, but the numbers can differ per unit. This holds especially for the **mic choice**: which mic picks up the own speaker more, or whether a mic is dull or damaged, depends on the individual board and its assembly. On another unit averaging or the left mic may be the better choice, so measure each device (see "Quick check of other devices") instead of copying the test device's choice.

## Summary

| Topic | Finding | Setting |
| --- | --- | --- |
| Mic read bug | The full-duplex RX read the SPH0645 data one BCLK late or slipping, random per audio session (only ~19 % of sessions were correct) | `rx_clock: fast` + `rx_data_delay: pos_edge` + `input_gain: 2.0` (Audio Stack fork branch, upstream [PR #17](https://github.com/n-IA-hane/esphome-audio-stack/pull/17)) |
| Mic choice | The left mic picks up the own speaker ~20 dB louder (internal path); with music the right mic alone is clearly better; units may differ | default: average both; per device: measure and override (e.g. `mic_channel: right`) |
| AEC | The echo delay varies per music start (~50–160 ms); `filter_length: 4` removes almost nothing above ~100 ms, 8 removes 27–45 dB below 3 kHz at every delay measured | `mode: sr_high_perf`, `filter_length: 8`; never 6, never change at runtime; details and fallbacks in `ECHO_CANCELLATION.md` |
| Gain | 16× keeps ≥ 7 dB headroom for a loud sentence close by; wake word and HA's VAD do not depend on the gain | `gain_factor: 16` (wake word and Assist) |
| HA processing | HA up to 2026.11.1 ignores the satellite's NS/AGC/volume settings; the dev branch forwards them (PR home-assistant/core#190096) | `noise_suppression_level: 1`, `auto_gain: 16 dbfs`, `volume_multiplier: 1.0` (simulated, effective once HA forwards them) |
| Wake word → listening | Users start talking when the wake sound ends; the first word gets lost if streaming starts later | VA starts `wake_listen_delay` after the sound starts (default 300 ms for the 367 ms `sounds/wakeup_v2.flac`, 450 ms for the original `sounds/wakeup.flac`; a 550 ms trial variant was dropped) |
| "Dull" sound | Neither AEC nor resampler nor HA change the timbre; the 16 kHz pipeline cuts everything above 8 kHz | nothing to fix |

**After every flash/reboot and on every new device, run `bash tools/mic_check.sh <ip>`** (expects CORRECT; needs `diagnostics/audio_capture.yaml`).

## Mic read bug

### Symptoms

After some boots, and sometimes at runtime when Audio Stack rebuilt the audio session, the wake word stopped triggering (`Wake word model predicts 'Okay Nabu', but VAD model doesn't`) and STT returned hallucinated text in random languages, until the next reboot. Playback was fine. Toggling the wake word did not help (the speaker keeps the bus open, so the session is not rebuilt).

### What the raw data showed

The error is already in the data `i2s_channel_read` delivers (`raw_mic` tap, before any DSP). With 32-bit slots, P(bit = 1) per bit of the right slot (bit 31 left; 9 = always 1, 0 = never, 5 = random):

| Read | bits 31–24 | 23–16 | 15–8 | 7–0 |
| --- | --- | --- | --- | --- |
| correct | `99999009` | `0+144455` | `55000000` | `00000000` |
| one BCLK late | `99990090` | `+1444555` | `50000000` | `00000009` |

- The correct read matches the SPH0645 datasheet frame exactly (18 data bits in 31–14, then zeros and tri-state). The late read is the same pattern one bit higher: the real MSB is lost (loud sound wraps around), bit 0 is the other mic's sign bit, the level is 6 dB higher.
- Slipping: the alignment differs per frame index mod 3, partly within one slot. The pattern repeats every 3 slots (96 BCLK = the period of the fractional MCLK divider 13 + 1/48 at 160 MHz). Wake word dead, STT hallucinates.
- Both slots (both mics) always get the same result: it is the whole RX stream, not the mic. **Averaging is not immune.**
- Each audio session (boot or `esp_audio_stack.stop` → `start`) is an independent draw; another restart redraws it.

### Cause

On a shared full-duplex STD bus ESP-IDF turns the RX channel into an internal slave: BCLK/WS come from TX, but the slave logic runs on its own RX clock divider. At 48 kHz / 32-bit slots that is 4 RX ticks of ~81 ns per BCLK, with a phase to BCLK that is random per session. The RX samples one or two ticks after it detects the rising edge (~80–160 ns), while the SPH0645 changes DATA 7–66 ns after the edge (datasheet). Depending on the phase the session reads the right bit or the next one; near the boundary the fractional-divider jitter makes it slip. Related: espressif/esp-idf#9513.

### What changes the rate (session restarts, no fix)

| Variant | Sessions | correct | late | slipping |
| --- | --- | --- | --- | --- |
| `mclk_multiple: 256` (default, 4 RX ticks per BCLK) | 74 | 14 | 45 | 15 |
| `mclk_multiple: 384` | 10 | 1 | 4 | 5 |
| `mclk_multiple: 512` | 15 | 9 | 1 | 5 |
| PLL 240 MHz for TX and RX | 30 | 8 | 18 | 4 |
| RX enabled before TX | 20 | 6 | 8 | 6 |

Tried, did not help: larger MCLK multiple (worse), 240 MHz clock source for both channels, RX/TX enable order, averaging both mics, `bits_per_sample: 32` (same hardware sampling; useful only to see the bits).

### RX clock margin scan

RX alone on PLL 240 MHz / k, optionally with the SD input delay `rx_timing.rx_sd_in_dm` (an ESP32-S3 register IDF leaves at bypass); TX unchanged, 20 sessions per block, registers confirmed in the log:

| RX clock | RX tick | `rx_sd_in_dm` | correct | late | slipping |
| --- | --- | --- | --- | --- | --- |
| 240 / 2 = 120 MHz (`rx_clock: fast`) | 8.3 ns | bypass | **20** | 0 | 0 |
| 240 / 4 = 60 MHz | 16.7 ns | bypass | **20** | 0 | 0 |
| 240 / 5 = 48 MHz | 20.8 ns | bypass | **20** | 0 | 0 |
| 240 / 8 = 30 MHz | 33.3 ns | bypass | 0 | 0 | 20 |
| 240 / 10 = 24 MHz | 41.7 ns | bypass | 0 | 0 | 20 |
| default 12.3 MHz (control) | 81 ns | bypass | 8 | 7 | 5 |
| default 12.3 MHz | 81 ns | 1 (`pos_edge`) | **20** | 0 | 0 |
| default 12.3 MHz | 81 ns | 2 (`neg_edge`) | 15 | 0 | 5 |
| 120 / 30 / 24 MHz | | 1 (`pos_edge`) | **20** each | 0 | 0 |
| 30 MHz | 33.3 ns | 2 (`neg_edge`) | **20** | 0 | 0 |

- Without the delay the read is correct up to ~21 ns ticks: on this unit the mic changes data ~40–65 ns after the edge (rough model, inside the datasheet). At 30/24 MHz the ticks per BCLK are not an integer, so the sampling point wanders through the bit: slipping in every session.
- `pos_edge` delays SD by one RX tick (the RX effectively samples earlier) and fixes every clock tested, even the default. `neg_edge` is about half a tick. Sampling too early would need ~260 ns; never seen.
- The esp32.com thread on SPH0645 + ESP32-S3 uses `rx_sd_in_dm = 2` in plain master RX, where it showed no measurable effect; in full duplex it matters, and 1 is the value that works.
- The datasheet guarantees only 7 ns hold, so the margin is unit dependent: check every device with `tools/mic_check.sh`.

### Fix and verification

- Chosen: both options together (most margin) + `input_gain: 2.0` (the correct read is exactly 6 dB lower than the former late read; raw DC ratio late/correct = 2.00 on both mics).
- Results: 6 of 6 reboots and, after going back to 16 bit, 10 of 10 session restarts and 3 of 3 reboots correct; one 60 s session with playback correct in every second; no `[W]`/`[E]`.
- Patch: two opt-in options in Audio Stack (`rx_clock: default | fast`, `rx_data_delay: none | pos_edge | neg_edge`, ESP32-S3 only, validated at config time), upstream [PR #17](https://github.com/n-IA-hane/esphome-audio-stack/pull/17). The test switches of the scan are kept as a patch with a rebuild guide in `diagnostics/RX_CLOCK_SCAN.md`.
- Not implemented (not needed with the fix): on-device detection and recovery (a session restart heals a wrong session, but the raw data is not accessible from YAML; the processed noise level differs by only 4–7 dB).

## Microphones: left, right, average

Quiet room, speech SNR (noise subtracted) for 300–1000 Hz / 1–3 kHz / 3–7 kHz:

| Position | left | right | average |
| --- | --- | --- | --- |
| 1.5 m | 29 / 17 / 11 | 28 / 20 / 16 | 30 / 18 / 15 |
| 3 m | 27 / 19 / 14 | 25 / 19 / 18 | 28 / 19 / 14 |

- The noise floor is the mics' self-noise (left and right uncorrelated), not room noise.
- Quiet: averaging and right-only are about equal (average +2.5 dB at 300–1000 Hz, right +0–3 dB at 1–7 kHz).
- **Own speaker:** the left mic picks it up **18–24 dB louder** than the right one, in every position and down to 100 Hz: an internal or structure-borne path. Echo coupling at 1–3 kHz: right −2..−5 dB, average +14 dB, left +19 dB.
- With music (3 m, paired within one capture, ideal linear AEC per option): right vs. average +1.8 / +6.4 / +6.2 dB speech-to-residual, before the AEC 15–18 dB better.
- The left mic is 3–7 dB duller at 2–8 kHz (possibly a damaged protective membrane).
- Stereo capture with native ESPHome (`i2s_audio`) had destroyed the SNR (1–2 dB instead of ~16 dB); Audio Stack's averaging does not have this problem (table above).
- Recommendation: right-only was best **on the test device only**. The duplex config therefore averages both mics by default; measure each unit (both-slot capture, see "Quick check of other devices") and override the mic choice in its device file.
- **A second unit was different:** both mics equal in self-noise, speech SNR at 1 m and echo pickup (echo coupling 1–3 kHz +6..+8 dB on both, between the test device's left and right mic); with music right was ~3 dB better after an ideal AEC, small enough to be mic orientation. Kept the average there.

## AEC

### AEC filter length

**The echo delay decides most of the AEC result**, and it varies per music start and audio session (~50–160 ms). Full analysis, measurements, costs and fallbacks: `ECHO_CANCELLATION.md`.

| Setting | Result |
| --- | --- |
| `sr_high_perf`, 8 (chosen) | 27–45 dB echo removed below 3 kHz at every delay measured (97–161 ms, 21 music starts), talker −2..−3 dB, wake word fine during music; core0 ~50 % with music (up to ~70 % with an Assist reply), needs an 80 kB contiguous DMA block at boot |
| `sr_high_perf`, 4 | works only when a music start gets a short delay: 49 ms 12–27 dB, 65 ms ~12 dB, 75 ms 6 dB, ≥ 97 ms ≤ 3 dB |
| `sr_low_cost`, 4 | only measured at 74 ms (7.6–8.5 dB, before the mic fix); no comparison with `sr_high_perf` at matched delay |
| `filter_length: 6` | **broken**: no audio frames, heap corruption. Never use ([issue #18](https://github.com/n-IA-hane/esphome-audio-stack/issues/18)) |
| Runtime change of mode or length | broke the I2S lockstep (17 s outage, extra echo until reboot): only change in YAML and flash |

- The earlier choice of `sr_high_perf` over `sr_low_cost` (15–30 / 11–22 dB vs. 8–9 / 8 dB with filter 4) was confounded by the delay: the `sr_high_perf` captures happened to get 49/65 ms, the `sr_low_cost` ones 74 ms. The earlier "filter 8 is not worth it" result came from captures with ~64–96 ms delay before the mic fix and is superseded.
- An even earlier comparison that favoured `sr_low_cost` (and saw a 32 ms / 3.5 dB reference shift with every other setting) ran before the mic fix on randomly misread sessions; the "shift" was most likely the same random delay.
- AEC off is always worse ("Echo Cancellation" switch only for A/B tests).
- During Assist the music is ducked, so double talk only happens during the wake word.

## Levels and gain

- All gains are digital (the SPH0645 has no analog gain). `input_gain` acts before the AEC, `mic_gain` after it, `gain_factor` per consumer (wake word, Assist); the last two clip hard. micro_wake_word normalizes the level itself, so SNR, residual echo and clipping matter more than the exact level.

### Level tuning

One guided capture (sentences at 1 m, 3 m, loud at 30–50 cm; quiet and with music) computed offline with HA's own libraries (`tools/level_tune.py`, `tools/mww_gain.py`). Decision rules fixed before measuring.

- **Assist `gain_factor`:** loud sentence peak 32× −3.6 / −1.4 dBFS (quiet / music), 24× −6.1 / −3.9, **16× −9.6 / −7.4** → 16 (rule: ≥ 6 dB below full scale).
- **Wake word `gain_factor`:** 18 of 18 "Okay Nabu" detected at 8×–32× alike (p 0.97–1.00); 16× keeps 7 dB headroom for a loud one close by → 16.
- **HA's VAD runs before Speex** and finds the same speech at 8×–64×: the gain does not decide start/end detection.
- **Noise suppression:** level 1 +1.5 dB speech-to-pause, 2–4 no better, onsets unchanged → 1.
- **Auto gain** (speech level, speech-to-pause with music): off −34 dBFS, 10: −33 / 8.9 dB, **16: −30 / 10.5 dB**, 20: −28 / 11.1 dB, 31: −24 / 12.0 dB but peaks at −6 dBFS. 16 and 20 are clearly better than 10, the difference between them is below what a few sentences can show → 16 (more headroom). Simulate per sentence: a continuous simulation exaggerates the auto gain.
- **`volume_multiplier`: 1.0** (a second gain stage in HA before VAD and Speex; > 1 would clip).

### What HA does with the audio

- Order in HA: `volume_multiplier` → VAD → Speex noise suppression (`noise_suppression_level` × −15 dB) and auto gain → STT.
- **HA up to 2026.11.1 ignores the satellite's settings:** the STT debug file is bit-exactly 16× the device output (no NS/AGC). `esphome/assist_satellite.py` receives the settings but does not pass them on (checked across releases since 2025.1); the dev branch forwards them since PR home-assistant/core#190096. The simulation used the same library version (`pyspeex-noise` 1.0.2), so the effect will be as simulated.
- After an HA update with that PR: check in the STT debug files that NS 1 / AGC 16 take effect.

## Wake word → listening

- HA's pipeline start is negligible (3–60 ms). Waiting for the whole wake sound plus buffers made streaming start 0.8–1.0 s after detection, so a question asked right after the sound lost its first word or was missed entirely (HA's VAD never saw the onset).
- **Fix:** start the VA `wake_listen_delay` after the sound starts playing (near the end of its audible part); the AEC removes the tail. Music is ducked already at detection. With this the VA starts ~0.3 s earlier; waiting until about the LED: 3 of 3 correct, also across the room with music. Speaking immediately still garbled one question (cause not separable: clipped onset or double talk with the sound's tail).
- Shorter wake sounds (the repository ships the original `sounds/wakeup.flac` and the 367 ms one as `sounds/wakeup_v2.flac`, which is the default; the 550 ms trial was dropped): original 745 ms → 550 ms (fade-out) → 367 ms (1.5× faster, a fifth higher; `tools/make_wake_variants.py`) with 300 ms delay (same tail level, −25 dB below the peak, as 450 ms with the 550 ms sound). The Voice PE is faster only because it starts after a fixed 300 ms.
- Speaker buffers: `onju_out` 500 → 150 ms and mixer sources 500 → 100 ms (as in `onju_media_player.yaml`). With music the full hardware buffer had delayed wake sound, TTS and ducking by up to 500 ms; no music stutter seen.
- One missed wake word a few seconds after a music start (Sendspin `Lost sync`, speaker underruns, MWW ring buffer reset at music starts; see `RESOURCE_INVESTIGATION.md`). Wake word probability with music on the device 0.85–0.89 (cutoff 0.85): watch in daily use.

## Sound quality ("dull")

This section is about the microphone side (what the voice assistant hears). For the music from the speaker (equalizer settings for Music Assistant) see [MUSIC_QUALITY.md](MUSIC_QUALITY.md).

- Timbre per stage (1/3-octave bands): raw mic → AEC input ≤ 2 dB change (−4 dB at 630 Hz), AEC ≤ 1.3 dB, HA 0 dB. The loss is everything above 8 kHz (the 16 kHz pipeline), which removes the crispness of s/t sounds; the rest is mic, enclosure and room.
- The 48 → 16 kHz conversion (`esp_ae_rate_cvt`, highest quality setting) is practically ideal: flat ±0.0 dB from 50 Hz to 7 kHz, −1..−1.6 dB at 7.6 kHz. A better resampler would gain nothing audible.

## Other findings

- **Wake word stuck with music:** `on_end` restarted the wake word only if the media player was not playing, so with music it stayed off after the first Assist run. The duplex config must not have that condition (the single-bus config needs it).
- **Reply LED and unducking too early:** `on_end` fires before the TTS reply plays. Wait until the VA is idle, then until the announcement resampler stopped (it stays "playing" until all frames were played); no extra delay needed.
- **Alarm siren from the device:** an `audio_file` on a second media source in the media pipeline instead of HTTP streaming (id `alarm` is reserved in ESPHome). The original file ends with 1 s of silence; a trimmed 8 s version shortens the gap between repeats.
- **LED animations** look less smooth since ESPHome 2026.9 (suspected: the gamma table fix makes the coarse low-brightness steps visible with `gamma_correct: 2.8`; untested: lower gamma or higher minimum brightness).
- **AFE/BSS with two mics** (`esp_afe`) was tested (`mic_num: 2`, `se_enabled: true`, `aec_filter_length: 8`, both `high_perf` and `low_cost`, with and without VAD/AGC): even with GMF ring buffers moved to PSRAM, dual-mic AFE/BSS saturates the ESP32-S3 CPU under music playback + wake word + Assist (`+in_drop` / playback stutter), so standalone `esp_aec` remains the limit on this hardware. Measurements: `RESOURCE_INVESTIGATION.md`.

## Quick check of other devices

No new clock scan; only look for problems:
1. Partition migration if the old 4MB layout is present (`PARTITION_MIGRATION.md`), then the duplex firmware.
2. Automatic: ~20 session restarts with the bit check (`tools/mic_check.sh <ip> <prefix> 20 --session --count`; for another unit's DC reference `--ref=R,L`, or `tools/r32_verdict.py --ref=auto` on 32-bit captures). Expected: all CORRECT. The DC differs per unit (a second unit read ~12 % higher than the test device and failed the default reference while the bits were correct). With the default `rx_mic_slots: [left, right]`, raw ch0 is the **left** mic (`r32_verdict.py` labels ch0 "R").
3. One guided both-slot capture (~3–5 min): better mic or average (speech vs. noise and vs. own music), `gain_factor` with the rules above, noise floor and DC per mic (damaged mic), echo level and AEC effect. Analyze without `--right-first` for the default slot order, and with `--device-path avg` if the processed option is reported as unknown.
4. Check the boot log for `esp_aec` `Initialized: YES` and run the automated AEC delay test (`ECHO_CANCELLATION.md`).

## Tools and workflow

| Tool | Purpose |
| --- | --- |
| `tools/capture_audio.py` | receiver for `esp_audio_capture` |
| `tools/analyze_capture.py` | per capture: integrity and raw I2S check, levels/clipping, SNR along the chain, echo coupling and AEC effect, speech preservation, left/right/average; `--compare` for variants with the same music; `--right-first` for both-slot captures; `--device-path avg` to skip the detection of the processed mic option |
| `tools/mic_recommend.py` | runs `analyze_capture.py --lowbits-ok` on the manual mic-measurement capture(s) and decides left / right / average from the speaker leak difference D (echo coupling left - right at 1-3 kHz, music-only part; rules of thumb at 6 and 10 dB; checks: SNR in silence >= -5 dB vs average or self-noise fallback, speech result not worse than -3 dB, captures agree), see [SETUP_GUIDE.md](SETUP_GUIDE.md); invalid captures (period pattern, low bits with DC > -23 dBFS, gaps, < 5 s music without speech, echo not measurable) are not counted |
| `tools/aec_delay.py` | echo delay as the AEC sees it, audio-loop cadence and device AEC reduction per capture, independent of the stream alignment (music-only captures) |
| `tools/mic_check.sh` | bit check after a reboot or "Session restart": CORRECT / LATE / SLIPPING, restarts until CORRECT; `--count`, `--log=<device>.yaml`, `--ref=R,L`, offline `--analyze` |
| `tools/r32_verdict.py`, `tools/raw32_bits.py` | exact verdict and bit view of 32-bit both-slot captures |
| `tools/level_tune.py`, `tools/mww_gain.py` | offline gain / HA NS / AGC simulation and wake word probability per gain |
| `tools/esphome_button.py`, `tools/esphome_play.py` | press device buttons / play a sound through the native API (inside the Device Builder container) |
| `tools/receive_captures.sh` | receiver + device log for captures started from HA |
| `tools/make_synthetic_capture.py`, `tools/make_synthetic_raw32.py` | synthetic captures with known ground truth; run after every analysis change |

Capture: include `diagnostics/audio_capture.yaml`, set `capture_address` to the receiving computer, open TCP 19091 there, run `uv run tools/capture_audio.py captures/<name> --timeout 900`, press "Start audio capture" (60 s).

Test protocol for config variants: same track and volume, wait ~10 s (AEC converged), capture: music only, talk at ~1 m and ~3 m, pause the music, silence, talk. Compare variants only on the music-only parts and with ratios within one capture; run all A then all B (first block = control) and fix the decision rule before measuring.

Pitfalls that caused wrong conclusions:
- Band powers at 48 and 16 kHz need the same window **duration**, not the same FFT size.
- High-pass at 80 Hz first: the SPH0645 drifts strongly below 50 Hz.
- The echo delay is **not** fixed (~50–160 ms, new per music start): note it per capture and compare AEC settings only at matched delays (`tools/aec_delay.py`). `processor_output` lags `processor_input` by one AEC frame (512 samples).
- Use the automatic speech detection, not hand-picked segments; an instrumental track can fool it.
- A capture started during music has one echo delay of echo without reference: exclude it.
- Stream alignment is anchored on `raw_mic`, the first tap of each audio cycle; anchoring on the last-started stream became ambiguous once the AEC took longer (filter 8).
- A test file without the playback DSP of the usual player can drive the amp/speaker into distortion at 50 % volume (less linear echo); use ~35 %.
- Compare captures only with matching reference level; exclude the own TTS before comparing mics.
- ESPHome replaces `/` in entity names with `⁄`: avoid `/` in button names.
- CLI logs: `timeout` inside the container, otherwise orphaned clients block the device API.
