"""Accept the 1 MiB kernel-upload variant from the same unchanged V9 source."""
from datetime import datetime,timezone
import json
import re
from accept_dla32_worker_v8 import ROOT,sha
from pathlib import Path


def main():
    package=ROOT/'artifacts/dla32-wch-worker-v9-upload1m';proof=package/'offline-validation.json'
    if proof.exists():raise RuntimeError('Fresh upload1m acceptance required')
    baseline=json.loads((ROOT/'artifacts/dla32-wch-worker-v9-upload4m/offline-validation.json').read_text())
    anchors={p:h for p,h in baseline['files_sha256'].items() if 'v9-upload4m' not in p}
    if any(sha(Path(p))!=h for p,h in anchors.items()):raise RuntimeError('Frozen V9 inputs changed')
    logs=[ROOT/f'logs/dla32-worker-v9-upload1m-{kind}.log' for kind in ('build','general-checks','unit')]
    if not re.search(r'CC\s+src/hardware/fnirsi-dla32/api.lo',logs[0].read_text()) or '100%: Checks: 92, Failures: 0, Errors: 0' not in logs[1].read_text():
        raise RuntimeError('Native/general checks failed')
    text=logs[2].read_text()
    if 'Audit: 234 checks, 0 pending behavioral failures' not in text or 'Upload worker audit: 24 checks, 0 failures.' not in text or re.search(r'^FAIL:',text,re.M):
        raise RuntimeError('Upload worker checks failed')
    files=logs+[ROOT/'artifacts/dla32-wch-worker-v9-tests/upload1m-unit.exe']
    files += [ROOT/'tools'/name for name in ('build_dla32_worker_v9_upload1m.sh','accept_dla32_worker_v9_upload1m.py')]
    files += [p for p in package.iterdir() if p.is_file()]
    anchors.update({str(p.resolve()):sha(p) for p in files})
    record=dict(baseline);record.update(variant='upload1m',SDK_upload_transfer_bytes=1048576,
        created_utc=datetime.now(timezone.utc).isoformat(),core_dll_sha256=sha(package/'libsigrok-4.dll'),files_sha256=anchors)
    with proof.open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps({'passed':True,'variant':'upload1m','core_dll_sha256':record['core_dll_sha256']}))


if __name__=='__main__':main()
