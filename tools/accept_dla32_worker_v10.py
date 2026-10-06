"""Freeze the native-buffer upload experiment after offline validation."""
from datetime import datetime,timezone
import json
import re
from pathlib import Path
from accept_dla32_worker_v8 import ROOT,sha


def main():
    package=ROOT/'artifacts/dla32-wch-worker-v10-native1m';proof=package/'offline-validation.json'
    if proof.exists():raise RuntimeError('Fresh native acceptance required')
    logs=[ROOT/f'logs/dla32-worker-v10-{kind}.log' for kind in ('build','general-checks','unit','native-response')]
    if not re.search(r'CC\s+src/hardware/fnirsi-dla32/api.lo',logs[0].read_text()) or '100%: Checks: 92, Failures: 0, Errors: 0' not in logs[1].read_text():
        raise RuntimeError('Native object/general gates failed')
    if 'Audit: 234 checks, 0 pending behavioral failures' not in logs[2].read_text() or 'Upload worker audit: 24 checks, 0 failures.' not in logs[2].read_text() or re.search(r'^FAIL:',logs[2].read_text(),re.M):
        raise RuntimeError('Worker/upload gates failed')
    if 'Native response audit: 10 checks, 0 failures.' not in logs[3].read_text() or re.search(r'^FAIL:',logs[3].read_text(),re.M):
        raise RuntimeError('Native response gates failed')
    for label,message in (('bad-native','DLA_WCH_NATIVE_READ must be 0 or 1'),
        ('missing-upload','Native sample reads require the opt-in Win32 upload worker')):
        path=ROOT/f'logs/dla32-worker-v10-guard-{label}.log'
        if message not in path.read_text():raise RuntimeError('Native compile guard failed')
        logs.append(path)
    source=ROOT/'artifacts/dla32-wch-worker-v10-source/libsigrok'
    if (ROOT/'tools/dla32_native_sample_read.h').read_text() not in (source/'src/hardware/fnirsi-dla32/transport-wch.h').read_text():
        raise RuntimeError('Tested native backend differs from built source')
    baseline=json.loads((ROOT/'artifacts/dla32-wch-worker-v9-upload1m/offline-validation.json').read_text())
    anchors={p:h for p,h in baseline['files_sha256'].items() if any(x in p for x in (
        'test_dla32_worker_v8_continuous','dla32_lossless_logic_sink','continuous-self-test'))}
    if not anchors or any(sha(Path(p))!=h for p,h in anchors.items()):raise RuntimeError('Frozen continuous harness changed')
    files=logs+[p for p in source.rglob('*') if p.is_file()]
    files += [p for p in (ROOT/'artifacts/dla32-wch-worker-v10-tests').iterdir() if p.is_file()]
    files += [ROOT/'tools'/name for name in ('prepare_dla32_worker_v10.py','dla32_native_sample_read.h',
        'build_dla32_worker_v10.sh','test_dla32_native_response.c','test_dla32_native_response.sh','accept_dla32_worker_v10.py')]
    files += [p for p in package.iterdir() if p.is_file()]
    anchors.update({str(p.resolve()):sha(p) for p in files})
    record={'passed':True,'hardware_accessed':False,'created_utc':datetime.now(timezone.utc).isoformat(),
        'variant':'native1m','sample_read_bytes':1048576,'sample_buffer_slots':16,'ring_bytes':16777216,
        'SDK_upload_enabled':True,'SDK_upload_transfer_bytes':1048576,'native_sample_read':True,
        'SDK_owned_native_handle':True,'driver_binding_changed':False,'SDK_retirement_before_STOP':True,
        'general_checks':92,'lifecycle_checks':234,'upload_checks':24,'native_response_checks':10,
        'core_dll_sha256':sha(package/'libsigrok-4.dll'),'files_sha256':anchors,
        'discarded_kernel_tail_byte_count':'unknown','physical_integrity_verified':False,
        'lossless_stream_verified':False,'main_runtime_promoted':False}
    with proof.open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps({k:record[k] for k in ('passed','general_checks','lifecycle_checks','upload_checks','native_response_checks','core_dll_sha256')}))


if __name__=='__main__':main()
