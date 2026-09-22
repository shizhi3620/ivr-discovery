#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${here}/.env.local"
wav_path="${1:-/tmp/apple-root-45s.wav}"

if [[ ! -f "${env_file}" ]]; then
  echo "缺少 ${env_file}" >&2
  exit 1
fi
if [[ ! -f "${wav_path}" ]]; then
  echo "测试 WAV 不存在：${wav_path}" >&2
  exit 1
fi
if ! command -v pjsua >/dev/null 2>&1; then
  echo "缺少 pjsua，请先执行：brew install pjproject" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${env_file}"

: "${SIP_DEFAULT_PASSWORD:?SIP_DEFAULT_PASSWORD 未设置}"
: "${LAN_IP:?LAN_IP 未设置}"

config_file="$(mktemp /tmp/pjsua-testclient.XXXXXX)"
trap 'rm -f "${config_file}"' EXIT
umask 077

{
  printf '%s\n' \
    "--id=sip:testclient@${LAN_IP}" \
    "--registrar=sip:${LAN_IP}:5060" \
    "--realm=*" \
    "--username=testclient" \
    "--password=${SIP_DEFAULT_PASSWORD}" \
    "--ip-addr=${LAN_IP}" \
    "--bound-addr=${LAN_IP}" \
    "--local-port=0" \
    "--no-tcp" \
    "--auto-answer=200" \
    "--auto-play" \
    "--auto-play-hangup" \
    "--play-file=${wav_path}" \
    "--duration=60" \
    "--log-level=3" \
    "--app-log-level=3" \
    "--no-color"
} > "${config_file}"

pjsua --config-file "${config_file}"
