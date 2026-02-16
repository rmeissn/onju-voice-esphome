# Onju Voice ESPHome

ESPHome configuration for the original Onju Voice pcb

Code is inspired by https://github.com/tetele/onju-voice-satellite and https://github.com/s00500/onjuconfig and https://github.com/esphome/home-assistant-voice-pe

## Capabilities

The configuration supports:

* Wake word activation & wake by action button (top button)
* Muting (hardware switch and software switch)
* Music playback via Music Assistant
* Volume change via side touch buttons
* Timers (setting, ringing, cancelling)
* Alarm Siren mode (full volume with custom sound)
* Dynamic capacitive button calibration (working for the revised esphome esp32_touch code)
* Improved LED control script & proper visual feedback of states
* Play/Pause/Stop Timer Ringing/Activate Voice Assistant via top action button
* Soft reset via long press on the top action button
* Tuned timings for stopping and starting the microphone - due to a hardware limitation
* Tuned microphone characteristics (based on what ESPHome provides)

What is currently missing (in comparison to Voice PE):

* Stop word to stop a ringing timer - hardware limitation
* Wake word while audio output - hardware limitation
* Visual timer "time elapsed" feedback - too few LEDs
* Sendspin support - not stable yet

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

Beware that functionality might break with other ESPHome versions than 2026.1.
Also, the script for the touch calibration depends heavily on the esphome implementation of esp32_touch.

## Areas to improve on

This is a list of areas that would improve the Onju from a ESPHome perspective. Most of these things are currently not supported or implemented by ESPHome, but maybe someone is willing to do so.

1. Full duplex I2S bus together with a proper microphone downsampling algorithm
    - We wouldn't need to start/stop the microphone anymore and also wouldn't need the custom component anymore. As music playback is favorable at 48kHz, the bus must operate at 48kHz. As mww and va require 16kHz input, the microphone component needs a downsampling approach (other than each third sample, which introduces audible artifacts)
2. Noise Suppression & Auto Gain for the microphone
    - The microphone on the Onju is okay, but captures a lot of noise so that the signal-to-noise ratio (SNR) isn't as good as it could be. Applying noise suppression would help here, while auto gain would help in issuing voice commands from more than 2m away without shouting. See this link: https://github.com/esphome/issues/issues/6599#issuecomment-3896756251
3. Support for both microphones to apply Blind Source Seperation (BSS) and Multi Input Single Output (MISO) strategies
    - when the microphone is set to stereo, the SNR goes from about 16-20db down to 1-2db, which is very very bad (must be a bug or something like that, that needs to be fixed first). BSS helps in seperating the signal (your voice) from background garbage by using two mics, while MISO selects the mic with the best SNR to proceed with. See this link: https://github.com/esphome/issues/issues/6599#issuecomment-3896756251
4. Software based Acousting Echo Cancellation for enabling to use wake words (like "okay nabu" or "stop") while something is played on the speaker
    - Requires 1. as a prereqisite, but might be too intensive for the processor.... software AEC might not be the holy grail in comparison to specialized hardware chips, but might be good enough to e.g. stop the timer with the "stop" wakeword. See this link: https://github.com/esphome/issues/issues/6599#issuecomment-3896756251

## Hardware Limitations

On the Onju, the microphone needs to be stopped on any audio playback and started on non-audio playback, which is a bit hacky. This is necessary as only one I2S bus for both the microphone and speaker exist, while these components need to use different I2S configurations (speaker: sending, 48kHz, microphone: receiving, 16kHz).

The applied workaround mainly implements starting and stopping the microphone at the correct position/timings, so the speaker isn't playing anything from the bus that the microphone sends to the bus (causing a weird cracking sound).

The only problem remaining was that when voice_assistant was asking a followup question, it started the microphone by itself while the low_level speaker wasn't finished yet. Unfortunately it wasn't possible to make assist wait a bit longer or to wait for some condition (like speaker finished), thus I implemented (with Google Gemini) a proxy component that catches state updates from the media_player and delays their delivery to voice_assistant until the speaker is really finished. This makes voice_assistant wait a bit longer, until the media_player says "I'm ready", before voice_assistant starts the microphone. Everything else is delivered to the media_player and vice versa as it is.
