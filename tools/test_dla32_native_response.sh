#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-worker-v10-source/libsigrok"
build=/tmp/dla32-wch-worker-v10-build/libsigrok
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
output="$project/artifacts/dla32-wch-worker-v10-tests/native-response.exe"
[[ ! -e "$output" ]]
flags='-DDLA_WCH_WORKER=1 -DDLA_WCH_WORKER_UPLOAD=1 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_WCH_NATIVE_READ=1 -DDLA_WCH_READ_SIZE=1048576 -DDLA_WORKER_BUFFER_SLOTS=16 -DDLA_WCH_UPLOAD_LENGTH=1048576'
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections $flags \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$project/tools/test_dla32_native_response.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$output"
"$output" > "$project/logs/dla32-worker-v10-native-response.log" 2>&1
for spec in bad-native missing-upload; do
    guard=(-DDLA_WCH_NATIVE_READ=1)
    [[ "$spec" != bad-native ]] || guard=(-DDLA_WCH_NATIVE_READ=2)
    logfile="$project/logs/dla32-worker-v10-guard-$spec.log"
    [[ ! -e "$logfile" ]]
    header=$(cygpath -m "$source/src/hardware/fnirsi-dla32/transport-wch.h")
    if printf '#include "%s"\n' "$header" | gcc -E "${guard[@]}" -x c - > /dev/null 2>"$logfile"; then
        echo "FAIL: expected native guard rejection $spec";exit 1
    fi
    echo "PASS: expected native guard rejection $spec"
done
