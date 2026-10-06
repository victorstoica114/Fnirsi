#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
build=/tmp/dla32-wch-worker-v9-build/libsigrok
source="$project/artifacts/dla32-wch-worker-v9-source/libsigrok"
tests="$project/artifacts/dla32-wch-worker-v9-tests"
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
flags='-DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 -DDLA_WCH_WORKER_UPLOAD=1 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_WCH_READ_SIZE=2097152 -DDLA_WORKER_BUFFER_SLOTS=16 -DDLA_WCH_UPLOAD_LENGTH=4194304'
[[ ! -e "$tests/upload-unit-r3.exe" ]]
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections $flags \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$project/tools/test_dla32_worker_v9_upload.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$tests/upload-unit-r3.exe"
"$tests/upload-unit-r3.exe" > "$project/logs/dla32-worker-v9-upload-unit-r3.log" 2>&1
for spec in missing-optin worker-off upload-off bad-optin; do
    guard=(-DDLA_WCH_WORKER=1 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_WCH_WORKER_UPLOAD=1)
    [[ "$spec" != missing-optin ]] || guard=(-DDLA_WCH_WORKER=1 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1)
    [[ "$spec" != worker-off ]] || guard=(-DDLA_WCH_WORKER=0 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_WCH_WORKER_UPLOAD=1)
    [[ "$spec" != upload-off ]] || guard=(-DDLA_WCH_WORKER=1 -DDLA_WCH_WORKER_UPLOAD=1)
    [[ "$spec" != bad-optin ]] || guard=(-DDLA_WCH_WORKER_UPLOAD=2)
    logfile="$project/logs/dla32-worker-v9-guard-$spec.log"
    [[ ! -e "$logfile" ]]
    header=$(cygpath -m "$source/src/hardware/fnirsi-dla32/transport-wch.h")
    if printf '#include "%s"\n' "$header" | gcc -E "${guard[@]}" -x c - > /dev/null 2>"$logfile"; then
        echo "FAIL: expected guard rejection $spec";exit 1
    fi
    echo "PASS: expected guard rejection $spec"
done
