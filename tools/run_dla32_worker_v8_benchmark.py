"""Guarded finite all-channel SDK0 probe; NUL output has no integrity verdict."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import run_dla32_worker_v7_r2_capture as inherited
import run_dla32_worker_v7_r2_probes as protected_v7

ROOT = inherited.ROOT
BASE = ROOT/'captures/wch-worker-v8-2026-10-06'
VARIANTS = ('1m2','2m2','4m2','4m16','2m16')


def preflight(g, variant):
    protected, old_core = protected_v7.core_preflight(g)
    package = ROOT/f'artifacts/dla32-wch-worker-v8-{variant}'
    proof = package/'offline-validation.json'
    core = json.loads(proof.read_text())
    if core['passed'] is not True or core['hardware_accessed'] is not False or core['variant'] != variant:
        raise RuntimeError('V8 offline acceptance is missing or invalid')
    protected.update(g.verified_files({Path(p):h for p,h in core['files_sha256'].items()}))
    protected[str(proof)] = g.sha(proof)
    protected[str(Path(__file__))] = g.sha(Path(__file__))
    return protected,core,package


def evaluate(text, core, samples):
    issues = []
    packets = re.findall(r'Received SR_DF_LOGIC packet \((\d+) bytes, unitsize = (\d+)\)', text)
    if not packets or any(int(u)!=4 for _,u in packets) or sum(int(n) for n,_ in packets)!=samples*4:
        issues.append('LOGIC count/unitsize mismatch')
    if any(text.count('Received SR_DF_'+kind+' packet')!=1 for kind in ('HEADER','END')):
        issues.append('HEADER/END mismatch')
    terminal = re.findall(r'WCH WORKER terminal: generation (\d+), reads (\d+), stop_status (-?\d+), pending ([01]), stop_start_us (\d+), stop_duration_us (\d+), drain_bytes (\d+), drain_reads (\d+), empty_reads (\d+), quiet ([01]), diagnostics_failed ([01]), unconsumed_bytes (\d+); native thread exited\.',text)
    if len(terminal)!=1 or any(terminal[0][i]!=v for i,v in {2:'0',3:'0',9:'1',10:'0'}.items()) or int(terminal[0][8])<2:
        issues.append('STOP/drain/native thread termination failed')
    for phase in ('pre-arm','stop'):
        if text.count('WCH capture STOP complete: phase '+phase+'; command accepted.')!=1:
            issues.append('STOP acknowledgement missing: '+phase)
    read = re.compile(r'WCH WORKER read: generation (\d+), read_id (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+), Windows error (\d+), payload_offset (\d+), prefix32 ([0-9a-f]*)\.')
    rows = [tuple(map(int,m.groups()[:-1])) for m in read.finditer(text)]
    size = core['sample_read_bytes']; slots = core['sample_buffer_slots']
    if not rows or any(r[4]!=0 or r[5]!=size or r[6]!=1000 or not 0<=r[7]<=size for r in rows):
        issues.append('SDK read contract/status failed')
    if rows and [r[1] for r in rows]!=list(range(1,len(rows)+1)):
        issues.append('FIFO read identity mismatch')
    if rows and (len({r[0] for r in rows})!=1 or any(r[3]<0 for r in rows) or
                 any(b[2]<a[2]+a[3] for a,b in zip(rows,rows[1:]))):
        issues.append('Read generation/timeline overlap mismatch')
    if terminal and len(rows)!=int(terminal[0][1]):
        issues.append('Terminal read accounting mismatch')
    count = sum(r[7] for r in rows)
    if not samples*4<=count<=samples*4+size*slots:
        issues.append('SDK return count outside finite acquisition/prefetch bounds')
    if terminal and int(terminal[0][11])>size*slots:
        issues.append('Unconsumed result count exceeds bounded ring')
    span = rows[-1][2]+rows[-1][3]-rows[0][2] if rows else 0
    io = sum(r[3] for r in rows)
    gaps = [b[2]-(a[2]+a[3]) for a,b in zip(rows,rows[1:])]
    return {'passed':not issues,'issues':issues,'LOGIC_bytes':samples*4,
        'SDK_returned_bytes':count,'SDK_reads':len(rows),
        'first_read_to_last_return_seconds':span/1e6,'SDK_call_seconds':io/1e6,
        'path_MB_per_second':count/span if span else None,
        'SDK_call_only_MB_per_second':count/io if io else None,
        'between_read_gap_max_us':max(gaps,default=0),
        'between_read_gap_total_us':sum(gaps),'between_read_gaps_over_1ms':sum(g>1000 for g in gaps),
        'terminal':terminal,'sample_integrity_verified':False,
        'scope':'Instrumented SDK/decoder/CLI, decimal MB/s; not USB bus utilization'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant',choices=VARIANTS,required=True)
    parser.add_argument('--block',required=True)
    parser.add_argument('--rate',type=int,choices=(25000000,50000000),default=50000000)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.block):
        parser.error('Simple fresh block required')
    directory = BASE/args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):
        parser.error('Fresh workspace output required')
    g = inherited.v6.guards()
    with g.exclusive_runner():
        protected,core,package = preflight(g,args.variant)
        inherited.v6.no_active_dla_etw()
        config = inherited.v6.settings('Stream',args.rate,100000000,'all','1.6','none')
        command = inherited.v6.command(SimpleNamespace(PACKAGE=package),config,Path('NUL'))
        env = dict(os.environ); env['PATH']=str(package)+os.pathsep+env.get('PATH','')
        for key in ('FNIRSI_DLA32_WIRE_TRACE','FNIRSI_WCH_DLL'):
            env.pop(key,None)
        directory.mkdir(parents=True)
        report = {'variant':args.variant,'started_utc':datetime.now(timezone.utc).isoformat(),
            'command':command,'settings':config,'protected_files':protected,
            'core_dll_sha256':core['core_dll_sha256'],'hardware_accessed':True,
            'RAW_saved':False,'source_outputs_stopped':False,'sample_integrity_verified':False}
        g.write_json(directory/'start.json',report)
        result,out,err = g.bounded_process(command,env,60)
        (directory/'stdout.log').write_bytes(out); (directory/'stderr.log').write_bytes(err)
        text = err.decode('utf-8',errors='replace')
        report.update(result); report['measurement']=evaluate(text,core,100000000)
        if 'WIRE trace enabled:' in text:
            report['measurement']['issues'].append('Unexpected RAW trace')
            report['measurement']['passed']=False
        report['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        try: report['ETW_postflight']=inherited.v6.no_active_dla_etw()
        except Exception as exc: report['ETW_postflight']={'failed':repr(exc)}
        report['passed']=result['passed'] and report['measurement']['passed'] and not report['protected_files_changed'] and 'failed' not in report['ETW_postflight']
        report['ended_utc']=datetime.now(timezone.utc).isoformat()
        g.write_json(directory/'result.json',report)
        print(json.dumps({'passed':report['passed'],'variant':args.variant,'measurement':report['measurement'],'record':str(directory/'result.json')}))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
