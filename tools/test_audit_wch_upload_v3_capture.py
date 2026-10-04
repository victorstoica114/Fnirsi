"""Small offline file/CLI integration tests; synthetic samples, no SDK/hardware."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parent.parent
tool = root / "tools/audit_wch_upload_v3_capture.py"
directory = root / "artifacts/dla32-wch-upload-v3-sdk-audit"
if len(sys.argv) == 2:
    directory = Path(sys.argv[1]).resolve()
    directory.relative_to(root.resolve())
    directory.mkdir(exist_ok=False)
elif len(sys.argv) != 1:
    raise SystemExit("Optional argument: NEW validation directory inside the workspace")
spec = importlib.util.spec_from_file_location("v3_sdk_auditor", tool)
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

frozen_v2_sha = "35e4fe7b25dd7d75921186ce0becbc1a10fb4ea601cf373365c8fdac29793d96"
assert sha(root / "tools/audit_wch_upload_capture.py") == frozen_v2_sha
semantic = auditor.self_test()
checks = []

# Reuse the public self-test's literal recorded-message fixture, without
# executing or synthesizing a real capture or WCH API call.
function = next(node for node in ast.parse(tool.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "self_test")
assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "bodies" for t in node.targets))
bodies = ast.literal_eval(assignment.value)
fixture = directory / "synthetic-file-fixture"
fixture.mkdir(exist_ok=False)
log_path = fixture / "rep001-step1-Stream.driver.log"
log_path.write_text("\n".join(f"time_us={i+1} level=4 {body}" for i, body in enumerate(bodies)) + "\n")
logic_path = fixture / "rep001-step1-Stream.logic.bin"
wire_path = fixture / "rep001-step1-Stream.wire.bin"
logic_path.write_bytes(bytes(32))
wire_path.write_bytes(bytes.fromhex("03000000") + bytes(60))
metadata = {field: 0 for field in ("guard_expired", "invalid_packet", "logic_close_failed",
    "driver_log_write_failures", "driver_log_close_failed", "context_mismatches", "running_after_run",
    "run_status", "destroy_status", "driver_error_log_count", "wire_trace_tail_mod32", "stop_status", "logic_after_stop")}
metadata.update(capture="rep001-step1-Stream", lifecycle_and_count_pass=True, headers=1, ends=1,
    unitsize=4, physical_channel_mask="ffffffff", samplerate_hz=50000000, threshold_volts=1.6,
    actual_samples=8, configured_sample_limit=8, written_logic_bytes=32, wire_trace_bytes=64,
    mode="Stream", observed_sample_bits_or="00300000", stopped_callbacks=1, logic_packets=1,
    stop_requested=False, context="single-default", trigger="D0 rising hardware request", raw_trigger_position=500,
    driver_log_file=log_path.name, wire_file=wire_path.name, logic_file=logic_path.name)
meta_path = fixture / "rep001-step1-Stream.json"
meta_path.write_text(json.dumps(metadata, indent=2) + "\n")

result = auditor.audit_capture(meta_path, "queued-v3", expected_samples=8)
assert result["audit_pass"]
assert result["provenance"]["wire_sha256"] == sha(wire_path)
assert result["provenance"]["logic_sha256"] == sha(logic_path)
assert result["provenance"]["metadata_sha256"] == sha(meta_path)
assert result["provenance"]["driver_log_sha256"] == sha(log_path)
checks.append("file_provenance_hashes_and_overshoot_verified")

try:
    auditor.audit_capture(meta_path, "queued-v3")
except ValueError as error:
    assert "configured sample limit" in str(error)
else:
    raise AssertionError("default 5M expectation accepted an 8-sample synthetic record")
checks.append("default_expected5M_is_independent_of_capture_metadata")

for field in ("wire_file", "logic_file", "driver_log_file"):
    altered = dict(metadata)
    altered[field] = "../outside-file"
    meta_path.write_text(json.dumps(altered))
    try:
        auditor.audit_capture(meta_path, "queued-v3", expected_samples=8)
    except ValueError as error:
        assert "capture directory" in str(error)
    else:
        raise AssertionError(f"path traversal accepted for {field}")
checks.append("capture_file_paths_are_confined_to_one_directory")
meta_path.write_text(json.dumps(metadata, indent=2) + "\n")

for expected_count, expected_exit, label in [(1, 0, "matching-count"), (2, 1, "wrong-count")]:
    output = directory / f"synthetic-{label}.json"
    command = [sys.executable, "-B", str(tool), "--directory", str(fixture), "--expected-samples", "8",
               "--expected-captures", str(expected_count), "--json", str(output)]
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode == expected_exit, completed.stderr
    data = json.loads(output.read_text())
    assert data["all_log_and_count_audits_pass"] is (expected_exit == 0)
    assert data["captures_found"] == 1 and data["captures"][0]["audit_pass"]
    checks.append(f"CLI_{label}_exit_status")

assert sha(root / "tools/audit_wch_upload_capture.py") == frozen_v2_sha
record = {"status": "PASS", "hardware_accessed": False, "SDK_loaded": False,
    "semantic_checks": semantic["count"], "file_CLI_checks": len(checks),
    "total_checks": semantic["count"] + len(checks), "semantic": semantic,
    "file_CLI_cases": checks, "tool_sha256": sha(tool), "test_sha256": sha(Path(__file__)),
    "frozen_v2_auditor_sha256": frozen_v2_sha,
    "fixture_is_synthetic_and_not_hardware_evidence": True}
with (directory / "offline-tests.json").open("x", encoding="utf-8") as handle:
    handle.write(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
