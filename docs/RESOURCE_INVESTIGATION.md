# CPU and RAM investigation (duplex config)

**Internal RAM is the bottleneck** of `onju_duplex_i2s.yaml` on the ESP32-S3 N16R8: the internal heap is ~259 kB, PSRAM (8 MB) always has more than 4 MB free. All numbers come from one test device; kB = 1024 bytes. Audio results: `AUDIO_INVESTIGATION.md`.

## Current state and recommendations

- AEC `sr_high_perf` + `filter_length: 4`: with music core0 ~32 % / core1 ~25 %, internal RAM ~53 kB free (~46 kB while a PCM capture runs). It spreads the load over both cores and leaves more RAM than `sr_low_cost` (core0 ~42 %, 43 kB free).
- In the duplex config: IPv6 off, WiFi code out of IRAM, task stacks of mixer, resamplers and media sources in PSRAM. Deliberately internal: Sendspin hub (network + time sync), micro_wake_word (inference timing), Audio Stack audio task (real-time I2S/AEC).
- Per device (optional, `main.yaml`): `flash_chip: <vendor>` as named in the boot log.
- Never use `filter_length: 6`, never change the AEC mode at runtime (see "Problems seen").

## How to measure

- **Resource monitor** (`diagnostics/monitor.yaml`, tag `monitor`, every 10 s): internal / DMA-capable / PSRAM used, free and minimum free since boot, CPU load per core. Optional `log_details()` (largest free block, busiest tasks, least stack headroom). `diagnostics/heap_owners.yaml` (diagnostic build with `CONFIG_HEAP_TASK_TRACKING`, costs RAM/CPU) lists RAM per allocating task; blocks allocated in a component's `setup()` count for `loopTask`.
- **Audio Stack telemetry** (`telemetry: true`): `Perf[...]` lines with frame interval, speaker underruns, internal heap. Missing lines = the audio task produces no frames.
- **Realistic scenario** (the minimum only means something after everything was allocated once): music at ~35 %, an Assist run during the music, then ~70 s more music.
- **Scan every measurement log** (`rg '\[[WE]\]|spk_underrun=[1-9]'`) and note each hit with what happened just before under "Problems seen":
  - `micro_wake_word: Not enough free bytes in ring buffer`
  - `spk_underrun` > 0 **while music plays** = real dropouts. Without music the speaker pads silence and the counter rises too (e.g. 128 per line), that is not stutter.
  - any other `[W]`/`[E]` from `audio_stack`, `speaker_mixer`, `sendspin`, `voice_assistant`.
- **Static RAM** (no flash needed), in the Device Builder container: `podman exec <c> /root/.cache/esphome/idf/penvs/5.5.6/bin/python -m esp_idf_size [--archives] /config/.esphome/build/<name>/build/<name>.map`. On the S3, IRAM code and data share the internal SRAM: less IRAM code = more heap.
- **Crash backtraces** (`*** CRASH DETECTED ON PREVIOUS BOOT ***`): `xtensa-esp32s3-elf-addr2line -pfiaC -e firmware.elf <addresses>` (toolchain under `/root/.cache/esphome/idf/tools/` in the container), only with the ELF of the crashed build.

## Measured levers

