#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${here}/.env.local"
runtime="${here}/.runtime"
template="${here}/conf"

# shellcheck disable=SC1091
source "${here}/scripts/lib.sh"

if existing_pid="$(freeswitch_running_pid "${here}")" && [[ -n "${existing_pid}" ]]; then
  echo "FreeSWITCH 已在运行 (pid ${existing_pid})；禁止重复启动并覆盖共享运行库。" >&2
  exit 1
fi

if [[ ! -f "${env_file}" ]]; then
  echo "缺少 ${env_file}，请先复制 .env.example 并填写密码。" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${env_file}"

export SIP_DEFAULT_PASSWORD ESL_PASSWORD LAN_IP ESL_PORT

: "${SIP_DEFAULT_PASSWORD:?SIP_DEFAULT_PASSWORD 未设置}"
: "${ESL_PASSWORD:?ESL_PASSWORD 未设置}"
: "${LAN_IP:?LAN_IP 未设置}"
: "${ESL_PORT:-18021}"

if [[ ! "${LAN_IP}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "LAN_IP 必须是 IPv4 地址，当前为：${LAN_IP}" >&2
  exit 1
fi

rm -rf "${runtime}"
mkdir -p "${runtime}/log" "${runtime}/db"
cp -R "${template}/." "${runtime}/"

# 只替换占位符，真实密码不会写回仓库。
find "${runtime}" -type f -name '*.xml' -print0 \
  | xargs -0 perl -pi -e "s/\\bSIP_PASSWORD_PLACEHOLDER\\b/\$ENV{SIP_DEFAULT_PASSWORD}/g; s/\\bLAN_IP_PLACEHOLDER\\b/\$ENV{LAN_IP}/g"
perl -pi -e "s/\\bESL_PASSWORD_PLACEHOLDER\\b/\$ENV{ESL_PASSWORD}/g; s/\\bESL_PORT_PLACEHOLDER\\b/\$ENV{ESL_PORT}/g" "${runtime}/autoload_configs/event_socket.conf.xml"

echo "配置已渲染到 ${runtime}"
echo "启动 FreeSWITCH（前台）："
echo "  scripts/run.sh"
