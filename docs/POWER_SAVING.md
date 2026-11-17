# Power saving for the media player config (idea, not built yet)

Goal: `onju_media_player.yaml` (media player only, no microphone) sleeps while nothing plays and runs at full speed while it plays. The duplex and single configs are not candidates: their I2S bus and wake word run all the time, so the chip can never sleep, and dynamic frequency scaling would throttle the AEC and wake word tasks (see "CPU frequency" below).

Needs ESPHome >= 2026.11 (`power_management:` with the `esp32_power_management` platform, new in that release). Everything below marked *verified* was checked in the ESP-IDF 5.5.6 or ESPHome 2026.11 beta sources; everything else is unverified until it was measured on a device.

## How it works

ESP-IDF power management works with **locks** (`esp_pm_lock_*`). Drivers hold locks while they need a clock or must not sleep; the chip picks the lowest mode no lock forbids.

| Locks held | CPU clock (ESP32-S3, `max_frequency` 240 MHz) | Light sleep |
| --- | --- | --- |
| none | `min_frequency` (default: crystal, 40 MHz) | yes, when every task is idle for `idle_time_before_sleep` (default 3 ms) |
| `ESP_PM_APB_FREQ_MAX` only | **80 MHz** | no |
| `ESP_PM_CPU_FREQ_MAX` | 240 MHz | no |

*Verified* (`esp_pm/pm_impl.c`, `esp_driver_i2s`, `esp_wifi/src/wifi_init.c`, ESPHome `esp32_power_management.cpp`):

- An enabled I2S channel holds `ESP_PM_APB_FREQ_MAX` (with the PLL clock source). While the speaker plays, the chip does not sleep, but the CPU runs at **80 MHz**, not 240 MHz.
- WiFi holds only `ESP_PM_APB_FREQ_MAX` too. `ESP_PM_CPU_FREQ_MAX` is held only by SPI, LCD and the mbedTLS hardware crypto drivers, never by audio, WiFi or ESPHome itself. "Waking up" does not raise the clock to 240 MHz; only a CPU lock does.
- WiFi stays connected in light sleep: the station wakes for the AP's beacons (DTIM, typically every 100–300 ms) and receives buffered packets then. This needs WiFi power save: ESPHome's default `power_save_mode` on ESP32 is `light`, but the shared `wifi.yaml` sets `NONE` (radio always receiving), which keeps the chip awake. The media player devices would need `power_save_mode: light`; a play command then waits for the next beacon instead of arriving at once (unverified how noticeable).
- Sendspin switches WiFi power save off by itself while it streams (`enable_runtime_power_save_control`). Other media sources (HA URLs via `audio_http`) do not.

So the wanted behaviour comes mostly by itself: idle → no locks → light sleep; play command arrives over the network → speaker starts → I2S lock → awake until the speaker stops.

## Today

`onju_media_player.yaml` runs at a fixed `cpu_frequency: 240MHz` (no `power_management:`, so no frequency scaling and no light sleep), and `wifi.yaml` sets `power_save_mode: NONE`, so the WiFi radio never sleeps. Why `NONE` was chosen is not documented (likely streaming stability and reaction time). The always-on radio is probably the largest idle consumer (estimate, not measured).

## What the config needs

0. **WiFi power save** for the media player devices: `power_save_mode: light` as an override (not in `wifi.yaml`, the duplex devices keep `NONE`). Works without ESPHome 2026.11 and can be measured on its own.
1. **Let I2S stop when idle.** Today the hardware speaker and both mixer inputs have `timeout: never`, so the I2S channel stays open and the chip never sleeps. Set e.g. `timeout: 2s` on all three (resampler/mixer chain must all go idle for the I2S speaker to stop). Why `never` was chosen is not documented (it came with the first version of the config); possible reasons: a pop at playback start, start latency. With a timeout, a paused stream also lets the chip sleep after the timeout.
2. **Power management block:**

   ```yaml
   power_management:
     - platform: esp32_power_management
       enable_light_sleep: true
       # max_frequency defaults to esp32 cpu_frequency (240MHz)
   ```

   `power_down_flash` is rejected with PSRAM; `power_down_peripherals` does not exist on the S3.
3. **CPU clock while playing**, one of:
   - **A: own CPU lock while playing** (matches the idea "idle low, playing 240 MHz"): create an `ESP_PM_CPU_FREQ_MAX` lock once (`esp_pm_lock_create`), acquire it when the media player starts playing or an announcement starts, release it when it goes idle. Needs a lambda with `#include "esp_pm.h"` and a global handle; not built, exact triggers to be chosen (media player state changes or speaker start/stop).
   - **B: `min_frequency: 240MHz`**: no frequency scaling at all, only light sleep. Simplest, saves less while awake.
   - **C: keep 80 MHz while playing**: may be enough for FLAC/MP3 decoding, resampler and mixer of a mono 48 kHz stream; measure CPU load with `diagnostics/monitor.yaml` and listen for stutter / `spk_underrun` first.

## Risks and test points (all unverified)

- **Touch buttons** (`esp32_touch`, GPIO2/3/4): are presses during sleep detected reliably? The main loop wakes every ~16 ms anyway; unclear whether ESPHome reads the S3 touch sensor by interrupt or polling.
- **Hardware mute switch** (GPIO38, `gpio` binary sensor): a GPIO edge during light sleep may be lost and leave a wrong state. Fallback: `use_interrupt: false` (polled every loop).
- **Sendspin time sync** gets noisier while the chip sleeps between beacons; check that group playback is in sync right from the start of a stream.
- **Pop/click** when I2S starts again after the timeout (`keep_alive` from ESPHome 2026.11 does not help: it keeps the channel open and therefore the lock).
- **LEDs** (SK6812 on GPIO11) latch their colour; check for glitches on wake. LED animations keep waking the chip (fine, only costs savings).
- **Alarm siren** (on-device file, started via the API): must start as reliably as today; test while asleep.
- **Interrupt latency** grows to ~40 µs (ESPHome docs); nothing timing-critical runs while idle.
- USB: light sleep is blocked while a USB host (PC) is connected; a USB power supply is fine.
- The component is new in 2026.11 and uses a tick overflow workaround for ESP-IDF < 6.0.1.

## Measurement plan

USB power meter between supply and device, one black device, each step a few minutes idle plus one playback:

1. Today's firmware: idle and playing.
2. `power_save_mode: light` (WiFi modem sleep), check reaction time to play commands and HA-URL streams for stutter. Works on the current ESPHome version.
3. Additionally `timeout: 2s` on the speaker and mixer inputs (I2S off when idle), optionally the amplifier muted via the DAC mute switch (GPIO21) while idle. Works on the current ESPHome version.
4. ESPHome 2026.11 + `esp32_power_management` with light sleep, CPU option A, B or C.

Decision rule: keep it if the idle draw drops noticeably and none of the test points above fails. Rough expectation (estimate, not measured): 0.1–0.3 W less idle, about 1–2.5 kWh per device and year.
