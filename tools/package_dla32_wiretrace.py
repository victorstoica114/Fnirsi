"""Package the isolated DLA32 diagnostic DLL with a read-only reference CLI."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
reference = root / "artifacts/pulseview-la1010-reference"
destination = root / "artifacts/pulseview-dla32-wiretrace"
destination.mkdir(parents=True, exist_ok=True)
for source in reference.iterdir():
    if source.is_file() and (source.suffix.lower() == ".dll"
                             or source.name in {"sigrok-cli.exe", "LICENSE-libsigrok.txt",
                                                "LICENSE-sigrok-cli.txt", "sources.lock.json"}):
        if source.name != "libsigrok-4.dll":
            shutil.copy2(source, destination / source.name)
shutil.copy2(msys / "opt/dla32-wiretrace/bin/libsigrok-4.dll",
             destination / "libsigrok-4.dll")

local_source = root / "artifacts/la1010-reference-source/libsigrok"
patch_files = ["src/usb.c", "src/hardware/fnirsi-dla32/api.c"]
patch = []
for name in patch_files:
    before = (root / "src/libsigrok" / name).read_text(encoding="utf-8").splitlines(True)
    after = (local_source / name).read_text(encoding="utf-8").splitlines(True)
    patch.extend(difflib.unified_diff(before, after, fromfile="a/" + name, tofile="b/" + name))
(destination / "diagnostic-wiretrace.patch").write_text("".join(patch), encoding="utf-8")
(destination / "Sigrok-DLA32-Wiretrace.ps1").write_text("""$env:PATH=$PSScriptRoot+';'+$env:PATH
& (Join-Path $PSScriptRoot 'sigrok-cli.exe') @args
exit $LASTEXITCODE
""", encoding="utf-8")
(destination / "README.md").write_text("""# DLA32: same-capture wire diagnostics

Separate CLI runtime for diagnostic captures only; the installed DLA32/PulseView
and LA1010 reference runtimes are unchanged. Drivers: demo and fnirsi-dla32.
The vendor WCH DLL is loaded from its existing FNIRSI installation.

Set `FNIRSI_DLA32_WIRE_TRACE` to an absolute UTF-8 path for a NEW `.wire.bin`
before launching this CLI. An existing file or an unwritable directory rejects
acquisition startup before the driver's STOP/SETUP/ARM commands. Unset the
variable after the diagnostic series. Use `-l 4` to retain provenance in stderr.
Capture the full 32-channel mask for the initial alignment comparison.

The raw file concatenates exactly the bytes supplied to the decoder in the same
CLI acquisition that writes the `.sr` file. Its start is unchanged; no prefix
removal, channel rotation, or data correction is applied. It includes bytes
beyond the software sample limit and a final incomplete frame. Therefore compare
only the decoded sample count stored in `.sr`, without asserting identical file
lengths. Pre-arm/post-stop drain data and libusb callbacks retired after stop
are excluded; their counts/prefixes/timing are logged separately. The file is
closed on normal completion, startup rollback, and private-resource cleanup.

Per-read logs retain command prefixes, read start/duration/status/count/mod32,
the exact decoder-input offset/pending tail/prefix, and drain byte totals.
Read/write errors request the existing orderly stop. The CLI can still return
zero for some acquisition errors; inspect the log for `WIRE trace ... failed`.
No integrity verdict is encoded by this instrumentation.

Unbuffered disk writes and diagnostic logging change timing. These captures
cannot establish USB throughput performance or the 200 MB/s target.

Build provenance: `package-manifest.json`, `diagnostic-wiretrace.patch`,
`logs/build-dla32-wiretrace.log`; dedicated configure tree
`/tmp/dla32-wiretrace-build/libsigrok`, prefix `/opt/dla32-wiretrace`.
Hardware-free lifecycle/trace tests are in
`logs/dla32-wiretrace-driver-test.log`.
""", encoding="utf-8")
for option, name in [("--version", "version"), ("--list-supported", "drivers")]:
    result = subprocess.run([str(destination / "sigrok-cli.exe"), option],
                            text=True, capture_output=True)
    (root / f"logs/dla32-wiretrace-{name}.log").write_text(result.stdout + result.stderr,
                                                        encoding="utf-8")
    if result.returncode:
        raise SystemExit(f"CLI {option} failed with {result.returncode}")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

source_files = patch_files + ["src/hardware/fnirsi-dla32/protocol.h",
                            "src/hardware/fnirsi-dla32/transport-wch.h"]
manifest = {
    "purpose": "unchanged decoder-input wire trace from the same DLA32 CLI acquisition",
    "drivers": ["demo", "fnirsi-dla32"],
    "reference_cli": str(reference / "sigrok-cli.exe"),
    "source_directory": str(local_source),
    "source_sha256": {name: sha(local_source / name) for name in source_files},
    "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
              for p in sorted(destination.iterdir())
              if p.is_file() and p.name != "package-manifest.json"},
    "existing_runtime_dll_sha256": {
        "DLA32": sha(root / "artifacts/pulseview-dla32-reliability/libsigrok-4.dll"),
        "Kingst": sha(reference / "libsigrok-4.dll"),
    },
}
(destination / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                 encoding="utf-8")
print(json.dumps({"destination": str(destination),
                  "dll_sha256": sha(destination / "libsigrok-4.dll"),
                  "files": len(manifest["files"]),
                  "existing_runtime_dll_sha256": manifest["existing_runtime_dll_sha256"]}, indent=2))
