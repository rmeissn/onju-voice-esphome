# Onju Voice ESPHome

ESPHome configuration for the original Onju Voice pcb

Code is inspired by https://github.com/tetele/onju-voice-satellite and by an edited version by someone else (will append the link when I find the repo again).

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
* tuned timings for stopping and starting the microphone - due to a hardware limitation

What is currently missing (in comparison to Voice PE):

* Stop word to stop a ringing timer - hardware limitation
* Wake word while audio output - hardware limitation
* Visual timer "time elapsed" feedback - too few LEDs
* Sendspin support - not stable yet

Overall, the onju works quite well in combination with
Music Assistant. But it's still not an Alexa or other proprietary
product... don't expect a comparable experience, it's only similar.

## Recommendations

I use it with a fast OpenAI oder Google LLM, Mistral Voxtral for STT and Elevenlabs for TTS, which seems to be the fastest combination I've found (action & response
speed).

Beware that functionality might break with other ESPHome versions than 2026.1.
Also, the script for the touch calibration depends heavily on the esphome implementation of esp32_touch.

## Hardware Limitations

On the Onju, the microphone needs to be stopped on any audio playback and started on non-audio playback, which is a bit hacky. This is necessary as only one I2S bus for both the microphone and speaker exist, while these components need to use different configuration (speaker: 48kHz, microphone: 16kHz). 
