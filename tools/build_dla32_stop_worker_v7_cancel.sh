#!/usr/bin/env bash
set -euo pipefail
# Compile only. This separate harness does not replace any frozen executable.
if [[ $# != 2 || $1 != /* || $2 != /* ]]; then
    printf 'Usage: build_dla32_stop_worker_v7_cancel.sh ABS_PREFIX ABS_NEW_EXE_PATH\n' >&2
    exit 2
fi
variant_prefix=$1
output_exe=$2
project=/tmp/dla32-project
export PATH="$variant_prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$variant_prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
[[ -f "$variant_prefix/bin/libsigrok-4.dll" ]]
[[ -d "$(dirname "$output_exe")" && ! -e "$output_exe" ]]
gcc -std=c99 -D_WIN32_WINNT=0x0600 -O2 -Wall -Wextra -Werror \
    $(pkg-config --cflags libsigrok glib-2.0) \
    "$project/tools/test_dla32_stop_worker_v7_cancel.c" \
    $(pkg-config --libs libsigrok glib-2.0) -o "$output_exe"
printf 'Built separate cancellation harness %s; no hardware executed.\n' "$output_exe"
