#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
target=$(cygpath -u "${DLA32_STREAM_BACKUP_WORKTREE:?Explicit backup worktree required}")
source="$target/artifacts/dla32-winusb-v11-source/libsigrok"
build=/tmp/dla32-winusb-v11-build/libsigrok
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
label=${1:-portable-backup-unit-r2}
[[ "$label" =~ ^[A-Za-z0-9_-]+$ ]]
mkdir -p "$target/logs"
output="$target/artifacts/dla32-winusb-v11-tests/$label.exe"
[[ ! -e "$output" ]]
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$target/tools/test_dla32_winusb_v11.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$output"
"$output" > "$target/logs/$label.log" 2>&1
