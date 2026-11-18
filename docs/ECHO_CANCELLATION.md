# Echo cancellation (duplex config)

How the AEC (`esp_aec`, ESP-SR) behaves on the Onju, why `filter_length: 8` is needed, what it costs and what to do if it causes problems. Mic and level results: `AUDIO_INVESTIGATION.md`; CPU and RAM in general: `RESOURCE_INVESTIGATION.md`.

## Summary

- The Onju has no hardware loopback (MEMS mics + I2S amp, no codec), so the AEC gets a **software reference**: the speaker data as the audio task hands it to the I2S driver.
- The **echo delay** the AEC sees (reference vs. mic) is not fixed. It is decided anew at every music start and every audio session start and ranged from **~50 to ~160 ms** across two units.
- With `filter_length: 4` the AEC only works at the short end of that range: 49 ms → 12–27 dB echo removed (300–1000 Hz), 65 ms → ~12 dB, 74–75 ms → 6–8.5 dB, ≥ 97 ms → ≤ 3 dB (almost nothing). Which case a music start gets is luck.
- With **`sr_high_perf` + `filter_length: 8`** the AEC removed **27–45 dB below 3 kHz at every delay measured (97–161 ms, 21 music starts)**. Chosen setting.
- Cost: more core0 load and one 80 kB contiguous DMA block at boot (details below). The talker is attenuated by ~2–3 dB; the wake word worked during music.

## Why the delay varies

Code reading of Audio Stack v2026.11.0 (`esp_audio_stack.cpp`, `audio_pipeline.cpp`), confirmed against logs and captures:

- The audio task runs one 32 ms cycle: read the mics → AEC (takes the reference from the ring) → write speaker data (and store it as the next reference). So the AEC always gets the speaker block of the previous cycle; in steady state `aec_reference: ring_buffer` and `previous_frame` give the same reference.
- The reference is taken when a block is **queued** for the speaker, not when it is played. It is played later: the TX interrupt copies it into the DMA buffer that just finished, which plays after the current one.
- DMA geometry on the Onju (log: `dma_desc=6, dma_frames=1536`): **32 ms per DMA buffer** in the normal 16-bit build. The automatic size depends on the sample width (4092-byte descriptor limit): with `diagnostics/raw32.yaml` (`bits_per_sample: 32`) it is 768 frames = 16 ms. Speaker side: 2 staging blocks + 2 DMA buffers; mic side: the IDF queue holds up to 5 finished buffers (`desc_num - 1`, oldest dropped on overflow).
- How many of these whole buffers are filled when playback starts is not fixed: older mic data or a fuller speaker queue make the delay longer. The measured delays cluster in 32 ms steps (97 / 129 / 161 ms), which fits. Every speaker stop/start empties the speaker queue (`service_speaker_reset_`), so every music start re-rolls the delay; a session restart re-rolls it too.
- The audio-loop cadence in the capture timestamps predicts the group: a steady 32 ms cycle gave 144–161 ms, an alternating 21.3/42.7 ms cycle 97–134 ms (unit 2; unit 1 had a steady 32 ms cycle at 49–74 ms, so the rule does not carry over between units). Hypothesis: when alternating, the loop waits alternately on mic and speaker, which are ~10.7 ms out of phase; a steady loop is paced by one side only, with a large mic backlog by the speaker, which gives the longest delays. So the mic backlog is likely the main variable, possibly built up by a stall at stream start.
- There is no YAML option for a fixed reference delay. Smaller DMA buffers would narrow the range but leave less reserve against audio-loop stalls (up to ~140 ms were seen at stream starts while a capture started).

## Measurements

Delay measured as the AEC sees it (`tools/aec_delay.py`: `reference` vs. `processor_input`, both tapped in the same cycle), reduction as band power before vs. after the AEC, independent of the stream alignment.

| Unit / setting | Delay | Echo removed by the device (300–1000 Hz / 1–3 kHz) |
| --- | --- | --- |
| unit 1, `sr_high_perf` 4 | 49 ms (3 captures) | 12–27 / 14–29 dB |
| unit 1, `sr_high_perf` 4 | 65 ms (2) | ~12 / 9–10 dB |
| unit 1, `sr_low_cost` 4 (before the mic fix) | 74 ms (2) | 7.6–8.5 / 6.7–7.5 dB |
| unit 2, `sr_high_perf` 4, 32-bit build | 75 ms (1) | 6.1 / 5.5 dB |
| unit 2, `sr_high_perf` 4 | 97–156 ms (21) | 0.2–2.7 / 0.0–1.8 dB |
| unit 2, `sr_high_perf` 8 | 97–161 ms (21) | 27–45 / 32–41 dB |

Unit 2 delays per playback start, automated test (ms, count): filter 4: 97 (1), 124 (1), 129 (7), 134 (1), 144/145 (7), 156 (3); filter 8: 97 (6), 129 (6), 144/145 (3), 155 (4), 161 (2). The second play of a session got a different delay in 8 of 10 sessions (filter 4) and 2 of 10 (filter 8). Within a capture the delay is constant (±1 ms in 2 s windows).

