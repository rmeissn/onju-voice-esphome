# Onju Voice ESPHome

ESPHome configuration for the original Onju Voice pcb (ESP32-S3, 2x I2S MEMS microphones, I2S amplifier, 6 LEDs, capacitive touch buttons, hardware mute switch).

Code is inspired by https://github.com/tetele/onju-voice-satellite and https://github.com/s00500/onjuconfig and https://github.com/esphome/home-assistant-voice-pe

## Configurations

| File | What it is | Status |
| --- | --- | --- |
| `onju_duplex_i2s.yaml` | Microphone and speaker run at the same time (full duplex) via [esphome-audio-stack](https://github.com/n-IA-hane/esphome-audio-stack), with software echo cancellation. Voice assistant, wake word (also while music is playing) and music via Music Assistant / Sendspin. | Builds with ESPHome 2026.10, not yet tested on the device after the update to ESPHome 2026.10 / Audio Stack 2026.11. Should work, feedback welcome. |
| `onju_media_player.yaml` | Media player only (no microphone, no voice assistant), with Sendspin. E.g. for speakers that should only play music. | Rock stable, in daily use. |
| `onju_single_i2s.yaml` | Classic variant with ESPHome components only. Microphone and speaker share the I2S bus alternately, so the microphone is stopped during playback (no wake word while audio is playing, no Sendspin). | Not yet tested after the update to ESPHome 2026.10. |

`main.yaml` is an example device file that combines one of the configurations with `wifi.yaml` and `ota.yaml`.

## Requirements

- ESPHome **2026.10.0** or newer (ESP-IDF framework)
- `onju_duplex_i2s.yaml` uses esphome-audio-stack **v2026.11.0** (pulled automatically as external component)
- Music Assistant (2.10.7 or newer for the native Sendspin pairing) if you want to use Sendspin

## Getting started

1. Copy `main.yaml` (one file per device), set `name` and `friendly_name` and choose one of the configurations under `packages`.
2. Create a `secrets.yaml` with `wifi_ssid`, `wifi_password`, `fallback_ap_password` and `onju_ota`.
3. Put the sound files into the `www` folder of Home Assistant (served as `/local/...`) and override the `*_sound_url` substitutions with the address of your Home Assistant instance. The sounds are downloaded at build time and stored on the device. They should be FLAC, mono, 48kHz (matching the speaker sample rate), highest compression. The sources of the sounds are listed in the substitutions.
4. Build and flash (the first time via USB cable, afterwards via OTA). See the note on the partition table below.

### Partition table / OTA size

An OTA update only replaces the firmware, **not the partition table**. The device keeps the partition table of the last flash via USB cable. With `flash_size: 16MB`, ESPHome 2026.10 creates two app partitions of about 7.9MB each, and the build output reports the free space based on this table. If your Onju was flashed via cable with an older or smaller layout, the real OTA partitions on the device are smaller and larger builds (e.g. `onju_duplex_i2s.yaml` with about 2.4MB) fail via OTA, although the build says there is plenty of space left.

Fix: flash once via USB cable with the `firmware.factory.bin` from the build output (it contains bootloader, partition table and firmware). Afterwards, OTA updates use the large partitions. Settings stored on the device (volume, switch states, Sendspin pairing) are reset by this; Wi-Fi and API come from the configuration. The partition table of a device is printed in its serial boot log.

## Capabilities

`onju_duplex_i2s.yaml` supports:

* Wake word ("Okay Nabu", adjustable sensitivity) and activation via the top action button
* Wake word and voice commands while music is playing or a timer is ringing (software echo cancellation)
* Both microphones averaged to one signal; adjustable mic gain and an echo cancellation switch for testing
* Music playback via Music Assistant (Sendspin, incl. group playback) and Home Assistant
* Ducking: music is practically muted while you speak to the assistant and quieter during replies and Home Assistant announcements
* Muting (hardware switch and software switch)
* Volume change via the side touch buttons, with LED volume indicator (optionally also for volume changes from HA / Music Assistant)
* Timers (setting, ringing, cancelling) and an alarm siren mode (full volume with a custom sound)
* Play/Pause, stop timer ringing, stop announcements and start the voice assistant via the top action button
* Soft reset via long press on the top action button, double/triple/long press events for automations in Home Assistant
* LED feedback for all states, including a (switchable) connection error indicator

What is currently missing (in comparison to the Voice PE):

* A dedicated stop word to stop a ringing timer (the wake word works while ringing, though)
* Visual timer "time elapsed" feedback - too few LEDs
* Hardware echo cancellation - the software echo cancellation of the Onju isn't as good as a dedicated chip, so triggering the wake word while loud music is playing might not work each and every time

Overall, the onju works quite well in combination with
Music Assistant. But it's still not an Alexa or other proprietary
product... don't expect a comparable experience, it's only similar.

## Recommendations

You can use any speech-to-text (STT) engine, conversation agent and text-to-speech (TTS) engine you like.

My current setup, tuned for speedy responses, is:

- STT: Mistral Voxtral via [Whisper STT Cloud API Integation](https://github.com/fabio-garavini/ha-openai-whisper-stt-api) - excels other models for European languages (other than English), is good for bad signal-noise-ratios and usually responds in less than 500ms
- Conversation Agent: gpt-oss-120b through openrouter (pinned to Groq Cloud or Cerebras - lowest latency and highest througput) via a [Streaming capable Integration](https://github.com/skye-harris/hass_local_openai_llm). The model seems capable in calling the right functions/tools and is much cheaper than e.g. OpenAI. Switching to Groq.cloud directly might improve response speeds even more.
- TTS: Elevenlabs - quite fast, offers a free tier, adjustable speaking speed (really a pro!), but got problems with pronunciation of dates and numbers as numerics (at least in German)

This whole setup is the fastest combination I've found so far (action & response speed), while not setting things up locally.

## Hardware notes

The Onju has only one I2S bus for both the microphones and the speaker amplifier. With the standard ESPHome components, the bus can either receive (microphone) or send (speaker), so `onju_single_i2s.yaml` has to stop the microphone during any audio playback and start it again afterwards. This is why the wake word doesn't work while audio is playing in that configuration.

`onju_duplex_i2s.yaml` solves this with esphome-audio-stack: one component owns the shared bus and runs capture and playback at the same time. The bus runs at 48kHz for music playback, the microphone signal is converted to 16kHz for the wake word and the voice assistant, and an echo canceller removes the device's own playback from the microphone signal, using the played audio as reference (the Onju has no hardware loopback).

In earlier tests, using both microphones as a stereo signal reduced the signal-to-noise ratio from about 16-20db to 1-2db. The duplex configuration now averages both microphones into one signal; if this turns out to be worse on your device, the configuration contains a comment on how to fall back to a single microphone.

## Known issues and ideas

* With esphome-audio-stack 2026.7, the wake word detection could stop after a while until the device was restarted (an I2S error permanently stopped the microphone). Since 2026.11 the audio path recovers, but only on the next start request. If this still happens, a wake word watchdog (described in a comment in `onju_duplex_i2s.yaml`) could restart the wake word automatically.
* The automatic switch-off of a ringing timer (15 min) and of the alarm siren (5 min) uses a delay that isn't cancelled when it is triggered again, so a second timer within that time could be switched off early. A fix is described in a comment in the configurations.

## Areas to improve on

This is a list of areas that would improve the Onju from an ESPHome perspective, with their current status.

1. Full duplex I2S bus together with a proper microphone downsampling algorithm
   - **Implemented in `onju_duplex_i2s.yaml`** (not yet tested on the device after the update to ESPHome 2026.10): esphome-audio-stack owns the shared bus, runs microphone and speaker at the same time and converts the microphone from 48kHz to 16kHz with Espressif's rate converter (instead of taking each third sample, which introduces audible artifacts). The former custom proxy component isn't needed anymore.
   - Native ESPHome (`i2s_audio`) still can't do full duplex on one bus. Since 2026.10 it has a resampler microphone platform, which covers the downsampling part.
2. Noise Suppression & Auto Gain for the microphone
   - **Partially:** the voice assistant requests noise suppression and auto gain from the Home Assistant pipeline (`noise_suppression_level`, `auto_gain`). On the device, the duplex configuration only applies echo cancellation (`esp_aec`).
   - On-device noise suppression and auto gain are available with the Espressif AFE (`esp_afe`) of esphome-audio-stack, but not used yet. The microphone on the Onju captures a lot of noise, so this could help with voice commands from more than 2m away. See this link: https://github.com/esphome/issues/issues/6599#issuecomment-3896756251
3. Support for both microphones to apply Blind Source Separation (BSS) and Multi Input Single Output (MISO) strategies
   - **Open:** the duplex configuration averages both microphones into one signal, which is neither BSS nor MISO. It is not tested yet whether the old stereo problem (SNR dropping from about 16-20db to 1-2db) still exists.
   - esphome-audio-stack supports BSS with both microphones via `esp_afe` (`mic_num: 2`), which replaces `esp_aec` and needs more CPU and memory. MISO isn't offered.
4. Software based Acoustic Echo Cancellation for enabling to use wake words (like "okay nabu" or "stop") while something is played on the speaker
   - **Implemented in `onju_duplex_i2s.yaml`** with `esp_aec` (`sr_low_cost`, which preserves the features the wake word model needs). Software AEC isn't as good as a dedicated hardware chip, so the wake word might not trigger every time while loud music is playing.
5. A dedicated "stop" wake word to stop a ringing timer or an announcement
   - **Open:** possible now thanks to echo cancellation, e.g. with the `stop` model that the Voice PE uses.
6. Fine tuning on the device
   - Microphone gain, echo cancellation mode and filter length, and wake word sensitivity need to be tuned on the real device.
