#!/usr/bin/env bash
set -euo pipefail
sdk=${1:?SDK policy required}
project=/tmp/dla32-project
source="$project/artifacts/dla32-wch-stop-v5-source/libsigrok"
build="/tmp/dla32-wch-stop-v5-sdk$sdk-build/libsigrok"
prefix="/tmp/dla32-wch-stop-v5-sdk$sdk-prefix"
testdir="$project/artifacts/dla32-wch-stop-v5-tests"
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
includes=(-I"$build" -I"$build/include" -I"$source/include" -I"$source/src")
read -r -a dependencies <<< "$(pkg-config --cflags glib-2.0 zlib nettle libusb-1.0 gio-2.0 libzip)"
driver="$source/src/hardware/fnirsi-dla32/api.c"
for mode in default explicit0 sdk1; do
    flags=()
    if [[ "$mode" == explicit0 ]]; then flags=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=0); fi
    if [[ "$mode" == sdk1 ]]; then flags=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=1); fi
    gcc -E "${flags[@]}" "${includes[@]}" "${dependencies[@]}" "$driver" > "$testdir/sdk$sdk-$mode-api.i"
    gcc -E -dM "${flags[@]}" "${includes[@]}" "${dependencies[@]}" "$driver" > "$testdir/sdk$sdk-$mode-macros.txt"
done
python - "$project" "$testdir" "$prefix" "$sdk" <<'PY'
from pathlib import Path
import sys
project,directory,prefix=map(Path,sys.argv[1:4]);sdk=int(sys.argv[4])
for mode in ['default','explicit0']:
    text=(directory/f'sdk{sdk}-{mode}-api.i').read_text()
    macros=(directory/f'sdk{sdk}-{mode}-macros.txt').read_text()
    assert '#define DLA_WCH_UPLOAD_DIAGNOSTIC 0\n' in macros
    assert 'upload_stop_pending' in text and 'has_wch_stop_ownership' in text
    assert 'WCH capture STOP complete: phase %s; command accepted.' in text
    assert 'CH375SetBufUploadEx' not in text and 'CH375ClearBufUpload' not in text
    assert 'dla_wch_operation_timeout' in text
    print(f'PASS: {mode} retains WCH STOP ownership/timeouts without upload exports or fake SDK-disable marker.')
text=(directory/f'sdk{sdk}-sdk1-api.i').read_text()
assert 'CH375SetBufUploadEx' in text and 'CH375ClearBufUpload' in text
assert 'WCH UPLOAD capture STOP complete: phase %s; disable precedes STOP.' in text
assert 'WCH capture STOP complete: phase %s; command accepted.' not in text
print('PASS: SDK1 retains exact accepted STOP marker, SDK queue exports and disable-before-STOP helper.')
binary=(prefix/'bin/libsigrok-4.dll').read_bytes()
for symbol in [b'CH375SetBufUploadEx',b'CH375ClearBufUpload']:
    assert (symbol in binary)==bool(sdk)
print(f'PASS: actual SDK{sdk} runtime upload-export dependency matches tested policy.')
old=(project/'artifacts/dla32-wch-upload-v4-source/libsigrok/src/hardware/fnirsi-dla32/api.c').read_text()
new=(project/'artifacts/dla32-wch-stop-v5-source/libsigrok/src/hardware/fnirsi-dla32/api.c').read_text()
def body(text,name):
    start=text.index(name+'(');begin=text.index('{',start);depth=1;i=begin+1
    while depth:
        depth+=(text[i]=='{')-(text[i]=='}');i+=1
    return text[start:i]
for name in ['request_stop','free_prepared_transfers','finish_acquisition','retire_transfer','cancel_transfers','receive_transfer','send_command','read_samples_sync']:
    assert body(old,name)==body(new,name),name
tail='\tdla_stop(packet);\n\tdevc->stop_sent = TRUE;\n\tif (send_command(sdi, packet) != SR_OK)\n\t\tsr_err("Failed to send the acquisition stop command.");'
assert tail in old and tail in new
print('PASS: libusb I/O/cancellation/callback retirement and existing non-WCH STOP branch remain unchanged.')
PY
for guard in invalid-upload changed-prearm; do
    flags=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=2)
    expected='DLA_WCH_UPLOAD_DIAGNOSTIC must be 0 or 1'
    if [[ "$guard" == changed-prearm ]]; then
        flags=(-DDLA_WCH_UPLOAD_DIAGNOSTIC=1 -DDLA_PREARM_EMPTY_READS=2)
        expected='WCH upload diagnostic requires the baseline one-empty pre-arm policy'
    fi
    if gcc -E "${flags[@]}" "${includes[@]}" "${dependencies[@]}" "$driver" -o /dev/null 2> "$testdir/sdk$sdk-guard-$guard.log"; then
        printf 'FAIL: compile-time guard accepted %s.\n' "$guard" >&2;exit 1
    fi
    python - "$testdir/sdk$sdk-guard-$guard.log" "$expected" <<'PY'
from pathlib import Path
import sys
assert sys.argv[2] in Path(sys.argv[1]).read_text()
print(f'PASS: intended compile guard rejects {sys.argv[2]}.')
PY
done
