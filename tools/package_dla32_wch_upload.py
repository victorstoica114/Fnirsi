"""Package the isolated WCH queue diagnostic after native-status/mock checks."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
reference = root / "artifacts/pulseview-la1010-reference"
destination = root / "artifacts/dla32-wch-upload"
source_directory = root / "artifacts/dla32-wch-upload-source"
source = source_directory / "libsigrok"
prefix = msys / "tmp/dla32-wch-upload-prefix"
build = msys / "tmp/dla32-wch-upload-build/libsigrok"
provenance = json.loads((source_directory / "original-provenance.json").read_text())
source_manifest = json.loads((source_directory / "source-manifest.json").read_text())
native_status = json.loads((root / "logs/build-dla32-wch-upload.status.json").read_text())
assert native_status["native_bash_exit_code"] == 0

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

assert {name: sha(root / name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
source_hashes = json.loads((source_directory / "source-sha256.json").read_text())
assert {p.relative_to(source).as_posix(): sha(p) for p in sorted(source.rglob("*")) if p.is_file()} == source_hashes
original = Path(provenance["original_source"])
assert {p.relative_to(original).as_posix(): sha(p) for p in sorted(original.rglob("*")) if p.is_file()} == provenance["original_source_sha256"]
protocol = "src/hardware/fnirsi-dla32/protocol.h"
assert source_hashes[protocol] == provenance["original_source_sha256"][protocol]
unit_log = root / "logs/dla32-wch-upload-mock-final.log"
unit_text = unit_log.read_text()
unit = re.search(r"Audit: (\d+) checks, (\d+) pending behavioral failures", unit_text)
assert unit and unit.group(2) == "0" and "FAIL:" not in unit_text
assert int(unit.group(1)) == unit_text.count("PASS:")
make_check = re.search(r"100%: Checks: (\d+), Failures: (\d+), Errors: (\d+)", (build / "tests/main.log").read_text())
assert make_check and make_check.group(2) == make_check.group(3) == "0"
config_status = (build / "config.status").read_text()
assert "-DDLA_WCH_UPLOAD_DIAGNOSTIC=1" in config_status and "-DDLA_PREARM_EMPTY_READS=1" in config_status
vendor = Path(r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll")
assert sha(vendor) == "db19ce17fe9b731cc82b9c40f2c7da23b55f19661c46ce1cfba9952b8a8621f7"
destination.mkdir(exist_ok=False)
for path in reference.iterdir():
    if path.is_file() and (path.suffix.lower() == ".dll" or path.name in {
        "sigrok-cli.exe", "LICENSE-libsigrok.txt", "LICENSE-sigrok-cli.txt", "sources.lock.json"
    }) and path.name != "libsigrok-4.dll":
        shutil.copy2(path, destination / path.name)
shutil.copy2(prefix / "bin/libsigrok-4.dll", destination / "libsigrok-4.dll")
assert b"CH375SetBufUploadEx" in (destination / "libsigrok-4.dll").read_bytes()
assert b"CH375ClearBufUpload" in (destination / "libsigrok-4.dll").read_bytes()
for name in ["wch-upload-diagnostic.patch", "source-manifest.json", "source-sha256.json", "original-provenance.json"]:
    shutil.copy2(source_directory / name, destination / name)
(destination / "Sigrok-DLA32-WCH-Upload.ps1").write_text(
    "$env:PATH=$PSScriptRoot+';'+$env:PATH\n"
    "& (Join-Path $PSScriptRoot 'sigrok-cli.exe') @args\n"
    "exit $LASTEXITCODE\n", encoding="utf-8")
subprocess.run([
    str(msys / "usr/bin/bash.exe"), "-lc",
    "bash /tmp/dla32-project/tools/build_dla32_drain_ab.sh "
    "/tmp/dla32-wch-upload-prefix /tmp/dla32-project/artifacts/dla32-wch-upload",
], check=True, env=dict(os.environ, MSYSTEM="UCRT64", CHERE_INVOKING="1"))
(destination / "README.md").write_text("""# DLA32 WCH upload queue: isolated diagnostic

One diagnostic variable is the capture-scoped WCH/kernel upload queue lifecycle.
`DLA_WCH_UPLOAD_DIAGNOSTIC=1`; baseline pre-arm policy remains one empty read,
sample reads remain 1 MiB with the existing 1000 ms timeout, and drain reads
remain 20 ms. Decoder, masks, trigger commands and sample limits are unchanged.
This runtime does not implement a lane rotation, prefix removal or duty change.

Chosen ordering (native cross-thread ordering has not been established):
STOP -> disable/clear(pipe1) -> normal drain -> SETUP -> source/HEADER ->
clear/enable(pipe1, length1MiB) -> ARM -> unchanged ReadEndP.
On completion or failed ARM: STOP -> disable/clear -> normal post-stop drain.
Before close, retirement is retried and failure refuses close/context cleanup.
Sources and application buffers may retire while SDKdirty remains; new capture,
dev_clear/std_cleanup/sr_exit remain blocked until explicit successful retirement.
Any SDK BOOLfalse at startup rejects acquisition even if rollback succeeds.
Missing required exports reject the transport before identification/capture.

