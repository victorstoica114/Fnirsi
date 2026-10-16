#!/usr/bin/env bash
set -euo pipefail
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-upload-v2-source/libsigrok"
build=/tmp/dla32-wch-upload-v2-build/libsigrok
testdir="$project/artifacts/dla32-wch-upload-v2-tests"
export PATH=/ucrt64/bin:/usr/bin
includes=(-I"$build" -I"$build/include" -I"$source/include" -I"$source/src")
read -r -a dependencies <<< "$(pkg-config --cflags glib-2.0 zlib nettle libusb-1.0 gio-2.0 libzip)"
driver="$source/src/hardware/fnirsi-dla32/api.c"
gcc -E -dM "${includes[@]}" "${dependencies[@]}" "$driver" \
    > "$testdir/default-upload-macros.txt"
gcc -E "${includes[@]}" "${dependencies[@]}" "$driver" \
    > "$testdir/default-upload-api.i"
python - "$testdir" <<'PY'
from pathlib import Path
import sys
directory = Path(sys.argv[1])
assert '#define DLA_WCH_UPLOAD_DIAGNOSTIC 0\n' in (directory / 'default-upload-macros.txt').read_text()
text = (directory / 'default-upload-api.i').read_text()
assert 'CH375SetBufUploadEx' not in text and 'CH375ClearBufUpload' not in text
print('PASS: upload macro omitted defaults0 and SDK exports/calls are absent.')
PY
for guard in invalid-upload changed-prearm; do
    flags=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=2)
    expected='DLA_WCH_UPLOAD_DIAGNOSTIC must be 0 or 1'
    if [[ "$guard" == changed-prearm ]]; then
        flags=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_PREARM_EMPTY_READS=2)
        expected='WCH upload diagnostic requires the baseline one-empty pre-arm policy'
    fi
    if gcc -E "${flags[@]}" "${includes[@]}" "${dependencies[@]}" "$driver" \
        -o /dev/null 2> "$testdir/guard-$guard.log"; then
        printf 'FAIL: %s compile-time guard unexpectedly accepted.\n' "$guard" >&2
        exit 1
    fi
    python - "$testdir/guard-$guard.log" "$expected" <<'PY'
from pathlib import Path
import sys
assert sys.argv[2] in Path(sys.argv[1]).read_text()
print(f'PASS: intended compile-time guard rejects {sys.argv[2]}.')
PY
done
