"""Own the hardware mutex and 60 s process watchdog for native pipeline probes."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import probe_dla32_native_pipeline as probe


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--depth',type=int,choices=(1,4,8),required=True)
    parser.add_argument('--block',required=True)
    parser.add_argument('--save-raw',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.block):parser.error('Simple fresh block required')
    directory=probe.BASE/args.block
    if directory.exists() or (probe.BASE/(args.block+'.watchdog.json')).exists():parser.error('Fresh output required')
    g=probe.inherited.inherited.v6.guards()
    with g.exclusive_runner():
        protected,core=probe.inherited.protected_v7.core_preflight(g)
        script=Path(probe.__file__);protected[str(script)]=g.sha(script)
        protected[str(Path(__file__))]=g.sha(Path(__file__))
        env=dict(os.environ);env['DLA_NATIVE_PIPELINE_CHILD_SHA']=g.sha(script)
        command=[sys.executable,'-B',str(script),'--block',args.block,'--depth',str(args.depth)]
        if args.save_raw:command.append('--save-raw')
        probe.BASE.mkdir(parents=True,exist_ok=True)
        process,out,err=g.bounded_process(command,env,60)
        (probe.BASE/(args.block+'.stdout.log')).write_bytes(out)
        (probe.BASE/(args.block+'.stderr.log')).write_bytes(err)
        report={'process':process,'protected_files':protected,
            'protected_files_changed':[p for p,h in protected.items() if g.sha(Path(p))!=h],
            'hardware_accessed':True,'source_outputs_stopped':False}
        report['passed']=process['passed'] and not report['protected_files_changed']
        if (directory/'result.json').is_file():
            result=json.loads((directory/'result.json').read_text());report['passed']&=result['passed']
            report['measurement']={k:v for k,v in result.items() if k not in ('native_reads','events')}
        else:report['passed']=False
        g.write_json(probe.BASE/(args.block+'.watchdog.json'),report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('protected_files','protected_files_changed')}))
        return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
