"""Bounded V9 upload probes. Native kernel-tail retirement is reported explicitly."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import run_dla32_worker_v8_benchmark as inherited
import run_dla32_worker_v8_continuous as continuous

ROOT=inherited.ROOT
PACKAGE=ROOT/'artifacts/dla32-wch-worker-v9-upload4m'
BASE=ROOT/'captures/wch-worker-v9-2026-10-06'


def check_upload(text,report,upload_bytes=4194304):
    report['issues']=[i for i in report['issues'] if i!='STOP acknowledgement missing: pre-arm']
    if text.count('WCH UPLOAD capture STOP complete: phase pre-arm; disable precedes STOP.')!=1:
        report['issues'].append('Upload pre-arm STOP missing')
    if text.count('WCH worker SDK queue retired before STOP; discarded kernel tail byte count unknown.')!=1:
        report['issues'].append('Worker SDK retirement before STOP missing')
    for phase,enable in (('pre-arm','0'),('before-arm','1'),('worker-stop','0')):
        entries=re.findall(r'WCH UPLOAD SetBufUploadEx: phase '+phase+r', index 0, enable (\d+), pipe 1, length (\d+), start_us (\d+), duration_us (\d+), BOOL (\d+), Windows error (\d+)\.',text)
        if len(entries)!=1 or entries[0][0]!=enable or entries[0][1]!=str(upload_bytes) or entries[0][4]!='1':
            report['issues'].append('SDK upload lifecycle failed: '+phase)
    clears=re.findall(r'WCH UPLOAD ClearBufUpload: phase before-arm, index 0, pipe 1, start_us (\d+), duration_us (\d+), BOOL (\d+), Windows error (\d+); erased queue byte count unknown\.',text)
    if len(clears)!=1 or clears[0][2]!='1':report['issues'].append('Pre-arm upload clear failed')
    report['passed']=not report['issues']
    report['discarded_kernel_tail_byte_count']='unknown'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('benchmark','continuous'),required=True)
    parser.add_argument('--variant',choices=('upload4m','upload1m'),default='upload4m')
    parser.add_argument('--block',required=True)
    parser.add_argument('--rate',type=int,choices=(25000000,50000000),default=50000000)
    args=parser.parse_args()
    package=ROOT/f'artifacts/dla32-wch-worker-v9-{args.variant}'
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.block):parser.error('Simple fresh block required')
    directory=BASE/args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):parser.error('Fresh workspace output required')
    g=inherited.inherited.v6.guards()
    with g.exclusive_runner():
        protected,oldcore=inherited.protected_v7.core_preflight(g)
        proof=package/'offline-validation.json';core=json.loads(proof.read_text())
        if core['passed'] is not True or core['hardware_accessed'] is not False or core['SDK_upload_enabled'] is not True:
            raise RuntimeError('V9 offline acceptance failed')
        protected.update(g.verified_files({Path(p):h for p,h in core['files_sha256'].items()}))
        protected[str(proof)]=g.sha(proof);protected[str(Path(__file__))]=g.sha(Path(__file__))
        inherited.inherited.v6.no_active_dla_etw()
        BASE.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ);env['PATH']=str(package)+os.pathsep+env.get('PATH','')
        for key in ('FNIRSI_DLA32_WIRE_TRACE','FNIRSI_WCH_DLL'):env.pop(key,None)
        if args.kind=='benchmark':
            config=inherited.inherited.v6.settings('Stream',args.rate,100000000,'all','1.6','none')
            command=inherited.inherited.v6.command(SimpleNamespace(PACKAGE=package),config,Path('NUL'))
        else:
            command=[str(continuous.EXE),str(ROOT),str(directory),core['core_dll_sha256'],str(args.rate)]
            config={'mode':'Stream','samplerate_hz':args.rate,'continuous':True,'host_timer_ms':12000,'channels':'all32','trigger':'D0 rising'}
        report={'kind':args.kind,'started_utc':datetime.now(timezone.utc).isoformat(),'command':command,
            'settings':config,'core_dll_sha256':core['core_dll_sha256'],'protected_files':protected,
            'hardware_accessed':True,'source_outputs_stopped':False,'RAW_saved':False,'sample_integrity_verified':False}
        g.write_json(BASE/(args.block+'.start.json'),report)
        result,out,err=g.bounded_process(command,env,60)
        (BASE/(args.block+'.stdout.log')).write_bytes(out);(BASE/(args.block+'.stderr.log')).write_bytes(err)
        report.update(result)
        if args.kind=='benchmark':
            directory.mkdir();text=err.decode('utf-8',errors='replace')
            report['verification']=check_upload(text,inherited.evaluate(text,core,100000000),core['SDK_upload_transfer_bytes'])
        elif (directory/'capture.json').exists():
            text=(directory/'capture.driver.log').read_text()
            verification=continuous.evaluate(directory,core,g.sha)
            verification['measurement']=check_upload(text,verification['measurement'],core['SDK_upload_transfer_bytes'])
            verification['issues']=[i for i in verification['issues'] if i!='STOP acknowledgement missing: pre-arm']
            verification['issues']+=verification['measurement']['issues']
            verification['passed']=not verification['issues']
            report['verification']=verification
        else:report['verification']={'passed':False,'issues':['No capture metadata']}
        report['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        try:report['ETW_postflight']=inherited.inherited.v6.no_active_dla_etw()
        except Exception as exc:report['ETW_postflight']={'failed':repr(exc)}
        report['passed']=result['passed'] and report['verification']['passed'] and not report['protected_files_changed'] and 'failed' not in report['ETW_postflight']
        report['ended_utc']=datetime.now(timezone.utc).isoformat()
        g.write_json(BASE/(args.block+'.result.json'),report)
        print(json.dumps({'passed':report['passed'],'kind':args.kind,'verification':report['verification']}))
        return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
