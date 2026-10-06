"""Freeze offline evidence and assemble a separate V7 SDK0 CLI runtime."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'artifacts/dla32-wch-worker-v7-r2-source/libsigrok'
PACKAGE = ROOT / 'artifacts/dla32-wch-worker-v7-r2-sdk0'
MSYS = Path((ROOT/'tools/toolchain-path.txt').read_text().strip())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    if PACKAGE.exists():
        raise RuntimeError('Fresh V7 runtime required')
    cases = {
        'general-checks':r'100%: Checks: 92, Failures: 0, Errors: 0',
        'final-r2':r'Audit: 206 checks, 0 pending behavioral failures',
        'default0-r2':r'Audit: 551 checks, 0 pending behavioral failures',
        'explicit0-r2':r'Audit: 551 checks, 0 pending behavioral failures',
        'sdk1-worker0':r'Audit: 598 checks, 0 pending behavioral failures',
        'timeout-worker1':r'Timeout audit: 47 checks, 0 failures',
        'harness-self-test':r'42 checks, 0 failures',
    }
    files, checks = [], []
    for label,pattern in cases.items():
        path = ROOT/f'logs/dla32-wch-worker-v7-r2-{label}.log'
        if not re.search(pattern,path.read_text()) or re.search(r'^FAIL:',path.read_text(),re.M):
            raise RuntimeError('Offline checks failed: '+label)
        files.append(path); checks.append({'name':label,'passed':True})
    guarddir = ROOT/'artifacts/dla32-wch-worker-v7-r2-tests/compile-guards-r2'
    for label,message in (('worker2','DLA_WCH_WORKER must be 0 or 1'),
                          ('sdk1','isolated WCH worker supports Win32 SDK0 only'),
                          ('nonwindows','isolated WCH worker supports Win32 SDK0 only')):
        path = guarddir/(label+'.log')
        if message not in path.read_text():
            raise RuntimeError('Compile guard missing: '+label)
        files.append(path);checks.append({'name':'compile-guard-'+label,'passed':True})
    PACKAGE.mkdir()
    old = ROOT/'artifacts/dla32-wch-worker-v6-sdk0'
    for path in old.iterdir():
        if path.suffix.lower() in ('.dll','.exe') and path.name != 'libsigrok-4.dll':
            shutil.copyfile(path,PACKAGE/path.name)
    dll = MSYS/'tmp/dla32-wch-worker-v7-r2-prefix/bin/libsigrok-4.dll'
    shutil.copyfile(dll,PACKAGE/dll.name)
    (PACKAGE/'README.md').write_text('''# Isolated DLA-32 V7 experiment

SDK0 only. Event-driven completion, two bounded sample buffers and a dedicated
Stop drain buffer. No channel or sample compensation. No claim of lossless
200 MB/s, firmware correctness or full hardware validation. Sources stay active.
The vendor SDK remains in its existing installation and is not redistributed.
''',encoding='utf-8',newline='\n')
    files.extend(p for p in PACKAGE.iterdir() if p.is_file())
    files.extend(p for p in SOURCE.rglob('*') if p.is_file())
    tests = ROOT/'artifacts/dla32-wch-worker-v7-r2-tests'
    files.extend(p for p in tests.rglob('*') if p.is_file())
    files.extend(ROOT/'tools'/name for name in (
        'prepare_dla32_worker_v7.py','build_dla32_wch_worker_v7.sh',
        'finalize_dla32_wch_worker_v7.sh','test_dla32_wch_worker_v7.sh',
        'test_dla32_wch_worker_v7_inherited.sh','test_dla32_stop_worker_v7_cancel.c',
        'build_dla32_stop_worker_v7_cancel.sh','run_dla32_worker_v7_capture.py',
        'run_dla32_stop_worker_v7_cancel.py','accept_dla32_worker_v7_offline.py'))
    files.append(ROOT/'artifacts/test_dla32_stop_worker_v7_cancel.exe')
    files.extend(ROOT/'tools'/name for name in (
        'prepare_dla32_worker_v7_r2_build.py','build_dla32_wch_worker_v7_r2.sh',
        'test_dla32_wch_worker_v7_r2.sh','test_dla32_wch_worker_v7_r2_inherited.sh',
        'accept_dla32_worker_v7_r2_offline.py','run_dla32_worker_v7_r2_capture.py'))
    files.append(ROOT/'artifacts/dla32-wch-worker-v7-sdk0/offline-validation.json')
    record = {'created_utc':datetime.now(timezone.utc).isoformat(),'passed':True,
        'hardware_accessed':False,'independent_agent_review_performed':False,
        'checks':checks,'core_dll_sha256':sha(PACKAGE/'libsigrok-4.dll'),
        'files_sha256':{str(p.resolve()):sha(p) for p in sorted(set(files))},
        'source_changes':['completion-event','bounded-two-buffer-FIFO','dedicated-drain-buffer',
                          'explicit-device-loss-close-reopen'],
        'physical_integrity_verified':False,'lossless_stream_verified':False,
        'main_runtime_promoted':False}
    with (PACKAGE/'offline-validation.json').open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(record,stream,indent=2); stream.write('\n')
    print(json.dumps({'passed':True,'core_dll_sha256':record['core_dll_sha256'],
                      'anchored_files':len(record['files_sha256']),'hardware_accessed':False}))


if __name__ == '__main__':
    main()
