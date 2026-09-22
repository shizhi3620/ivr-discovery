#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="${here}/.runtime/log/freeswitch.pid"

if [[ ! -f "${pid_file}" ]]; then
  echo "未找到 ${pid_file}，FreeSWITCH 可能未在运行。"
  exit 0
fi

pid="$(cat "${pid_file}")"
if kill -0 "${pid}" 2>/dev/null; then
  kill "${pid}"
  for _ in {1..20}; do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 0.5
  done
  kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" || true
  echo "已停止 FreeSWITCH (pid ${pid})。"
else
  echo "进程 ${pid} 不存在，清理 PID 文件。"
fi
rm -f "${pid_file}"
