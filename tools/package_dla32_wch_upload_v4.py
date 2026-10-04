"""Package only the isolated SDK1 V3 lifecycle + A07 timeout correction."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
reference = root / "artifacts/pulseview-la1010-reference"
destination = root / "artifacts/dla32-wch-upload-v4"
source_directory = root / "artifacts/dla32-wch-upload-v4-source"
source = source_directory / "libsigrok"
prefix = msys / "tmp/dla32-wch-upload-v4-prefix"
build = msys / "tmp/dla32-wch-upload-v4-build/libsigrok"
provenance = json.loads((source_directory / "original-provenance.json").read_text())
source_manifest = json.loads((source_directory / "source-manifest.json").read_text())
native_path = root / "logs/build-dla32-wch-upload-v4.status.json"
native_status = json.loads(native_path.read_text())
assert native_status["native_bash_exit_code"] == 0
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path):
    return {p.relative_to(path).as_posix(): sha(p) for p in sorted(path.rglob("*")) if p.is_file()}
assert {name: sha(root/name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
source_hashes = json.loads((source_directory / "source-sha256.json").read_text())
assert tree(source) == source_hashes
original = Path(provenance["original_source"])
assert tree(original) == provenance["original_source_sha256"]
api = "src/hardware/fnirsi-dla32/api.c"
protocol = "src/hardware/fnirsi-dla32/protocol.h"
assert source_hashes[api] == provenance["original_source_sha256"][api]
assert source_hashes[protocol] == provenance["original_source_sha256"][protocol]
unit_log = root / "logs/dla32-wch-upload-v4-mock-final.log"
unit_text = unit_log.read_text()
unit = re.search(r"Audit: (\d+) checks, (\d+) pending behavioral failures", unit_text)
assert unit and unit.group(2) == "0" and "FAIL:" not in unit_text
assert int(unit.group(1)) == unit_text.count("PASS:") and int(unit.group(1)) >= 585
assert "V3 inherited checks: 534 (expected534)." in unit_text
timeout_log = root / "logs/dla32-wch-upload-v4-timeout-unit.log"
timeout_text = timeout_log.read_text()
assert "Timeout audit: 47 checks, 0 failures." in timeout_text and timeout_text.count("PASS:") == 47
make_log = build / "tests/main.log"
make_check = re.search(r"100%: Checks: (\d+), Failures: (\d+), Errors: (\d+)", make_log.read_text())
assert make_check and make_check.group(1) == "92" and make_check.group(2) == make_check.group(3) == "0"
config_status = (build / "config.status").read_text()
assert "-DDLA_WCH_UPLOAD_DIAGNOSTIC=1" in config_status and "-DDLA_PREARM_EMPTY_READS=1" in config_status
build_log = root / "logs/build-dla32-wch-upload-v4.log"
assert build_log.read_text().count("PASS:") >= 636
for marker in ["macro omitted defaults0", "explicit SDK0 and omitted macro", "DLA_WCH_UPLOAD_DIAGNOSTIC must be 0 or 1", "baseline one-empty pre-arm policy"]:
    assert marker in build_log.read_text()
frozen_harness = root / "artifacts/test_dla32_drain_ab.exe"
assert sha(frozen_harness) == "65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82"
vendor = Path(r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll")
assert sha(vendor) == "db19ce17fe9b731cc82b9c40f2c7da23b55f19661c46ce1cfba9952b8a8621f7"
destination.mkdir(exist_ok=False)
for path in reference.iterdir():
    if path.is_file() and (path.suffix.lower() == ".dll" or path.name in {
        "sigrok-cli.exe", "LICENSE-libsigrok.txt", "LICENSE-sigrok-cli.txt", "sources.lock.json"
    }) and path.name != "libsigrok-4.dll":
        shutil.copy2(path, destination/path.name)
shutil.copy2(prefix / "bin/libsigrok-4.dll", destination / "libsigrok-4.dll")
assert b"CH375SetBufUploadEx" in (destination / "libsigrok-4.dll").read_bytes()
assert b"CH375ClearBufUpload" in (destination / "libsigrok-4.dll").read_bytes()
for name in ["wch-upload-v4-diagnostic.patch", "source-manifest.json", "source-sha256.json", "original-provenance.json"]:
    shutil.copy2(source_directory/name, destination/name)
# ABI is unchanged. Copy the exact frozen harness; do not compile or overwrite it.
shutil.copy2(frozen_harness, destination / frozen_harness.name)
assert sha(destination / frozen_harness.name) == sha(frozen_harness)
(destination / "Sigrok-DLA32-WCH-Upload-V4.ps1").write_text(
    "$env:PATH=$PSScriptRoot+';'+$env:PATH\n& (Join-Path $PSScriptRoot 'sigrok-cli.exe') @args\nexit $LASTEXITCODE\n", encoding="utf-8")
(destination / "README.md").write_text('''# DLA32 WCH upload V4: per-operation deadlines

This separate SDK1 diagnostic combines the byte-identical frozen V3 API,
including disable-before-STOP and retained pending hardware STOP, with A07's
timeout correction. Only transport-wch.h changes versus V3. Before each read,
all Ex fields (or both legacy fields) receive the requested deadline; before
each WriteData, all fields receive 1000 ms. A false setter returns IO without
calling the associated SDK I/O. The sample request is 1000 ms; drain request
20 ms; identification pipe2 request500 ms. A requested deadline is not a hard
measured wall-clock bound; scheduling and SDK queue implementation still matter.

SDK queue functions and V3 API/protocol/decoder are unchanged. SDK disable must
succeed before capture STOP; failed STOP retains pending ownership across END.
Explicit inactive stop/close can retry. The queue order remains enable -> required
enabled Clear -> ARM. No disabled Clear is attempted during retirement. One empty
prearm read, 1 MiB reads, pipe1 and 1 MiB upload length remain unchanged. The omitted
macro/default0 and explicit SDK0 do not require queue exports; the SDK0 pending
STOP generalization remains a separate future change and is not included here.

Tests retain 534 inherited real-driver checks, add Ex/legacy setter failures in
startup/ARM/sample/terminal STOP/public retry/context exit, and reuse 47 real-
transport helper checks. Setter false suppresses SDK I/O; false/short I/O remains
IO failure. General make check has92 cases. Hardware has not been accessed by
source/build/tests/packaging. The frozen657 harness is copied unchanged, not rebuilt.

The API's post-stop drainer remains void/warning-only. A post-drain setter or read
failure can leave application resources clean; this alone is not a capture PASS.
For hardware require outer runner/acquisition.exit_code=0, frozen V3 SDK log
audit success, exact requested sample count and independent RAW-to-LOGIC all32
comparison. Close/sr_exit errors can occur after the per-capture log closes.
The current frozen audit matrix conservatively requires aggregate RAW bytes to
be a multiple of32; fragmented reads/carry are allowed, while unused partial
overshoot tails require separate investigation. No bytes are deleted/remapped.

SDK Clear/disable erase a queue-data amount whose count is unknown. Postdrain
accounts only returned bytes. Queue acceptance/quiet logs prove neither physical
FIFO reset nor FPGA alignment, lossless transport or throughput performance.
V3 still reproduced shifted channels after Buffer -> Stream; V4 is a controlled
timeout comparison, with no established alignment fix or decoder compensation.

Source: artifacts/dla32-wch-upload-v4-source/libsigrok.
Build: /tmp/dla32-wch-upload-v4-build/libsigrok (fresh objects).
Prefix: /tmp/dla32-wch-upload-v4-prefix.
Use tools/run_dla32_wch_upload_v4.ps1 only under root's hardware ownership.
All earlier source/evidence/packages and primary/Kingst runtimes stay frozen.
''', encoding="utf-8")
cli_checks = {}
for argument, label in [("--version", "version"), ("--list-supported", "drivers"), ("--self-test", "harness-self-test")]:
    executable = destination / (frozen_harness.name if argument == "--self-test" else "sigrok-cli.exe")
    result = subprocess.run([str(executable),argument], text=True,capture_output=True)
    path = root / f"logs/dla32-wch-upload-v4-{label}.log"
    assert not path.exists()
    path.write_text(result.stdout+result.stderr,encoding="utf-8")
    assert result.returncode == 0, (argument,result.stderr)
    if argument == "--self-test": assert "PASS self-test:" in result.stdout and "no hardware" in result.stdout
    cli_checks[argument] = {"exit_code":0,"log_sha256":sha(path)}
manifest = {
    "variant":"wch-upload-v4", "created_utc":datetime.now(timezone.utc).isoformat(),
    "prearm_empty_reads":1,"sdk_disable_precedes_hardware_stop":True,
    "pending_hardware_stop_survives_application_end":True,"upload_diagnostic_enabled":True,
    "read_size_bytes":1048576,"sample_read_timeout_ms":1000,"drain_read_timeout_ms":20,
    "command_write_timeout_ms":1000,"timeout_policy_unchanged":False,
    "timeout_policy":source_manifest["timeout_policy"],"per_operation_timeout_setter_required":True,
    "failed_timeout_setter_suppresses_io":True,"upload_pipe":1,"upload_length_bytes":1048576,
    "upload_clear_erased_byte_count":None,"upload_retirement_erased_byte_count":None,
    "upload_length_is_known_total_queue_capacity":False,"decoder_or_lane_correction":False,
    "api_unchanged_vs_v3":True,"api_sha256":source_hashes[api],"protocol_sha256":source_hashes[protocol],
    "protocol_unchanged":True,"sdk0_pending_stop_generalization_included":False,
    "unit_checks":int(unit.group(1)),"unit_failures":0,"inherited_v3_checks":534,
    "timeout_interaction_checks":int(unit.group(1))-534,"timeout_helper_checks":47,"timeout_helper_failures":0,
    "unit_fixture_sha256":sha(root / "artifacts/dla32-wch-upload-v4-tests/test_dla32_wch_upload_v4_unit.c"),
    "unit_log_sha256":sha(unit_log),"timeout_helper_log_sha256":sha(timeout_log),
    "make_check_cases":92,"make_check_failures":0,"make_check_errors":0,"make_check_log_sha256":sha(make_log),
    "compile_guards":4,"native_bash_exit_code":0,"native_build_status_sha256":sha(native_path),
    "source_patch_sha256":sha(source_directory / "wch-upload-v4-diagnostic.patch"),
    "kernel_sys_sha256":provenance["kernel_sys_sha256"],"source_directory":str(source),
    "source_manifest":source_manifest,"build_directory":str(build),"prefix_directory":str(prefix),
    "config_status_sha256":sha(build / "config.status"),"vendor_sdk_path":str(vendor),"vendor_sdk_sha256":sha(vendor),
    "hardware_harness_source_sha256":sha(root / "tools/test_dla32_drain_ab.c"),
    "hardware_harness_sha256":sha(frozen_harness),"hardware_harness_copied_unchanged":True,
    "hardware_accessed_during_build_or_packaging":False,"cli_checks":cli_checks,
    "protected_dll_sha256":provenance["protected_dll_sha256"],
    "files":{p.name:{"bytes":p.stat().st_size,"sha256":sha(p)} for p in sorted(destination.iterdir()) if p.is_file()},
}
(destination / "package-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
assert {name:sha(root/name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
assert tree(source) == source_hashes and tree(original) == provenance["original_source_sha256"]
print(json.dumps({"destination":str(destination),"dll_sha256":sha(destination / "libsigrok-4.dll"),
    "unit_checks":manifest["unit_checks"],"timeout_helper_checks":47,"make_check_cases":92,"native_bash_exit_code":0},indent=2))
