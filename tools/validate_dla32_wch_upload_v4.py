"""Read-only provenance verification, then create one new immutable record."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

root = Path(__file__).resolve().parent.parent
package = root / "artifacts/dla32-wch-upload-v4"
manifest_path = package / "package-manifest.json"
manifest = json.loads(manifest_path.read_text())
source_dir = root / "artifacts/dla32-wch-upload-v4-source"
source = source_dir / "libsigrok"
provenance = json.loads((source_dir / "original-provenance.json").read_text())
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path):
    return {p.relative_to(path).as_posix():sha(p) for p in sorted(path.rglob("*")) if p.is_file()}
assert manifest["variant"] == "wch-upload-v4"
assert manifest["unit_checks"] == 585 and manifest["unit_failures"] == 0
assert manifest["inherited_v3_checks"] == 534 and manifest["timeout_interaction_checks"] == 51
assert manifest["timeout_helper_checks"] == 47 and manifest["timeout_helper_failures"] == 0
assert manifest["make_check_cases"] == 92 and not manifest["make_check_failures"] and not manifest["make_check_errors"]
assert manifest["compile_guards"] == 4 and manifest["native_bash_exit_code"] == 0
assert manifest["api_unchanged_vs_v3"] and not manifest["timeout_policy_unchanged"]
assert not manifest["sdk0_pending_stop_generalization_included"]
assert manifest["hardware_harness_copied_unchanged"] and not manifest["hardware_accessed_during_build_or_packaging"]
for name,record in manifest["files"].items():
    path = package/name
    assert path.stat().st_size == record["bytes"] and sha(path) == record["sha256"], name
assert {p.name for p in package.iterdir() if p.is_file()} == set(manifest["files"]) | {"package-manifest.json"}
source_hashes = json.loads((source_dir / "source-sha256.json").read_text())
assert tree(source) == source_hashes
original = Path(provenance["original_source"])
assert tree(original) == provenance["original_source_sha256"]
changed = [name for name in source_hashes if source_hashes[name] != provenance["original_source_sha256"][name]]
assert changed == ["src/hardware/fnirsi-dla32/transport-wch.h"]
for name in ["src/hardware/fnirsi-dla32/api.c", "src/hardware/fnirsi-dla32/protocol.h"]:
    assert source_hashes[name] == provenance["original_source_sha256"][name]
old_sources = {}
for name in ["dla32-drain-ab-source", "dla32-wch-upload-source", "dla32-wch-upload-v2-source", "dla32-wch-upload-v3-source", "dla32-wch-timeout-source"]:
    directory = root / "artifacts" / name
    hashes_path = directory / "source-sha256.json"
    if hashes_path.exists():
        saved = json.loads(hashes_path.read_text())
        assert tree(directory / "libsigrok") == saved, name
        old_sources[name] = {"files":len(saved),"source_sha256_record_sha256":sha(hashes_path)}
assert {name:sha(root/name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
old_packages = {}
for name in ["dla32-drain-ab-baseline", "dla32-drain-ab-two-empty", "dla32-wch-upload", "dla32-wch-upload-v2", "dla32-wch-upload-v3", "dla32-wch-timeout"]:
    directory = root / "artifacts" / name
    previous = json.loads((directory / "package-manifest.json").read_text())
    if "files" not in previous: continue
    for filename,record in previous["files"].items():
        expected = record["sha256"] if isinstance(record,dict) else record
        assert sha(directory/filename) == expected, (name,filename)
    old_packages[name] = {"verified_files":len(previous["files"]),"package_manifest_sha256":sha(directory/"package-manifest.json")}
assert sha(root / "artifacts/test_dla32_drain_ab.exe") == manifest["hardware_harness_sha256"] == "65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82"
assert sha(package / "test_dla32_drain_ab.exe") == manifest["hardware_harness_sha256"]
assert sha(root / "tools/test_dla32_drain_ab.c") == manifest["hardware_harness_source_sha256"]
assert sha(root / "tools/audit_wch_upload_v3_capture.py") == "1d3294dbf101fc3ec8c2d36711ea6c0968d978f61b4758096062f6c250a37ed4"
runner_log = root / "logs/dla32-wch-upload-v4-runner-guards.log"
text = runner_log.read_text(encoding="utf-16" if runner_log.read_bytes().startswith(b"\xff\xfe") else "utf-8")
assert text.count("PASS:") == 12 and "Runner guard audit: 12 checks, 0 failures." in text
files = [manifest_path, source_dir/"wch-upload-v4-diagnostic.patch", source_dir/"source-manifest.json", source_dir/"source-sha256.json",
         source_dir/"original-provenance.json", root/"artifacts/dla32-wch-upload-v4-tests/test_dla32_wch_upload_v4_unit.c",
         root/"artifacts/dla32-wch-upload-v4-tests/test_dla32_wch_upload_v4_timeout_unit.c",
         root/"logs/build-dla32-wch-upload-v4.log", root/"logs/build-dla32-wch-upload-v4.status.json",
         root/"logs/dla32-wch-upload-v4-mock-final.log", root/"logs/dla32-wch-upload-v4-timeout-unit.log", runner_log,
         root/"tools/run_dla32_wch_upload_v4.ps1", root/"tools/dla32_wch_upload_v4_cases.c.inc",
         root/"tools/prepare_dla32_wch_upload_v4.py", root/"tools/prepare_dla32_wch_upload_v4_tests.py",
         root/"tools/package_dla32_wch_upload_v4.py", root/"tools/build_dla32_wch_upload_v4.sh",
         root/"tools/run_dla32_wch_upload_v4_build_with_status.sh", root/"tools/test_dla32_wch_upload_v4_mock.sh",
         root/"tools/test_dla32_wch_upload_v4_timeout.sh", root/"tools/test_dla32_wch_upload_v4_compile_guards.sh",
         root/"tools/test_dla32_wch_upload_v4_runner_guards.ps1", Path(manifest["build_directory"])/"tests/main.log"]
record = {"variant":"wch-upload-v4", "created_utc":datetime.now(timezone.utc).isoformat(),
    "hardware_accessed":False,"runner_executed":False,"source_files_verified":len(source_hashes),
    "changed_files_vs_v3":changed,"api_protocol_decoder_unchanged":True,
    "package_files_verified":len(manifest["files"]),"protected_runtime_count":len(provenance["protected_dll_sha256"]),
    "protected_dll_sha256":provenance["protected_dll_sha256"],"old_sources_verified":old_sources,"old_packages_verified":old_packages,
    "unit_checks":585,"unit_failures":0,"inherited_v3_checks":534,"timeout_interaction_checks":51,
    "timeout_helper_checks":47,"make_check_cases":92,"compile_guards":4,"runner_guard_checks":12,"native_bash_exit_code":0,
    "hardware_harness_sha256":manifest["hardware_harness_sha256"],
    "required_hardware_gates":["outer runner acquisition.exit_code=0","frozen queued-v3 SDK/log audit success","exact5M per capture","independent RAW-to-LOGIC all32 comparison"],
    "hardware_evidence_exists_for_v4":False,"alignment_fix_established":False,
    "warnings":["poststop drain remains void/warning-only; log/outer failure overrides resources_clean", "SDK Clear/disable erased byte count unknown", "SDK0 pendingSTOP generalization remains separate", "RAW aggregate-tail0 gate is a conservative frozen-matrix scope limit"],
    "files_sha256":{str(p.relative_to(root)) if p.is_relative_to(root) else str(p):sha(p) for p in files}}
target = root / "artifacts/dla32-wch-upload-v4-tests/validation-manifest.json"
with target.open("x",encoding="utf-8") as stream: stream.write(json.dumps(record,indent=2)+"\n")
print(json.dumps({"validation_manifest":str(target),"package_manifest_sha256":sha(manifest_path),"validation_manifest_sha256":sha(target),
    "source_files_verified":len(source_hashes),"package_files_verified":len(manifest["files"]),"old_packages":old_packages},indent=2))
