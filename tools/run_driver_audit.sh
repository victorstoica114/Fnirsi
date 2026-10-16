#!/usr/bin/env bash
set -euo pipefail
export PKG_CONFIG_PATH=/opt/dla32/lib/pkgconfig
export PATH=/opt/dla32/bin:/ucrt64/bin:/usr/bin
project=/tmp/dla32-project
build=/tmp/dla32-build/libsigrok
gcc -std=c99 -O1 -Wall -Wextra -ffunction-sections -fdata-sections \
    -I"$build" -I"$build/include" -I"$project/src/libsigrok/include" \
    -I"$project/src/libsigrok/src" $(pkg-config --cflags libsigrok) \
    "$project/tools/audit_dla32_driver.c" "$build/.libs/libsigrok.a" \
    $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
    -o "$project/artifacts/audit_dla32_driver.exe"
"$project/artifacts/audit_dla32_driver.exe" 2>&1 \
    | tee "$project/logs/driver-audit-behavior.log"
