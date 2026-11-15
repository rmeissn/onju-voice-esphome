# RX clock margin scan: how to rebuild and repeat it

Written for a future agent. Purpose: after an esphome-audio-stack or ESP-IDF update, or on a new device type,
re-measure whether the Onju's mic read is still correct and how much margin the fix has. Background and the original
results: `docs/AUDIO_INVESTIGATION.md` sections "Mic read bug" and
"RX clock margin scan". Nothing of this kit is built in by default; build it only for the measurement and remove it afterwards.

## What the kit consists of

1. **Audio Stack runtime test switches**: `diagnostics/rx_clock_scan_test_switches.patch` (sha256 starts
   `94d38183`). It is a `git diff` against the
   esphome-audio-stack release commit `3787f50` ("Release 2026.11.0 …") and touches only
   `esphome/components/esp_audio_stack/{__init__.py,esp_audio_stack.cpp,esp_audio_stack.h}`. It contains:
   - YAML options `rx_clock: default|fast`, `rx_data_delay: none|pos_edge|neg_edge` (codegen calls
     `set_rx_fast_clock(true)` and `set_rx_timing(1|2)`), and `i2s_clock_source: default|pll_240m` (no benefit, ignore).
   - Runtime setters, all read at the NEXT session start (`esp_audio_stack.stop` → `start`):
     `set_rx_clock_div(k)` (RX slave on PLL 240 MHz / k, k >= 2, 240 MHz / k must be an integer multiple of the sample
     rate; 0 = same clock as TX), `set_rx_fast_clock(bool)` (= k 2 / 0), `set_rx_timing(int32)` (raw value written to
     `I2Sn.rx_timing` before RX enable; bits 1:0 = `rx_sd_in_dm`: 0 bypass, 1 delay by pos edge, 2 neg edge; −1 = do not
     touch), `set_rx_first(bool)` (enable RX before TX; did not help, ignore). `set_mclk_multiple()` exists in the
     release already.
2. **Buttons** (YAML below) that set a variant and rebuild the session with a capture.
3. **Block runner** (bash below): N sessions per variant in blocks, verdict per capture.
4. Existing pieces that stay in the repo: `diagnostics/session_test.yaml` (script `session_test_restart`, no
   parameters), `diagnostics/i2s_clock.h` (logs the real RX/TX dividers and `rx_timing` per session),
   `diagnostics/audio_capture.yaml`, `diagnostics/raw32.yaml`, `diagnostics/both_slots_raw.yaml`,
   `tools/r32_verdict.py`, `tools/capture_audio.py`, `tools/esphome_button.py`, `tools/mic_check.sh`.

## Step 1: Audio Stack with the test switches (separate branch, do not mix with the clean fix)

The clean fix lives on branch `onju/i2s-clock-source` (fork `rmeissn/esphome-audio-stack`, loaded by the duplex config
from GitHub; only `rx_clock` + `rx_data_delay`, setters `set_rx_fast_clock()` / `set_rx_data_delay()`). The patch replaces these files
completely with the test version, so apply it to the release files, not on top of the clean branch:

```bash
# from the root of this repo; <audio-stack-repo> = your local esphome-audio-stack clone
# (the worktree lives inside this repo because the Device Builder container only sees /config)
repo=$(pwd)
git -C <audio-stack-repo> worktree add -b onju/rx-clock-scan "$repo/.local_components/esphome-audio-stack-scan" 3787f50
cd "$repo/.local_components/esphome-audio-stack-scan"
git apply --check "$repo/diagnostics/rx_clock_scan_test_switches.patch" && git apply "$repo/diagnostics/rx_clock_scan_test_switches.patch"
```

(Checked: the patch applies cleanly to `3787f50`.) If the release changed, port the hunks by hand; the
logic is small: in `prepare_i2s_channels_()` after the RX slot config, if `rx_clock_div_ >= 2` set
`rx_cfg.clk_cfg.clk_src = I2S_CLK_SRC_PLL_240M` and `rx_cfg.clk_cfg.mclk_multiple = 240000000 / k / sample_rate`
(cast to `i2s_mclk_multiple_t`; IDF only multiplies it); in `enable_i2s_channels_()` before the first RX enable,
if `rx_timing_ >= 0` write `I2S0/I2S1.rx_timing.val` (`#include <soc/i2s_struct.h>`, ESP32-S3 only). The register
must be written per session: the peripheral reset at channel allocation clears it; `i2s_ll_rx_start()` sets
`rx_update` itself.

