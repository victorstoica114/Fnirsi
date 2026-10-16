#!/usr/bin/env bash
set -euo pipefail
# Compile only. Never launch a hardware capture from this script.
export PATH=/opt/dla32-wiretrace/bin:/ucrt64/bin:/usr/bin
export PKG_CONFIG_PATH=/opt/dla32-wiretrace/lib/pkgconfig:/ucrt64/lib/pkgconfig
project=/tmp/dla32-project
prefix=/opt/dla32-wiretrace
dll_hash=$(sha256sum "$prefix/bin/libsigrok-4.dll" | cut -d' ' -f1)
gcc -std=c99 -O2 -Wall -Wextra -Werror \
    -DEXPECTED_LIBSIGROK_SHA256="\"$dll_hash\"" \
    $(pkg-config --cflags libsigrok glib-2.0) \
    "$project/tools/test_dla32_wire_sequence.c" \
    $(pkg-config --libs libsigrok glib-2.0) \
    -o "$project/artifacts/test_dla32_wire_sequence.exe"
printf 'Built hardware harness only; expected diagnostic DLL SHA256 %s\n' "$dll_hash"
