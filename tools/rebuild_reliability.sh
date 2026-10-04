#!/usr/bin/env bash
set -euo pipefail
export PATH=/opt/dla32/bin:/ucrt64/bin:/usr/bin
export PKG_CONFIG_PATH=/opt/dla32/lib/pkgconfig:/ucrt64/lib/pkgconfig
build=/tmp/dla32-build/libsigrok
make -C "$build" -j4
make -C "$build" check
make -C "$build" install
