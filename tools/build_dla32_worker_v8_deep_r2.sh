#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
build=/tmp/dla32-wch-worker-v8-build/libsigrok
source="$project/artifacts/dla32-wch-worker-v8-source/libsigrok"
prefix=/tmp/dla32-wch-worker-v7-r2-prefix
tests="$project/artifacts/dla32-wch-worker-v8-tests"
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
for spec in 4m16:4194304 2m16:2097152; do
    IFS=: read -r label bytes <<< "$spec"
    flags="-DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 -DDLA_WCH_READ_SIZE=$bytes -DDLA_WORKER_BUFFER_SLOTS=16"
    [[ ! -e "$tests/$label-deep-r2.exe" && ! -e "$project/artifacts/dla32-wch-worker-v8-$label" ]]
    if [[ "$label" == 2m16 ]]; then
        touch "$source/src/hardware/fnirsi-dla32/api.c"
        (cd "$build"; make -j4 CPPFLAGS="$flags"; make check CPPFLAGS="$flags") \
            > "$project/logs/dla32-wch-worker-v8-$label-build.log" 2>&1
        cp "$build/tests/main.log" "$project/logs/dla32-wch-worker-v8-$label-general-checks.log"
    fi
    gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
        -ffunction-sections -fdata-sections $flags \
        -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
        $(pkg-config --cflags libsigrok) "$tests/test_dla32_wch_worker_v8_unit_deep_r2.c" \
        "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
        -Wl,--gc-sections -o "$tests/$label-deep-r2.exe"
    "$tests/$label-deep-r2.exe" > "$project/logs/dla32-wch-worker-v8-$label-deep-r2-unit.log" 2>&1
    package="$project/artifacts/dla32-wch-worker-v8-$label"
    mkdir "$package"
    cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/"*.dll "$package/"
    cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/sigrok-cli.exe" "$package/"
    cp "$build/.libs/libsigrok-4.dll" "$package/libsigrok-4.dll"
    echo "PASS: $label corrected deep-ring fixture"
done
