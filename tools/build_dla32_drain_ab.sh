#!/usr/bin/env bash
set -euo pipefail
# Compile only, using the isolated variant install prefix and package directory.
# Example: tools/build_dla32_drain_ab.sh /tmp/dla32-drain-ab-prefix/baseline \
#          /tmp/dla32-project/artifacts/dla32-drain-ab-baseline
# Root runs --self-test separately; this script never opens a device.
if [[ $# != 2 || $1 != /* || $2 != /* ]]; then
    printf 'Usage: build_dla32_drain_ab.sh ABSOLUTE_VARIANT_INSTALL_PREFIX ABSOLUTE_PACKAGE_DIR\n' >&2
    exit 2
fi
variant_prefix=$1
package_dir=$2
project=/tmp/dla32-project
export PATH="$variant_prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$variant_prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
[[ -f "$variant_prefix/bin/libsigrok-4.dll" && -d "$package_dir" ]]
[[ ! -e "$package_dir/test_dla32_drain_ab.exe" ]]
gcc -std=c99 -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags libsigrok glib-2.0) \
    "$project/tools/test_dla32_drain_ab.c" \
    $(pkg-config --libs libsigrok glib-2.0) \
    -o "$package_dir/test_dla32_drain_ab.exe"
printf 'Built %s/test_dla32_drain_ab.exe; no hardware executed.\n' "$package_dir"
