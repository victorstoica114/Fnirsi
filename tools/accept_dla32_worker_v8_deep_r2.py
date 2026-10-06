"""Accept corrected deep-ring tests; retain the initial failed fixture/log."""
from datetime import datetime,timezone
import json
from pathlib import Path
import re
from accept_dla32_worker_v8 import ROOT,sha


def main():
    baseline=json.loads((ROOT/'artifacts/dla32-wch-worker-v8-1m2/offline-validation.json').read_text())
    shared={p:h for p,h in baseline['files_sha256'].items() if
            not any(part in p for part in ('v8-1m2','/1m2.exe','\\1m2.exe'))}
    if any(sha(Path(p))!=h for p,h in shared.items()):raise RuntimeError('Frozen shared acceptance changed')
    extra=[ROOT/'tools'/name for name in ('build_dla32_worker_v8_deep_r2.sh','accept_dla32_worker_v8_deep_r2.py')]
    extra.append(ROOT/'artifacts/dla32-wch-worker-v8-tests/test_dla32_wch_worker_v8_unit_deep_r2.c')
    for label,size in (('4m16',4194304),('2m16',2097152)):
        package=ROOT/f'artifacts/dla32-wch-worker-v8-{label}'
        proof=package/'offline-validation.json'
        if proof.exists():raise RuntimeError('Fresh deep acceptance required')
        logs=[ROOT/f'logs/dla32-wch-worker-v8-{label}-{suffix}.log' for suffix in ('build','general-checks','deep-r2-unit')]
        if 'Audit: 234 checks, 0 pending behavioral failures' not in logs[2].read_text() or re.search(r'^FAIL:',logs[2].read_text(),re.M):
            raise RuntimeError('Deep lifecycle checks failed: '+label)
        if '100%: Checks: 92, Failures: 0, Errors: 0' not in logs[1].read_text() or not re.search(r'CC\s+src/hardware/fnirsi-dla32/api.lo',logs[0].read_text()):
            raise RuntimeError('Native/general checks failed: '+label)
        files=extra+logs+[ROOT/f'artifacts/dla32-wch-worker-v8-tests/{label}-deep-r2.exe']
        files+=[p for p in package.iterdir() if p.is_file()]
        anchors=dict(shared);anchors.update({str(p.resolve()):sha(p) for p in files})
        record={'passed':True,'hardware_accessed':False,'created_utc':datetime.now(timezone.utc).isoformat(),
            'variant':label,'sample_read_bytes':size,'sample_buffer_slots':16,'ring_bytes':size*16,
            'command_drain_read_bytes':1048576,'general_checks':92,'lifecycle_checks':234,
            'core_dll_sha256':sha(package/'libsigrok-4.dll'),'files_sha256':anchors,
            'lossless_stream_verified':False,'main_runtime_promoted':False,
            'initial_test_failure':'Exact queue-count wait assumed two slots; corrected test waits for at least one queued result. Driver source unchanged.'}
        with proof.open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
        print(json.dumps({k:record[k] for k in ('passed','variant','lifecycle_checks','core_dll_sha256')}))


if __name__=='__main__':main()
