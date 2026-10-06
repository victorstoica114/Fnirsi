"""Freeze exact tested SDK0 variants. No hardware or source changes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SPECS = {'1m2':(1048576,2),'2m2':(2097152,2),'4m2':(4194304,2),
         '4m16':(4194304,16)}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    shared = list((ROOT/'artifacts/dla32-wch-worker-v8-source/libsigrok').rglob('*'))
    shared = [p for p in shared if p.is_file()]
    shared += list((ROOT/'artifacts/dla32-wch-worker-v8-tests').glob('*.c'))
    shared += [ROOT/'tools'/name for name in ('prepare_dla32_worker_v8.py','build_dla32_worker_v8.sh',
                                             'accept_dla32_worker_v8.py')]
    extra = {
        'ring-2':('logs/dla32-worker-v8-ring-2.log','Ring audit: 13 checks, 0 failures'),
        'ring-16':('logs/dla32-worker-v8-ring-16.log','Ring audit: 27 checks, 0 failures'),
        'compressed-harness':('logs/dla32-worker-v8-continuous-self-test.log','42 checks, 0 failures'),
    }
    for label,(path,pattern) in extra.items():
        path=ROOT/path
        if pattern not in path.read_text() or re.search(r'^FAIL:',path.read_text(),re.M):
            raise RuntimeError('Additional offline check failed: '+label)
        shared.append(path)
    if 'Lossless LOGIC sink: PASS byte-exact split-write round trip.' not in (ROOT/extra['compressed-harness'][0]).read_text():
        raise RuntimeError('Compressed capture round trip failed')
    shared += [ROOT/'tools'/name for name in ('test_dla32_worker_v8_ring.c','build_dla32_worker_v8_extra_tests.sh',
        'dla32_lossless_logic_sink.h','prepare_dla32_worker_v8_continuous.py','test_dla32_worker_v8_continuous.c')]
    shared += [ROOT/'artifacts/test_dla32_worker_v8_continuous.exe']
    shared += [ROOT/f'artifacts/dla32-wch-worker-v8-tests/ring-{n}.exe' for n in (2,16)]
    for label,(size,slots) in SPECS.items():
        package = ROOT/f'artifacts/dla32-wch-worker-v8-{label}'
        proof = package/'offline-validation.json'
        if proof.exists():
            raise RuntimeError('Fresh acceptance required')
        logs = [ROOT/f'logs/dla32-wch-worker-v8-{label}-{kind}.log' for kind in ('build','general-checks','unit')]
        expected = 206 + slots - 2
        if not re.search(r'100%: Checks: 92, Failures: 0, Errors: 0',logs[1].read_text()):
            raise RuntimeError('General checks failed: ' + label)
        if f'Audit: {expected} checks, 0 pending behavioral failures' not in logs[2].read_text() or re.search(r'^FAIL:',logs[2].read_text(),re.M):
            raise RuntimeError('Lifecycle fixture failed: ' + label)
        # A real driver compilation is required for every variant; no stale DLL.
        if not re.search(r'CC\s+src/hardware/fnirsi-dla32/api.lo',logs[0].read_text()):
            raise RuntimeError('Driver object was not rebuilt: ' + label)
        files = shared + logs + [ROOT/f'artifacts/dla32-wch-worker-v8-tests/{label}.exe']
        files += [p for p in package.iterdir() if p.is_file()]
        record = {'passed':True,'hardware_accessed':False,'created_utc':datetime.now(timezone.utc).isoformat(),
            'variant':label,'sample_read_bytes':size,'sample_buffer_slots':slots,'ring_bytes':size*slots,
            'command_drain_read_bytes':1048576,'general_checks':92,'lifecycle_checks':expected,
            'core_dll_sha256':sha(package/'libsigrok-4.dll'),
            'files_sha256':{str(p.resolve()):sha(p) for p in sorted(set(files))},
            'lossless_stream_verified':False,'main_runtime_promoted':False}
        with proof.open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(record,stream,indent=2); stream.write('\n')
        print(json.dumps({k:record[k] for k in ('variant','passed','sample_read_bytes','sample_buffer_slots','lifecycle_checks','core_dll_sha256')}))


if __name__ == '__main__':
    main()
