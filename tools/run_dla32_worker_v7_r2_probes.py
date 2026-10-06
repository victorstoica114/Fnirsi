"""Bounded V7 continuous and revised cancel probes; no source/reset commands."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import run_dla32_worker_v7_r2_capture as v7
import run_dla32_stop_worker_v7_cancel as cancel_v1

ROOT = v7.ROOT
PROOF = ROOT/'artifacts/dla32-wch-worker-v7-r2-probe-validation.json'
EXES = {'cancel':ROOT/'artifacts/test_dla32_stop_worker_v7_cancel_r2.exe',
        'continuous':ROOT/'artifacts/test_dla32_worker_v7_continuous.exe'}


def env():
    result = dict(os.environ)
    result['PATH'] = str(v7.PACKAGE)+os.pathsep+result.get('PATH','')
    for key in ('FNIRSI_WCH_DLL','FNIRSI_DLA32_WIRE_TRACE','DLA32_CANCEL_MODE'):
        result.pop(key,None)
    return result


def core_preflight(g):
    protected = g.preflight()
    core = json.loads(v7.PROOF.read_text())
    if core['passed'] is not True or core['hardware_accessed'] is not False:
        raise RuntimeError('Core offline acceptance failed')
    protected.update(g.verified_files({Path(p):h for p,h in core['files_sha256'].items()}))
    protected[str(v7.PROOF)] = g.sha(v7.PROOF)
    return protected,core


def accept(g):
    if PROOF.exists():
        raise RuntimeError('Fresh probe acceptance required')
    protected,core = core_preflight(g)
    files = list(EXES.values()) + [ROOT/'tools'/name for name in (
        'test_dla32_stop_worker_v7_cancel_r2.c','build_dla32_stop_worker_v7_cancel_r2.sh',
        'test_dla32_worker_v7_continuous.c','build_dla32_worker_v7_probes.sh',
        'run_dla32_worker_v7_r2_probes.py')]
    checks = []
    for label,path in EXES.items():
        result,out,err = g.bounded_process([str(path),'--self-test'],env(),15)
        passed = result['passed'] and '42 checks, 0 failures' in out.decode()
        checks.append({'name':label,'passed':passed,'process':result,
                       'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace')})
    # Invalid arguments must exit before DLL checks, directory creation or scan.
    path = ROOT/'captures/v7-invalid-continuous-arguments'
    if path.exists():
        raise RuntimeError('Negative-test output must be absent')
    result,out,err = g.bounded_process([str(EXES['continuous']),str(ROOT),str(path),
                                     core['core_dll_sha256'],'123'],env(),10)
    checks.append({'name':'invalid-rate-before-hardware','passed':result['exit_code']==2 and not path.exists(),
                   'process':result,'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace')})
    record = {'passed':all(c['passed'] for c in checks),'hardware_accessed':False,
        'created_utc':datetime.now(timezone.utc).isoformat(),'checks':checks,
        'core_offline_proof_sha256':g.sha(v7.PROOF),
        'files_sha256':{str(p):g.sha(p) for p in files},'core_dll_sha256':core['core_dll_sha256']}
    g.write_json(PROOF,record)
    print(json.dumps({'passed':record['passed'],'hardware_accessed':False,'checks':len(checks)}))
    return 0 if record['passed'] else 1


def verify_long(directory,rate,sha):
    metadata = json.loads((directory/'capture.json').read_text())
    issues = []
    criteria = {'harness':'V7-continuous-12s','continuous':True,'mode':'Stream',
        'samplerate_hz':rate,'host_timer_ms':12000,'headers':1,'ends':1,'stopped_callbacks':1,
        'run_status':0,'destroy_status':0,'close_status':0,'exit_status':0,
        'logic_after_stop':0,'context_mismatches':0,'invalid_packet':0,'guard_expired':0,
        'stop_reason':'cancel-timer','lifecycle_passed':True,'timer_resolution_changed':False}
    if any(metadata.get(k)!=value for k,value in criteria.items()):
        issues.append('Continuous lifecycle/close/count metadata failed')
    if metadata['actual_samples'] <= 0 or not 12000000 <= metadata['run_duration_us'] <= 16000000:
        issues.append('Continuous host duration/data progress failed')
    text = (directory/'capture.driver.log').read_text()
    terminal = re.findall(r'WCH WORKER terminal: generation (\d+), reads (\d+), stop_status (-?\d+), pending ([01]), stop_start_us (\d+), stop_duration_us (\d+), drain_bytes (\d+), drain_reads (\d+), empty_reads (\d+), quiet ([01]), diagnostics_failed ([01]), unconsumed_bytes (\d+); native thread exited\.',text)
    if len(terminal)!=1 or any(terminal[0][i]!=v for i,v in {2:'0',3:'0',9:'1',10:'0'}.items()):
        issues.append('STOP/drain/native-exit failed')
    rows = [tuple(map(int,m)) for m in re.findall(
        r'read_id (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+)',text)]
    if not rows or [r[0] for r in rows]!=list(range(1,len(rows)+1)) or any(
        r[3] not in (0,-7) or r[4]!=1048576 or r[5]!=1000 or not 0<=r[6]<=r[4] for r in rows):
        issues.append('Continuous SDK read identities/contract failed')
    files = {name:{'bytes':(directory/name).stat().st_size,'sha256':sha(directory/name)} for name in
        ('capture.wire.bin','capture.logic.bin','capture.wire.bin.worker-unconsumed.bin')}
    raw = files['capture.wire.bin']['bytes']; side=files['capture.wire.bin.worker-unconsumed.bin']['bytes']
    if files['capture.logic.bin']['bytes'] != metadata['actual_samples']*4 or raw+side!=sum(r[6] for r in rows) or side>2097152:
        issues.append('Continuous RAW/LOGIC/prefetch accounting failed')
    if terminal and (int(terminal[0][1])!=len(rows) or int(terminal[0][11])!=side):
        issues.append('Terminal result accounting differs')
    if re.findall(r'WCH WORKER unconsumed trace closed: (\d+) bytes, failed ([01]);',text)!=[(str(side),'0')]:
        issues.append('Prefetch trace failed')
    if re.findall(r'WIRE trace closed: (\d+) decoder-input bytes, tail (\d+), failed ([01])\.',text)!=[(str(raw),str(raw%32),'0')]:
        issues.append('RAW trace failed')
    if any('level=1 ' in line for line in text.splitlines()):
        issues.append('Driver error log present')
    span=rows[-1][1]+rows[-1][2]-rows[0][1] if rows else 0
    io=sum(r[2] for r in rows); total=sum(r[6] for r in rows)
    return {'passed':not issues,'issues':issues,'capture':metadata,'files':files,
        'measurement':{'SDK_returned_bytes':total,'SDK_reads':len(rows),
            'first_read_to_last_return_seconds':span/1e6,
            'path_MB_per_second':total/span if span else None,
            'SDK_call_only_MB_per_second':total/io if io else None,
            'scope':'Instrumented continuous SDK/decoder/C callback, decimal MB/s; not USB bus speed'},
        'physical_integrity_verified':False,'lossless_stream_verified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline-validation',action='store_true')
    parser.add_argument('--block')
    parser.add_argument('--kind',choices=('cancel','continuous'))
    parser.add_argument('--cancel-mode',choices=('Buffer','Stream'))
    parser.add_argument('--rate',type=int,choices=(25000000,50000000))
    args=parser.parse_args()
    g=v7.v6.guards()
    if args.offline_validation:
        return accept(g)
    if not args.block or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.block) or not args.kind or (
            args.kind=='cancel' and not args.cancel_mode) or (args.kind=='continuous' and not args.rate):
        parser.error('Fresh block, kind and its settings required')
    directory=v7.BASE/args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):
        raise RuntimeError('Fresh workspace output required')
    with g.exclusive_runner():
        protected,core=core_preflight(g)
        proof=json.loads(PROOF.read_text())
        if proof['passed'] is not True or proof['hardware_accessed'] is not False or proof['core_offline_proof_sha256']!=g.sha(v7.PROOF):
            raise RuntimeError('Probe acceptance failed')
        protected.update(g.verified_files({Path(p):h for p,h in proof['files_sha256'].items()}))
        protected[str(PROOF)]=g.sha(PROOF)
        reserve=2*12*(args.rate or 5000000)*4+268435456 if args.kind=='continuous' else 268435456
        if shutil.disk_usage(ROOT).free<reserve:
            raise RuntimeError('Insufficient space to preserve all original data')
        command=[str(EXES[args.kind]),str(ROOT),str(directory),core['core_dll_sha256']]
        if args.kind=='continuous': command.append(str(args.rate))
        environment=env()
        if args.kind=='cancel': environment['DLA32_CANCEL_MODE']=args.cancel_mode
        record={'started_utc':datetime.now(timezone.utc).isoformat(),'command':command,
            'kind':args.kind,'rate_hz':args.rate,'cancel_mode':args.cancel_mode,
            'hardware_accessed':True,'ESP32_accessed':False,'signal_generator_commands_sent':False,
            'timer_resolution_changed':False,'ETW_preflight':v7.v6.no_active_dla_etw(),
            'protected_files_sha256':protected}
        v7.BASE.mkdir(parents=True,exist_ok=True)
        g.write_json(v7.BASE/(args.block+'.start.json'),record)
        result,out,err=g.bounded_process(command,environment,60)
        record['process']=result
        (v7.BASE/(args.block+'.stdout.log')).write_bytes(out)
        (v7.BASE/(args.block+'.stderr.log')).write_bytes(err)
        try:
            record['verification']=(verify_long(directory,args.rate,g.sha) if args.kind=='continuous' else
                cancel_v1.verify(directory,args.cancel_mode,core['core_dll_sha256'],g.sha))
        except (OSError,ValueError,KeyError,IndexError) as exc:
            record['verification']={'passed':False,'issues':['Evidence validation failed: '+repr(exc)]}
        record['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        record['ETW_postflight']=v7.v6.no_active_dla_etw()
        record['passed']=result['passed'] and record['verification']['passed'] and not record['protected_files_changed']
        record['ended_utc']=datetime.now(timezone.utc).isoformat()
        g.write_json(v7.BASE/(args.block+'.result.json'),record)
        print(json.dumps({'passed':record['passed'],'issues':record['verification']['issues'],
            'measurement':record['verification'].get('measurement'),
            'stages':[{k:s[k] for k in ('capture','actual_samples','preserved_prefetch_bytes')}
                      for s in record['verification'].get('stages',[])]}))
        return 0 if record['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
