#!/usr/bin/env bash
set -euo pipefail

duration=75
interval=5
peer="${LAN_IP:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration)
      duration="${2:?missing duration}"
      shift 2
      ;;
    --interval)
      interval="${2:?missing interval}"
      shift 2
      ;;
    --peer)
      peer="${2:?missing peer address}"
      shift 2
      ;;
    --help|-h)
      printf 'usage: %s [--duration seconds] [--interval seconds] [--peer ipv4]\n' "$0"
      exit 0
      ;;
    *)
      printf 'error: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

[[ "$duration" =~ ^[1-9][0-9]*$ ]] || {
  printf 'error: --duration must be a positive integer\n' >&2
  exit 2
}
[[ "$interval" =~ ^[1-9][0-9]*$ ]] || {
  printf 'error: --interval must be a positive integer\n' >&2
  exit 2
}

adb_cmd=(adb)
if [[ -n "${ADB_SERIAL:-}" ]]; then
  adb_cmd+=(-s "$ADB_SERIAL")
fi

command -v adb >/dev/null 2>&1 || {
  printf 'error: adb is not installed\n' >&2
  exit 1
}

if [[ -z "$peer" ]]; then
  env_file="$(cd "$(dirname "$0")/../../freeswitch" && pwd)/.env.local"
  if [[ -f "$env_file" ]]; then
    peer="$(sed -n 's/^LAN_IP=//p' "$env_file" | tail -1 | tr -d '"')"
  fi
fi
if [[ -z "$peer" ]]; then
  peer="$(ipconfig getifaddr en0 2>/dev/null || true)"
fi
[[ -n "$peer" ]] || {
  printf 'error: peer address not found; pass --peer\n' >&2
  exit 2
}
[[ "$peer" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  printf 'error: --peer must be an IPv4 address\n' >&2
  exit 2
}

device_state="$("${adb_cmd[@]}" get-state 2>/dev/null | tr -d '\r')"
[[ "$device_state" == "device" ]] || {
  printf 'error: no authorized Android device\n' >&2
  exit 1
}

samples=$(( duration / interval + 1 ))
first_bssid=""
last_bssid=""
failures=0

printf 'Checking %s for %ss at %ss intervals\n' "$peer" "$duration" "$interval"
for ((sample = 1; sample <= samples; sample++)); do
  now="$(date '+%H:%M:%S')"
  state="$("${adb_cmd[@]}" shell "
    bssid=\$(iw dev wlan0 link 2>/dev/null | sed -n \"s/.*Connected to \\([^ ]*\\).*/\\1/p\" | head -1)
    power=\$(iw dev wlan0 get power_save 2>/dev/null | sed \"s/Power save: //\")
    if ping -c 1 -W 1 '$peer' >/dev/null 2>&1; then ping=ok; else ping=FAIL; fi
    printf \"%s|%s|%s\\n\" \"\$bssid\" \"\$power\" \"\$ping\"
  " | tr -d '\r' | tail -1)"

  IFS='|' read -r bssid power ping_result <<<"$state"
  [[ -n "$first_bssid" ]] || first_bssid="$bssid"
  last_bssid="$bssid"

  sample_failed=0
  [[ -n "$bssid" ]] || sample_failed=1
  [[ "$power" == "off" ]] || sample_failed=1
  [[ "$ping_result" == "ok" ]] || sample_failed=1
  if [[ "$bssid" != "$first_bssid" ]]; then
    sample_failed=1
  fi
  if (( sample_failed )); then
    failures=$((failures + 1))
  fi
  result=ok
  if (( sample_failed )); then
    result=FAIL
  fi

  printf '%s bssid=%s power_save=%s ping=%s status=%s\n' \
    "$now" "${bssid:-missing}" "${power:-missing}" "${ping_result:-missing}" "$result"

  if (( sample < samples )); then
    sleep "$interval"
  fi
done

if (( failures > 0 )); then
  printf 'preflight failed: %d/%d samples invalid\n' "$failures" "$samples" >&2
  exit 1
fi

printf 'preflight passed: %d samples, stable BSSID %s, zero packet loss\n' \
  "$samples" "$last_bssid"
