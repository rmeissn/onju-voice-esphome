# Onju Voice ESPHome

ESPHome configuration for the original Onju Voice pcb (ESP32-S3, 2x I2S MEMS microphones, I2S amplifier, 6 LEDs, capacitive touch buttons, hardware mute switch).

Code is inspired by https://github.com/tetele/onju-voice-satellite and https://github.com/s00500/onjuconfig and https://github.com/esphome/home-assistant-voice-pe

## Configurations

| File | What it is | Status |
| --- | --- | --- |
| `onju_duplex_i2s.yaml` | Microphone and speaker run at the same time (full duplex) via [esphome-audio-stack](https://github.com/n-IA-hane/esphome-audio-stack), with software echo cancellation. Voice assistant, wake word (also while music is playing) and music via Music Assistant / Sendspin. | Tested on two devices with ESPHome 2026.10 / Audio Stack 2026.11 (wake word, Assist, music, wake word during music). The microphone read issue is fixed via two Audio Stack options (fork branch until upstream releases them), see [docs/AUDIO_INVESTIGATION.md](docs/AUDIO_INVESTIGATION.md). Feedback welcome. |
| `onju_media_player.yaml` | Media player only (no microphone, no voice assistant), with Sendspin. E.g. for speakers that should only play music. | Rock stable, in daily use. |
| `onju_single_i2s.yaml` | Classic variant with ESPHome components only. Microphone and speaker share the I2S bus alternately, so the microphone is stopped during playback (no wake word while audio is playing). Sendspin is included. | Not yet tested after the update to ESPHome 2026.10. |

`main.yaml` is an example device file that combines one of the configurations with `wifi.yaml` and `ota.yaml`.

## Requirements

- ESPHome **2026.10.0** or newer (ESP-IDF framework)
- `onju_duplex_i2s.yaml` uses esphome-audio-stack **v2026.11.0** plus the mic fix options `rx_clock` / `rx_data_delay`, from the fork branch `rmeissn/esphome-audio-stack@onju/i2s-clock-source` until an upstream release contains them (pulled automatically as external component)
- Music Assistant (2.10.7 or newer for the native Sendspin pairing) if you want to use Sendspin

## Getting started

A detailed step-by-step guide for reusing this configuration is in [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md): flashing a general state, finding and setting your flash chip, the sounds included in [`sounds/`](sounds/) (used by default; or use your own / the Voice PE sounds) and, optionally, finding out which of the two microphones is better for your unit.

### Partition table / OTA size

An OTA update only replaces the firmware, **not the partition table**. The device keeps the partition table of the last flash via USB cable. With `flash_size: 16MB`, ESPHome 2026.10 creates two app partitions of about 7.9MB each, and the build output reports the free space based on this table. If your Onju was flashed via cable with an older or smaller layout, the real OTA partitions on the device are smaller and larger builds (e.g. `onju_duplex_i2s.yaml` at ~2.9MB) fail via OTA, although the build says there is plenty of space left.

Fix: flash once via USB cable with the factory image (bootloader, partition table and firmware; in the ESPHome Device Builder via Install -> manual download -> factory format, OTA installs don't build it). Afterwards, OTA updates use the large partitions. Settings stored on the device (volume, switch states, Sendspin pairing) are reset by this; Wi-Fi and API come from the configuration. The partition table of a device is printed in its boot log (with the `debug` component also in the normal ESPHome log, look for `Partition table:`).

Without USB, ESPHome 2026.10+ can also migrate the layout via OTA: a small temporary firmware with `ota: allow_partition_access: true`, then an OTA update of the bootloader (the old one may only know 4MB flash) and of the partition table with the `esphome upload --bootloader / --partition-table` CLI. Loss of power during these steps can require a USB recovery. See [docs/PARTITION_MIGRATION.md](docs/PARTITION_MIGRATION.md).

## Capabilities

`onju_duplex_i2s.yaml` supports:

* Wake word ("Okay Nabu", adjustable sensitivity) and activation via the top action button
* Wake word and voice commands while music is playing or a timer is ringing (via software echo cancellation)
* Music playback via Music Assistant (Sendspin, incl. group playback) and Home Assistant
* Ducking: music is practically muted while you speak to the assistant and quieter during replies and Home Assistant announcements
* Muting (hardware switch and software switch)
* Volume change via the side touch buttons, with LED volume indicator (optionally also for volume changes from HA / Music Assistant)
* Timers (setting, ringing, cancelling) and an alarm siren mode, with adjustable timer and alarm volumes
* Play/Pause, stop timer ringing, stop announcements and start the voice assistant via the top action button
* Soft reset via long press on the top action button, double/triple/long press events for automations in Home Assistant
* LED feedback for all states, including a (switchable) connection error indicator
* A dedicated `"stop"` wake word to stop a ringing timer (enabled only while ringing)
* Requests Home Assistant pipeline noise suppression (`level 1`) and auto gain (`16 dBFS`) (ignored by HA up to 2026.11, forwarded from a later release via home-assistant/core PR #190096)

What is currently missing (in comparison to the Voice PE):
* Visual timer "time elapsed" feedback - too few LEDs
* Hardware echo cancellation - the software echo cancellation of the Onju isn't as good as a dedicated chip, so triggering the wake word while loud music is playing might not work each and every time

Overall, the onju works quite well in combination with
Music Assistant. But it's still not an Alexa or other proprietary
product... don't expect a comparable experience, it's only similar.

## Recommendations

You can use any speech-to-text (STT) engine, conversation agent and text-to-speech (TTS) engine you like.

My current setup, tuned for speedy responses, is:

- STT: Mistral Voxtral via [Whisper STT Cloud API Integration](https://github.com/fabio-garavini/ha-openai-whisper-stt-api) - excels other models for European languages (other than English), is good for bad signal-noise-ratios and usually responds in less than 500ms
- Conversation Agent: Gemini 3.5 Flash Lite through OpenRouter via the streaming capable [Local OpenAI LLM Integration](https://github.com/skye-harris/hass_local_openai_llm).
  - Alternative: gpt-oss-120b is a usable model as well - fast and cheap, especially when pinned to providers like Groq Cloud or Cerebras (lowest latency and highest throughput). It seems capable in calling the right functions/tools.
- TTS: ElevenLabs Turbo v4 through OpenRouter via the [OpenAI TTS Integration](https://github.com/sfortis/openai_tts) - quite fast, adjustable speaking speed (really a pro!)
  - Alternative: the official [ElevenLabs Integration](https://www.home-assistant.io/integrations/elevenlabs/) works well, too, and ElevenLabs offers a free tier that is often sufficient.

This whole setup is the fastest combination I've found so far (action & response speed), while not setting things up locally.

## Hardware notes

The Onju has only one I2S bus for both the microphones and the speaker amplifier. With the standard ESPHome components, the bus can either receive (microphone) or send (speaker), so `onju_single_i2s.yaml` has to stop the microphone during any audio playback and start it again afterwards. This is why the wake word doesn't work while audio is playing in that configuration.

`onju_duplex_i2s.yaml` solves this with esphome-audio-stack: one component owns the shared bus and runs capture and playback at the same time. The bus runs at 48kHz for music playback, the microphone signal is converted to 16kHz for the wake word and the voice assistant, and an echo canceller removes the device's own playback from the microphone signal, using the played audio as reference (the Onju has no hardware loopback).

In earlier tests, using both microphones as a stereo signal reduced the signal-to-noise ratio from about 16-20db to 1-2db. The duplex configuration now averages both microphones into one signal; if this turns out to be worse on your device, the configuration contains a comment on how to fall back to a single microphone.

## Known issues and ideas

* **Hardware limit – no on-device AFE / BSS (beamforming), NS, or AGC:** `esp_afe` with dual-mic BSS/beamforming, noise suppression, VAD, and auto gain was tested (also seperately), but in this combination of media player playback, wake word detection, and Voice Assistant it exceeds the ESP32-S3's CPU headroom ([docs/RESOURCE_INVESTIGATION.md](docs/RESOURCE_INVESTIGATION.md)). Standalone echo cancellation (`esp_aec`) is the practical limit for this workload.
* With the two SPH0645 microphones on the shared full-duplex bus, the current esphome-audio-stack release reads the microphone data at a random position per audio session (boot): often one bit late, sometimes slipping (then the wake word and speech recognition fail until the next restart). Cause: the internal I2S receive slave samples too late with its coarse clock. A fix as two new options (`rx_clock: fast`, `rx_data_delay: pos_edge`, ESP32-S3) is tested on two devices and offered to esphome-audio-stack as [pull request #17](https://github.com/n-IA-hane/esphome-audio-stack/pull/17); until a release contains it, `onju_duplex_i2s.yaml` loads the component from the fork branch. With the fix the level is 6 dB lower, `input_gain: 2.0` restores it.
* With esphome-audio-stack 2026.7, the wake word detection could stop after a while until the device was restarted (an I2S error permanently stopped the microphone). Since 2026.11 the audio path recovers, but only on the next start request. If this still happens, a wake word watchdog (described in a comment in `onju_duplex_i2s.yaml`) could restart the wake word automatically.
* The automatic switch-off of a ringing timer (15 min) and of the alarm siren (5 min) uses a delay that isn't cancelled when it is triggered again, so a second timer within that time could be switched off early. A fix is described in a comment in the configurations.
* Idea: also enable the `"stop"` wake word during long TTS replies or Home Assistant announcements (currently only active while a timer rings).
* Note: microphone gain and the better microphone (left, right, or averaged) can vary per unit; [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md#4-optional-find-out-which-microphone-is-better) explains how to measure your unit and override the microphone choice, [docs/AUDIO_INVESTIGATION.md](docs/AUDIO_INVESTIGATION.md) has the background.

## Credits

- Sounds in [`sounds/`](sounds/): authors and licenses (CC BY 4.0, CC0) in [`sounds/CREDITS.md`](sounds/CREDITS.md).
