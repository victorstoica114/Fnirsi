#!/usr/bin/env bash
# Validate the portable restored driver fixture without any hardware access.
set -euo pipefail
[[ $# -le 1 ]]
repo_input=${1:-${DLA32_BACKUP_WORKTREE:-}}
[[ -n "$repo_input" ]]
repo=$(cygpath -u "$repo_input")
source="$repo/artifacts/dla32-wch-worker-v7-r2-source/libsigrok"
build=/tmp/dla32-wch-worker-v7-r2-build/libsigrok
prefix=/tmp/dla32-wch-worker-v7-r2-prefix
tests="$repo/artifacts/dla32-wch-worker-v7-r2-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
output="$repo/artifacts/v7-r2-portable-fixtures.exe"
[[ -f "$repo/artifacts/dla32-wch-worker-v7-r2-overlay/manifest.json" && ! -e "$output" ]]
mkdir -p "$repo/logs"
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$tests/test_dla32_wch_worker_v7_r2_unit.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$output"
"$output" >"$repo/logs/v7-r2-portable-fixtures.log" 2>&1
tail -n 3 "$repo/logs/v7-r2-portable-fixtures.log"
