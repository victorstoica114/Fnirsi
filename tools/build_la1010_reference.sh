#!/usr/bin/env bash
set -euo pipefail
# Separate configure tree and prefix; existing DLA32 builds remain loaded safely.
src=/tmp/dla32-src
sigrok_src=/tmp/la1010-reference-src/libsigrok
build=/tmp/la1010-reference-build
prefix=/opt/la1010-reference
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export CFLAGS='-O2 -g'
export LC_ALL=C
mkdir -p "$build/libsigrok" "$build/sigrok-cli" "$prefix"

cd "$build/libsigrok"
"$sigrok_src/configure" --prefix="$prefix" \
    --disable-all-drivers --enable-demo --enable-kingst-la2016 \
    --enable-fnirsi-dla32 --disable-cxx --disable-python --disable-ruby --disable-java
make -j4
make check
make install

cd "$build/sigrok-cli"
"$src/sigrok-cli/configure" --prefix="$prefix" --without-libsigrokdecode
make -j4
make install
"$prefix/bin/sigrok-cli.exe" --list-supported
