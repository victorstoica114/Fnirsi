"""Fault injection for V5 accepted STOP records; no hardware or SDK access."""
import argparse
import json
from pathlib import Path
import re

import audit_wch_stop_v5_capture as audit


def move(lines, needle, target, after=False):
    result = list(lines)
    original = next(i for i, s in enumerate(result) if needle in s)
    value = result.pop(original)
    index = next(i for i, s in enumerate(result) if target in s) + int(after)
    result.insert(index, value)
    return result


def log_with_monotonic_timestamps(lines):
    # Moving a success marker must be rejected by order, not merely by time.
    # Source read timings remain recorded; there are no SDK calls in this fixture.
    return "\n".join(re.sub(r"^time_us=\d+", f"time_us={10000000+i}", s)
                     for i, s in enumerate(lines)) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    directory = project / "captures/wch-upload-v4-2026-10-04-r1/Q-stream-1"
    meta_path = directory / "rep001-step1-Stream.json"
    metadata = json.loads(meta_path.read_bytes())
    source_log = directory / metadata["driver_log_file"]
    lines = source_log.read_text(encoding="utf-8").splitlines()
    # This is synthetic SDK0 evidence constructed from an immutable SDK1 log;
    # the original files remain unchanged and it is not a physical SDK0 result.
    bodies = [s.replace("WCH UPLOAD capture STOP complete:", "WCH capture STOP complete:")
              .replace("disable precedes STOP.", "command accepted.") for s in lines
              if "WCH UPLOAD SetBufUploadEx:" not in s and
                 "WCH UPLOAD ClearBufUpload:" not in s]
    sizes = {"logic": (directory / metadata["logic_file"]).stat().st_size,
             "wire": (directory / metadata["wire_file"]).stat().st_size}
    good = log_with_monotonic_timestamps(bodies)
    assert audit.audit_text(good, metadata, sizes)["audit_pass"]
    checks = ["valid_synthetic_SDK0_accepted_STOP_and_full_frozen_base_checks"]
    negatives = {
        "missing_initial_completion": [s for s in bodies if "STOP complete: phase pre-arm" not in s],
        "missing_terminal_completion": [s for s in bodies if "STOP complete: phase stop" not in s],
        "no_completion_records": [s for s in bodies if "WCH capture STOP complete" not in s],
        "duplicate_initial_completion": bodies + [next(s for s in bodies if "STOP complete: phase pre-arm" in s)],
        "unexpected_phase": [s.replace("STOP complete: phase stop;", "STOP complete: phase close;") for s in bodies],
        "completion_only_attempted": [s.replace("command accepted.", "command attempted.") for s in bodies],
        "warning_level_success_marker": [s.replace("level=3", "level=2") if "WCH capture STOP complete" in s else s for s in bodies],
        "initial_completion_before_attempt": move(bodies, "STOP complete: phase pre-arm", "WIRE command: opcode 15,"),
        "initial_completion_after_drain": move(bodies, "STOP complete: phase pre-arm", "WIRE read: phase pre-arm-drain", True),
        "initial_completion_after_SETUP": move(bodies, "STOP complete: phase pre-arm", "WIRE command: opcode 11,", True),
        "terminal_completion_before_ARM": move(bodies, "STOP complete: phase stop", "WIRE command: opcode 12,"),
        "terminal_completion_after_postdrain": move(bodies, "STOP complete: phase stop", "WIRE read: phase post-stop-drain", True),
        "queued_marker_not_SDK0": [s.replace("WCH capture STOP complete:", "WCH UPLOAD capture STOP complete:") for s in bodies],
        "pending_failure_after_apparent_success": bodies + ["time_us=1 level=3 fnirsi-dla32: WCH capture STOP failed: phase stop; pending ownership retained."],
        "underreported_payload_count": [s.replace("SR_DF_LOGIC packet (1048576 bytes", "SR_DF_LOGIC packet (1048572 bytes") for s in bodies],
    }
    for name, invalid in negatives.items():
        assert not audit.audit_text(log_with_monotonic_timestamps(invalid), metadata, sizes)["audit_pass"], name
        checks.append(name)
    assert not audit.audit_text(source_log.read_text(encoding="utf-8"), metadata, sizes)["audit_pass"]
    checks.append("actual_frozen_SDK1_capture_rejected_as_SDK0")
    # Demonstrate the blind spot that motivated the supplement: otherwise valid
    # direct logs lacking new STOP markers are accepted by the frozen baseline.
    missing = log_with_monotonic_timestamps(negatives["no_completion_records"])
    assert audit.base.audit_text(missing, metadata, sizes, "direct-baseline")["audit_pass"]
    checks.append("missing_markers_pass_old_baseline_but_fail_V5_supplement")
    result = {"status": "PASS", "count": len(checks), "checks": checks,
              "fixture_classification": "Synthetic SDK0 log transformations of preserved SDK1 evidence",
              "fixture_metadata_sha256": audit.base.file_digest(meta_path),
              "fixture_log_sha256": audit.base.file_digest(source_log),
              "tool_sha256": audit.base.file_digest(Path(audit.__file__)),
              "frozen_base_auditor_sha256": audit.BASE_SHA,
              "hardware_access": False, "original_inputs_changed": False}
    with args.json.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
