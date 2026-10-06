#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-worker-v7-source/libsigrok"
build=/tmp/dla32-wch-worker-v7-build/libsigrok
prefix=/tmp/dla32-wch-worker-v7-prefix
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export LC_ALL=C
export CFLAGS='-O2 -g'
export CPPFLAGS='-DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1'
mkdir -p "$build" "$prefix"
cd "$build"
"$source/configure" --build=x86_64-w64-mingw32 --host=x86_64-w64-mingw32 \
    --prefix="$prefix" --disable-all-drivers \
    --enable-demo --enable-fnirsi-dla32 --disable-cxx \
    --disable-python --disable-ruby --disable-java
make -j4
make check
make install
echo 'V7 isolated SDK0 worker build complete; lifecycle interaction tests run separately.'
