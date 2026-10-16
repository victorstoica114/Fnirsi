#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-timeout-source/libsigrok"
build=/tmp/dla32-wch-timeout-build/libsigrok
prefix=/tmp/dla32-wch-timeout-prefix
testdir="$project/artifacts/dla32-wch-timeout-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export LC_ALL=C
export CFLAGS='-O2 -g'
export CPPFLAGS='-DDLA_PREARM_EMPTY_READS=1'
mkdir -p "$build" "$prefix" "$testdir"
cd "$build"
"$source/configure" --prefix="$prefix" --disable-all-drivers \
    --enable-demo --enable-fnirsi-dla32 --disable-cxx \
    --disable-python --disable-ruby --disable-java
make -j4
make check
make install
gcc -std=c99 -O1 -Wall -Wextra -ffunction-sections -fdata-sections \
    -DDLA_PREARM_EMPTY_READS=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) \
    "$testdir/test_dla32_wch_timeout_lifecycle.c" "$build/.libs/libsigrok.a" \
    $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
    -o "$testdir/test_dla32_wch_timeout_lifecycle.exe"
"$testdir/test_dla32_wch_timeout_lifecycle.exe" 2>&1 \
    | tee "$project/logs/dla32-wch-timeout-lifecycle.log"
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) \
    "$project/tools/test_dla32_wch_timeout_unit.c" "$build/.libs/libsigrok.a" \
    $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
    -o "$testdir/test_dla32_wch_timeout_unit.exe"
"$testdir/test_dla32_wch_timeout_unit.exe" 2>&1 \
    | tee "$project/logs/dla32-wch-timeout-helper.log"
# Verify this SDK0 source and runtime cannot load the upload queue exports.
gcc -E -dM -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$source/src/hardware/fnirsi-dla32/api.c" \
    > "$testdir/default-macros.txt"
if grep -q 'DLA_WCH_UPLOAD_DIAGNOSTIC 1' "$testdir/default-macros.txt"; then exit 1; fi
if strings "$prefix/bin/libsigrok-4.dll" | grep -E 'CH375SetBufUploadEx|CH375ClearBufUpload'; then exit 1; fi
printf 'PASS: SDK upload queue exports are absent from the SDK0 runtime.\n' \
    | tee "$project/logs/dla32-wch-timeout-sdk0-guard.log"
