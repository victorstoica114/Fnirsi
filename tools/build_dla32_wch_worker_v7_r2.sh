#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
prefix=/tmp/dla32-wch-worker-v7-r2-prefix
build=/tmp/dla32-wch-worker-v7-r2-build/libsigrok
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig"
cd "$build"
make -j4
make check
make install
cp tests/main.log "$project/logs/dla32-wch-worker-v7-r2-general-checks.log"
cp test-suite.log "$project/logs/dla32-wch-worker-v7-r2-general-summary.log"
bash "$project/tools/test_dla32_wch_worker_v7_r2.sh" dla32-wch-worker-v7-r2-final-r2
# Final interaction tests and inherited checks use fresh evidence labels.
bash "$project/tools/test_dla32_wch_worker_v7_r2_inherited.sh"
"$project/artifacts/test_dla32_stop_worker_v7_cancel.exe" --self-test \
    > "$project/logs/dla32-wch-worker-v7-r2-harness-self-test.log" 2>&1
