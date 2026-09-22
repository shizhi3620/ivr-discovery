#!/usr/bin/env bash
set -euo pipefail

version="${MOD_AUDIO_STREAM_VERSION:-v1.1.0}"
commit="${MOD_AUDIO_STREAM_COMMIT:-3e0f52d2bf25912cf276cc1bd9fa31627f758860}"
repo_url="https://github.com/amigniter/mod_audio_stream.git"

for command in cmake git pkg-config; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "缺少构建依赖：${command}" >&2
    exit 1
  fi
done

fs_version="$(pkg-config --modversion freeswitch)"
fs_pkgconfig="$(pkg-config --variable=pcfiledir freeswitch)"
fs_mod_dir="$(pkg-config --variable=modulesdir freeswitch)"
tmp_dir="$(mktemp -d /tmp/mod-audio-stream.XXXXXX)"
trap 'rm -rf "${tmp_dir}"' EXIT

git clone --recurse-submodules --depth 1 --branch "${version}" "${repo_url}" "${tmp_dir}/source"
git -C "${tmp_dir}/source" checkout --detach "${commit}"
git -C "${tmp_dir}/source" submodule update --init --recursive

export PKG_CONFIG_PATH="${fs_pkgconfig}:/opt/homebrew/opt/libevent/lib/pkgconfig:/opt/homebrew/opt/openssl@3/lib/pkgconfig:/opt/homebrew/opt/speexdsp/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

cmake \
  -S "${tmp_dir}/source" \
  -B "${tmp_dir}/source/build-macos" \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS="-I/opt/homebrew/include" \
  -DCMAKE_CXX_FLAGS="-I/opt/homebrew/include" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L/opt/homebrew/lib -lspeexdsp"
cmake --build "${tmp_dir}/source/build-macos" -j 4

# Homebrew FreeSWITCH uses the .so suffix for Mach-O modules.
install -m 755 \
  "${tmp_dir}/source/build-macos/mod_audio_stream.dylib" \
  "${fs_mod_dir}/mod_audio_stream.so"

echo "已安装 mod_audio_stream ${version} (${commit})，FreeSWITCH ${fs_version}"
echo "模块路径：${fs_mod_dir}/mod_audio_stream.so"
