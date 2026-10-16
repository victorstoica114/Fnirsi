#!/usr/bin/env bash
set -euo pipefail
sdk=${1:?SDK policy required}
[[ "$sdk" == 0 || "$sdk" == 1 ]]
project=/tmp/dla32-project
task_log="$project/logs/build-dla32-wch-stop-v5-sdk$sdk.log"
task_status="$project/logs/build-dla32-wch-stop-v5-sdk$sdk.status.json"
[[ ! -e "$task_log" && ! -e "$task_status" ]]
set +e
bash "$project/tools/build_dla32_wch_stop_v5.sh" "$sdk" > "$task_log" 2>&1
task_code=$?
set -e
printf '{"native_bash_exit_code":%d,"sdk_policy":%s,"log":"build-dla32-wch-stop-v5-sdk%s.log"}\n' "$task_code" "$sdk" "$sdk" > "$task_status"
printf 'WCH STOP V5 SDK%s native Bash exit: %s\n' "$sdk" "$task_code"
exit "$task_code"
