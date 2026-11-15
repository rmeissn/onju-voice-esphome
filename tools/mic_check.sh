#!/usr/bin/env bash
# Check whether the right mic is read correctly (mic bug, docs/AUDIO_INVESTIGATION.md), after a reboot or after an Audio
# Stack session restart. No user action needed. With the fix (rx_clock: fast + rx_data_delay: pos_edge) every check
# should say CORRECT.
#
# Usage (from the repo root; the device file must include diagnostics/audio_capture.yaml, and for --session also
# diagnostics/session_test.yaml; capture_address = this PC; TCP port 19091 open in the firewall):
#   bash tools/mic_check.sh <device-ip> [name-prefix] [max] [--session] [--count] [--log=<device>.yaml] [--ref=R,L]
# Default (reboot mode): the first check runs without a restart (e.g. right after a flash), then "Restart" + ~35 s
#   per try; each check: start tools/capture_audio.py, press "Start audio capture", "Stop audio capture" after ~6 s.
# --session: press "Session restart" instead (rebuilds the session in ~2 s and records a 2.5 s capture itself). Warns
#   if the capture generation did not grow by 2 (= the session was not rebuilt).
# --count: run all <max> tries and print a summary instead of stopping at the first CORRECT one.
# --log=<device>.yaml: record the device log meanwhile (esphome logs in the container, timeout inside it) to
#   captures/<prefix>-<time>.log, with this script's own actions as "### HH:MM:SS ..." lines in between; at the end
#   print the hits of rg '\[[WE]\]|spk_underrun=[1-9]' plus the action before each (docs/RESOURCE_INVESTIGATION.md
#   "How to measure"; spk_underrun without music is padding, not stutter; session restarts stop playback).
# --ref=R,L: correct-read DC per mic (18-bit units) for tools/r32_verdict.py (default: grey's values).
# Offline (no device, for tests): bash tools/mic_check.sh --analyze [--ref=R,L] captures/<run> [...] prints the
#   verdict of existing captures with the same rule.
# Verdict per check:
#   32-bit raw (diagnostics/raw32.yaml): tools/r32_verdict.py (exact: bits 13..0 never set, phase check, DC).
#   16-bit raw: CORRECT = DC <= -23 dBFS (grey: correct read -25.6 dBFS), LATE = raw check ok but DC > -23 dBFS (read
#   one BCLK late, grey -19.6 dBFS), SLIPPING = period pattern or failed raw check. The DC threshold is device specific.
# Captures go to captures/<prefix>-<time>/ (gitignored).

set -u
cd "$(dirname "$0")/.." || exit 1
ref=""

# verdict for capture dir $1 (uses $ref): sets verdict, dc, line, ch1, gen
verdict_for() {
  local name=$1 out r32
  out=$(uv run tools/analyze_capture.py "$name" 2>&1)
  gen=$(echo "$out" | grep -o -m1 'generation [0-9/?]*' | cut -d' ' -f2)
  # 16-bit rule for raw channel $1: period pattern first (can come with a low DC too), then the DC (the correct read
  # can bias bit 2 slightly and trip the low-bit check)
  classify() {
    line=$(echo "$out" | grep -m1 "^raw ch$1")
    dc=$(echo "$out" | grep -o -m1 "raw ch$1: DC *-\?[0-9.]*" | grep -o -- '-\?[0-9.]*$')
    if [ -z "$line" ]; then verdict=NO-CAPTURE
    elif [[ "$line" == *"<--"*"period "* ]]; then verdict=SLIPPING # the reason after "<--", not "(period 2/3/4)"
    elif awk -v d="${dc:-0}" 'BEGIN { exit !(d <= -23) }'; then verdict=CORRECT
    elif [[ "$line" == *"  ok"* ]]; then verdict=LATE
    else verdict=SLIPPING
    fi
  }
  ch1=""
  if echo "$out" | grep -q '^raw ch1'; then # both slots (diagnostics/both_slots_raw.yaml): ch1 = left mic
    classify 1
    ch1=" | ch1: $verdict, DC ${dc:-?} dBFS"
  fi
  classify 0
  # shellcheck disable=SC2086 # $ref is empty or one --ref=... argument
  r32=$(uv run -q tools/r32_verdict.py $ref "$name" 2>/dev/null | grep -v '^reference DC')
  if [[ -n "$r32" && "$r32" != *NOT-32BIT* && "$r32" != *NO-CAPTURE* ]]; then
    case "$r32" in # 32-bit raw: the exact bit check decides (both mics must agree)
      *": CORRECT "*) verdict=CORRECT ;;
      *": LATE "*) verdict=LATE ;;
      *) verdict=SLIPPING ;;
    esac
    ch1=" | 32 bit: ${r32#*: }"
  fi
}

if [ "${1:-}" = "--analyze" ]; then
  shift
  for a in "$@"; do if [[ "$a" == --ref=* ]]; then ref=$a; fi; done
  for a in "$@"; do
    if [[ "$a" == --ref=* ]]; then continue; fi
    verdict_for "$a"
    echo "$(basename "$a") (generation ${gen:-?}): $verdict, DC ${dc:-?} dBFS$ch1"
  done
  exit 0
