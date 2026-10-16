#!/usr/bin/env bash
set -euo pipefail
export PATH=/opt/dla32-wiretrace/bin:/ucrt64/bin:/usr/bin
export PKG_CONFIG_PATH=/opt/dla32-wiretrace/lib/pkgconfig:/ucrt64/lib/pkgconfig
project=/tmp/dla32-project
build=/tmp/dla32-wiretrace-build/libsigrok
source=/tmp/la1010-reference-src/libsigrok
testdir="$project/artifacts/dla32-wiretrace-tests"
make -C "$build" -j4
make -C "$build" check
make -C "$build" install
gcc -std=c99 -O1 -Wall -Wextra -ffunction-sections -fdata-sections \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) \
    "$testdir/test_dla32_wiretrace.c" "$build/.libs/libsigrok.a" \
    $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
    -o "$testdir/test_dla32_wiretrace.exe"
"$testdir/test_dla32_wiretrace.exe" 2>&1 \
    | tee "$project/logs/dla32-wiretrace-driver-test.log"
