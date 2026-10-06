#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
output="$project/artifacts/test_dla32_winusb_continuous_r2.exe"
[[ ! -e "$output" ]]
gcc -std=c99 -D_WIN32_WINNT=0x0600 -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags libsigrok glib-2.0 libzstd) \
    "$project/tools/test_dla32_winusb_continuous_r2.c" \
    $(pkg-config --libs libsigrok glib-2.0 libzstd) -o "$output"
"$output" --self-test > "$project/logs/dla32-winusb-continuous-self-test-r2.log" 2>&1
