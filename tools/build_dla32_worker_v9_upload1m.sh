#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-worker-v9-source/libsigrok"
build=/tmp/dla32-wch-worker-v9-build/libsigrok
tests="$project/artifacts/dla32-wch-worker-v9-tests"
export PATH="$project/artifacts/dla32-wch-worker-v7-r2-sdk0:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="/tmp/dla32-wch-worker-v7-r2-prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
flags='-DDLA_PREARM_EMPTY_READS=1 -DDLA_WCH_WORKER=1 -DDLA_WCH_WORKER_UPLOAD=1 -DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_WCH_READ_SIZE=2097152 -DDLA_WORKER_BUFFER_SLOTS=16 -DDLA_WCH_UPLOAD_LENGTH=1048576'
[[ ! -e "$tests/upload1m-unit.exe" && ! -e "$project/artifacts/dla32-wch-worker-v9-upload1m" ]]
gcc -std=c99 -O1 -Wall -Wextra -Werror -Wno-unused-function \
    -ffunction-sections -fdata-sections $flags \
    -I"$build" -I"$build/include" -I"$source/include" -I"$source/src" \
    $(pkg-config --cflags libsigrok) "$project/tools/test_dla32_worker_v9_upload.c" \
    "$build/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
    -Wl,--gc-sections -o "$tests/upload1m-unit.exe"
"$tests/upload1m-unit.exe" > "$project/logs/dla32-worker-v9-upload1m-unit.log" 2>&1
touch "$source/src/hardware/fnirsi-dla32/api.c"
(cd "$build";make -j4 CPPFLAGS="$flags";make check CPPFLAGS="$flags") \
    > "$project/logs/dla32-worker-v9-upload1m-build.log" 2>&1
cp "$build/tests/main.log" "$project/logs/dla32-worker-v9-upload1m-general-checks.log"
package="$project/artifacts/dla32-wch-worker-v9-upload1m"
mkdir "$package"
cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/"*.dll "$package/"
cp "$project/artifacts/dla32-wch-worker-v7-r2-sdk0/sigrok-cli.exe" "$package/"
cp "$build/.libs/libsigrok-4.dll" "$package/libsigrok-4.dll"
echo 'PASS: isolated 1 MiB kernel upload variant built and checked offline'
