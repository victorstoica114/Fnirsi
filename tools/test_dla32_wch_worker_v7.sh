#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-worker-v7-source/libsigrok"
support=${DLA32_UNIT_SUPPORT_VERSION:-v7}
[[ "$support" == v6 || "$support" == v7 ]]
build=/tmp/dla32-wch-worker-$support-build/libsigrok
prefix=/tmp/dla32-wch-worker-$support-prefix
tests="$project/artifacts/dla32-wch-worker-v7-tests"
label=${1:-dla32-wch-worker-v7-unit}
[[ "$label" =~ ^[A-Za-z0-9_.-]+$ ]]
[[ ! -e "$tests/$label.exe" && ! -e "$project/logs/$label.log" ]]
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$tests/test_dla32_wch_worker_v7_unit.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$tests/$label.exe"
"$tests/$label.exe" 2>&1 | tee "$project/logs/$label.log"
