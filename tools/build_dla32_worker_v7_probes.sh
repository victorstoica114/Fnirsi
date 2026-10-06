#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
prefix=/tmp/dla32-wch-worker-v7-prefix
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
bash "$project/tools/build_dla32_stop_worker_v7_cancel_r2.sh" "$prefix" \
    "$project/artifacts/test_dla32_stop_worker_v7_cancel_r2.exe"
output="$project/artifacts/test_dla32_worker_v7_continuous.exe"
[[ ! -e "$output" ]]
gcc -std=c99 -D_WIN32_WINNT=0x0600 -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags libsigrok glib-2.0) \
    "$project/tools/test_dla32_worker_v7_continuous.c" \
    $(pkg-config --libs libsigrok glib-2.0) -o "$output"
