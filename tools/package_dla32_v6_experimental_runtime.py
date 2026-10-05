"""Archive only hash-verified files from the existing experimental V6 package.

Offline packaging only. Excludes captures, notes, SDK/driver installers and
generated cache files. Does not promote or launch the candidate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'artifacts/pulseview-dla32-v6-cxx'
MANIFEST_SHA = '4ce376ba4b842e54f98d6b13e28889dd885637071dcbbbdd5fb6074a131f0cbb'
README = '''# PulseView / FNIRSI DLA-32 Plus: experimental V6

Windows x64, standalone test package. Extract the entire archive to a new
directory and run Start-PulseView-DLA32-V6.cmd. The frozen binaries and manifest
are included. The launcher verifies the core DLL, C++ binding and PulseView.

The FNIRSI/WCH driver and CH375DLL64.dll must already be installed separately.
Close other programs accessing the DLA-32 before launching this package. Select
FNIRSI DLA-32 Plus (experimental). A fresh session starts in Buffer mode.

Selected Buffer captures and short Stream captures work. GUI cancellation,
repeated sessions and a full save/load/binary-export round trip have been
checked. High-rate 32-channel Stream captures have failed integrity checks;
sustained lossless 200 MB/s is not established. Maximum-rate/1 GS/s waveform
anomalies and complete physical input/threshold/trigger/PWM checks remain open.

This package remains experimental and does not replace the main installation.
It contains no measurement evidence or private notes. Closing a capture does
not intentionally disable the independent test signal sources.

Original component licenses/notices are retained. Proprietary FNIRSI/WCH
software and drivers are not bundled. Source development is backed up at
https://github.com/victorstoica114/Fnirsi/tree/backup/v6-protocols-2026-10-05
'''
START = '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-PulseView-V6.ps1" -c -d fnirsi-dla32\r\n'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,
        default=ROOT/'artifacts/PulseView-DLA32-V6-experimental-2026-10-05.zip')
    args = parser.parse_args()
    output = args.output.resolve()
    record = output.with_suffix('.zip.sha256.json')
    if output.exists() or record.exists() or not output.is_relative_to(ROOT) or not output.parent.is_dir():
        parser.error('Fresh archive and sidecar inside workspace required')
    source_manifest = PACKAGE/'package-manifest.json'
    if sha(source_manifest) != MANIFEST_SHA:
        raise RuntimeError('Frozen package manifest differs')
    manifest = json.loads(source_manifest.read_text())
    entries = {}
    for name,item in manifest['files'].items():
        path = (PACKAGE/name).resolve(strict=True)
        if not path.is_relative_to(PACKAGE) or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise RuntimeError('Frozen package file differs: '+name)
        if Path(name).name.lower() in ('ch375dll64.dll','ch375w64.sys'):
            raise RuntimeError('Proprietary SDK/driver unexpectedly included')
        entries[name]=dict(item)
    entries['package-manifest.json']={'bytes':source_manifest.stat().st_size,'sha256':MANIFEST_SHA}
    additions={'README-EXPERIMENTAL.md':README.encode('utf-8'),
               'Start-PulseView-DLA32-V6.cmd':START.encode('ascii')}
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in sorted(entries):
            archive.write(PACKAGE/name,name)
        for name,data in additions.items():
            archive.writestr(name,data)
            entries[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    with zipfile.ZipFile(output) as archive:
        if set(archive.namelist()) != set(entries) or len(archive.namelist()) != len(entries):
            raise RuntimeError('Archive inventory differs')
        for name,item in entries.items():
            digest=hashlib.sha256();size=0
            with archive.open(name) as stream:
                for chunk in iter(lambda:stream.read(1048576),b''):
                    digest.update(chunk);size+=len(chunk)
            if size!=item['bytes'] or digest.hexdigest()!=item['sha256']:
                raise RuntimeError('Archived file differs: '+name)
    report={'schema':1,'created_utc':datetime.now(timezone.utc).isoformat(),
        'archive':str(output),'archive_bytes':output.stat().st_size,'archive_sha256':sha(output),
        'package_manifest_sha256':MANIFEST_SHA,'files':entries,'all_files_verified':True,
        'experimental':True,'hardware_accessed':False,'installed_driver_replaced':False,
        'captures_private_notes_vendor_materials_included':False,
        'packager_sha256':sha(Path(__file__))}
    with record.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({k:report[k] for k in ('archive','archive_bytes','archive_sha256','all_files_verified','experimental')}))


if __name__ == '__main__':
    main()