| Lever | Gain | Side effects / decision |
| --- | --- | --- |
| Task stacks of mixer, resamplers, media sources in PSRAM | before: minimum ~14.5 kB in an Assist run, ~7 kB while capturing, and the mixer task could not start (no TTS, no wake sound) | none; kept |
| `flash_chip: winbond` + IPv6 off | −6.1 kB static, +8.7 kB free idle | none; kept |
| WiFi IRAM optimizations off (`CONFIG_ESP_WIFI_IRAM_OPT`, `_RX_IRAM_OPT`, `_EXTRA_IRAM_OPT`) | −22.8 kB IRAM code | slightly lower peak WiFi throughput, no music stutter in daily use; kept |
| 10 instead of 16 static WiFi RX buffers (+ `CONFIG_ESP_WIFI_RX_BA_WIN: 20`) | ~9.6 kB | more packet loss risk on WiFi bursts, gain not needed; not kept |
| AEC `filter_length` 4 → 8 | ~3.7 dB less residual echo below 1 kHz | −13 kB internal, core0 load without playback ~11 % → ~50 % (with music + capture ~74 %); not kept |
| `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` 4096 → 1024 | ~2 kB | the 1–4 kB blocks are internal on purpose (task stacks, DMA buffers); not kept |
| Monitor, telemetry, capture component | 280 B static; the capture buffer/task/socket exist only while capturing (~7 kB) | removing them gains nothing |
| Logger level | RAM ~0 | saves flash and some CPU only |

Static internal RAM (`esp_idf_size`): baseline 164.5 kB D/IRAM used (88.5 kB IRAM code) → 135.7 kB (62.8 kB IRAM code) with `flash_chip`, IPv6 off and WiFi code out of IRAM.

### Where the internal RAM goes

Diagnostic build after music + Assist (`filter_length: 8`): `loopTask` (component `setup()` allocations incl. task stacks) ~106 kB, Audio Stack audio task ~61 kB (I2S/AEC buffers), WiFi driver ~33 kB, `ipc0` ~12 kB, lwIP ~11 kB, Sendspin protocol ~8 kB; micro_wake_word and Sendspin sync keep their ~56 kB each in PSRAM. By size, blocks of 1–4 kB dominate (52 blocks, 116 kB).

## Problems seen

- **`filter_length: 6` is broken:** core0 at 100 %, no audio frames, heap corrupted (the next reboot aborted in `tlsf_free`). Never use it.
- **Runtime AEC reinit** (mode switch at runtime): internal RAM dropped to 5.6 kB (two AEC instances), I2S error "TX frame not aligned to DMA buffer", 17 s audio outage, then the AEC added echo until the next reboot. Change AEC settings in YAML and flash.
- **micro_wake_word ring buffer overflow** (`Not enough free bytes in ring buffer ... Resetting`) at music/stream starts (and capture start/stop), usually right after `speaker_source.media_player took a long time for an operation (~70–130 ms)`. Not a RAM problem: the ring buffer holds only 120 ms (fixed in ESPHome) and the inference task runs at priority 3, so stream/codec setup blocks it. Effect: ~120 ms of audio missing for the wake word, once per music start. Harmless unless it shows up during normal playback or near missed wake words.
- **Music start transient:** Sendspin `Lost sync` and `spk_underrun` 26–80 in the first window after a stream start (short audible glitch), then 0 during steady music.
- **Harmless at boot:** `debug set Warning flag: unspecified` (cleared ~1 s later) and once `api took a long time for an operation (52 ms)`.
- Clean: hundreds of session restarts and reboots (mic bug tests), the RX clock scan, `bits_per_sample: 32` (49.5 kB internal free) and a full switch/feature run (siren, mute, LEDs, AEC off/on, wake word off/on, Assist) showed no `[E]`, no crash and no `spk_underrun` during steady music.

## Further levers (not tested)

| Lever | Expected gain | Risk / cost |
| --- | --- | --- |
| `aec_ref_ring_in_psram: true` | a few kB | more PSRAM traffic on core 0 |
| `dma_desc_num` 6 → 4 (or 3) | ~10 kB (~16 kB) | less reserve against dropouts |
| `audio_task_stack_in_psram: true` | 8 kB | real-time task, measure timing |
| Disable Sendspin | hub task + buffers + protocol task | no multi-room playback |

Notes: `sr_high_perf` needs a **contiguous** DMA-capable internal block of 40 kB per 4 filter taps at setup, otherwise the AEC fails until the next flash. `bits_per_sample: 32` doubles the I2S DMA buffers (~32 → ~64 kB).
