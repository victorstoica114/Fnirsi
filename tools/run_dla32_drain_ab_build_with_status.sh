#!/usr/bin/env bash
set -euo pipefail
# Future builds only. Refuses existing logs and stores the native Bash status.
# All compiler stderr stays inside Bash, avoiding PowerShell NativeCommandError.
project=/tmp/dla32-project
if [[ $# != 1 || ! $1 =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    printf 'Usage: run_dla32_drain_ab_build_with_status.sh UNIQUE_BUILD_LABEL\n' >&2
    exit 2
fi
task_log="$project/logs/build-dla32-drain-ab-$1.log"
task_status="$project/logs/build-dla32-drain-ab-$1.status.json"
[[ ! -e "$task_log" && ! -e "$task_status" ]]
set +e
bash "$project/tools/build_dla32_drain_ab_variants.sh" > "$task_log" 2>&1
task_code=$?
set -e
printf '{"native_bash_exit_code":%d,"log":"build-dla32-drain-ab-%s.log"}\n' \
    "$task_code" "$1" > "$task_status"
printf 'Native Bash exit code: %s; transcript: %s; status: %s\n' \
    "$task_code" "$task_log" "$task_status"
exit "$task_code"
