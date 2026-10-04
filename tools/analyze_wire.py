"""Inspect 32-channel bit-sliced raw captures, without changing the device."""
import argparse
import json
from pathlib import Path
from statistics import median

parser=argparse.ArgumentParser()
parser.add_argument('capture',type=Path)
parser.add_argument('--rate',type=int,default=50000000)
parser.add_argument('--channel',type=int,default=0)
args=parser.parse_args()
if not 0<=args.channel<32: parser.error('Channel must be 0..31')
data=args.capture.read_bytes()
lanes=data[args.channel::32]
edges=[]
previous=None
for frame,byte in enumerate(lanes):
    for bit in range(8):
        value=(byte>>bit)&1
        if previous is not None and value!=previous:
            edges.append((frame*8+bit,value))
        previous=value
rising=[index for index,value in edges if value]
periods=[b-a for a,b in zip(rising,rising[1:])]
high=[b[0]-a[0] for a,b in zip(edges,edges[1:]) if a[1]]
low=[b[0]-a[0] for a,b in zip(edges,edges[1:]) if not a[1]]
result=dict(file=str(args.capture),channel=args.channel,bytes=len(data),
            frames=len(data)//32,trailing_bytes=len(data)%32,
            samples=len(data)//32*8,commanded_rate=args.rate,transitions=len(edges),
            median_period_samples=median(periods) if periods else None,
            calculated_frequency=args.rate/median(periods) if periods else None,
            min_period_samples=min(periods) if periods else None,
            max_period_samples=max(periods) if periods else None,
            median_high_samples=median(high) if high else None,
            median_low_samples=median(low) if low else None,
            calibrated_timing_verified=False,
            first_edges=edges[:20])
print(json.dumps(result,indent=2))
args.capture.with_suffix('.analysis.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
