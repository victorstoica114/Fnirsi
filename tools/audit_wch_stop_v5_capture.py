"""Offline SDK0 capture audit: frozen base checks plus accepted STOP ordering.

No SDK is loaded or hardware accessed. STOP completion means an accepted full
transport write, not a firmware ACK or proof of physical frame origin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

BASE_PATH = Path(__file__).with_name("audit_wch_upload_v3_capture.py")
BASE_SHA = "1d3294dbf101fc3ec8c2d36711ea6c0968d978f61b4758096062f6c250a37ed4"
if hashlib.sha256(BASE_PATH.read_bytes()).hexdigest() != BASE_SHA:
    raise RuntimeError("Frozen base auditor changed")
import audit_wch_upload_v3_capture as base

COMPLETION = re.compile(
    r"WCH capture STOP complete: phase ([\w-]+); command accepted\.$")


def stop_order(log: str) -> dict:
    completions, commands, reads, violations = [], [], [], []
    for number, line in enumerate(log.splitlines(), 1):
        m = base.LINE.fullmatch(line)
        if not m:
            continue  # The frozen base auditor rejects malformed lines.
        timestamp, level, body = int(m[1]), int(m[2]), m[3]
        if "WCH capture STOP complete" in body:
            match = COMPLETION.search(body)
            if match is None or level < 3:
                violations.append(f"Malformed/unsafe STOP completion at line {number}")
            else:
                completions.append({"line": number, "time_us": timestamp,
                                    "phase": match[1], "level": level})
        match = re.search(r"WIRE command: opcode ([0-9a-f]{2}),", body)
        if match:
            commands.append({"line": number, "opcode": int(match[1], 16)})
        match = base.READ.search(body)
        if match:
            reads.append({"line": number, "phase": match[1]})
        if "WCH UPLOAD" in body:
            violations.append(f"Upload diagnostic record in SDK0 at line {number}")
    if [c["phase"] for c in completions] != ["pre-arm", "stop"]:
        violations.append("Expected exactly pre-arm and terminal accepted STOP markers")
    if [c["opcode"] for c in commands] != [0x15, 0x11, 0x12, 0x15]:
        violations.append("STOP/SETUP/ARM/STOP command sequence differs")
    if len(commands) == 4 and len(completions) == 2:
        pre, final = completions
        if not commands[0]["line"] < pre["line"] < commands[1]["line"]:
            violations.append("Pre-arm completion is outside initial STOP-to-SETUP interval")
        if not commands[3]["line"] < final["line"]:
            violations.append("Terminal completion precedes its STOP attempt")
        pre_reads = [r for r in reads if r["phase"] == "pre-arm-drain"]
        post_reads = [r for r in reads if r["phase"] == "post-stop-drain"]
        if not pre_reads or not all(pre["line"] < r["line"] for r in pre_reads):
            violations.append("Pre-arm drain precedes accepted STOP completion or is absent")
        if not post_reads or not all(final["line"] < r["line"] for r in post_reads):
            violations.append("Post-stop drain precedes accepted STOP completion or is absent")
    return {"audit_pass": not violations, "violations": violations,
            "accepted_STOP_records": completions,
            "STOP_success_means_transport_write_not_firmware_ACK": True}


def audit_text(log: str, metadata: dict, sizes: dict) -> dict:
    original = base.audit_text(log, metadata, sizes, "direct-baseline")
    additional = stop_order(log)
    return {"audit_pass": original["audit_pass"] and additional["audit_pass"],
            "base_audit": original, "accepted_STOP_order": additional,
            "lane_origin_or_lossless_verified": False}


def audit_capture(path: Path, expected_samples: int) -> dict:
    original = base.audit_capture(path, "direct-baseline", expected_samples)
    raw = path.read_bytes()
    metadata = json.loads(raw)
    log_path = path.parent / metadata["driver_log_file"]
    log_raw = log_path.read_bytes()
    if base.digest(raw) != original["provenance"]["metadata_sha256"] or \
            base.digest(log_raw) != original["provenance"]["driver_log_sha256"]:
        raise ValueError("Inputs changed between base and supplemental audits")
    additional = stop_order(log_raw.decode("utf-8"))
    if path.read_bytes() != raw or log_path.read_bytes() != log_raw:
        raise ValueError("Inputs changed during supplemental audit")
    return {"audit_pass": original["audit_pass"] and additional["audit_pass"],
            "metadata": str(path), "base_audit": original,
            "accepted_STOP_order": additional,
            "lane_origin_or_lossless_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-captures", type=int, required=True)
    parser.add_argument("--expected-samples", type=int, default=5000000)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if args.expected_captures < 1 or args.expected_samples < 1:
        parser.error("Positive capture and sample counts required")
    reports = []
    for path in sorted(args.directory.glob("rep*-step*.json")):
        try:
            reports.append(audit_capture(path, args.expected_samples))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reports.append({"metadata": str(path), "audit_pass": False,
                            "error": str(exc)})
    matches = len(reports) == args.expected_captures
    result = {"tool_sha256": base.file_digest(Path(__file__)),
              "frozen_base_auditor_sha256": BASE_SHA,
              "directory": str(args.directory.resolve()),
              "profile": "direct-pending-v5", "captures_found": len(reports),
              "expected_captures": args.expected_captures,
              "expected_samples": args.expected_samples,
              "capture_count_matches": matches,
              "all_log_count_and_accepted_STOP_audits_pass":
                  bool(reports) and matches and all(r["audit_pass"] for r in reports),
              "captures": reports, "outer_runner_exit_zero_also_required": True,
              "physical_frame_origin_or_lossless_verdict": "NOT_EVALUATED"}
    with args.json.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "captures"}, indent=2))
    return 0 if result["all_log_count_and_accepted_STOP_audits_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
