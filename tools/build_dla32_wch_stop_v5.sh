#!/usr/bin/env bash
set -euo pipefail
sdk=${1:?SDK policy required}
[[ "$sdk" == 0 || "$sdk" == 1 ]]
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-stop-v5-source/libsigrok"
build="/tmp/dla32-wch-stop-v5-sdk$sdk-build/libsigrok"
prefix="/tmp/dla32-wch-stop-v5-sdk$sdk-prefix"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export LC_ALL=C
export CFLAGS='-O2 -g'
export CPPFLAGS='-DDLA_PREARM_EMPTY_READS=1'
# SDK0 deliberately omits the upload macro to exercise the true default.
if [[ "$sdk" == 1 ]]; then export CPPFLAGS="$CPPFLAGS -DDLA_WCH_UPLOAD_DIAGNOSTIC=1"; fi
mkdir -p "$build" "$prefix"
cd "$build"
"$source/configure" --prefix="$prefix" --disable-all-drivers \
    --enable-demo --enable-fnirsi-dla32 --disable-cxx \
    --disable-python --disable-ruby --disable-java
make -j4
make check
make install
bash "$project/tools/test_dla32_wch_stop_v5.sh" "$sdk"
