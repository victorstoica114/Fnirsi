#!/usr/bin/env bash
set -euo pipefail
export PATH=/opt/la1010-reference/bin:/ucrt64/bin:/usr/bin
export PKG_CONFIG_PATH=/opt/la1010-reference/lib/pkgconfig:/ucrt64/lib/pkgconfig
build=/tmp/la1010-reference-build/libsigrok
source=/tmp/la1010-reference-src/libsigrok
testdir=/tmp/dla32-project/artifacts/la1010-reference-tests
gcc -std=c99 -O2 -Wall -Wextra -Werror \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" -I"$testdir" \
    $(pkg-config --cflags libsigrok libusb-1.0) \
    /tmp/dla32-project/tools/test_la1010_usb_timer.c \
    -o "$testdir/test_la1010_usb_timer.exe" \
    $(pkg-config --libs libsigrok libusb-1.0)
"$testdir/test_la1010_usb_timer.exe"
