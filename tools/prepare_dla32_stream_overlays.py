"""Create small source-only overlays for the transport experiments; no captures."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
BASE=ROOT/'artifacts/dla32-wch-stop-v5-source/libsigrok'
OUT=ROOT/'artifacts/dla32-stream-source-overlays'
PROFILES={
    'v8':'artifacts/dla32-wch-worker-v8-source/libsigrok',
    'v9':'artifacts/dla32-wch-worker-v9-source/libsigrok',
    'v10':'artifacts/dla32-wch-worker-v10-source/libsigrok',
    'v11':'artifacts/dla32-winusb-v11-source/libsigrok',
}


def tree(directory):
    return {p.relative_to(directory).as_posix():{'bytes':p.stat().st_size,
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(directory.rglob('*')) if p.is_file()}


def main():
    if OUT.exists():raise RuntimeError('Fresh overlays required')
    baseline=tree(BASE);OUT.mkdir()
    for label,relative in PROFILES.items():
        source=ROOT/relative;expected=tree(source);folder=OUT/label;folder.mkdir()
        changed={name:value for name,value in expected.items() if baseline.get(name)!=value}
        removed=sorted(set(baseline)-set(expected))
        if removed or set(changed)!= {'src/hardware/fnirsi-dla32/'+n for n in ('api.c','transport-wch.h','worker-wch.h')}:
            raise RuntimeError('Unexpected source scope: '+label)
        for name in changed:(folder/Path(name).name).write_bytes((source/name).read_bytes())
        identity=hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        record={'profile':label,'base_files':baseline,'expected_source_files':expected,'source_tree_identity':identity,
            'overlay_files':{Path(n).name:v for n,v in changed.items()},'captures_or_private_notes_included':False}
        with (folder/'manifest.json').open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
        print(json.dumps({'profile':label,'source_files':len(expected),'overlay_files':len(changed),'identity':identity}))


if __name__=='__main__':main()
