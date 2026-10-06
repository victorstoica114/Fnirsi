#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
export PATH=/ucrt64/bin:/usr/bin
output="$project/artifacts/dla32-driver-binding.exe"
[[ ! -e "$output" ]]
gcc -std=c99 -O2 -Wall -Wextra -Werror -municode -static-libgcc \
    "$project/tools/dla32_driver_binding.c" -lsetupapi -lnewdev -ladvapi32 -o "$output"