In the device file point `external_components` to the scan worktree and add `.local_components/esphome-audio-stack-scan`
to `.git/info/exclude`:

```yaml
external_components:
  - source:
      type: local
      path: .local_components/esphome-audio-stack-scan/esphome/components
    components: [esp_audio_stack]
esp_audio_stack:
  rx_clock: fast            # the patch knows these two options too
  rx_data_delay: pos_edge
```

## Step 2: buttons file (create as `diagnostics/rx_clock_scan.yaml`, include it in the device file)

Rules learned the hard way: **no `/` in names** (ESPHome turns it into U+2044 `⁄` and `esphome_button.py` then does
not find the name), avoid commas in names (old `mic_check.sh` split at commas; the runner below does not care, but
keep names simple). Every button sets ALL switches, so no state leaks from the previous variant.

```yaml
# RX clock margin scan buttons (diagnostics/RX_CLOCK_SCAN.md). Needs the Audio Stack scan branch,
# diagnostics/session_test.yaml and diagnostics/audio_capture.yaml.
button:
  # one entry per variant: name suffix, divider k (0 = TX clock), rx_timing raw value (0 bypass, 1 pos, 2 neg)
  - platform: template
    name: "Scan RX div 0 delay 0"     # control: the original clock (12.288 MHz, 4 ticks per BCLK)
    entity_category: diagnostic
    on_press:
      - lambda: |-
          id(i2s_shared).set_rx_first(false);
          id(i2s_shared).set_rx_clock_div(0);
          id(i2s_shared).set_rx_timing(0);
      - script.execute: session_test_restart
  # ... repeat the block for every variant you need, e.g.:
  #   "Scan RX div 2 delay 0"  (k 2 = 120 MHz, the fix clock)    "Scan RX div 2 delay 1"  (the fix: clock + pos delay)
  #   "Scan RX div 4 delay 0"  (60 MHz)   "Scan RX div 5 delay 0" (48 MHz)   "Scan RX div 8 delay 0" (30 MHz)
  #   "Scan RX div 10 delay 0" (24 MHz)   "Scan RX div 0 delay 1" / "Scan RX div 0 delay 2"  (original clock + delay)
  #   "Scan RX div 8 delay 1" / "Scan RX div 8 delay 2" / "Scan RX div 10 delay 1"
  # Restore after the test: "Scan RX div 2 delay 1" (= the YAML fix) or reboot (the YAML options apply again).
```

Valid dividers at 48 kHz: k in {2, 4, 5, 8, 10} (also 3, 6 work: 240 MHz / k must be a multiple of 48 kHz). Expected
registers (`i2s_clock.h` log line `I2S0 RX: clkm_div …, bck_div …, slave 1, rx_timing 0x… | TX: clkm_div 13, bck_div 4`):

| k | RX MCLK | tick | RX clkm_div / bck_div |
| --- | --- | --- | --- |
| 0 | 12.288 MHz (160 MHz / 13 + 1/48) | 81 ns | 13 / 4 |
| 2 | 120 MHz | 8.3 ns | 2 / 39 |
| 4 | 60 MHz | 16.7 ns | 4 / 19 |
| 5 | 48 MHz | 20.8 ns | 5 / 15 |
| 8 | 30 MHz | 33.3 ns | 8 / 9 |
| 10 | 24 MHz | 41.7 ns | 10 / 7 |

## Step 3: measurement setup

- Device file includes: `diagnostics/audio_capture.yaml`, `diagnostics/session_test.yaml`, `diagnostics/raw32.yaml`
  (32-bit raw, exact bit check), `diagnostics/both_slots_raw.yaml` (both mics; raw ch0 = right), the buttons file.
  For an averaging config (`mic_mix: average`) the both-slots package sets `rx_mic_slots: [right, left]` anyway.
