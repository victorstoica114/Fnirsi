"""Guarded 12 s all-32-channel continuous probe with lossless LOGIC storage."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import run_dla32_worker_v8_benchmark as benchmark

ROOT=benchmark.ROOT
EXE=ROOT/'artifacts/test_dla32_worker_v8_continuous.exe'


def evaluate(directory,core,sha):
    m=json.loads((directory/'capture.json').read_text())
    text=(directory/'capture.driver.log').read_text()
    checks={'harness':'V8-continuous-12s-zstd','LOGIC_encoding':'zstd','RAW_saved':False,
        'continuous':True,'mode':'Stream','host_timer_ms':12000,'headers':1,'ends':1,'stopped_callbacks':1,
        'run_status':0,'destroy_status':0,'close_status':0,'exit_status':0,'logic_after_stop':0,
        'context_mismatches':0,'invalid_packet':0,'guard_expired':0,'stop_reason':'cancel-timer',
        'lifecycle_passed':True,'timer_resolution_changed':False}
    issues=[]
    if any(m.get(k)!=v for k,v in checks.items()):
        issues.append('Continuous lifecycle/source contract failed')
    if m['actual_samples']<=0 or not 12000000<=m['run_duration_us']<=16000000:
        issues.append('Capture duration or data progress failed')
    if m['LOGIC_uncompressed_bytes']!=m['actual_samples']*4 or not re.fullmatch('[0-9a-f]{64}',m['LOGIC_uncompressed_sha256']):
        issues.append('Compressed original sample count/hash failed')
    compressed=directory/'capture.logic.zst'
    if compressed.stat().st_size!=m['LOGIC_saved_bytes']:
        issues.append('Compressed file count differs')
    measurement=benchmark.evaluate(text,core,m['actual_samples'])
    measurement['issues']=[x for x in measurement['issues'] if x not in ('LOGIC count/unitsize mismatch','HEADER/END mismatch')]
    terminal=measurement['terminal']
    if terminal and measurement['SDK_returned_bytes']!=m['LOGIC_uncompressed_bytes']+int(terminal[0][11]):
        issues.append('SDK returned bytes are not conserved as LOGIC plus unconsumed')
    if 'WIRE trace enabled:' in text:
        issues.append('Unexpected RAW disk writes')
    issues+=measurement['issues']; measurement['passed']=not measurement['issues']
    return {'passed':not issues,'issues':issues,'capture':m,'measurement':measurement,
        'compressed_LOGIC_sha256':sha(compressed),'physical_integrity_verified':False,
        'lossless_stream_verified':False,'scope':'All original LOGIC bytes saved; protocol/physical integrity evaluated separately'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant',choices=benchmark.VARIANTS,required=True)
    parser.add_argument('--block',required=True)
    parser.add_argument('--rate',type=int,choices=(25000000,50000000),default=50000000)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.block): parser.error('Simple fresh block required')
    directory=benchmark.BASE/args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT): parser.error('Fresh workspace output required')
    g=benchmark.inherited.v6.guards()
    with g.exclusive_runner():
        protected,core,package=benchmark.preflight(g,args.variant)
        protected[str(Path(__file__))]=g.sha(Path(__file__))
        benchmark.inherited.v6.no_active_dla_etw()
        env=dict(os.environ);env['PATH']=str(package)+os.pathsep+env.get('PATH','')
        for key in ('FNIRSI_DLA32_WIRE_TRACE','FNIRSI_WCH_DLL'): env.pop(key,None)
        # DLL SHA is also checked by the native harness before any scan/open.
        command=[str(EXE),str(ROOT),str(directory),core['core_dll_sha256'],str(args.rate)]
        benchmark.BASE.mkdir(parents=True,exist_ok=True)
        report={'variant':args.variant,'started_utc':datetime.now(timezone.utc).isoformat(),
            'command':command,'core_dll_sha256':core['core_dll_sha256'],'protected_files':protected,
            'hardware_accessed':True,'source_outputs_stopped':False,'RAW_saved':False,'sample_integrity_verified':False}
        g.write_json(benchmark.BASE/(args.block+'.start.json'),report)
        result,out,err=g.bounded_process(command,env,60)
        (benchmark.BASE/(args.block+'.stdout.log')).write_bytes(out)
        (benchmark.BASE/(args.block+'.stderr.log')).write_bytes(err)
        report.update(result)
        if (directory/'capture.json').is_file(): report['verification']=evaluate(directory,core,g.sha)
        else: report['verification']={'passed':False,'issues':['No capture metadata']}
        report['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        try:report['ETW_postflight']=benchmark.inherited.v6.no_active_dla_etw()
        except Exception as exc: report['ETW_postflight']={'failed':repr(exc)}
        report['passed']=result['passed'] and report['verification']['passed'] and not report['protected_files_changed'] and 'failed' not in report['ETW_postflight']
        report['ended_utc']=datetime.now(timezone.utc).isoformat()
        g.write_json(benchmark.BASE/(args.block+'.result.json'),report)
        print(json.dumps({'passed':report['passed'],'variant':args.variant,'verification':report['verification']}))
        return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
