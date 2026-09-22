#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${here}/.runtime"

if [[ ! -d "${runtime}" ]]; then
  echo "运行时目录不存在，请先执行 scripts/start.sh 渲染配置。" >&2
  exit 1
fi

mkdir -p "${runtime}/log" "${runtime}/db"

# -np 禁用实时优先级，-nocal 禁用时钟校准：
# 容器/沙箱环境通常不允许设置 nice/SCHED_FIFO，且校准会拖慢启动。
exec /opt/homebrew/opt/freeswitch/bin/freeswitch \
  -nf -nonat -np -nocal \
  -conf "${runtime}" -log "${runtime}/log" -db "${runtime}/db"
