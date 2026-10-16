#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
task_log="$project/logs/build-dla32-wch-upload.log"
task_status="$project/logs/build-dla32-wch-upload.status.json"
[[ ! -e "$task_log" && ! -e "$task_status" ]]
set +e
bash "$project/tools/build_dla32_wch_upload.sh" > "$task_log" 2>&1
task_code=$?
set -e
printf '{"native_bash_exit_code":%d,"log":"build-dla32-wch-upload.log"}\n' "$task_code" > "$task_status"
printf 'WCH upload build native Bash exit code: %s\n' "$task_code"
exit "$task_code"
