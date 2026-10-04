"""Read-only verification before root's hardware-controlled queue experiment."""
from pathlib import Path
import hashlib
import json
import re

root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wch-upload-v2-source"
package = root / "artifacts/dla32-wch-upload-v2"
manifest_path = package / "package-manifest.json"
manifest = json.loads(manifest_path.read_text())
provenance = json.loads((directory / "original-provenance.json").read_text())
source = directory / "libsigrok"
original = Path(provenance["original_source"])

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

assert {name: sha(root / name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
assert {p.relative_to(original).as_posix(): sha(p) for p in original.rglob("*") if p.is_file()} == provenance["original_source_sha256"]
hashes = json.loads((directory / "source-sha256.json").read_text())
assert {p.relative_to(source).as_posix(): sha(p) for p in source.rglob("*") if p.is_file()} == hashes
changed = [name for name, digest in hashes.items() if digest != provenance["original_source_sha256"][name]]
assert changed == ["src/hardware/fnirsi-dla32/transport-wch.h"]
assert manifest["prearm_empty_reads"] == 1 and manifest["upload_diagnostic_enabled"] is True
assert manifest["read_size_bytes"] == manifest["upload_length_bytes"] == 1048576 and manifest["upload_pipe"] == 1
assert manifest["sample_read_timeout_ms"] == 1000 and manifest["drain_read_timeout_ms"] == 20
assert manifest["unit_failures"] == 0 and manifest["unit_checks"] == 501
assert manifest["native_bash_exit_code"] == 0
assert manifest["hardware_harness_source_sha256"] == sha(root / "tools/test_dla32_drain_ab.c")
for name, record in manifest["files"].items():
    path = package / name
    assert path.stat().st_size == record["bytes"] and sha(path) == record["sha256"], name
api = (source / "src/hardware/fnirsi-dla32/api.c").read_text()
old_api = (original / "src/hardware/fnirsi-dla32/api.c").read_text()
unchanged_regions = {}
for name, start, end in [
    ("decode_transfer", "static uint64_t decode_transfer", "static void LIBUSB_CALL receive_transfer"),
    ("read_samples_sync", "static int read_samples_sync", "static void drain_stopped_endpoint"),
    ("receive_events", "static int receive_events", "static int prepare_trigger"),
    ("prepare_trigger", "static int prepare_trigger", "static uint64_t configured_sample_count"),
]:
    actual = api[api.index(start):api.index(end, api.index(start))]
    expected = old_api[old_api.index(start):old_api.index(end, old_api.index(start))]
    assert actual == expected, name
    unchanged_regions[name] = hashlib.sha256(actual.encode()).hexdigest()
protocol = "src/hardware/fnirsi-dla32/protocol.h"
assert hashes[protocol] == provenance["original_source_sha256"][protocol] == manifest["protocol_sha256"]
unit_log = root / "logs/dla32-wch-upload-v2-mock-final.log"
assert "Audit: 501 checks, 0 pending behavioral failures" in unit_log.read_text()
assert unit_log.read_text().count("PASS:") == 501 and "FAIL:" not in unit_log.read_text()
assert (root / "logs/dla32-wch-upload-v2-compile-guards.log").read_text().count("PASS:") == 3
status = json.loads((root / "logs/build-dla32-wch-upload-v2.status.json").read_text())
assert status["native_bash_exit_code"] == 0
assert "PASS self-test:" in (root / "logs/dla32-wch-upload-v2-harness-self-test.log").read_text()
record = {
    "scope": "hardware-free build, real transport mocked API tests, immutable package/source integrity",
    "hardware_accessed": False, "alignment_fix_established": False,
    "native_bash_exit_code": 0, "make_check_checks": manifest["make_check_cases"],
    "make_check_failures": 0, "mock_checks": 501, "mock_failures": 0,
    "compile_guards_passed": 3, "harness_selftest_passed": True,
    "package_directory": str(package), "package_manifest_sha256": sha(manifest_path),
    "dll_sha256": sha(package / "libsigrok-4.dll"),
    "package_files_verified": len(manifest["files"]), "source_files_verified": len(hashes),
    "changed_files": changed, "unchanged_code_regions_sha256": unchanged_regions,
    "protocol_sha256": hashes[protocol], "source_patch_sha256": manifest["source_patch_sha256"],
    "protected_dll_sha256": provenance["protected_dll_sha256"],
    "upload_clear_erased_byte_count": None, "upload_retirement_erased_byte_count": None,
    "tools_sha256": {name: sha(root / "tools" / name) for name in [
        "prepare_dla32_wch_upload_v2.py", "prepare_dla32_wch_upload_v2_tests.py",
        "dla32_wch_upload_v2_cases.c.inc", "test_dla32_wch_upload_v2_mock.sh",
        "test_dla32_wch_upload_v2_compile_guards.sh", "build_dla32_wch_upload_v2.sh",
        "run_dla32_wch_upload_v2_build_with_status.sh",
        "prepare_dla32_wch_upload_v2_packaging.py", "package_dla32_wch_upload_v2.py",
        "verify_dla32_wch_upload_v2_package.py",
    ]},
}
target = directory / "build-validation.json"
with target.open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
