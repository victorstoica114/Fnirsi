"""Guarded 12 s WinUSB capture; compressed originals remain separate from analysis."""
from datetime import datetime,timezone
import argparse
import json
import os
from pathlib import Path
import re
import run_dla32_winusb_v11 as finite

ROOT=finite.ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--block',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.block):parser.error('Simple fresh block required')
    directory=finite.BASE/args.block
    if directory.exists():parser.error('Fresh output required')
    g=finite.inherited.inherited.v6.guards()
    with g.exclusive_runner():
        protected,old=finite.inherited.protected_v7.core_preflight(g)
        proof=finite.PACKAGE/'offline-validation.json';core=json.loads(proof.read_text())
        if core['passed'] is not True or core['hardware_accessed'] is not False:raise RuntimeError('V11 offline acceptance failed')
        protected.update(g.verified_files({Path(p):h for p,h in core['files_sha256'].items()}))
        protected[str(proof)]=g.sha(proof);protected[str(Path(__file__))]=g.sha(Path(__file__))
        finite.inherited.inherited.v6.no_active_dla_etw();node=finite.connection()
        env=dict(os.environ);env['PATH']=str(finite.PACKAGE)+os.pathsep+env.get('PATH','')
        env['DLA32_WINUSB_CONN']=f"{node['bus']}.{node['address']}"
        for key in ('FNIRSI_DLA32_WIRE_TRACE','FNIRSI_WCH_DLL'):env.pop(key,None)
        command=[str(ROOT/'artifacts/test_dla32_winusb_continuous_r2.exe'),str(ROOT),str(directory),core['core_dll_sha256'],'50000000']
        finite.BASE.mkdir(parents=True,exist_ok=True)
        report={'started_utc':datetime.now(timezone.utc).isoformat(),'command':command,'device':node,
            'core_dll_sha256':core['core_dll_sha256'],'protected_files':protected,'hardware_accessed':True,
            'RAW_saved':False,'source_outputs_stopped':False,'sample_integrity_verified':False}
        g.write_json(finite.BASE/(args.block+'.start.json'),report)
        process,out,err=g.bounded_process(command,env,60)
        (finite.BASE/(args.block+'.stdout.log')).write_bytes(out);(finite.BASE/(args.block+'.stderr.log')).write_bytes(err)
        report.update(process);issues=[]
        if (directory/'capture.json').exists():
            m=json.loads((directory/'capture.json').read_text());report['capture']=m
            expected={'harness':'WinUSB-continuous-12s-zstd','samplerate_hz':50000000,'headers':1,'ends':1,
                'stopped_callbacks':1,'run_status':0,'destroy_status':0,'close_status':0,'exit_status':0,
                'logic_after_stop':0,'context_mismatches':0,'invalid_packet':0,'guard_expired':0,
                'lifecycle_passed':True,'LOGIC_encoding':'zstd','RAW_saved':False}
            if any(m.get(k)!=v for k,v in expected.items()):issues.append('Continuous lifecycle/count contract failed')
            if m['LOGIC_uncompressed_bytes']!=m['actual_samples']*4 or (directory/'capture.logic.zst').stat().st_size!=m['LOGIC_saved_bytes']:issues.append('Compressed LOGIC byte accounting failed')
            report['nominal_LOGIC_MB_per_run_second']=m['LOGIC_uncompressed_bytes']/m['run_duration_us']
            report['compressed_LOGIC_sha256']=g.sha(directory/'capture.logic.zst')
        else:issues.append('Missing capture metadata')
        report['issues']=issues;report['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        report['passed']=process['passed'] and not issues and not report['protected_files_changed']
        g.write_json(finite.BASE/(args.block+'.result.json'),report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('command','protected_files')}))
        return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
