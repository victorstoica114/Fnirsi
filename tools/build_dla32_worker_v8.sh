#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-worker-v8-source/libsigrok"
build=/tmp/dla32-wch-worker-v8-build/libsigrok
prefix=/tmp/dla32-wch-worker-v8-prefix
tests="$project/artifacts/dla32-wch-worker-v8-tests"
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export LC_ALL=C
for spec in 1m2:1048576:2 2m2:2097152:2 4m2:4194304:2 4m16:4194304:16; do
    IFS=: read -r label bytes slots <<< "$spec"
    package="$project/artifacts/dla32-wch-worker-v8-$label"
    [[ ! -e "$package" ]]
    flags="-DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 -DDLA_WCH_READ_SIZE=$bytes -DDLA_WORKER_BUFFER_SLOTS=$slots"
    if [[ ! -e "$tests/$label.exe" ]]; then
    touch "$source/src/hardware/fnirsi-dla32/api.c"
    (cd "$build"; make -j4 CPPFLAGS="$flags"; make check CPPFLAGS="$flags") \
        > "$project/logs/dla32-wch-worker-v8-$label-build.log" 2>&1
    cp "$build/tests/main.log" "$project/logs/dla32-wch-worker-v8-$label-general-checks.log"
    gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
        -ffunction-sections -fdata-sections $flags \
        -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
        $(pkg-config --cflags libsigrok) "$tests/test_dla32_wch_worker_v8_unit.c" \
        "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
        -Wl,--gc-sections -o "$tests/$label.exe"
    fi
    "$tests/$label.exe" > "$project/logs/dla32-wch-worker-v8-$label-unit.log" 2>&1
    mkdir "$package"
    cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/"*.dll "$package/"
    cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/sigrok-cli.exe" "$package/"
    cp "$build/.libs/libsigrok-4.dll" "$package/libsigrok-4.dll"
    echo "PASS: isolated $label build and lifecycle fixture"
done
