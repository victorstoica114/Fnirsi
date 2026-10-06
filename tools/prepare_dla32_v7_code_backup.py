"""Copy explicit V7 development sources into an isolated backup worktree.

Private journals, captures, keys, binaries and vendor correspondence are never
read or copied. Existing encrypted notes are left unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worktree',type=Path)
    args=parser.parse_args()
    target=args.worktree.resolve(strict=True)
    if not target.is_relative_to(ROOT/'backups') or not (target/'.git').is_file():
        raise RuntimeError('Existing isolated backup worktree required')
    manifest=target/'backup/v7-driver-source-update.sha256.json'
    if manifest.exists():
        raise RuntimeError('Fresh backup manifest required')
    # Every matching file is source code in our dedicated tools directory.
    names=[p.relative_to(ROOT).as_posix() for p in (ROOT/'tools').glob('*v7*')
           if p.is_file() and p.suffix in ('.py','.sh','.c')]
    names += ['artifacts/dla32-wch-worker-v7-r2-overlay/'+name for name in
              ('api.c','worker-wch.h','transport-wch.h','manifest.json')]
    names += [p.relative_to(ROOT).as_posix() for p in
              (ROOT/'artifacts/dla32-wch-worker-v7-r2-tests').glob('*.c')]
    records=[]
    for name in sorted(set(names)):
        source=ROOT/name; dest=target/name
        if source.is_symlink() or dest.is_symlink():
            raise RuntimeError('Symlink source/destination rejected')
        data=source.read_bytes(); original=hashlib.sha256(data).hexdigest()
        portable=name.startswith('artifacts/dla32-wch-worker-v7-r2-tests/')
        if portable:
            prefix=b'D:/Documente/analizor logic/artifacts/dla32-wch-worker-v7-r2-source/libsigrok/src/hardware/fnirsi-dla32/'
            if prefix not in data: raise RuntimeError('Fixture include anchor missing')
            data=data.replace(prefix,b'../dla32-wch-worker-v7-r2-source/libsigrok/src/hardware/fnirsi-dla32/')
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data)
        records.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                        'original_source_sha256':original,'fixture_include_made_relative':portable})
    source=ROOT/'V7_VALIDATION_STATUS_PUBLIC.md'; dest=target/'V7_VALIDATION_STATUS.md'
    dest.write_bytes(source.read_bytes())
    records.append({'path':'V7_VALIDATION_STATUS.md','bytes':dest.stat().st_size,
                    'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
    with manifest.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump({'source_files':records,'captures_private_notes_vendor_materials_copied':False,
                   'existing_encrypted_notes_archive_changed':False},stream,indent=2); stream.write('\n')
    print(json.dumps({'files':len(records),'bytes':sum(r['bytes'] for r in records),
                      'private_materials_copied':False,'committed':False,'pushed':False}))


if __name__=='__main__':
    main()
