#!/usr/bin/env bash
set -euo pipefail
sdk=${1:?SDK policy required}
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-stop-v5-source/libsigrok"
build="/tmp/dla32-wch-stop-v5-sdk$sdk-build/libsigrok"
prefix="/tmp/dla32-wch-stop-v5-sdk$sdk-prefix"
testdir="$project/artifacts/dla32-wch-stop-v5-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
flags=(-DDLA_PREARM_EMPTY_READS=1)
if [[ "$sdk" == 1 ]]; then flags+=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=1); fi
compile_case() {
    local testsource=$1 label=$2
    shift 2
    gcc -std=c99 -O1 -Wall -Wextra -ffunction-sections -fdata-sections \
        "${flags[@]}" "$@" -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
        $(pkg-config --cflags libsigrok) "$testdir/$testsource" "$build/.libs/libsigrok.a" \
        $(pkg-config --libs --static libsigrok) -Wl,--gc-sections -o "$testdir/$label.exe"
    "$testdir/$label.exe" 2>&1 | tee "$project/logs/$label.log"
}
compile_case "test_dla32_wch_stop_v5_sdk$sdk"_unit.c "dla32-wch-stop-v5-sdk$sdk-unit"
if [[ "$sdk" == 0 ]]; then
    compile_case test_dla32_wch_stop_v5_sdk0_unit.c dla32-wch-stop-v5-sdk0-explicit-unit -DDLA_WCH_UPLOAD_DIAGNOSTIC=0
fi
compile_case test_dla32_wch_stop_v5_timeout_unit.c "dla32-wch-stop-v5-sdk$sdk-timeout" -Werror -Wno-unused-function
bash "$project/tools/test_dla32_wch_stop_v5_guards.sh" "$sdk"
