#!/usr/bin/env bash

# Return the PID of a running FreeSWITCH bound to this checkout's runtime.
# The new .run PID file is preferred; .runtime/log is checked for migration
# from older versions of the harness.
freeswitch_running_pid() {
  local here="$1"
  local pid_file
  local pid

  for pid_file in \
    "${here}/.run/freeswitch.pid" \
    "${here}/.runtime/log/freeswitch.pid"; do
    if [[ -f "${pid_file}" ]]; then
      pid="$(cat "${pid_file}" 2>/dev/null || true)"
      if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null; then
        printf '%s\n' "${pid}"
        return 0
      fi
    fi
  done

  pid="$(pgrep -f "freeswitch .* -conf ${here}/.runtime" 2>/dev/null | head -1 || true)"
  if [[ "${pid}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${pid}"
  fi
}