fi

host=${1:?usage: mic_check.sh <device-ip> [name-prefix] [max] [--session] [--count] [--log=<device>.yaml]}
shift
prefix=boot
max=6
session=0
count=0
logyaml=""
pos=0
for arg in "$@"; do
  case "$arg" in
    --session) session=1 ;;
    --session=*) echo "--session takes no variants any more (only \"Session restart\" exists)"; exit 2 ;;
    --count) count=1 ;;
    --log=*) logyaml=${arg#--log=} ;;
    --ref=*) ref=$arg ;;
    *) pos=$((pos + 1)); if [ "$pos" -eq 1 ]; then prefix=$arg; else max=$arg; fi ;;
  esac
done
container=$(podman ps --format '{{.Names}}' | head -1)
[ -n "$container" ] || { echo "no running Device Builder container"; exit 1; }

logfile=""
event() { [ -n "$logfile" ] && echo "### $(date +%T) $*" >> "$logfile"; return 0; }
press() { event "press '$1'"; podman exec "$container" python3 /config/tools/esphome_button.py "$host" "$1" >/dev/null 2>&1; }

if [ -n "$logyaml" ]; then
  logfile="captures/$prefix-$(date +%H%M%S).log"
  per_try=$([ "$session" -eq 1 ] && echo 15 || echo 50)
  # timeout INSIDE the container: an orphaned log client would block the device API
  # (no pkill in the container: timeout's PID goes to a file, timeout forwards the TERM to esphome logs)
  # TZ: the container runs in UTC, the "###" action lines use local time
  podman exec -e TZ="$(timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Berlin)" "$container" \
    sh -c 'echo $$ > /tmp/mic_check_log.pid; exec timeout "$0" esphome logs "$1" --device "$2"' \
    $((max * per_try + 30)) "/config/$logyaml" "$host" >> "$logfile" 2>&1 &
  lpid=$!
  sleep 3
fi
finish_log() {
  [ -n "$logfile" ] || return 0
  sleep 2
  podman exec "$container" sh -c 'kill "$(cat /tmp/mic_check_log.pid)"' >/dev/null 2>&1
  wait "$lpid" 2>/dev/null
  echo "device log: $logfile; hits (with the action before each):"
  awk '/^### / { last = $0; next } /\[[WE]\]|spk_underrun=[1-9]/ { print "  " $0; print "      after: " last }' "$logfile"
  echo "I2S clock per session (diagnostics/i2s_clock.h):"
  grep -o 'I2S0 RX: .*' "$logfile" | sort | uniq -c
}

n_ok=0 n_late=0 n_slip=0 n_lost=0
last_gen=""
last_i=0
for i in $(seq 1 "$max"); do
  name="captures/$prefix-$(date +%H%M%S)"
  if [ "$session" -eq 1 ]; then
    uv run tools/capture_audio.py "$name" --timeout 30 >/dev/null 2>&1 &
    rpid=$!
    sleep 1
    press "Session restart"
  else
    if [ "$i" -gt 1 ]; then
      press "Restart"
      sleep 35
    fi
    uv run tools/capture_audio.py "$name" --timeout 60 >/dev/null 2>&1 &
    rpid=$!
    sleep 2
    press "Start audio capture"
    sleep 6
    press "Stop audio capture"
  fi
  wait "$rpid"
  verdict_for "$name"
  label=$([ "$session" -eq 1 ] && echo "session $i" || echo "boot $i")
  echo "$label ($name, generation ${gen:-?}): $verdict, DC ${dc:-?} dBFS$ch1 | ${line#raw ch0: }"
  event "result $label: $verdict"
  # a lost capture (NO-CAPTURE) skips one generation step, so compare per try; only numeric generations
  if [[ "$gen" =~ ^[0-9]+$ ]]; then
    if [ "$session" -eq 1 ] && [[ "$last_gen" =~ ^[0-9]+$ ]] && [ "$gen" -ne $((last_gen + 2 * (i - last_i))) ]; then
      echo "  WARNING generation $last_gen -> $gen (expected +2 per try): session not rebuilt as planned"
    fi
    last_gen=$gen
    last_i=$i
  fi
  case "$verdict" in
    CORRECT) n_ok=$((n_ok + 1)) ;;
    LATE) n_late=$((n_late + 1)) ;;
    SLIPPING) n_slip=$((n_slip + 1)) ;;
    *) n_lost=$((n_lost + 1)) ;;
  esac
  if [ "$count" -eq 0 ] && [ "$verdict" = CORRECT ]; then finish_log; exit 0; fi
done
finish_log
if [ "$count" -eq 1 ]; then
  echo "summary: correct $n_ok, late $n_late, slipping $n_slip, no capture $n_lost"
  exit 0
fi
echo "no correct check in $max tries"
exit 1