- The same spread appeared with Music Assistant (Sendspin, Opus, 50 % volume) and with a FLAC over HTTP (35 %, one capture 50 %): the stream source, codec and volume do not matter.
- Not a convergence effect: the reduction is reached within the first second of playback and does not grow.
- Not the echo level: captures with similar echo coupling but different delays differ by 10–20 dB.
- **An earlier mode comparison was confounded by the delay:** the `sr_high_perf` captures happened to have 49/65 ms, the `sr_low_cost` captures 74 ms (and predate the mic fix). There is no capture pair at matched delay, so the two modes are not compared; the difference fits the delay trend.
- Unit 1 numbers come from captures that partly contain speech or other content; the music-only ones are listed. Unit 1 used the right mic only.
- Unit 2's mics pick up the own speaker ~16 dB more than unit 1's right mic, so it needs more echo removal for the same residual (possibly missing silicone seals around the mic ports; not verified).

## Cost of `filter_length: 8` (unit 2, `sr_high_perf`)

| Situation | core0 | core1 | internal RAM free |
| --- | --- | --- | --- |
| idle, filter 4 | 21 % | 22 % | 51 kB |
| idle, filter 8 | 32 % | 20 % | 49 kB |
| music, filter 8 | 48–50 % | 44–46 % | 48.5 kB |
| music + Assist runs, filter 8 (no capture) | up to 69 % | up to 42 % | ≥ 39.6 kB |
| music + Assist reply + PCM capture, filter 8 | up to 86 % | 58 % | 31.9 kB minimum |
| HTTP playback + capture + session restarts, filter 8 | up to 73 % | | 24.7 kB minimum |

- `sr_high_perf` needs one **contiguous DMA-capable internal block of 40 kB per 4 taps** at setup (80 kB for 8), otherwise the wrapper refuses to start the AEC (`esp_aec ... Refusing to call aec_create`, component failed until the next boot). With the current duplex config (WiFi code out of IRAM, IPv6 off) the block was free on every boot tested (3 reboots + flashes); with an older config without these savings the block was not available.
- Most of the larger AEC state lives in PSRAM: internal RAM went down by only ~1.5 kB compared with 4.
- Once, at a music start, `esp_audio_stack.speaker` / `.microphone` blocked the main loop for ~200 ms (followed by the known micro_wake_word ring buffer reset). Not seen with filter 4 so far; watch it.

## If it causes problems

Check the boot log for `esp_aec` → `Initialized: YES` (shown in the config dump when a log client connects).

| Problem | What to do |
| --- | --- |
| AEC does not start (`Refusing to call aec_create`, `Initialized: NO`) | free internal DMA RAM (e.g. drop diagnostics packages) or switch to `mode: sr_low_cost` with `filter_length: 8` (no 80 kB block needed) |
| core0 too busy (music stutters, `spk_underrun` > 0 during music, wake word misses) | `sr_low_cost` + 8 (cheaper per tap; at 97 ms an older test gave 12–14 dB); measure with `tools/aec_delay.py` |
| internal RAM too tight | same; or the levers in `RESOURCE_INVESTIGATION.md` |
| echo still audible to the wake word | check the delay of that music start with a capture; above ~160 ms even 8 may not reach |
| last resort | `filter_length: 4`: works only when a music start happens to get a short delay |

Never use `filter_length: 6` (broken, [issue #18](https://github.com/n-IA-hane/esphome-audio-stack/issues/18)) and never change mode or filter length at runtime (breaks the I2S lockstep until reboot).

Not useful here:
- `aec_reference: previous_frame`: same reference as the ring in steady state, so the same delay.
- `aec_reference_buffer_ms`: only the capacity of the ring, not a delay.
- `dma_desc_num`: also raises the speaker-side buffer count (longer fixed delay).

Untested options:
- `dma_frame_num: 512` (10.7 ms buffers instead of 32 ms; mic queue ≤ 53 instead of 160 ms): should narrow and shorten the delay range; check for mic overflows and gaps.
- A fork change that aligns the reference per session from the speaker interrupt timestamps (the proper fix, see below).
- Audio Stack `dev` changes the mic read (`e4468bc`: timeouts and partial reads); with the ring reference this can add backlog and shift the delay. Measure before adopting.

## Upstream

Reported as [issue #19](https://github.com/n-IA-hane/esphome-audio-stack/issues/19). Proposal for Audio Stack: measure per playback start how long a queued speaker block takes until it is played (the TX interrupt already records this) and how old the mic data is (`dev` already timestamps RX buffers, cccd44d), then hold back the reference so that the AEC always sees about the same short delay (~10–30 ms; needs `aec_reference_buffer_ms` > ~160). Simpler alternative: drop the mic backlog at playback start. Until then, the split-bus hint "`filter_length: 8`" in `esp_audio_stack/README.md` applies to a shared bus too. Measured on v2026.11.0 only; `dev` changes the mic read (e4468bc) and adds TX preload.

## How to measure

- Automated delay test (no human needed): play the same track 2× per round over 10 rounds with "Session restart" (and a few reboots) in between, a ~12 s capture each (`diagnostics/audio_capture.yaml`, `tools/capture_audio.py`, media commands through the native API), then `uv run tools/aec_delay.py captures/<prefix>-*`. Keep the volume moderate (~35 %): at 50 % the amp/speaker distorted a FLAC file noticeably (less linear echo).
- Record the device log meanwhile in chunks (`podman exec <c> timeout 120 esphome logs ...` in a loop, timeout inside the container) so it survives reboots.
- In every AEC comparison write down the delay per capture; compare settings only at matched delays.
- `tools/analyze_capture.py --device-path avg` when both mics get the same echo (the automatic detection of the processed mic option is ill-conditioned then).
