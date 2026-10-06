"""Back up explicitly selected driver-development code and English status only."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('worktree',type=Path)
    args=parser.parse_args();target=args.worktree.resolve(strict=True)
    if not target.is_relative_to(ROOT/'backups') or not (target/'.git').is_file():raise RuntimeError('Isolated backup worktree required')
    manifest=target/'backup/stream-transport-source-update.sha256.json'
    if manifest.exists():raise RuntimeError('Fresh backup manifest required')
    files=set()
    for pattern in ('*v8*','*v9*','*v10*','*winusb*','*native*'):
        files.update(p for p in (ROOT/'tools').glob(pattern) if p.is_file() and p.suffix in ('.py','.sh','.c','.h'))
    files.update(ROOT/'tools'/name for name in ('prepare_dla32_stream_overlays.py','restore_dla32_stream_source.py',
        'prepare_dla32_stream_code_backup.py','dla32_lossless_logic_sink.h','project_dla32_lossless_logic.py',
        'decode_dla32_wire_offline.c','dla32_usb_descriptors.c','validate_dla32_stream_backup.sh',
        'dla32_driver_binding.c','build_dla32_driver_binding.sh','install_existing_dla32_driver.ps1',
        'temporary_dla32_winusb.ps1'))
    for directory in ('dla32-wch-worker-v8-tests','dla32-wch-worker-v9-tests','dla32-wch-worker-v10-tests','dla32-winusb-v11-tests'):
        files.update((ROOT/'artifacts'/directory).glob('*.c'))
    files.update(p for p in (ROOT/'artifacts/dla32-stream-source-overlays').rglob('*') if p.is_file())
    records=[]
    for source in sorted(files):
        if source.is_symlink():raise RuntimeError('Symlink source rejected')
        relative=source.relative_to(ROOT).as_posix();dest=target/relative
        data=source.read_bytes();original=hashlib.sha256(data).hexdigest()
        portable=relative.startswith('artifacts/') and relative.endswith('.c')
        if portable:data=data.replace(b'D:/Documente/analizor logic/artifacts/',b'../')
        if dest.is_symlink():raise RuntimeError('Symlink destination rejected')
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        records.append({'path':relative,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'original_source_sha256':original,'fixture_include_made_relative':portable})
    status=ROOT/'STREAM_TRANSPORT_STATUS_PUBLIC.md';dest=target/'STREAM_TRANSPORT_STATUS.md'
    dest.write_bytes(status.read_bytes())
    records.append({'path':dest.relative_to(target).as_posix(),'bytes':dest.stat().st_size,
        'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
    readme=target/'README.md';text=readme.read_text()
    text=text.replace('Current development backup:', 'Previous development backup:',1)
    text=text.replace('# FNIRSI DLA-32 Plus in PulseView\n',
        '# FNIRSI DLA-32 Plus in PulseView\n\nCurrent investigation: [Stream transport V8–V11](STREAM_TRANSPORT_STATUS.md). The WCH paths tested remain below 200 MB/s; the corrected WinUSB command path is prepared and awaits hardware validation. This branch preserves experiments and software tests.\n',1)
    readme.write_text(text,newline='\n')
    build=target/'BUILD_AND_RESTORE.md'
    with build.open('a',encoding='utf-8',newline='\n') as stream:
        stream.write('''

## Stream transport profiles V8–V11

Restore the archived V5 base first using the existing instructions, then choose
the required overlay. The destination must be new and inside the repository:

```powershell
python -B tools/restore_dla32_stream_source.py --profile v11 --destination artifacts/dla32-winusb-v11-source/libsigrok
```

The restorer verifies every base file, every overlay byte and the full 490-file
result. V8 is SDK0; V9 is opt-in kernel upload; V10 adds native sample reads;
V11 keeps the V7 r2 WCH lifecycle and corrects libusb command/model endpoints.
These are separate experiments, not a stable release. Source fixtures have
relative includes; workstation build/runners still require their documented
MSYS2 paths, installed SDK and newly generated acceptance evidence. Hardware
runners do not qualify arbitrary rebuilt DLLs automatically.
''')
    ignore=target/'.gitignore'
    with ignore.open('a',encoding='utf-8',newline='\n') as stream:
        stream.write('''
# Transport experiment source restoration and local runtime outputs.
/artifacts/dla32-wch-worker-v8-source/
/artifacts/dla32-wch-worker-v9-source/
/artifacts/dla32-wch-worker-v10-source/
/artifacts/dla32-wch-worker-v8-*/offline-validation.json
/artifacts/dla32-wch-worker-v9-*/offline-validation.json
/artifacts/dla32-wch-worker-v10-*/offline-validation.json
/artifacts/dla32-winusb-v11-source/
/artifacts/dla32-winusb-v11/
/TRANSPORT_STREAM50_*.md
''')
    for name in ('README.md','BUILD_AND_RESTORE.md','.gitignore'):
        path=target/name;records.append({'path':name,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    with manifest.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump({'source_files':records,'captures_private_notes_vendor_materials_copied':False,
            'existing_encrypted_notes_archive_changed':False,'driver_packages_copied':False},stream,indent=2);stream.write('\n')
    print(json.dumps({'files':len(records),'bytes':sum(r['bytes'] for r in records),'private_materials_copied':False,'committed':False,'pushed':False}))


if __name__=='__main__':main()
