#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
prefix=/tmp/dla32-wch-worker-v7-prefix
build=/tmp/dla32-wch-worker-v7-build/libsigrok
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
cd "$build"
make -j4
make check
make install
cp tests/main.log "$project/logs/dla32-wch-worker-v7-general-checks.log"
cp test-suite.log "$project/logs/dla32-wch-worker-v7-general-summary.log"
bash "$project/tools/test_dla32_wch_worker_v7.sh" dla32-wch-worker-v7-final
bash "$project/tools/test_dla32_wch_worker_v7_inherited.sh"
bash "$project/tools/build_dla32_stop_worker_v7_cancel.sh" "$prefix" \
    "$project/artifacts/test_dla32_stop_worker_v7_cancel.exe"
"$project/artifacts/test_dla32_stop_worker_v7_cancel.exe" --self-test \
    2>&1 | tee "$project/logs/dla32-wch-worker-v7-harness-self-test.log"
