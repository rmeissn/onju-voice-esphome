#!/usr/bin/env bash
# Receive guided captures one after the other (the user starts each one with "Start audio capture" in HA; the device
# stops after its configured duration) and record the device log meanwhile. Used for level tuning, wake word and AEC
# A/B captures (docs/AUDIO_INVESTIGATION.md "Level tuning").
#
# Usage (repo root; capture_address = this PC, TCP 19091 open):
#   bash tools/receive_captures.sh <device-ip> <device>.yaml <name> [<name> ...]
#   -> captures/<name>/ per capture (+ <name>.recv.txt), log captures/<first name>-<time>.log
# Prints one line per received capture, then the [W]/[E]/spk_underrun hits of the log. Each receiver waits up to
# 20 min. Check the length afterwards: an aborted capture says "Capture incomplete" in its .recv.txt.

set -u
cd "$(dirname "$0")/.." || exit 1
ip=${1:?usage: receive_captures.sh <device-ip> <device>.yaml <name> [<name> ...]}
yaml=${2:?device yaml missing}
shift 2
[ "$#" -ge 1 ] || { echo "no capture name given"; exit 2; }
c=$(podman ps --format '{{.Names}}' | head -1)
[ -n "$c" ] || { echo "no running Device Builder container"; exit 1; }
log="captures/$1-$(date +%H%M%S).log"
# timeout INSIDE the container (an orphaned log client blocks the device API); it ends on its own
podman exec -e TZ=Europe/Berlin "$c" timeout $(($# * 1260 + 60)) esphome logs "/config/$yaml" --device "$ip" >> "$log" 2>&1 &
lpid=$!
for name in "$@"; do
  if [ -e "captures/$name" ]; then echo "captures/$name exists, skipped"; continue; fi
  echo "$(date +%T) waiting for $name (press \"Start audio capture\")"
  uv run tools/capture_audio.py "captures/$name" --timeout 1200 > "captures/$name.recv.txt" 2>&1
  echo "$(date +%T) received $name: $(tail -1 "captures/$name.recv.txt")"
done
kill "$lpid" 2> /dev/null
echo "log: $log"
grep -nE '\[[WE]\]|spk_underrun=[1-9]' "$log" | cut -c1-160
