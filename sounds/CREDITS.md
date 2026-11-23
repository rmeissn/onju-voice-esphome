# Sound credits

All files are FLAC, mono, 48 kHz, 16-bit to match the speaker `sample_rate`. Files with a source marked "converted" were re-encoded to this format; any other change is listed in the last column. The licenses ask for the credit below; if you use or pass on these sounds, keep it.

| File | Original | Author | Source | License | Changes |
|---|---|---|---|---|---|
| `wakeup.flac` | New Notification #7 | UNIVERSFIELD | [freesound 736267](https://freesound.org/people/UNIVERSFIELD/sounds/736267/) ([archived](https://web.archive.org/web/20250728051441/https://freesound.org/people/UNIVERSFIELD/sounds/736267/)) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | converted |
| `wakeup_v2.flac` | New Notification #7 | UNIVERSFIELD | as `wakeup.flac` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | first 550 ms played 1.5x faster (a fifth higher), 367 ms; variant 5 of `tools/make_wake_variants.py` |
| `error.flac` | Error #8 | UNIVERSFIELD | [freesound 734442](https://freesound.org/people/UNIVERSFIELD/sounds/734442/) ([archived](https://web.archive.org/web/20250912164840/https://freesound.org/people/UNIVERSFIELD/sounds/734442/)) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | converted |
| `timer_finished.flac` | New Notification #6 | UNIVERSFIELD | [freesound 734445](https://freesound.org/people/UNIVERSFIELD/sounds/734445/) ([archived](https://web.archive.org/web/20250912051027/https://freesound.org/people/UNIVERSFIELD/sounds/734445/)) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | converted |
| `mute.flac` | jack_disconnected.flac | Clayton Charles Tapp | [home-assistant-voice-pe](https://github.com/esphome/home-assistant-voice-pe/blob/dev/sounds/jack_disconnected.flac) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | converted (level unchanged) |
| `unmute.flac` | jack_connected.flac | Clayton Charles Tapp | [home-assistant-voice-pe](https://github.com/esphome/home-assistant-voice-pe/blob/dev/sounds/jack_connected.flac) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | converted (level unchanged) |
| `alarm_8s.flac` | SCIAlrm_Alarm, Repeat, Danger, Warning, Error_04_JW Audio | JW_Audio | [freesound 828581](https://freesound.org/people/JW_Audio/sounds/828581/) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | trimmed to 7.95 s (the original ends with about 1 s of silence) |

The three UNIVERSFIELD sounds were later deleted from freesound; their CC BY 4.0 license is recorded in the Internet Archive copies of the pages (2025-07-28 and 2025-09-12).

Which file is used for what: `wakeup_v2` (wake word detected, the default; shortened, works with `wake_listen_delay: 300ms`, see `docs/AUDIO_INVESTIGATION.md`), `wakeup` (the original wake sound, use it with `wake_listen_delay: 450ms`), `error`, `timer_finished`, `mute`, `unmute`, `alarm_8s` (alarm siren, looped).
