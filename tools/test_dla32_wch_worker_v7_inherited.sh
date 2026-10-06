#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-worker-v7-source/libsigrok"
build=/tmp/dla32-wch-worker-v7-build/libsigrok
prefix=/tmp/dla32-wch-worker-v7-prefix
tests="$project/artifacts/dla32-wch-worker-v7-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
compile_case() {
    local kind=$1 label=$2
    shift 2
    [[ ! -e "$tests/$label.exe" && ! -e "$project/logs/$label.log" ]]
    gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-error=sign-compare -Wno-unused-function \
        -ffunction-sections -fdata-sections -DDLA_PREARM_EMPTY_READS=1 "$@" \
        -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
        $(pkg-config --cflags libsigrok) "$tests/test_dla32_wch_worker_v7_inherited_$kind.c" \
        "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
        -Wl,--gc-sections -o "$tests/$label.exe"
    "$tests/$label.exe" 2>&1 | tee "$project/logs/$label.log"
}
if [[ "${1:-all}" != guards-only ]]; then
    compile_case sdk0 dla32-wch-worker-v7-default0
    compile_case sdk0 dla32-wch-worker-v7-explicit0 -DDLA_WCH_WORKER=0 -DDLA_WCH_UPLOAD_DIAGNOSTIC=0
    compile_case sdk1 dla32-wch-worker-v7-sdk1-worker0 -DDLA_WCH_WORKER=0 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1
    compile_case timeout dla32-wch-worker-v7-timeout-worker1 -DDLA_WCH_WORKER=1
fi
guardbase="$tests/compile-guards-r2"
guard_header=$(cygpath -m "$source/src/hardware/fnirsi-dla32/transport-wch.h")
mkdir -p "$guardbase"
for entry in worker2 sdk1 nonwindows; do
    flags=(-DDLA_WCH_WORKER=1)
    [[ "$entry" != worker2 ]] || flags=(-DDLA_WCH_WORKER=2)
    [[ "$entry" != sdk1 ]] || flags+=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=1)
    [[ "$entry" != nonwindows ]] || flags+=(-U_WIN32)
    logfile="$guardbase/$entry.log"
    [[ ! -e "$logfile" ]]
    if printf '#include "%s"\n' "$guard_header" | \
            gcc -E "${flags[@]}" -x c - >"$logfile" 2>&1; then
        echo "FAIL: expected compile rejection $entry"; exit 1
    fi
    if [[ "$entry" == worker2 ]]; then
        grep -Fq 'DLA_WCH_WORKER must be 0 or 1' "$logfile"
    else
        grep -Fq 'isolated WCH worker supports Win32 SDK0 only' "$logfile"
    fi
    echo "PASS: compile rejection $entry"
done