- PC: `capture_address` in the device file = this PC (`ip -4 addr`); TCP 19091 open
  (`sudo firewall-cmd --zone=home --add-port=19091/tcp`, the user does that). Device Builder container name:
  `podman ps --format '{{.Names}}'`. Flash with the user's OK only (CLAUDE.md "Device access").
- **Decision rule before measuring** (user's method): blocks of 20 sessions per variant, all A then all B, the first
  block is the control (`div 0 delay 0` or `div 2 delay 1`); "stable correct" = 20 of 20 CORRECT; the limit is the
  first variant with any LATE/MIXED. Stop larger dividers once a whole block fails.
- Quiet room is fine; typing nearby does not matter (the bit check looks at bits the mic never drives).

## Step 4: block runner (save as `tools/session_blocks.sh` only for the measurement)

```bash
#!/usr/bin/env bash
# Usage: bash tools/session_blocks.sh <device-ip> <device>.yaml <sessions> "<button name>|<prefix>" ...
# Per block: log in the container (timeout INSIDE the container), N x (receiver, press button, wait, verdict).
set -u
cd "$(dirname "$0")/.." || exit 1
ip=$1 yaml=$2 n=$3; shift 3
c=$(podman ps --format '{{.Names}}' | head -1)
for item in "$@"; do
  button=${item%%|*} prefix=${item##*|}
  log="captures/$prefix-$(date +%H%M%S).log"
  podman exec -e TZ="$(timedatectl show -p Timezone --value)" "$c" timeout $((n * 15 + 30)) esphome logs "/config/$yaml" --device "$ip" > "$log" 2>&1 &
  sleep 3
  echo "=== $(date +%T) block '$button' ($prefix)"
  for i in $(seq 1 "$n"); do
    name="captures/$prefix-$(date +%H%M%S)"
    uv run tools/capture_audio.py "$name" --timeout 30 > /dev/null 2>&1 &
    r=$!; sleep 1
    echo "### $(date +%T) press '$button'" >> "$log"
    podman exec "$c" python3 /config/tools/esphome_button.py "$ip" "$button" > /dev/null 2>&1
    wait "$r"
    echo "  $i: $(uv run -q tools/r32_verdict.py "$name" | cut -c1-60)"
  done
  wait
  echo "--- summary:"; uv run -q tools/r32_verdict.py $(fd -t d "^$prefix-" captures | sort) | awk '{print $2}' | sort | uniq -c
  echo "--- registers:"; grep -o 'I2S0 RX: .*' "$log" | sort | uniq -c
  echo "--- warnings:"; grep -E '\[[WE]\]|spk_underrun=[1-9]' "$log" | cut -c1-150
done
```

Run it in the background and watch it with a Monitor whose pipeline ends with `grep --line-buffered` (a trailing
`cut` buffers and hides the events). Check after the first 2–3 sessions of every block that the log shows the
expected registers and that captures arrive (once a block ran 10 min into the void because of a `/` in the
button name). In zsh never pass a list in an unquoted variable (no word splitting); use bash for such loops.

## Step 5: evaluate and clean up

- `tools/r32_verdict.py` (validated on synthetic data, `tools/make_synthetic_raw32.py`, and 223 real verdicts):
  CORRECT = bits 13..0 never set + phase spread < 2 % + DC within 10 % of the correct read; for another device use
  `--ref=auto` over >= 5 clean sessions or `--ref=R,L`. LATE = read one BCLK late, MIXED = slipping.
- Compare with the original results (one test device, 48 kHz, 20 sessions each): k 2/4/5 delay 0: 20/20 correct; k 8/10 delay 0: 0/20
  (all mixed); k 0 delay 0: 8 correct / 7 late / 5 mixed; k 0 delay 1: 20/20; k 0 delay 2: 15/20; k 2/8/10 delay 1
  and k 8 delay 2: 20/20.
- Afterwards: restore the fix variant, remove the buttons include, remove the local `external_components` override
  (the duplex config's GitHub source applies again), remove the scan worktree
  (`git -C <audio-stack-repo> worktree remove …`), document results in `docs/AUDIO_INVESTIGATION.md` and the
  `[W]`/`[E]` hits in `docs/RESOURCE_INVESTIGATION.md` "Problems seen".
