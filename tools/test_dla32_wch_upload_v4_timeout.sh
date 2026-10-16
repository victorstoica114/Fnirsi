#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-upload-v4-source/libsigrok"
build=/tmp/dla32-wch-upload-v4-build/libsigrok
prefix=/tmp/dla32-wch-upload-v4-prefix
testdir="$project/artifacts/dla32-wch-upload-v4-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$testdir/test_dla32_wch_upload_v4_timeout_unit.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
    -o "$testdir/test_dla32_wch_upload_v4_timeout_unit.exe"
"$testdir/test_dla32_wch_upload_v4_timeout_unit.exe" 2>&1 \
    | tee "$project/logs/dla32-wch-upload-v4-timeout-unit.log"
