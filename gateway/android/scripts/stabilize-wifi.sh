#!/usr/bin/env bash
set -euo pipefail

adb_cmd=(adb)
if [[ -n "${ADB_SERIAL:-}" ]]; then
  adb_cmd+=(-s "$ADB_SERIAL")
fi

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

command -v adb >/dev/null 2>&1 || fail "adb is not installed"

device_state="$("${adb_cmd[@]}" get-state 2>/dev/null | tr -d '\r')"
[[ "$device_state" == "device" ]] || fail "no authorized Android device"

model="$("${adb_cmd[@]}" shell getprop ro.product.model | tr -d '\r')"
root_id="$("${adb_cmd[@]}" shell 'su -c id' 2>/dev/null | tr -d '\r')"
[[ "$root_id" == uid=0* ]] || fail "$model does not provide root through su"

printf 'Applying WiFi stability policy to %s\n' "$model"

"${adb_cmd[@]}" shell 'cmd wifi set-ipreach-disconnect disabled'
"${adb_cmd[@]}" shell 'su -c "cmd wifi set-network-selection-config disabled disabled -a 2"'
"${adb_cmd[@]}" shell 'su -c "iw dev wlan0 set power_save off"'

ipreach="$("${adb_cmd[@]}" shell 'cmd wifi get-ipreach-disconnect' | tr -d '\r' | tail -1)"
power_save="$("${adb_cmd[@]}" shell 'iw dev wlan0 get power_save' | tr -d '\r' | tail -1)"
wifi_state="$("${adb_cmd[@]}" shell 'cmd wifi status' | tr -d '\r' | grep -m1 -E 'Wifi is connected|Wifi is not connected')"

[[ "$ipreach" == *"false"* ]] || fail "IP reachability disconnect is still enabled"
[[ "$power_save" == *"off"* ]] || fail "WiFi power save is still enabled"
[[ "$wifi_state" == *"connected to"* ]] || fail "WiFi is not connected"

printf '  %s\n' "$ipreach"
printf '  %s\n' "$power_save"
printf '  %s\n' "$wifi_state"
