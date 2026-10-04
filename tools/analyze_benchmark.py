"""Summarize raw WCH read timing; never infer losslessness from throughput."""
import argparse
import json
from pathlib import Path
from statistics import median

parser=argparse.ArgumentParser()
parser.add_argument('capture_json',type=Path)
args=parser.parse_args()
data=json.loads(args.capture_json.read_text())
middle=[read for read in data['reads'][1:-1] if read['ok'] and read['bytes']]
result=dict(capture_json=str(args.capture_json),middle_reads=len(middle),
    received_MB=data['received_bytes']/1e6,
    entire_acquisition_MBps=data['raw_bytes_per_second']/1e6,
    middle_USB_read_MBps=sum(read['bytes'] for read in middle)/sum(read['elapsed'] for read in middle)/1e6
        if middle else None,
    median_middle_read_ms=median(read['elapsed']*1000 for read in middle) if middle else None,
    timing_excludes_disk_and_conversion=True,signal_integrity_verified=False)
args.capture_json.with_suffix('.benchmark.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
