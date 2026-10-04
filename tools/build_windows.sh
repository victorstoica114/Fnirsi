#!/usr/bin/env bash
set -euo pipefail
# Run using the project's MSYS2 UCRT64 bash. Source junction avoids path spaces.
src=/tmp/dla32-src
build=/tmp/dla32-build
prefix=/opt/dla32
mkdir -p "$build" "$prefix"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin:$PATH"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export CFLAGS="-O2 -g"
export CXXFLAGS="-O2 -g"
jobs=4
export LC_ALL=C

component=${1:-all}
if [[ $component == all || $component == decode ]]; then
    (cd "$src/libsigrokdecode" && ./autogen.sh)
    mkdir -p "$build/libsigrokdecode"
    (cd "$build/libsigrokdecode" && "$src/libsigrokdecode/configure" --prefix="$prefix" && make -j"$jobs" && make install)
fi
if [[ $component == all || $component == sigrok ]]; then
    gcc -std=c99 -O2 -Wall -Wextra -Werror /tmp/dla32-project/tools/test_dla32_protocol.c -o "$build/test_dla32_protocol.exe"
    "$build/test_dla32_protocol.exe"
    if [[ ! -f "$src/libsigrok/configure" || "$src/libsigrok/configure.ac" -nt "$src/libsigrok/configure" ]]; then
        (cd "$src/libsigrok" && ./autogen.sh)
    fi
    mkdir -p "$build/libsigrok"
    (cd "$build/libsigrok" && "$src/libsigrok/configure" --prefix="$prefix" --disable-all-drivers --enable-demo --enable-fnirsi-dla16 --enable-fnirsi-dla32 --enable-cxx --disable-python --disable-ruby --disable-java && make -j"$jobs" && make check && make install)
fi
if [[ $component == all || $component == cli ]]; then
    (cd "$src/sigrok-cli" && ./autogen.sh)
    mkdir -p "$build/sigrok-cli"
    (cd "$build/sigrok-cli" && "$src/sigrok-cli/configure" --prefix="$prefix" && make -j"$jobs" && make install)
fi
if [[ $component == all || $component == pulseview ]]; then
    cmake -S "$src/pulseview" -B "$build/pulseview" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_PREFIX_PATH="$prefix;/ucrt64" -DSTATIC_PKGDEPS_LIBS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    cmake --build "$build/pulseview" -j"$jobs"
    cmake --install "$build/pulseview"
fi
printf 'Build component completed: %s\n' "$component"
