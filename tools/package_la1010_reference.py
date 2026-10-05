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
(destination / "README.md").write_text("""# Kingst LA1010: isolated CLI runtime

Local libsigrok 0.6.0 build with the `kingst-la2016`, `fnirsi-dla32` and
`demo` drivers. The `kingst-la2016` driver includes LA1010 and selects its
revision through EEPROM. This package contains only sigrok-cli and its
libraries; PulseView and the existing DLA32 runtime stay in their directories.

The library and CLI were built in a separate source tree,
`/tmp/la1010-reference-build`, with the prefix `/opt/la1010-reference`.
libsigrokdecode support is disabled for this capture tool; `.sr` files can
be opened later in the existing PulseView installation.

List the drivers (without scanning devices):

```powershell
& .\\Sigrok-LA1010.ps1 --list-supported
```

Scan or capture later, once the test connections are ready:

```powershell
& .\\Sigrok-LA1010.ps1 --scan -d kingst-la2016
```

The script sets `SIGROK_FIRMWARE_DIR` to the `firmware` subdirectory, which
contains the MCU firmware `kingst-la-01a2.fw` and the three LA1010 bitstreams.
They are extracted unchanged from the official KingstVIS 3.6.6 Linux package
using the sigrok extractor. Provenance is recorded in
`firmware/asset-download-record.json`. During scanning, the driver may
automatically load firmware into RAM/FPGA if the device requires it; listing
the drivers does not perform this operation. No ESP32 firmware is read or saved.

The package does not install a Windows USB driver. Hardware access depends
on a compatible USB driver already installed or prepared separately. LA1010
provides 16 inputs, CH0–CH15; sigrok captures use two-byte samples.

The local Windows fix is saved in `windows-usb-fix.patch`, with the complete
source tree stored separately in `artifacts/la1010-reference-source/libsigrok`.
On Windows, libusb does not expose pollfds; GSource uses a 2 ms timer and the
Kingst callback services events without blocking. Startup checks the event
source and HEADER; stopping retains the buffers until all cancellation
callbacks complete. A stop requested inside a callback is deferred to the
event source. The main libsigrok tree and DLA32 DLL do not include this fix.
Primary source: https://libusb.sourceforge.io/api-1.0/group__libusb__poll.html

The original driver for the LA1010 without onboard memory does not implement
software triggering in Stream mode and may exceed the sample limit until the
end of a USB chunk. Evaluation must use the actual sample count and complete
cycles; accepting the trigger option does not constitute physical validation.
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
