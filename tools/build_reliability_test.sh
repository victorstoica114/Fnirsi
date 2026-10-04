#!/usr/bin/env bash
set -euo pipefail
# Compile only. Root runs the resulting executable after confirming the source.
export PKG_CONFIG_PATH=/opt/dla32/lib/pkgconfig
export PATH=/opt/dla32/bin:/ucrt64/bin:/usr/bin
project=/tmp/dla32-project
build=/tmp/dla32-build/libsigrok
gcc -std=c99 -O2 -Wall -Wextra -Werror -ffunction-sections -fdata-sections \
    -I"$build" -I"$build/include" -I"$project/src/libsigrok/include" \
    $(pkg-config --cflags libsigrok) \
    "$project/tools/test_dla32_reliability.c" "$build/.libs/libsigrok.a" \
    $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
    -o "$project/artifacts/test_dla32_reliability.exe"
printf 'Built %s\n' "$project/artifacts/test_dla32_reliability.exe"
