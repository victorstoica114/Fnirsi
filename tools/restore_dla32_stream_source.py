"""Verify and restore one transport source profile from the archived V5 base."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from prepare_dla32_stream_overlays import ROOT,BASE,OUT,tree


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',required=True,choices=('v8','v9','v10','v11'))
    parser.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args();destination=args.destination.resolve()
    if destination.exists() or not destination.is_relative_to(ROOT) or destination==ROOT:parser.error('Fresh workspace destination required')
    folder=OUT/args.profile;manifest=json.loads((folder/'manifest.json').read_text())
    if tree(BASE)!=manifest['base_files']:raise RuntimeError('Archived base changed')
    for name,value in manifest['overlay_files'].items():
        path=folder/name
        if Path(name).name!=name or path.stat().st_size!=value['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=value['sha256']:raise RuntimeError('Overlay changed')
    shutil.copytree(BASE,destination)
    for name in manifest['overlay_files']:shutil.copyfile(folder/name,destination/'src/hardware/fnirsi-dla32'/name)
    restored=tree(destination)
    if restored!=manifest['expected_source_files']:raise RuntimeError('Restored source differs')
    identity=hashlib.sha256(json.dumps(restored,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if identity!=manifest['source_tree_identity']:raise RuntimeError('Source identity differs')
    print(json.dumps({'profile':args.profile,'restored':str(destination),'verified_source_files':len(restored),
        'source_tree_identity':identity,'hardware_accessed':False}))


if __name__=='__main__':main()
