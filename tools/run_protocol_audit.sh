#!/usr/bin/env bash
set -euo pipefail
export PATH=/opt/dla32/bin:/ucrt64/bin:/usr/bin
project=/tmp/dla32-project
{
    if [[ $(gcc -print-file-name=libubsan.a) == libubsan.a ]]; then
        printf 'UBSan unavailable in this GCC runtime; using -fanalyzer and -ftrapv. These do not replace UBSan.\n'
        audit_flags=(-fanalyzer -ftrapv)
    else
        audit_flags=(-fsanitize=undefined -fno-sanitize-recover=undefined)
    fi
    for mode in simd table; do
        flags=()
        if [[ "$mode" == table ]]; then flags=(-DDLA_DISABLE_SIMD); fi
        gcc -std=c99 -O2 -Wall -Wextra -Werror "${audit_flags[@]}" "${flags[@]}" \
            "$project/tools/test_dla32_protocol.c" \
            -o "$project/artifacts/protocol-audit-$mode.exe"
        printf 'Protocol %s audit:\n' "$mode"
        "$project/artifacts/protocol-audit-$mode.exe"
    done
} 2>&1 | tee "$project/logs/driver-audit-protocol.log"
