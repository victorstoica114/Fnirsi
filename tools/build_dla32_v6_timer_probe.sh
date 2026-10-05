#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
prefix=/tmp/dla32-wch-worker-v6-prefix
output="$project/artifacts/test_dla32_v6_timer_probe.exe"
[[ ! -e "$output" ]]
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
gcc -std=c99 -D_WIN32_WINNT=0x0600 -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags libsigrok glib-2.0) \
    "$project/tools/test_dla32_v6_timer_probe.c" \
    $(pkg-config --libs libsigrok glib-2.0) -lwinmm -o "$output"
"$output" --self-test
