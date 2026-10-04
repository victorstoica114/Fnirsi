"""Validate saved srzip metadata and D0 periods independently of our driver."""
import argparse
import configparser
from collections import Counter
import json
from pathlib import Path
import zipfile

parser=argparse.ArgumentParser()
parser.add_argument('capture',type=Path)
parser.add_argument('--expect-samples',type=int,default=50000)
parser.add_argument('--expect-rate',type=int,default=50000000)
parser.add_argument('--expect-period',type=int,default=500)
args=parser.parse_args()
with zipfile.ZipFile(args.capture) as archive:
    metadata=configparser.ConfigParser()
    metadata.read_string(archive.read('metadata').decode())
    device=metadata['device 1']
    unitsize=int(device['unitsize'])
    probes=int(device['total probes'])
    chunks=sorted((name for name in archive.namelist() if name.startswith('logic-1-')),
                  key=lambda name:int(name.rsplit('-',1)[1]))
    data=b''.join(archive.read(name) for name in chunks)
    samplerate=device['samplerate']
levels=[byte&1 for byte in data[::unitsize]]
rising=[i for i in range(1,len(levels)) if levels[i] and not levels[i-1]]
periods=Counter(b-a for a,b in zip(rising,rising[1:]))
edges=sum(a!=b for a,b in zip(levels,levels[1:]))
multiplier={'Hz':1,'kHz':1000,'MHz':1000000,'GHz':1000000000}
number,unit=samplerate.split()
actual_rate=int(float(number)*multiplier[unit])
result=dict(capture=str(args.capture),samples=len(levels),unitsize=unitsize,probes=probes,
    samplerate_hz=actual_rate,edges_d0=edges,rising_d0=len(rising),period_histogram=dict(periods),
    independently_calibrated=False)
result['passed']=(len(data)%unitsize==0 and unitsize==4 and probes==32 and
    len(levels)==args.expect_samples and actual_rate==args.expect_rate and
    len(rising)>8 and set(periods)=={args.expect_period})
args.capture.with_suffix('.sr.analysis.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
raise SystemExit(0 if result['passed'] else 1)
