#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
build=/tmp/dla32-wch-worker-v8-build/libsigrok
source="$project/artifacts/dla32-wch-worker-v8-source/libsigrok"
prefix=/tmp/dla32-wch-worker-v7-r2-prefix
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
for slots in 2 16; do
    output="$project/artifacts/dla32-wch-worker-v8-tests/ring-$slots.exe"
    [[ ! -e "$output" ]]
    gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
        -ffunction-sections -fdata-sections -DDLA_WCH_WORKER=1 \
        -DDLA_WCH_READ_SIZE=4194304 -DDLA_WORKER_BUFFER_SLOTS=$slots \
        -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
        $(pkg-config --cflags libsigrok) "$project/tools/test_dla32_worker_v8_ring.c" \
        "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
        -Wl,--gc-sections -o "$output"
    "$output" > "$project/logs/dla32-worker-v8-ring-$slots.log" 2>&1
done
output="$project/artifacts/test_dla32_worker_v8_continuous.exe"
[[ ! -e "$output" ]]
gcc -std=c99 -D_WIN32_WINNT=0x0600 -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags libsigrok glib-2.0 libzstd) \
    "$project/tools/test_dla32_worker_v8_continuous.c" \
    $(pkg-config --libs libsigrok glib-2.0 libzstd) -o "$output"
"$output" --self-test > "$project/logs/dla32-worker-v8-continuous-self-test.log" 2>&1
echo 'PASS: ring wrap and lossless compressed harness checks'
