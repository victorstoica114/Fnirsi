#!/usr/bin/env bash
# Compile/run the portable copied fixtures against the restored exact V6 source.
# All SDK calls are fake. No device scan, firmware or hardware operations.
set -euo pipefail
[[ $# -le 1 ]]
repo_input=${1:-${DLA32_BACKUP_WORKTREE:-}}
[[ -n "$repo_input" ]]
repo=$(cygpath -u "$repo_input")
[[ -f "$repo/artifacts/dla32-wch-worker-v6-overlay/manifest.json" ]]
source="$repo/artifacts/dla32-wch-worker-v6-source/libsigrok"
build=/tmp/dla32-wch-worker-v6-build/libsigrok
prefix=/tmp/dla32-wch-worker-v6-prefix
tests="$repo/artifacts/dla32-wch-worker-v6-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
mkdir -p "$repo/logs"
output="$repo/artifacts/v6-portable-fixtures.exe"
[[ ! -e "$output" ]]
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$tests/test_dla32_wch_worker_v6_unit.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$output"
"$output" >"$repo/logs/v6-portable-fixtures.log" 2>&1
tail -n 3 "$repo/logs/v6-portable-fixtures.log"
