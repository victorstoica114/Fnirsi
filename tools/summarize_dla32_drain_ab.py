"""Aggregate completed drain A/B blocks without changing captures or analysis.

Software lifecycle, counts, RAW conversion and endpoint quiet are separate
from expected D0/D1 lane activity. Missing analysis stays PENDING. The summary
does not infer a fault cause or validate physical Stream integrity.
"""
from argparse import ArgumentParser
from collections import Counter
from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
import tempfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def numeric_summary(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return {"count": len(values), "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "mean": sum(values) / len(values) if values else None,
            "sum": sum(values) if values else None}


def elapsed_seconds(record):
    try:
        start = datetime.fromisoformat(record["started_utc"])
        end = datetime.fromisoformat(record["ended_utc"])
        if start.tzinfo is None or end.tzinfo is None:
            return None
        return (end - start).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def expected_cases(record):
    count = record.get("repetitions")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000:
        raise ValueError("Acquisition record has invalid repetitions")
    sequence = record.get("sequence")
    if sequence == "stream-only":
        modes = ((1, "Stream"),)
    elif sequence == "buffer-stream":
        modes = ((1, "Buffer"), (2, "Stream"))
    else:
        raise ValueError("Unknown acquisition sequence")
    return [(f"rep{rep:03d}-step{step}-{mode}", rep, step, mode)
            for rep in range(1, count + 1) for step, mode in modes]


def timing_record(log):
    commands = log.get("commands", [])
    reads = log.get("wire_reads", [])
    first_stop = next((row["elapsed_us"] for row in commands if row.get("opcode_hex") == "15"), None)
    setup = next((row["elapsed_us"] for row in commands if row.get("opcode_hex") == "11"), None)
    arm = next((row["elapsed_us"] for row in commands if row.get("opcode_hex") == "12"), None)
    first_sample = next((row for row in reads if row.get("phase") == "samples"), None)
    prearm = [row for row in reads if row.get("phase") == "pre-arm-drain"]
    poststop = [row for row in reads if row.get("phase") == "post-stop-drain"]
    sample_reads = [row for row in reads if row.get("phase") == "samples"]

    def difference(end, start):
        return end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None

    return {
        "first_stop_command_elapsed_us": first_stop,
        "setup_command_elapsed_us": setup,
        "arm_command_elapsed_us": arm,
        "prearm_stop_to_setup_us": difference(setup, first_stop),
        "setup_to_arm_us": difference(arm, setup),
        "arm_to_first_sample_read_start_us": difference(first_sample.get("start_us"), arm) if first_sample else None,
        "arm_to_first_sample_read_completion_us": difference(first_sample.get("start_us", 0) + first_sample.get("duration_us", 0), arm) if first_sample else None,
        "prearm_read_count": len(prearm),
        "prearm_final_empty_read_count": log.get("prearm_final_consecutive_empty_reads"),
        "prearm_discarded_bytes": log.get("prearm_discarded_bytes_from_reads"),
        "prearm_read_durations_us": [row.get("duration_us") for row in prearm],
        "prearm_total_read_duration_us": sum(row.get("duration_us", 0) for row in prearm) if prearm else None,
        "prearm_requested_timeouts_ms": sorted(set(row.get("timeout_ms") for row in prearm)),
        "sample_read_durations_us": [row.get("duration_us") for row in sample_reads],
        "poststop_read_durations_us": [row.get("duration_us") for row in poststop],
        "poststop_total_read_duration_us": sum(row.get("duration_us", 0) for row in poststop) if poststop else None,
        "poststop_drain_summaries": log.get("poststop_drain_summaries", []),
        "drain_summary_log_lines": log.get("summary_lines", []),
        "commands": commands,
        "timing_provenance": "observed driver log elapsed/start/duration fields, not requested USB timeout",
    }


def first_frame_diagnostics(directory, record, state):
    """Read exactly one unchanged RAW frame from a completed analyzed case."""
    name = state.get("wire_file")
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Harness RAW filename must stay within its completed block")
    raw_path = directory / record["block"] / name
    with raw_path.open("rb") as source:
        frame = source.read(32)
    if len(frame) != 32:
        raise ValueError("Completed RAW file has no complete first frame")
    seen_high = [lane for lane, value in enumerate(frame) if value]
    both = [lane for lane, value in enumerate(frame) if value not in (0, 255)]
    outside = [lane for lane in seen_high if lane not in (0, 1)]
    return {
        "first_raw_frame_hex": frame.hex(),
        "first_raw_frame_lanes_seen_high": seen_high,
        "first_raw_frame_lanes_seen_both_levels": both,
        "first_raw_frame_lanes_seen_high_outside_D0_D1": outside,
        "first_raw_frame_any_lane_high_outside_D0_D1": bool(outside),
        "first_raw_frame_all32_lanes_seen_high": len(seen_high) == 32,
        "first_raw_frame_expanded_sample_words_hex": [f"{sum(((value >> sample) & 1) << lane for lane, value in enumerate(frame)):08x}"
                                                     for sample in range(8)],
        "first_raw_frame_diagnostics_scope": "Exact first unchanged 32 decoder-input bytes / eight samples; no inferred framing origin.",
    }


def case_summary(record, row, expected, directory=None):
    capture, repetition, step, mode = expected
    base = {
        "block": record["block"], "policy": record["policy"], "sequence": record["sequence"],
        "capture": capture, "repetition": repetition, "step": step, "mode": mode,
        "requested_samples": record.get("requested_samples_per_capture"),
        "samplerate_hz": record.get("samplerate_hz"), "threshold_volts": record.get("threshold_volts"),
        "dll_sha256": record.get("dll_sha256"), "harness_sha256": record.get("harness_sha256"),
        "block_exit_code": record.get("exit_code"),
    }
    if row is None:
        return {**base, "analysis_state": "PENDING", "software_state": "PENDING",
                "actual_samples": None, "lifecycle_and_count_pass": None,
                "all32_raw_to_logic_match": None, "reported_counts_match_artifacts": None,
                "trace_close_matches_artifacts": None, "poststop_endpoint_became_quiet": None,
                "lane_activity_placement": "PENDING", "lane_activity_anomaly": None,
                "raw_sha256": None, "logic_sha256": None, "timing": None}
    state = row.get("harness_state", {})
    log = row.get("driver_log_evidence", {})
    high = sorted(row.get("raw_lanes_seen_high", []))
    both = sorted(row.get("raw_lanes_seen_both_levels", []))
    outside = [lane for lane in high if lane not in (0, 1)]
    missing = [lane for lane in (0, 1) if lane not in both]
    windows = row.get("activity_windows", [])
    initial = windows[0].get("raw_lanes_seen_high", []) if windows else []
    final = windows[-1].get("raw_lanes_seen_high", []) if windows else []
    initial_all32_then_displaced = (initial == list(range(32)) and len(final) == 2 and any(lane not in (0, 1) for lane in final))
    anomaly = bool(outside or missing)
    counts = row.get("reported_counts_match_artifacts") is True and row.get("saved_samples") == base["requested_samples"]
    metadata_matches = state.get("capture") == capture and state.get("mode") == mode and state.get("repetition") == repetition and state.get("step") == step
    software_checks = {
        "lifecycle_and_count_pass": state.get("lifecycle_and_count_pass") is True,
        "all32_raw_to_logic_match": row.get("all32_raw_to_logic_match") is True,
        "reported_counts_match_artifacts": counts,
        "trace_close_matches_artifacts": row.get("trace_close_matches_artifacts") is True,
        "poststop_endpoint_became_quiet": row.get("poststop_endpoint_became_quiet") is True,
        "driver_errors_absent": not log.get("driver_error_log_count", 0),
        "unsafe_transport_warnings_absent": not log.get("unsafe_transport_warning_log_lines", []),
        "transport_read_failures_absent": not log.get("transport_read_failures", []),
        "metadata_matches_expected_case": metadata_matches,
    }
    passed = record.get("exit_code") == 0 and all(software_checks.values()) and row.get("software_conversion_and_count_pass") is True
    return {
        **base, "analysis_state": "COMPLETE", "software_state": "PASS" if passed else "FAIL",
        **software_checks, "actual_samples": row.get("saved_samples"),
        "raw_to_logic_bit_comparisons": row.get("raw_to_logic_bit_comparisons"),
        "raw_to_logic_differing_words": row.get("raw_to_logic_differing_words"),
        "wire_bytes": row.get("wire_bytes"), "logic_bytes": row.get("logic_bytes"),
        "extra_wire_bytes_after_saved_samples": row.get("extra_wire_bytes_after_saved_samples"),
        "wire_tail_mod32": row.get("wire_tail_mod32"),
        "raw_sha256": row.get("wire_sha256"), "logic_sha256": row.get("logic_sha256"),
        "raw_prefix_sha256": row.get("compared_wire_prefix_sha256"),
        "harness_state_sha256": row.get("harness_state_sha256"), "driver_log_sha256": log.get("sha256"),
        "raw_lanes_seen_high": high, "raw_lanes_seen_both_levels": both,
        "raw_lanes_seen_high_outside_D0_D1": outside, "required_D0_D1_missing_both_levels": missing,
        "lane_activity_placement": "ANOMALOUS" if anomaly else "EXPECTED_D0_D1_ACTIVITY",
        "lane_activity_anomaly": anomaly,
        "initial_all32_then_displaced_pair_windows": initial_all32_then_displaced,
        "initial_window_all32_lanes_seen_high": initial == list(range(32)),
        "initial_window_lanes_seen_high": initial, "final_window_lanes_seen_high": final,
        "observed_raw_lane_bits_or": row.get("observed_raw_lane_bits_or"),
        "observed_logic_sample_bits_or": row.get("observed_logic_sample_bits_or"),
        "logic_samples_with_bits_outside_D0_D1": row.get("logic_samples_with_bits_outside_known_fixture_D0_D1"),
        "timing": timing_record(log),
        **(first_frame_diagnostics(directory, record, state) if directory is not None else {}),
        "placement_is_diagnostic": True,
        "frame_origin_validated": False, "lossless_physical_stream_verified": False,
    }


def group_summary(rows):
    complete = [row for row in rows if row["analysis_state"] == "COMPLETE"]
    timings = [row["timing"] for row in complete]
    timing_means = {}
    for field in ("prearm_stop_to_setup_us", "setup_to_arm_us", "arm_to_first_sample_read_start_us",
                  "arm_to_first_sample_read_completion_us", "prearm_total_read_duration_us", "poststop_total_read_duration_us"):
        timing_means[field] = numeric_summary([timing[field] for timing in timings])
    for field in ("prearm_read_durations_us", "sample_read_durations_us", "poststop_read_durations_us"):
        timing_means[field] = numeric_summary([value for timing in timings for value in timing[field]])
    return {
        "expected_or_observed_cases": len(rows), "completed_analysis_cases": len(complete),
        "pending_cases": sum(row["analysis_state"] == "PENDING" for row in rows),
        "software_pass_cases": sum(row["software_state"] == "PASS" for row in rows),
        "software_fail_cases": sum(row["software_state"] == "FAIL" for row in rows),
        "lifecycle_and_count_pass_cases": sum(row.get("lifecycle_and_count_pass") is True for row in complete),
        "all32_conversion_pass_cases": sum(row.get("all32_raw_to_logic_match") is True for row in complete),
        "reported_artifact_count_pass_cases": sum(row.get("reported_counts_match_artifacts") is True for row in complete),
        "trace_close_pass_cases": sum(row.get("trace_close_matches_artifacts") is True for row in complete),
        "poststop_quiet_pass_cases": sum(row.get("poststop_endpoint_became_quiet") is True for row in complete),
        "placement_diagnostic_anomaly_cases": sum(row.get("lane_activity_anomaly") is True for row in complete),
        "placement_diagnostic_expected_cases": sum(row.get("lane_activity_anomaly") is False for row in complete),
        "cases_with_lanes_high_beyond_D0_D1": sum(bool(row.get("raw_lanes_seen_high_outside_D0_D1")) for row in complete),
        "cases_missing_D0_or_D1_activity": sum(bool(row.get("required_D0_D1_missing_both_levels")) for row in complete),
        "initial_all32_then_displaced_pair_cases": sum(row.get("initial_all32_then_displaced_pair_windows") is True for row in complete),
        "initial_window_all32_cases": sum(row.get("initial_window_all32_lanes_seen_high") is True for row in complete),
        "first_raw_frame_unknown_lane_high_cases": sum(row.get("first_raw_frame_any_lane_high_outside_D0_D1") is True for row in complete),
        "first_raw_frame_all32_lanes_high_cases": sum(row.get("first_raw_frame_all32_lanes_seen_high") is True for row in complete),
        "actual_samples": sum(row["actual_samples"] or 0 for row in complete),
        "raw_to_logic_bit_comparisons": sum(row.get("raw_to_logic_bit_comparisons") or 0 for row in complete),
        "active_lane_pair_or_set_counts": dict(sorted(Counter(
            ",".join(map(str, row["raw_lanes_seen_both_levels"])) for row in complete).items())),
        "prearm_final_empty_read_counts": dict(sorted(Counter(str(timing["prearm_final_empty_read_count"]) for timing in timings).items())),
        "timing_us": timing_means,
    }


def summarize(directory):
    discovered_records = sorted(directory.glob("*.acquisition.json"))
    records, excluded_records = [], []
    for record_path in discovered_records:
        record = load_json(record_path)
        if record.get("policy") not in ("baseline", "two-empty"):
            excluded_records.append({
                "file": str(record_path), "sha256": sha256(record_path),
                "reason": "Reference/source-control acquisition or other non-A/B record: no recognized baseline/two-empty policy.",
                "record_policy": record.get("policy"),
                "record_sequence": record.get("sequence"),
                "record_repetitions": record.get("repetitions"),
            })
            continue
        # A recognized A/B policy must retain valid repetitions/sequence. An
        # invalid real A/B record is an error, not a silently excluded control.
        expected_cases(record)
        records.append(record_path)
    if not records:
        raise ValueError("No completed acquisition records found")
    blocks, cases = [], []
    for record_path in records:
        record = load_json(record_path)
        name = record.get("block")
        if not isinstance(name, str) or Path(name).name != name or record_path.name != name + ".acquisition.json":
            raise ValueError("Block identity does not match its acquisition record filename")
        expected = expected_cases(record)
        analysis_path = directory / (name + ".analysis.json")
        analysis = load_json(analysis_path) if analysis_path.is_file() else None
        analysis_rows = analysis.get("captures", []) if analysis else []
        by_name = {}
        duplicate_names = []
        for index, row in enumerate(analysis_rows):
            capture_name = row.get("capture")
            if capture_name in by_name:
                duplicate_names.append(capture_name)
                # Preserve duplicate evidence as an additional explicit FAIL row.
                by_name[f"{capture_name}#duplicate{index}"] = row
            else:
                by_name[capture_name] = row
        block_cases = [case_summary(record, by_name.pop(capture, None), (capture, rep, step, mode), directory)
                       for capture, rep, step, mode in expected]
        extra_names = list(by_name)
        for name_key, row in by_name.items():
            state = row.get("harness_state", {})
            extra = case_summary(record, row, (row.get("capture", name_key), state.get("repetition"), state.get("step"), state.get("mode")), directory)
            extra["software_state"] = "FAIL"
            extra["unexpected_analysis_case"] = True
            block_cases.append(extra)
        cases.extend(block_cases)
        software_state = "FAIL" if record.get("exit_code") != 0 or extra_names or duplicate_names or any(row["software_state"] == "FAIL" for row in block_cases) else "PENDING" if any(row["software_state"] == "PENDING" for row in block_cases) else "PASS"
        blocks.append({
            "block": record["block"], "policy": record.get("policy"), "sequence": record.get("sequence"),
            "repetitions": record.get("repetitions"), "expected_capture_count": len(expected),
            "analysis_capture_count": len(analysis_rows), "software_state": software_state,
            "analysis_state": "COMPLETE" if analysis else "PENDING",
            "acquisition_record_file": str(record_path), "acquisition_record_sha256": sha256(record_path),
            "analysis_file": str(analysis_path) if analysis else None,
            "analysis_sha256": sha256(analysis_path) if analysis else None,
            "analysis_analyzer_sha256": analysis.get("analyzer_sha256") if analysis else None,
            "duplicate_analysis_case_names": duplicate_names, "unexpected_analysis_case_names": extra_names,
            "acquisition_record": record, "block_elapsed_seconds": elapsed_seconds(record),
            "statistics": group_summary(block_cases),
        })
    groups = []
    for policy, mode in sorted({(row["policy"], row["mode"]) for row in cases}, key=lambda pair: tuple(map(str, pair))):
        rows = [row for row in cases if row["policy"] == policy and row["mode"] == mode]
        groups.append({"policy": policy, "mode": mode, **group_summary(rows)})
    overall = "FAIL" if any(block["software_state"] == "FAIL" for block in blocks) else "PENDING" if any(block["software_state"] == "PENDING" for block in blocks) else "PASS"
    return {
        "summary": "DLA32_drain_AB_every_case_software_separate_from_lane_diagnostics",
        "directory": str(directory.resolve()), "summarizer_sha256": sha256(Path(__file__)),
        "completed_acquisition_records": len(records), "software_state": overall,
        "discovered_acquisition_record_files": len(discovered_records),
        "excluded_non_ab_acquisition_records": excluded_records,
        "statistics": group_summary(cases), "policy_mode_groups": groups, "blocks": blocks, "cases": cases,
        "physical_lane_origin_validated": False, "lossless_physical_stream_verified": False,
        "lane_remapping_applied": False, "cause_inferred": False,
        "sampling_scope": "Acquisition records identify completed blocks. Directories without a completion record are not read.",
        "placement_scope": "Any RAW lane seen HIGH beyond D0/D1 or either known source lane lacking both levels is an anomaly diagnostic.",
        "window_scope": "Initial all32 then displaced pair uses the stored analyzer windows, not an exact sample-boundary claim.",
    }


def write_table(path, rows):
    fields = ("block", "policy", "sequence", "capture", "repetition", "step", "mode", "requested_samples",
              "actual_samples", "software_state", "lane_activity_placement", "lifecycle_and_count_pass",
              "all32_raw_to_logic_match", "reported_counts_match_artifacts", "poststop_endpoint_became_quiet",
              "raw_lanes_seen_both_levels", "raw_lanes_seen_high_outside_D0_D1", "required_D0_D1_missing_both_levels",
              "initial_all32_then_displaced_pair_windows", "observed_raw_lane_bits_or", "observed_logic_sample_bits_or",
              "initial_window_all32_lanes_seen_high", "first_raw_frame_hex", "first_raw_frame_lanes_seen_high",
              "first_raw_frame_all32_lanes_seen_high", "first_raw_frame_any_lane_high_outside_D0_D1",
              "logic_samples_with_bits_outside_D0_D1", "wire_bytes", "logic_bytes", "raw_sha256", "logic_sha256",
              "dll_sha256", "harness_sha256", "prearm_final_empty_read_count", "prearm_stop_to_setup_us",
              "setup_to_arm_us", "arm_to_first_sample_read_start_us", "arm_to_first_sample_read_completion_us",
              "prearm_total_read_duration_us", "poststop_total_read_duration_us")
    with path.open("x", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            combined = {**row, **(row.get("timing") or {})}
            values = {field: combined.get(field) for field in fields}
            for key, value in values.items():
                if isinstance(value, (list, dict)):
                    values[key] = json.dumps(value, separators=(",", ":"))
            writer.writerow(values)


def fixture_row(capture="rep001-step1-Stream", high=None, windows=None):
    high = [0, 1] if high is None else high
    return {"capture": capture, "harness_state": {"capture": capture, "repetition": 1, "step": 1,
                "mode": "Stream", "lifecycle_and_count_pass": True, "wire_file": "fixture.wire.bin"},
            "saved_samples": 8, "all32_raw_to_logic_match": True, "reported_counts_match_artifacts": True,
            "trace_close_matches_artifacts": True, "poststop_endpoint_became_quiet": True,
            "software_conversion_and_count_pass": True, "raw_to_logic_bit_comparisons": 256,
            "raw_lanes_seen_high": high, "raw_lanes_seen_both_levels": high,
            "activity_windows": windows or [], "driver_log_evidence": {},
            "wire_sha256": "a" * 64, "logic_sha256": "b" * 64}


def self_test():
    with tempfile.TemporaryDirectory(prefix="dla32-drain-summary-selftest-") as temp:
        directory = Path(temp)
        record = {"block": "fixture", "policy": "baseline", "repetitions": 1,
                  "sequence": "stream-only", "requested_samples_per_capture": 8, "exit_code": 0}
        acquisition = directory / "fixture.acquisition.json"
        analysis = directory / "fixture.analysis.json"
        (directory / "fixture").mkdir()
        (directory / "fixture" / "fixture.wire.bin").write_bytes(bytes([0x55, 0xaa]) + bytes(30))
        acquisition.write_text(json.dumps(record), encoding="utf-8-sig")
        (directory / "Kingst-reference.acquisition.json").write_text(json.dumps({"driver": "kingst-la2016", "sample_count": 8}), encoding="utf-8")
        pending = summarize(directory)
        assert pending["software_state"] == "PENDING" and pending["cases"][0]["actual_samples"] is None
        assert pending["statistics"]["software_pass_cases"] == 0
        assert pending["completed_acquisition_records"] == 1
        assert len(pending["excluded_non_ab_acquisition_records"]) == 1
        normal = fixture_row()
        analysis.write_text(json.dumps({"captures": [normal]}), encoding="utf-8")
        result = summarize(directory)
        assert result["software_state"] == "PASS" and result["statistics"]["placement_diagnostic_anomaly_cases"] == 0
        assert result["cases"][0]["first_raw_frame_expanded_sample_words_hex"] == ["00000001", "00000002"] * 4
        assert not result["cases"][0]["first_raw_frame_any_lane_high_outside_D0_D1"]
        shifted = fixture_row(high=[20, 21])
        shifted_frame = bytes(0x55 if lane == 20 else 0xaa if lane == 21 else 0 for lane in range(32))
        (directory / "fixture" / "fixture.wire.bin").write_bytes(shifted_frame)
        analysis.write_text(json.dumps({"captures": [shifted]}), encoding="utf-8")
        result = summarize(directory)
        assert result["software_state"] == "PASS" and result["statistics"]["placement_diagnostic_anomaly_cases"] == 1
        assert result["cases"][0]["required_D0_D1_missing_both_levels"] == [0, 1]
        assert result["cases"][0]["first_raw_frame_lanes_seen_high"] == [20, 21]
        assert result["statistics"]["first_raw_frame_unknown_lane_high_cases"] == 1
        initial = fixture_row(high=list(range(32)), windows=[{"raw_lanes_seen_high": list(range(32))}, {"raw_lanes_seen_high": [28, 29]}])
        analysis.write_text(json.dumps({"captures": [initial]}), encoding="utf-8")
        assert summarize(directory)["statistics"]["initial_all32_then_displaced_pair_cases"] == 1
        normal["all32_raw_to_logic_match"] = False
        analysis.write_text(json.dumps({"captures": [normal]}), encoding="utf-8")
        assert summarize(directory)["software_state"] == "FAIL"
        record["repetitions"] = 2
        acquisition.write_text(json.dumps(record), encoding="utf-8")
        analysis.write_text(json.dumps({"captures": [shifted]}), encoding="utf-8")
        result = summarize(directory)
        assert result["software_state"] == "PENDING" and len(result["cases"]) == 2
        assert result["statistics"]["pending_cases"] == 1
        record["sequence"] = "buffer-stream"
        acquisition.write_text(json.dumps(record), encoding="utf-8")
        analysis.write_text(json.dumps({"captures": []}), encoding="utf-8")
        assert len(summarize(directory)["cases"]) == 4
        record["exit_code"] = 1
        acquisition.write_text(json.dumps(record), encoding="utf-8")
        assert summarize(directory)["software_state"] == "FAIL"
        write_table(directory / "cases.csv", result["cases"])
        with (directory / "cases.csv").open(newline="", encoding="utf-8") as source:
            assert len(list(csv.DictReader(source))) == 2
    print("PASS summary self-test: pending not PASS, allcases, software vs shifted lanes, initialall32, conversion failure, failed block, CSV")


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--table", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        if args.directory is None:
            return 0
    if args.directory is None or args.report is None:
        parser.error("Provide completed-block directory and --report, optionally --table; or --self-test")
    if args.report.exists() or args.table is not None and args.table.exists():
        parser.error("Output report/table must be NEW files")
    result = summarize(args.directory)
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    if args.table:
        write_table(args.table, result["cases"])
    for group in result["policy_mode_groups"]:
        print(f"{group['policy']} {group['mode']}: software {group['software_pass_cases']}PASS/{group['software_fail_cases']}FAIL/"
              f"{group['pending_cases']}PENDING; placementdiagnostics {group['placement_diagnostic_anomaly_cases']}anomalous/"
              f"{group['placement_diagnostic_expected_cases']}expected; samples={group['actual_samples']}")
    print(f"Overall software state={result['software_state']}; physical origin/integrity remain unvalidated, no cause inferred.")
    return {"PASS": 0, "FAIL": 1, "PENDING": 2}[result["software_state"]]


if __name__ == "__main__":
    raise SystemExit(main())
