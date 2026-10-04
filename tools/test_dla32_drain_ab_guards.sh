#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-drain-ab-source/libsigrok"
build=/tmp/dla32-drain-ab-build/baseline/libsigrok
testdir="$project/artifacts/dla32-drain-ab-tests"
export PATH=/ucrt64/bin:/usr/bin
includes=(-I"$build" -I"$build/include" -I"$source/include" -I"$source/src")
read -r -a dependency_flags <<< "$(pkg-config --cflags glib-2.0 zlib nettle libusb-1.0 gio-2.0 libzip)"
driver="$source/src/hardware/fnirsi-dla32/api.c"
gcc -E -dM "${includes[@]}" "${dependency_flags[@]}" "$driver" \
    > "$testdir/default-preprocessor-macros.txt"
python - "$testdir/default-preprocessor-macros.txt" <<'PY'
from pathlib import Path
import sys
assert '#define DLA_PREARM_EMPTY_READS 1\n' in Path(sys.argv[1]).read_text()
print('PASS: omitted compile-time policy defaults to one empty read.')
PY
for policy in 0 3; do
    if gcc -E -DDLA_PREARM_EMPTY_READS="$policy" \
        "${includes[@]}" "${dependency_flags[@]}" "$driver" \
        -o /dev/null 2> "$testdir/rejected-policy-$policy.log"; then
        printf 'FAIL: invalid policy %s unexpectedly accepted.\n' "$policy" >&2
        exit 1
    fi
    python - "$testdir/rejected-policy-$policy.log" "$policy" <<'PY'
from pathlib import Path
import sys
assert 'DLA_PREARM_EMPTY_READS must be 1 or 2' in Path(sys.argv[1]).read_text()
print(f'PASS: invalid policy {sys.argv[2]} rejected by intended compile-time guard.')
PY
done
