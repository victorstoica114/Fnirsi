#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-winusb-v11-source/libsigrok"
build=/tmp/dla32-winusb-v11-build/libsigrok
tests="$project/artifacts/dla32-winusb-v11-tests"
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
flags='-DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1'
[[ ! -e "$tests/endpoint-unit.exe" && ! -e "$project/artifacts/dla32-winusb-v11" ]]
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections $flags \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$project/tools/test_dla32_winusb_v11.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$tests/endpoint-unit.exe"
"$tests/endpoint-unit.exe" > "$project/logs/dla32-winusb-v11-unit.log" 2>&1
touch "$source/src/hardware/fnirsi-dla32/api.c"
(cd "$build";make -j4 CPPFLAGS="$flags";make check CPPFLAGS="$flags") \
    > "$project/logs/dla32-winusb-v11-build.log" 2>&1
cp "$build/tests/main.log" "$project/logs/dla32-winusb-v11-general-checks.log"
package="$project/artifacts/dla32-winusb-v11"
mkdir "$package"
cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/"*.dll "$package/"
cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/sigrok-cli.exe" "$package/"
cp "$build/.libs/libsigrok-4.dll" "$package/libsigrok-4.dll"
echo 'PASS: isolated libusb endpoint/identity correction built and checked offline'
