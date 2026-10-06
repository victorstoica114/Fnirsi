"""Freeze the opt-in upload worker only after all offline gates pass."""
from datetime import datetime,timezone
import json
from pathlib import Path
import re
from accept_dla32_worker_v8 import ROOT,sha


def main():
    package=ROOT/'artifacts/dla32-wch-worker-v9-upload4m';proof=package/'offline-validation.json'
    if proof.exists():raise RuntimeError('Fresh V9 acceptance required')
    general=ROOT/'logs/dla32-worker-v9-general-checks.log'
    unit=ROOT/'logs/dla32-worker-v9-upload-unit-r3.log'
    build=ROOT/'logs/dla32-worker-v9-build.log'
    text=unit.read_text()
    if '100%: Checks: 92, Failures: 0, Errors: 0' not in general.read_text() or not re.search(r'CC\s+src/hardware/fnirsi-dla32/api.lo',build.read_text()):
        raise RuntimeError('Native/general gates failed')
    if 'Audit: 234 checks, 0 pending behavioral failures' not in text or 'Upload worker audit: 24 checks, 0 failures.' not in text or re.search(r'^FAIL:',text,re.M):
        raise RuntimeError('Worker/upload behavior gates failed')
    files=[general,unit,build]
    for label,message in (('missing-optin','isolated WCH worker supports Win32 SDK0 only'),
                          ('worker-off','Worker upload requires Win32 worker and SDK upload enabled'),
                          ('upload-off','Worker upload requires Win32 worker and SDK upload enabled'),
                          ('bad-optin','DLA_WCH_WORKER_UPLOAD must be 0 or 1')):
        path=ROOT/f'logs/dla32-worker-v9-guard-{label}.log'
        if message not in path.read_text():raise RuntimeError('Opt-in guard failed: '+label)
        files.append(path)
    baseline=json.loads((ROOT/'artifacts/dla32-wch-worker-v8-2m16/offline-validation.json').read_text())
    # Reuse only the independently checked lossless harness files.
    inherited={p:h for p,h in baseline['files_sha256'].items() if any(x in p for x in (
        'test_dla32_worker_v8_continuous','dla32_lossless_logic_sink','continuous-self-test'))}
    if not inherited or any(sha(Path(p))!=h for p,h in inherited.items()):raise RuntimeError('Lossless harness gate changed')
    files += [p for p in (ROOT/'artifacts/dla32-wch-worker-v9-source/libsigrok').rglob('*') if p.is_file()]
    files += [p for p in (ROOT/'artifacts/dla32-wch-worker-v9-tests').glob('*.c')]
    files += [ROOT/'artifacts/dla32-wch-worker-v9-tests/upload-unit-r3.exe']
    files += [ROOT/'tools'/name for name in ('prepare_dla32_worker_v9.py','build_dla32_worker_v9.sh',
        'test_dla32_worker_v9_upload.c','test_dla32_worker_v9_r3.sh','accept_dla32_worker_v9.py')]
    files += [p for p in package.iterdir() if p.is_file()]
    anchors=dict(inherited);anchors.update({str(p.resolve()):sha(p) for p in files})
    record={'passed':True,'hardware_accessed':False,'created_utc':datetime.now(timezone.utc).isoformat(),
        'variant':'upload4m','sample_read_bytes':2097152,'sample_buffer_slots':16,'ring_bytes':33554432,
        'SDK_upload_enabled':True,'SDK_upload_transfer_bytes':4194304,'SDK_retirement_before_STOP':True,
        'general_checks':92,'lifecycle_checks':234,'upload_checks':24,
        'core_dll_sha256':sha(package/'libsigrok-4.dll'),'files_sha256':anchors,
        'discarded_kernel_tail_byte_count':'unknown','physical_integrity_verified':False,
        'lossless_stream_verified':False,'main_runtime_promoted':False}
    with proof.open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps({k:record[k] for k in ('passed','general_checks','lifecycle_checks','upload_checks','core_dll_sha256')}))


if __name__=='__main__':main()
