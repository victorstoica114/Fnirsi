"""Package the isolated Kingst-enabled CLI build; never touch existing runtimes."""
import hashlib
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zlib

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
prefix = msys / "opt/la1010-reference"
destination = root / "artifacts/pulseview-la1010-reference"
destination.mkdir(parents=True, exist_ok=True)
search = [prefix / "bin", msys / "ucrt64/bin"]
objdump = Path(r"C:\Strawberry\c\bin\objdump.exe")
queue = []


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


copy(prefix / "bin/sigrok-cli.exe", destination / "sigrok-cli.exe")
queue.append(destination / "sigrok-cli.exe")
seen = set()
missing = set()
system32 = Path(os.environ["SystemRoot"]) / "System32"
while queue:
    binary = queue.pop()
    key = str(binary).lower()
    if key in seen:
        continue
    seen.add(key)
    output = subprocess.check_output([str(objdump), "-p", str(binary)], text=True,
                                     errors="replace")
    for name in re.findall(r"DLL Name:\s*(\S+)", output):
        source = next((directory / name for directory in search
                       if (directory / name).is_file()), None)
        target = destination / name
        if source:
            if str(target).lower() not in seen:
                copy(source, target)
                queue.append(target)
        elif target.is_file():
            queue.append(target)
        elif (system32 / name).is_file() or name.lower().startswith(("api-ms-", "ext-ms-")):
            continue
        else:
            missing.add(name)
if missing:
    raise SystemExit("Unresolved DLL dependencies: " + ", ".join(sorted(missing)))

firmware_source = root / "downloads/kingst-la1010/firmware"
firmware_names = ["kingst-la-01a2.fw"] + [
    f"kingst-la1010a{revision}-fpga.bitstream" for revision in range(3)
]
expected_firmware_crc32 = {
    "kingst-la-01a2.fw": 0x720551A9,
    "kingst-la1010a0-fpga.bitstream": 0xA655CDD2,
    "kingst-la1010a1-fpga.bitstream": 0x2333D203,
    "kingst-la1010a2-fpga.bitstream": 0xD2CF8E9E,
}
for name in firmware_names:
    if zlib.crc32((firmware_source / name).read_bytes()) != expected_firmware_crc32[name]:
        raise SystemExit(f"Firmware CRC32 differs from verified extraction: {name}")
    copy(firmware_source / name, destination / "firmware" / name)
copy(root / "downloads/kingst-la1010/asset-download-record.json",
     destination / "firmware/asset-download-record.json")
copy(root / "sources.lock.json", destination / "sources.lock.json")
copy(root / "src/libsigrok/COPYING", destination / "LICENSE-libsigrok.txt")
copy(root / "src/sigrok-cli/COPYING", destination / "LICENSE-sigrok-cli.txt")
patch_files = ["src/usb.c", "src/hardware/kingst-la2016/api.c",
               "src/hardware/kingst-la2016/protocol.c", "src/hardware/kingst-la2016/protocol.h"]
local_source = root / "artifacts/la1010-reference-source/libsigrok"
local_patch = []
for name in patch_files:
    before = (root / "src/libsigrok" / name).read_text(encoding="utf-8").splitlines(True)
    after = (local_source / name).read_text(encoding="utf-8").splitlines(True)
    local_patch.extend(difflib.unified_diff(before, after, fromfile="a/" + name,
                                          tofile="b/" + name))
(destination / "windows-usb-fix.patch").write_text("".join(local_patch), encoding="utf-8")

(destination / "Sigrok-LA1010.ps1").write_text("""$env:PATH=$PSScriptRoot+';'+$env:PATH
$env:SIGROK_FIRMWARE_DIR=Join-Path $PSScriptRoot 'firmware'
& (Join-Path $PSScriptRoot 'sigrok-cli.exe') @args
exit $LASTEXITCODE
""", encoding="utf-8-sig")
(destination / "README.md").write_text("""# Kingst LA1010: runtime CLI separat

Build local libsigrok 0.6.0 cu driverele `kingst-la2016`, `fnirsi-dla32` și
`demo`. Driverul `kingst-la2016` include LA1010 și selectează revizia prin
EEPROM. Acest pachet conține numai sigrok-cli și bibliotecile sale; aplicațiile
PulseView și runtime-ul DLA32 existent rămân în directoarele lor.

Biblioteca și CLI au fost compilate într-un arbore separat
`/tmp/la1010-reference-build`, cu prefix `/opt/la1010-reference`.
Suportul libsigrokdecode este dezactivat pentru această unealtă de captură;
fișierele `.sr` se pot deschide ulterior în PulseView existent.

Listarea driverelor (fără scanare de dispozitive):

```powershell
& .\\Sigrok-LA1010.ps1 --list-supported
```

Scanarea/captura se face ulterior, când conexiunile de test sunt pregătite:

```powershell
& .\\Sigrok-LA1010.ps1 --scan -d kingst-la2016
```

Scriptul setează `SIGROK_FIRMWARE_DIR` spre subdirectorul `firmware`. Acesta
conține firmware-ul MCU `kingst-la-01a2.fw` și cele trei bitstream-uri LA1010.
Sunt extrase fără modificări din pachetul oficial KingstVIS 3.6.6 Linux,
folosind extractorul sigrok. Proveniența este în
`firmware/asset-download-record.json`. Driverul poate încărca automat firmware
în RAM/FPGA la scanare dacă dispozitivul are nevoie; listarea driverelor nu
face această operație. Nu se citește sau salvează firmware de pe ESP32.

Pachetul nu instalează un driver USB Windows. Accesul hardware depinde de
driverul USB compatibil deja instalat sau pregătit separat. LA1010 expune
16 intrări CH0–CH15; capturile sigrok au eșantioane de doi octeți.

Corecția locală Windows este salvată în `windows-usb-fix.patch`, cu sursa
completă separată în `artifacts/la1010-reference-source/libsigrok`. Pe Windows,
libusb nu expune pollfd-uri; GSource folosește un timer de 2 ms și callback-ul
Kingst pompează evenimentele neblocant. Pornirea verifică sursa și HEADER,
iar oprirea păstrează buffer-ele până la toate callback-urile de anulare.
Oprirea cerută dintr-un callback este amânată până la sursa de evenimente.
Arborele principal libsigrok și DLL-ul DLA32 nu includ această corecție.
Sursa primară: https://libusb.sourceforge.io/api-1.0/group__libusb__poll.html

Driverul original pentru LA1010 fără memorie nu implementează triggerul
software în stream și poate depăși limita de eșantioane până la capătul unui
chunk USB. Evaluarea trebuie să folosească numărul efectiv și ciclurile
complete, fără să considere acceptarea opțiunii trigger drept validare fizică.
""", encoding="utf-8")

manifest = {
    "scope": "isolated CLI capture runtime; no hardware scan during packaging",
    "drivers_enabled": ["demo", "fnirsi-dla32", "kingst-la2016"],
    "libsigrokdecode": False,
    "local_source": str(local_source),
    "windows_usb_fix": {
        name: hashlib.sha256((local_source / name).read_bytes()).hexdigest()
        for name in patch_files
    },
    "files": {
        str(path.relative_to(destination)): {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file() and path.name != "package-manifest.json"
    },
}
(destination / "package-manifest.json").write_text(
    json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Packaged Kingst reference CLI: {destination} ({len(manifest['files'])} files)")
