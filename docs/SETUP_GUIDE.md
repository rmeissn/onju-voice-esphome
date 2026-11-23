# Setup guide for reusing this configuration

Step-by-step instructions for everyone who wants to run one of the configurations of this repository on their own Onju Voice. You should know how to work with ESPHome and be able to run a command in a terminal; nothing else is assumed. The [README](../README.md) explains what the configurations do; this guide explains what **you** have to do and adjust.

## What to do, in short

**Recommended for every device:**

- **Find your flash chip and set it** ([section 2](#2-find-your-flash-chip-and-set-it)): a few minutes, frees some of the device's scarce memory.
- **Use your own sounds** ([section 3](#3-use-your-own-sounds-or-the-voice-pe-sounds)): the defaults point to my Home Assistant, so you have to set them anyway. The Voice PE sounds work out of the box.

**Optional, fine-tuning:**

- **Find out which microphone is better** ([section 4](#4-optional-find-out-which-microphone-is-better)): not necessary. By default the Onju uses both of its microphones together, which works well even if they differ a bit. A short measurement tells you whether one of them is clearly better, so that using only that one squeezes out a little more.

The sections in order:

1. [Flash a general state](#1-flash-a-general-state)
2. [Find your flash chip](#2-find-your-flash-chip-and-set-it)
3. [Use your own sounds (or the Voice PE sounds)](#3-use-your-own-sounds-or-the-voice-pe-sounds)
4. [Optional: find out which microphone is better](#4-optional-find-out-which-microphone-is-better)

Everything below was written for `onju_duplex_i2s.yaml`. `onju_media_player.yaml` needs the same steps 1 to 3 (it has fewer sounds, see section 3) and has no microphone.

## 1. Flash a general state

You need: an Onju Voice (original pcb, ESP32-S3 N16R8), a USB cable, [ESPHome](https://esphome.io) **2026.10.0 or newer** (the ESPHome Device Builder add-on in Home Assistant works, so does the `esphome` command line), and internet access on the build machine (a component, the wake word models and, depending on your sound setup, the sounds are downloaded while building).

1. **Get the files.** Clone or download this repository into your ESPHome configuration folder. Keep the layout: the configurations include each other by relative path (`onju_duplex_i2s.yaml`, `wifi.yaml`, `ota.yaml`, and `diagnostics/` if you measure the microphones later).
2. **Create `secrets.yaml`** in the same folder with these keys (`wifi.yaml` and `ota.yaml` read them, nothing is hard-coded):

   ```yaml
   wifi_ssid: "your network"
   wifi_password: "..."
   fallback_ap_password: "..."   # password of the fallback hotspot "Onju-Voice"
   onju_ota: "..."               # OTA password, choose any
   ```

   Never commit this file.
3. **Check `wifi.yaml`.** It sets `domain: .fritz.box` (my router). Change it to your local domain or remove the line, otherwise the device is looked up under a name your network does not know.
4. **Copy `main.yaml`** to a file of your own (one file per device, e.g. `living-room.yaml`) and set `name` and `friendly_name`. Under `packages` keep one of these configurations:

   | Package | Use it for |
   | --- | --- |
   | `onju_duplex_i2s.yaml` | Voice assistant, wake word (also during music) and music. The full feature set and fully tested. |
   | `onju_media_player.yaml` | Speaker only (music, timer/alarm sounds), no microphone. The most stable one. |

   `onju_duplex_i2s.yaml` gives the device a MAC suffix in its name (`name_add_mac_suffix: true`); add `esphome: name_add_mac_suffix: false` to your device file if you do not want that.
5. **Set up the sounds.** The six `*_sound_url` substitutions of `onju_duplex_i2s.yaml` (in `onju_media_player.yaml` the three it has are derived from one `ha_base_url`; overriding that one is enough there) point to **my** Home Assistant (`http://192.168.0.202:8123/local/...`). On your network the build fails until you override all of them. The quickest start is the preset in [section 3](#3-use-your-own-sounds-or-the-voice-pe-sounds), it needs no hosting.
6. **Recommended, per unit:** the flash chip setting ([section 2](#2-find-your-flash-chip-and-set-it)). You can add it after the first boot.
7. **Build and flash by USB cable the first time.** In the Device Builder: *Install* → *Plug into this computer*, or *Manual download* → *Factory format* and flash that file (for example with [ESPHome Web](https://web.esphome.io)). A cable flash is needed once because an OTA update does not replace the partition table; an old firmware of the Onju may have a smaller one. Details: [README, "Partition table / OTA size"](../README.md#partition-table--ota-size). Afterwards OTA updates work. This can also be done without a usb cable, see [Partition Migration](PARTITION_MIGRATION.md)
8. **Add the device in Home Assistant.** It is discovered by the ESPHome integration (there is no API encryption key in the configuration). On the device page choose the Assist pipeline (the entity names depend on your Home Assistant version) and, for Sendspin, pair it in Music Assistant (2.10.7 or newer).

### Check that it works

Open the device log (Device Builder: *Logs* → *Wirelessly*, or `esphome logs <file>.yaml`) and look at the boot output:

- `esp_aec` shows `Initialized: YES`. If not, the echo cancellation could not start because not enough memory was free; see [ECHO_CANCELLATION.md](ECHO_CANCELLATION.md) for the fallback.
- Say the wake word ("Okay Nabu") at about 1 m. If it never reacts although the LED/mute state is fine, restart the device and try again: after some restarts the microphone is read wrongly until the next restart (a known problem, details in [AUDIO_INVESTIGATION.md, "Mic read bug"](AUDIO_INVESTIGATION.md#mic-read-bug)).
- Play music, then say the wake word during the music.

## 2. Find your flash chip and set it

The firmware contains driver code for flash chips of several vendors, but only the one for the chip on your board is ever used. The rest wastes a few kB of the device's scarce internal memory, which the duplex configuration needs. Telling ESPHome your chip removes the unused drivers. The chip can differ from unit to unit, so this is a **per-device** setting.

**Find it:**

1. Open the device log (Device Builder: *Logs* → *Wirelessly*, or `esphome logs <file>.yaml`). ESPHome prints its start-up summary when the log connects, so you do not have to catch the boot; if the line is missing, press the device's *Restart* button while the log is open.
2. Look near the top, after `ESP32 Chip: ...`, for a warning like:

   ```text
   [W][app:...]: Set flash_chip: winbond under esp32 > framework > advanced to save IRAM
   ```

   The word after `flash_chip:` is your value. If you see no such line, nothing can be saved on this unit and you do not need the setting.

**Set it** in your device file (the commented block at the bottom of `main.yaml`), with exactly the value from the log:

```yaml
esp32:
  framework:
    advanced:
      flash_chip: winbond   # <- the vendor named in your log
```

Possible values (ESPHome 2026.11): `gd`, `issi`, `mxic`, `winbond`, `boya`, `th`, `mxic_opi`, `generic`. Flash again; the warning disappears from the next boot log. All three units I own report `winbond`.

## 3. Use your own sounds (or the Voice PE sounds)

The device plays short sounds (wake word detected, timer, mute, alarm siren, ...). They are stored **in the firmware**, so they work without Home Assistant. You choose them in your device file: each one takes either a web address (`https://...`) or a file path **relative to the folder of your device file**. The files are read when you build the firmware; each must be at most 5 MB, and WAV, MP3, FLAC and Opus work.

| Setting (substitution) | Played when | Where it exists |
| --- | --- | --- |
| `wakeup_sound_url` | the wake word was detected (and when the top button starts the assistant) | duplex |
| `error_sound_url` | the voice assistant reports an error | duplex |
| `timer_finished_sound_url` | a timer rings (repeated until stopped) | duplex |
| `mute_sound_url`, `unmute_sound_url` | muting / unmuting (hardware switch or software) | duplex, media player |
| `alarm_sound_url` | the alarm siren (looped) | duplex, media player |

The defaults of all of them point to my Home Assistant. The configurations in this repository credit the original sources of my own sounds in comments next to the defaults.

### Quick start: the Voice PE sounds

The sounds of the Home Assistant Voice Preview Edition already have the right format, so you can use them **unchanged**. The Voice PE has no error and no alarm sound, so for those **you have to provide your own files** (or use another Voice PE sound as a stopgap, e.g. `timer_finished.flac` for the alarm; I have not tested that).

Add to the `substitutions:` of your device file:

```yaml
substitutions:
  # ... name, friendly_name as before
  vpe_sounds: "https://github.com/esphome/home-assistant-voice-pe/raw/c16a4d010f39b06e6b14ef9aea85e2f5f280e20a/sounds"
  wakeup_sound_url: "${vpe_sounds}/wake_word_triggered.flac"
  timer_finished_sound_url: "${vpe_sounds}/timer_finished.flac"
  mute_sound_url: "${vpe_sounds}/jack_disconnected.flac"
  unmute_sound_url: "${vpe_sounds}/jack_connected.flac"
  error_sound_url: "sounds/error.flac"   # your own file, see below
  alarm_sound_url: "sounds/alarm.flac"   # your own file, see below
  wake_listen_delay: 550ms               # see "Wake sound and listening start"
```

The address is pinned to a fixed version of the Voice PE repository, so your sounds do not change when they change theirs. The Voice PE sounds are © Clayton Charles Tapp, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): if you pass your firmware on, name the author and the license.

If you prefer to build without internet access for the sounds, download the files into a `sounds/` folder next to your device file and use paths instead of addresses (in the Device Builder this is `/config/sounds/`):

```bash
mkdir -p sounds
for f in wake_word_triggered timer_finished jack_connected jack_disconnected; do
  curl -fL -o sounds/$f.flac \
    "https://github.com/esphome/home-assistant-voice-pe/raw/c16a4d010f39b06e6b14ef9aea85e2f5f280e20a/sounds/$f.flac"
done
```

```yaml
  wakeup_sound_url: "sounds/wake_word_triggered.flac"
  timer_finished_sound_url: "sounds/timer_finished.flac"
  mute_sound_url: "sounds/jack_disconnected.flac"
  unmute_sound_url: "sounds/jack_connected.flac"
```

### Your own sounds

Convert every file to **mono, 48 kHz, 16 bit FLAC** with [ffmpeg](https://ffmpeg.org) (the speaker runs at 48 kHz, so this format is the safe choice):

```bash
ffmpeg -i input.mp3 -ac 1 -ar 48000 -sample_fmt s16 -c:a flac -compression_level 12 sounds/my_sound.flac
```

Keep the sounds short: they sit in the firmware, and the wake sound adds to the time between your wake word and the start of listening. Files that are already mono 48 kHz FLAC (such as the Voice PE sounds) do not need converting. Then set the substitution to the file path or a web address. Free sounds: [freesound.org](https://freesound.org) (check the license of each one: CC0 needs nothing, CC BY needs a credit).

### Wake sound and listening start

After the wake word the device plays the wake sound and starts listening a little later (`wake_listen_delay`, default `450ms`, fits my 550 ms sound). If it starts listening too early, the sound itself ends up in what is sent to speech recognition; too late, and the first words are lost. If you use a different wake sound, set the delay **about 100 ms before the audible part of the sound ends**, for example `550ms` for a sound of 0.65 to 0.7 seconds (the preset above uses this for the Voice PE sound; I have not tuned it on a device).

### Check

After the next build the sounds are in the firmware. Try them: mute and unmute with the hardware switch, say the wake word, set a one-minute timer. If the build stops with a message like `Unable to determine audio file type` or `too large`, the file is broken or over 5 MB.

## 4. Optional: find out which microphone is better

**This section is optional.** The Onju has two microphones, and `onju_duplex_i2s.yaml` uses both together by default. That works well even if they differ a bit. But on some units one of the two microphones picks up the device's **own speaker** much louder than the other one. On my first unit the difference was 23 dB, and with music playing it made recognizing the wake word clearly worse; using only the other microphone helped. A second unit had two equal microphones, and nothing was to gain there.

You can find out which case you have in about 15 minutes with **one recording**. A script on your computer evaluates it and tells you in plain words what to do: keep both microphones, or use only the left or the right one. Background and my measurements: [AUDIO_INVESTIGATION.md, "Microphones: left, right, average"](AUDIO_INVESTIGATION.md#microphones-left-right-average).

### What you need

- The device runs `onju_duplex_i2s.yaml` with its **default** microphone setting. If you already set a single-microphone override, remove it for the measurement.
- A computer in the same network with [`uv`](https://docs.astral.sh/uv/) installed (it installs everything else the scripts need by itself).
- The computer must accept an incoming connection on TCP port 19091 while you record. If a firewall blocks it, allow it temporarily, e.g. `sudo firewall-cmd --zone=<zone of your network interface> --add-port=19091/tcp` (Fedora), `sudo ufw allow 19091/tcp` (Ubuntu), or an inbound rule in the Windows Defender Firewall.
- A quiet room and a way to start and pause music quickly (Music Assistant, any track).

### Step 1: add the recording package to your device file

Add `capture_address` to the `substitutions:` block your device file already has (a second `substitutions:` key would be an error) and include the package:

```yaml
substitutions:
  # ... name, friendly_name, sounds as before
  capture_address: "192.168.1.50"   # the IP address of your computer (it must not change meanwhile)

packages:
  - !include onju_duplex_i2s.yaml
  - !include diagnostics/audio_capture.yaml   # adds "Start audio capture" / "Stop audio capture" buttons
  # ... wifi.yaml, ota.yaml as before
```

Flash it (OTA is fine). The device now has two buttons, *Start audio capture* and *Stop audio capture*, in the *Diagnostic* section of its page in Home Assistant.

### Step 2: start the receiver on your computer

In a terminal, from the folder of this repository:

```bash
uv run tools/capture_audio.py captures/mictest-1 --timeout 900
```

It now waits (up to 15 minutes) for the device.

### Step 3: record 60 seconds

1. Start some music at a **moderate volume (about 35 % of the player volume)**. Loud playback distorts the small speaker and spoils the measurement. Wait about 10 seconds.
2. Press **Start audio capture** in Home Assistant. The recording ends by itself after 60 seconds. Stay still and follow this timeline:

   | Time | What you do |
   | --- | --- |
   | 0-15 s | Music plays, you say **nothing** and keep the room quiet. This is the important part: at least 10 seconds of music without any speech or other noise. |
   | 15-39 s | Music plays, you talk in short sentences with pauses, first about 1 m and then about 3 m from the device. |
   | about 39 s | **Pause the music.** |
   | 40-45 s | Silence. |
   | 45-57 s | You talk again, about 1 m away, short sentences. |

3. Wait until the receiver is finished (the terminal prompt returns). It prints `Capture incomplete: ...` if something went wrong; then record again.

### Step 4: let the script evaluate it

```bash
uv run tools/mic_recommend.py captures/mictest-1
```

It needs a few seconds (and a little longer the first time, while `uv` installs what it needs). Everything that matters is in the last lines. Shortened examples of what it prints, from real recordings:

**A clear case** (my first unit):

```text
  capture                       music   left  right  leak L-R
  04-couch-3m-zugewandt-mit-mu    16s  +19.9   -2.3     +22.2
[...]
Leak difference D (median of 1 capture(s)): the left mic picks up your own speaker 22.2 dB more than the right mic.
[...]
RECOMMENDATION: use the RIGHT microphone only (clear: |D| >= 10 dB).
  Add this to your device file (top level, not in packages):

  esp_audio_stack:
    mic_channel: right
    rx_mic_slots: !remove
    mic_mix: !remove
```

**Nothing to gain** (my second unit):

```text
  capture                       music   left  right  leak L-R
  b2music-105354                  22s   +8.1   +7.9      +0.2
[...]
Leak difference D (median of 1 capture(s)): the left mic picks up your own speaker 0.2 dB more than the right mic.
[...]
RECOMMENDATION: keep BOTH microphones averaged (the default). Nothing to change (|D| < 6 dB).
```

**How to read it.** The table shows how loudly each microphone hears the device's own speaker (the numbers in dB; the lower, the better). The line `Leak difference D` says how much louder the left microphone hears it than the right one. That one number decides, with these steps:

| Difference (D) | What it means | What to do |
| --- | --- | --- |
| below 6 dB | Both microphones hear the speaker about equally. | **Keep both (the default).** Nothing to change. |
| 6 to 10 dB | A small difference. | **Keep both.** Only if the wake word during music is unreliable, try the single microphone the script suggests. |
| 10 dB or more | A clear difference. | **Use only the better microphone.** The script prints the YAML for the right one. |

The script also checks by itself that the microphone it suggests is not just quieter because it is damaged, and it keeps both microphones if something does not add up (it says why on the `RECOMMENDATION` line). These steps are rules of thumb from the two units I measured (D = 0.2 dB and about 23 dB); I have not verified the range in between.

**If the script says `invalid`** for your recording, a line below it says why. What to do:

| Message (shortened) | What to do |
| --- | --- |
| `capture incomplete`, `more than 20 % of the run lost` | Record again; check the WiFi signal of the device. |
| `read slips`, `faulty read`, `raw data corrupted` | The known start-up problem of the microphone ([details](AUDIO_INVESTIGATION.md#mic-read-bug)): restart the device and record again. |
| `only ... s of music without speech` | Record again and stay silent for the first 15 seconds. |
| `only one microphone` | You recorded with a single-microphone override: remove it and record again. |
| `echo of at least one microphone is not measurable` | A microphone hears almost nothing of the speaker: the music was too quiet, or a microphone is dead or covered. Record again with the music louder. |
| `No valid capture.` | None of your recordings worked; fix the reasons above. |

Lines starting with `Note:` or `-` below a recording are hints, not errors. For all options of the script: `uv run tools/mic_recommend.py --help` (normally you need none of them).

### Step 5: apply the result

If the script says "keep both", you are done. If it recommends a single microphone, add the block it printed to your device file, at the top level (not inside `packages:`). For example for the right microphone:

```yaml
esp_audio_stack:
  mic_channel: right
  rx_mic_slots: !remove
  mic_mix: !remove
```

For the left microphone it is `mic_channel: left` (I only ran the right one on hardware). Leave everything else as it is. Flash, then **test it in practice**: with music playing and without, say the wake word from about 1 m and from about 3 m. It should work at least as well as before, and better during music. If it works worse, you probably have left and right the wrong way round: set `mic_channel` to the other side, or remove the block to go back to the default. If the wake word now reacts too weakly or too strongly, see [AUDIO_INVESTIGATION.md, "Levels and gain"](AUDIO_INVESTIGATION.md#levels-and-gain).

Afterwards you can remove `diagnostics/audio_capture.yaml` and `capture_address` from your device file again and close the firewall port.
