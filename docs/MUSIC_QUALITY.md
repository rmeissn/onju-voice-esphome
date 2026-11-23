# Better music quality with Music Assistant

The Onju has a very small speaker. Music from it sounds thin: it has little bass, and the treble and the midrange do not sit right. Music Assistant can correct this for every player with its built-in sound processing (DSP, an equalizer plus a few helpers). This page describes the settings I use for my Onju devices, how to set them up and where the limits are.

You do not need this to use the Onju, it only improves how music sounds.

## In short

- **It is a processing pipeline that I measured and tuned for Music Assistant.** The same pipeline (a high pass, an equalizer, a limiter) can in principle be used with other software, as long as that software supports such processing. The equalizer comes as a file in the widely used Equalizer APO format; Music Assistant's documentation names Equalizer APO and EasyEffects as other programs that read or write it. The high pass and the limiter are plain settings that you can recreate anywhere.
- **It is applied on the server side, not on the device.** The Onju cannot do this processing itself at the moment: the ESPHome configuration of this repository has no equalizer. So the correction has to be done by the software that sends the music (here Music Assistant), and nothing in the ESPHome configuration changes.
- **The difference is clearly audible.** In my listening, the Onju sounds much better with it: much less like a tin can, and somewhat warmer.
- **That is probably as much as you can get out of it.** The speaker is very small, and an equalizer cannot create bass that the speaker physically does not deliver. The device will not produce much more bass than this, at least as long as Music Assistant offers no further DSP options.

## What the settings do

The DSP is a chain of steps that the music passes through before it is sent to the player:

| Step | Setting | Purpose |
| --- | --- | --- |
| Input | gain 0 dB (the default) | unchanged |
| High pass | **120 Hz**, slope **24 dB/oct** | removes the deep bass the small speaker cannot reproduce anyway; it only adds distortion |
| Parametric EQ | preamp **-8 dB** and **7 bands** (below) | the correction of the frequency response |
| Safety limiter | ceiling **-1.5 dB**, as the **last** step after the EQ | catches peaks that the boosts would push into clipping |
| Output | gain 0 dB (the default) | unchanged |

The equalizer bands are in the file [music_assistant_eq_preset.txt](music_assistant_eq_preset.txt):

| Band | Frequency | Gain | Width (Q) |
| --- | --- | --- | --- |
| Preamp | | -8.0 dB | |
| 1 | 180 Hz | +8.0 dB | 0.9 |
| 2 | 420 Hz | -4.0 dB | 0.9 |
| 3 | 800 Hz | +3.0 dB | 2.0 |
| 4 | 1350 Hz | -4.5 dB | 1.3 |
| 5 | 2800 Hz | -1.0 dB | 1.2 |
| 6 | 5500 Hz | +3.5 dB | 1.0 |
| 7 | 9000 Hz | +8.0 dB | 0.9 |

In short, the correction lifts the bass (around 180 Hz) and the treble (around 9 kHz) and lowers the range around 420 Hz and 1.3 kHz. That is the shape you would expect for a small speaker that is weak at both ends and pushes the midrange. The preamp of -8 dB is as large as the biggest boost; it leaves room for the boosts, so the music does not clip.

## How I got the values

I measured the speaker with the iPhone app [HouseCurve](https://housecurve.com/) and interpreted the measurements together with an AI (Claude) to find a good equalizer setting.

- HouseCurve compensates for the iPhone's microphone, and the iPhone microphones are quite good. HouseCurve itself says that you get great results with just an iPhone or iPad, and that external calibrated microphones add accuracy.
- Without a professional measurement setup (a calibrated measurement microphone and controlled conditions) I do not expect to get noticeably further than this. The room and the position of the device still influence the result.
- The measurement data itself is not part of this repository, only the resulting settings.
- I did not measure how this affects the wake word or the echo cancellation while music plays; this repository has no data on that.

Use the settings as a very good starting point and judge them by ear (see "Compare" below).

## Set it up in Music Assistant

Music Assistant has DSP settings **for each player**. Do this for every Onju you want to improve.

1. In Music Assistant open the settings of your Onju player and go to its **DSP** page. Switch on the DSP for this player.
2. Leave the **input** gain at 0 dB.
3. Add a **High and Low Pass** filter: mode **High pass**, frequency **120 Hz**, slope **24 dB/oct**.
4. Add a **Parametric Equalizer** and press **Import APO/REW Preset**. Choose the file [music_assistant_eq_preset.txt](music_assistant_eq_preset.txt) from this repository. This sets the preamp and the 7 bands from the table above.
5. Add a **Safety Limiter** with the ceiling **-1.5 dB** and make sure it sits **after** the equalizer.
6. Leave the **output** gain at 0 dB.

The order must be: input, high pass, equalizer, safety limiter, output. The file contains only the equalizer (step 4); high pass and limiter you set by hand.

**Compare:** the DSP has an on/off switch. Switch it while the same song plays and listen carefully. The ear gets used to a change quickly, so a first impression can mislead. If you like, save your settings as a preset in Music Assistant to reuse them for other players.

## Limits

- **Only music from Music Assistant is improved.** The DSP works on the audio stream that Music Assistant sends to the player. Everything that does not come from Music Assistant is left as it is: for example announcements and voice replies from Home Assistant, audio that Home Assistant plays directly, and the sounds of the device itself (wake sound, timer, alarm siren).
- **It works per player.** Settings for one Onju have no effect on other players. When several players play together, Music Assistant uses the individual DSP settings only for Universal groups and when the players are used via AirPlay, Squeezelite or Sendspin; for groups using other protocols the DSP is switched off (as described in the Music Assistant documentation).
- **The music becomes quieter.** The preamp lowers the level by up to 8 dB, so you have to set the volume a bit higher than without the DSP.
- **Boosts have a price.** A small speaker has little headroom. If you hear distortion at high volume, reduce the boosts at 180 Hz and 9 kHz (and the preamp by the same amount), or lower the volume. The Music Assistant documentation recommends cutting rather than boosting where possible.
- **Your device can differ.** I tuned the settings for my speaker in my rooms. Other units and other rooms can sound a bit different; change the bands by ear if something sounds off.

## Make your own correction

If you want a correction for your own unit, Music Assistant imports the Equalizer APO format and the filter export of Room EQ Wizard (REW). Its documentation also mentions the iPhone app HouseCurve as a source of such presets, and that is the app I used for the measurement above. Import the file with **Import APO/REW Preset** in the Parametric Equalizer, as above. With **Export APO Preset** you get the current settings back as a file.

## More

- Music Assistant documentation: [Digital Signal Processing](https://www.music-assistant.io/dsp/), [Parametric Equalizer](https://www.music-assistant.io/dsp/parametriceq/).
- The microphone side of the Onju (what it hears) is covered in [AUDIO_INVESTIGATION.md](AUDIO_INVESTIGATION.md); this page is only about the speaker.
