#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-drain-ab-source/libsigrok"
testdir="$project/artifacts/dla32-drain-ab-tests"
export LC_ALL=C
for variant in baseline two-empty; do
    policy=1
    if [[ "$variant" == two-empty ]]; then policy=2; fi
    build="/tmp/dla32-drain-ab-build/$variant/libsigrok"
    prefix="/tmp/dla32-drain-ab-prefix/$variant"
    export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
    export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
    export CFLAGS='-O2 -g'
    export CPPFLAGS="-DDLA_PREARM_EMPTY_READS=$policy"
    mkdir -p "$build" "$prefix"
    cd "$build"
    "$source/configure" --prefix="$prefix" --disable-all-drivers \
        --enable-demo --enable-fnirsi-dla32 --disable-cxx \
        --disable-python --disable-ruby --disable-java
    make -j4
    make check
    make install
    gcc -std=c99 -O1 -Wall -Wextra -ffunction-sections -fdata-sections \
        -DDLA_PREARM_EMPTY_READS="$policy" \
        -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
        $(pkg-config --cflags libsigrok) \
        "$testdir/test_dla32_drain_ab_unit.c" "$build/.libs/libsigrok.a" \
        $(pkg-config --libs --static libsigrok) -Wl,--gc-sections \
        -o "$testdir/test_dla32_drain_ab_unit-$variant.exe"
    "$testdir/test_dla32_drain_ab_unit-$variant.exe" 2>&1 \
        | tee "$project/logs/dla32-drain-ab-unit-$variant.log"
done
# The protocol decoder is byte-for-byte unchanged; exercise both its code paths.
for mode in simd table; do
    flags=()
    if [[ "$mode" == table ]]; then flags=(-DDLA_DISABLE_SIMD); fi
    gcc -std=c99 -O2 -Wall -Wextra -Werror "${flags[@]}" \
        "$project/tools/test_dla32_protocol.c" \
        -o "$testdir/test_dla32_protocol-$mode.exe"
    "$testdir/test_dla32_protocol-$mode.exe" 2>&1 \
        | tee "$project/logs/dla32-drain-ab-protocol-$mode.log"
done
