#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_pid_file="${here}/.run/freeswitch.pid"
legacy_pid_file="${here}/.runtime/log/freeswitch.pid"

# shellcheck disable=SC1091
source "${here}/scripts/lib.sh"

pid="$(freeswitch_running_pid "${here}")"
if [[ -z "${pid}" ]]; then
  rm -f "${run_pid_file}" "${legacy_pid_file}"
  echo "未发现正在运行的 FreeSWITCH。"
  exit 0
fi

kill "${pid}"
for _ in {1..20}; do
  kill -0 "${pid}" 2>/dev/null || break
  sleep 0.5
done
kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" || true
rm -f "${run_pid_file}" "${legacy_pid_file}"
echo "已停止 FreeSWITCH (pid ${pid})。"
