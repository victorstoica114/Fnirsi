#!/usr/bin/env bash
set -euo pipefail
# Read-only access to original sources/runtimes; diagnostic build is isolated.
source=/tmp/la1010-reference-src/libsigrok
build=/tmp/dla32-wiretrace-build/libsigrok
prefix=/opt/dla32-wiretrace
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export CFLAGS='-O2 -g'
export LC_ALL=C
mkdir -p "$build" "$prefix"
cd "$build"
"$source/configure" --prefix="$prefix" --disable-all-drivers \
    --enable-demo --enable-fnirsi-dla32 --disable-cxx \
    --disable-python --disable-ruby --disable-java
make -j4
make check
make install