Every clear/set call logs phase, index, pipe, enable/length as appropriate,
monotonic start/duration, BOOL and immediate Windows error. Clear's erased queue
byte count is UNKNOWN. The reported post-stop drain byte count excludes bytes
erased by SDK clear. Upload length is not established total queue capacity.
Successful SDK calls do not prove physical quiet, frame alignment or lossless
capture. No conclusion that the Stream fault is fixed follows from this build.

The wire trace still records exactly all decoder inputs, including clipped-tail
bytes; there is no decoder-input dropping or correction. Set
FNIRSI_DLA32_WIRE_TRACE to an absolute new filename and retain -l4 logs.
Disk tracing/logging affect timing and cannot establish USB throughput.

The installed CH375DLL64 is read-only and not distributed/replaced. Its verified
Win64 ULONG32/BOOL32 ABI is recorded under
downloads/dla32-trigger-research/CH375_DLL_UPLOAD_ABI.md. Only its two verified
upload-control exports are added; abort/query/read-size/timeout changes are absent.
Generators are untouched. No device scan/capture occurred during packaging.

New source: artifacts/dla32-wch-upload-source/libsigrok.
New build: /tmp/dla32-wch-upload-build/libsigrok.
New prefix: /tmp/dla32-wch-upload-prefix.
Native build exit status: logs/build-dla32-wch-upload.status.json.
Real transport with fake WinAPI/SDK mock tests: logs/dla32-wch-upload-mock-final.log.
The primary, Kingst, wiretrace and both drain A/B runtimes remain unchanged.
""", encoding="utf-8")
cli_checks = {}
for argument, label in [("--version", "version"), ("--list-supported", "drivers"),
                         ("--self-test", "harness-self-test")]:
    executable = destination / ("test_dla32_drain_ab.exe" if argument == "--self-test" else "sigrok-cli.exe")
    result = subprocess.run([str(executable), argument], text=True, capture_output=True)
    path = root / f"logs/dla32-wch-upload-{label}.log"
    path.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, (argument, result.stderr)
    if argument == "--self-test":
        assert "PASS self-test:" in result.stdout and "no hardware" in result.stdout
    cli_checks[argument] = {"exit_code": result.returncode, "log_sha256": sha(path)}
manifest = {
    "variant": "wch-upload", "prearm_empty_reads": 1,
    "upload_diagnostic_enabled": True,
    "read_size_bytes": 1048576, "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20,
    "upload_pipe": 1, "upload_length_bytes": 1048576,
    "upload_clear_erased_byte_count": None,
    "upload_length_is_known_total_queue_capacity": False,
    "decoder_or_lane_correction": False,
    "unit_checks": int(unit.group(1)), "unit_failures": 0,
    "unit_fixture_sha256": sha(root / "artifacts/dla32-wch-upload-tests/test_dla32_wch_upload_unit.c"),
    "unit_log_sha256": sha(unit_log),
    "make_check_cases": int(make_check.group(1)), "make_check_failures": 0, "make_check_errors": 0,
    "make_check_log_sha256": sha(build / "tests/main.log"),
    "native_bash_exit_code": native_status["native_bash_exit_code"],
    "native_build_status_sha256": sha(root / "logs/build-dla32-wch-upload.status.json"),
    "source_patch_sha256": sha(source_directory / "wch-upload-diagnostic.patch"),
    "source_directory": str(source), "source_manifest": source_manifest,
    "protocol_sha256": source_hashes[protocol], "protocol_unchanged": True,
    "build_directory": str(build), "prefix_directory": str(prefix),
    "config_status_sha256": sha(build / "config.status"),
    "vendor_sdk_path": str(vendor), "vendor_sdk_sha256": sha(vendor),
    "hardware_harness_source_sha256": sha(root / "tools/test_dla32_drain_ab.c"),
    "hardware_harness_build_script_sha256": sha(root / "tools/build_dla32_drain_ab.sh"),
    "hardware_accessed_during_build_or_packaging": False,
    "cli_checks": cli_checks,
    "protected_dll_sha256": provenance["protected_dll_sha256"],
    "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
              for p in sorted(destination.iterdir()) if p.is_file()},
}
(destination / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
assert {name: sha(root / name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
print(json.dumps({"destination": str(destination), "dll_sha256": sha(destination / "libsigrok-4.dll"),
                  "variant": manifest["variant"], "unit_checks": manifest["unit_checks"],
                  "unit_failures": manifest["unit_failures"], "make_check_cases": manifest["make_check_cases"],
                  "native_bash_exit_code": manifest["native_bash_exit_code"]}, indent=2))
