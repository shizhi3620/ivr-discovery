#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${here}/.runtime"
env_file="${here}/.env.local"

if [[ ! -f "${env_file}" ]]; then
  echo "缺少 ${env_file}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${env_file}"
: "${ESL_PASSWORD:?ESL_PASSWORD 未设置}"
: "${ESL_PORT:=18021}"

exec /opt/homebrew/opt/freeswitch/bin/fs_cli \
  -H 127.0.0.1 -P "${ESL_PORT}" -p "${ESL_PASSWORD}" "$@"
