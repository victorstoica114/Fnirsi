"""Assemble a local Windows runtime from our built prefix and MSYS2 dependencies."""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parent.parent
msys=Path((root/'tools/toolchain-path.txt').read_text().strip())
prefix=msys/'opt/dla32'
ucrt=msys/'ucrt64'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=Path('artifacts/pulseview-dla32'))
args=parser.parse_args()
destination=(root/args.output).resolve()
artifact_root=(root/'artifacts').resolve()
if artifact_root not in destination.parents:
    parser.error('The runtime must be in a subdirectory of workspace artifacts.')
destination.mkdir(parents=True,exist_ok=True)
objdump=Path(r'C:\Strawberry\c\bin\objdump.exe')
search=[prefix/'bin',ucrt/'bin']
files=[]

def copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)
    files.append(str(target.relative_to(destination)))

queue=[]
for name in ('pulseview.exe','sigrok-cli.exe'):
    source=prefix/'bin'/name
    if not source.is_file(): raise SystemExit(f'Missing built executable: {source}')
    copy(source,destination/name); queue.append(destination/name)

for subdir,names in [('platforms',['qwindows.dll']),('imageformats',['qsvg.dll'])]:
    for name in names:
        source=ucrt/'share/qt5/plugins'/subdir/name
        copy(source,destination/subdir/name); queue.append(destination/subdir/name)

shutil.copytree(prefix/'share/libsigrokdecode/decoders',destination/'decoders',dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree(ucrt/'lib/python3.14',destination/'lib/python3.14',dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__','*.pyc','site-packages','test','tests','idlelib','tkinter','config-3.14'))
queue.extend(destination.rglob('*.pyd'))
seen=set()
missing=set()
system32=Path(os.environ['SystemRoot'])/'System32'
while queue:
    binary=queue.pop()
    key=str(binary).lower()
    if key in seen: continue
    seen.add(key)
    output=subprocess.check_output([str(objdump),'-p',str(binary)],text=True,errors='replace')
    for name in re.findall(r'DLL Name:\s*(\S+)',output):
        source=next((directory/name for directory in search if (directory/name).is_file()),None)
        if source:
            target=destination/name
            if str(target).lower() not in seen:
                copy(source,target); queue.append(target)
        elif (destination/name).is_file():
            queue.append(destination/name)
        elif (system32/name).is_file() or name.lower().startswith(('api-ms-','ext-ms-')):
            continue
        else:
            missing.add(name)
if missing:
    raise SystemExit('Unresolved DLL dependencies: '+', '.join(sorted(missing)))

copy(root/'sources.lock.json',destination/'sources.lock.json')
copy(root/'src/libsigrok/COPYING',destination/'LICENSE-libsigrok.txt')
copy(root/'src/pulseview/COPYING',destination/'LICENSE-PulseView.txt')
copy(root/'src/libsigrokdecode/COPYING',destination/'LICENSE-libsigrokdecode.txt')
(destination/'qt.conf').write_text('[Paths]\nPlugins=.\n',encoding='utf-8')
(destination/'Porneste-PulseView.ps1').write_text('''$env:PYTHONHOME=$PSScriptRoot
$env:SIGROKDECODE_DIR=Join-Path $PSScriptRoot 'decoders'
$env:QT_PLUGIN_PATH=$PSScriptRoot
Start-Process -FilePath (Join-Path $PSScriptRoot 'pulseview.exe') -ArgumentList '-c','-d','fnirsi-dla32' -WorkingDirectory $PSScriptRoot
''',encoding='utf-8-sig')
(destination/'Porneste-PulseView.cmd').write_text('@"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Porneste-PulseView.ps1"\r\n',encoding='ascii')
(destination/'package-manifest.json').write_text(json.dumps(dict(files=sorted(
    str(path.relative_to(destination)) for path in destination.rglob('*')
    if path.is_file() and path.name!='package-manifest.json'),
    note='Local experimental build; WCH runtime remains in its official installation.'),indent=2)+'\n',encoding='utf-8')
print(f'Packaged local PulseView runtime: {destination} ({len(set(files))} files refreshed)')
