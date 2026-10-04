"""Aggregate paired PWM measurements from an acquisition manifest, offline.

Uses analyze_duty_capture's unchanged edge/cycle definitions. The quantization
intervals are a theoretical reference, never a new acceptance test. Captures
are compared at their actual saved length; Kingst chunk overshoot is retained.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import zipfile

from analyze_duty_capture import (analyze_levels, capture_sha256,
                                  histogram_summary, read_capture)


DEFAULT_NODES = {0: "ESP32_GPIO32", 1: "FNIRSI_PWM0"}
CSV_COLUMNS = (
    "device", "mode", "node", "channel", "repeat", "threshold_volts",
    "sampling_rate_hz", "source_frequency_hz", "source_nominal_duty_percent",
    "native_application", "exit_code", "requested_samples", "actual_samples",
    "sample_count_difference", "actual_duration_ms", "complete_paired_cycles",
    "high_mean_ns", "low_mean_ns", "period_mean_ns", "high_min_ns",
    "high_max_ns", "low_min_ns", "low_max_ns", "period_min_ns", "period_max_ns",
    "duty_percent", "duty_deviation_percentage_points", "frequency_hz",
    "frequency_deviation_ppm", "high_minus_low_mean_ns",
    "periods_more_than_one_sample_from_median_count", "strict_passed",
    "sample_word_bytes", "probes", "generator_commands_sent",
    "high_sample_histogram_json", "low_sample_histogram_json", "period_sample_histogram_json",
    "canonical_paired_report_equal", "capture_sha256", "capture",
)


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def manifest_records(path):
    payload = load_json(path)
    records = payload.get("captures") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Manifest must be a list or an object with a captures list")
    return records


def resolve_record_path(record, name, base):
    value = record.get(name)
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else base / path


def ns_summary(samples_summary, tick):
    result = dict(samples_summary)
    for name in ("minimum", "maximum", "span", "mean", "median"):
        value = result[name]
        result[name] = value * tick if value is not None else None
    result["histogram"] = [
        {"samples": int(width), "ns": int(width) * tick, "count": count}
        for width, count in samples_summary["histogram"].items()
    ]
    return result


def quantization_reference(rate, frequency, nominal_duty):
    tick = 1e9 / rate
    period = 1e9 / frequency
    high = period * nominal_duty / 100
    low = period - high
    nominal_samples = rate / frequency
    return {
        "classification": "theoretical_reference_only_no_acceptance_verdict",
        "assumptions": [
            "Uniform ideal sampling grid; each edge timestamp is quantized to its next sample.",
            "Source frequency/duty and declared sampling rate are exact for this model only.",
            "No comparator asymmetry, threshold-crossing shift, hysteresis, or missed transitions.",
            "Clock phase can be correlated; averaging need not remove systematic offsets.",
            "HIGH, LOW and period errors share edge timestamps and cannot be assigned independently.",
        ],
        "sample_tick_ns": tick,
        "nominal_period_ns": period,
        "nominal_high_ns": high,
        "nominal_low_ns": low,
        "conservative_edge_pair_width_error_ns": tick,
        "high_reference_ns": [max(0, high - tick), high + tick],
        "low_reference_ns": [max(0, low - tick), low + tick],
        "period_reference_ns": [max(0, period - tick), period + tick],
        "high_minus_low_quantization_error_bound_ns": 2 * tick,
        "one_sample_duty_step_percentage_points_at_nominal_period": 100 / nominal_samples,
        "conservative_50_percent_duty_error_bound_percentage_points":
            100 / (nominal_samples - 1)
            if nominal_duty == 50 and nominal_samples > 1 else None,
        "strict_verdict_changed": False,
    }


def canonical_comparison(path, channel, rate, frequency, sha, measured):
    report = path.with_suffix(f".D{channel}.paired.json")
    if not report.exists():
        return {"report": None, "equal": None}
    previous = load_json(report)
    expected = {"capture_sha256": sha, "channel_physical_bit": channel,
                "sampling_rate_hz": rate, "nominal_source_frequency_hz": frequency}
    mismatches = [key for key, value in expected.items() if previous.get(key) != value]
    mismatches.extend(key for key, value in measured.items()
                      if key != "first_imbalanced_cycle_examples"
                      and previous.get(key) != value)
    return {"report": str(report.resolve()), "equal": not mismatches,
            "mismatched_fields": mismatches}


def saved_strict_result(path, channel, rate, frequency, samples):
    report = path.with_suffix(f".D{channel}.strict.json")
    if not report.exists():
        return {"report": None, "passed": None, "executed_by_this_tool": False}
    previous = load_json(report)
    expected = {"channel": channel, "samplerate_hz": rate,
                "nominal_frequency_hz": frequency, "samples": samples}
    mismatches = [key for key, value in expected.items() if previous.get(key) != value]
    if mismatches:
        raise ValueError(f"Strict report configuration mismatch: {report}: {mismatches}")
    return {"report": str(report.resolve()), "passed": previous.get("passed"),
            "executed_by_this_tool": False,
            "provenance": "existing_report_preserved_not_recalculated_no_hash_in_original_report"}


def analyze_record(record, base, channels):
    capture = resolve_record_path(record, "output", base)
    if capture is None:
        capture = resolve_record_path(record, "capture", base)
    if capture is None:
        raise ValueError("Manifest record lacks output/capture path")
    sha = capture_sha256(capture)
    frequency = record.get("source_frequency_hz", 1000000)
    nominal_duty = record.get("source_nominal_duty_percent", 50)
    if frequency <= 0 or not 0 < nominal_duty < 100:
        raise ValueError("Expected positive source frequency and 0 < nominal duty < 100")
    device = record.get("device", "unrecorded")
    mode = record.get("mode", "Buffer" if device == "FNIRSI-Buffer" else "unrecorded")
    log = resolve_record_path(record, "log", base)
    provenance = {"manifest_record": record,
                  "capture_log": str(log.resolve()) if log else None,
                  "capture_log_sha256": capture_sha256(log) if log and log.exists() else None}
    rows = []
    for channel in channels:
        levels, rate, metadata = read_capture(capture, channel)
        expected_rate = record.get("samplerate_hz")
        if expected_rate is not None and expected_rate != rate:
            raise ValueError(f"Manifest/metadata samplerate mismatch for {capture}")
        measured = analyze_levels(levels, rate, frequency, 0)
        tick = measured["sample_tick_ns"]
        high = ns_summary(measured["paired_high_samples"], tick)
        low = ns_summary(measured["paired_low_samples"], tick)
        period = ns_summary(measured["period_samples"], tick)
        duty = measured["paired_time_weighted_duty_percent"]
        measured_frequency = measured["measured_frequency_hz_from_declared_samplerate"]
        requested = record.get("requested_samples")
        canonical = canonical_comparison(capture, channel, rate, frequency, sha, measured)
        if canonical["equal"] is False:
            raise ValueError(f"Canonical paired report mismatch: {canonical}")
        strict = saved_strict_result(capture, channel, rate, frequency, len(levels))
        row = {
            "device": device, "mode": mode, "node":
                record.get("channel_nodes", {}).get(str(channel), DEFAULT_NODES.get(channel, f"bit{channel}")),
            "channel": channel, "channel_label": metadata["channel_label"],
            "repeat": record.get("repeat"), "threshold_volts": record.get("threshold_volts"),
            "sampling_rate_hz": rate, "source_frequency_hz": frequency,
            "source_nominal_duty_percent": nominal_duty,
            "native_application": record.get("native_application"),
            "exit_code": record.get("exit_code"), "requested_samples": requested,
            "actual_samples": len(levels),
            "sample_count_difference": len(levels) - requested if requested is not None else None,
            "requested_duration_ms": 1000 * requested / rate if requested is not None else None,
            "actual_duration_ms": 1000 * len(levels) / rate,
            "complete_paired_cycles": measured["complete_paired_cycles"],
            "high_mean_ns": high["mean"], "low_mean_ns": low["mean"],
            "period_mean_ns": period["mean"],
            "high_min_ns": high["minimum"], "high_max_ns": high["maximum"],
            "low_min_ns": low["minimum"], "low_max_ns": low["maximum"],
            "period_min_ns": period["minimum"], "period_max_ns": period["maximum"],
            "duty_percent": duty,
            "duty_deviation_percentage_points": duty - nominal_duty if duty is not None else None,
            "frequency_hz": measured_frequency,
            "frequency_deviation_ppm": (measured_frequency / frequency - 1) * 1e6
                if measured_frequency is not None else None,
            "high_minus_low_mean_ns": measured["mean_high_minus_low_ns"],
            "periods_more_than_one_sample_from_median_count":
                measured["diagnostic_flags"]["periods_more_than_one_sample_from_median_count"],
            "strict_passed": strict["passed"], "strict_result": strict,
            "canonical_paired_report_equal": canonical["equal"],
            "canonical_paired_report": canonical,
            "capture_sha256": sha, "capture": str(capture.resolve()),
            "sample_word_bytes": metadata["sample_word_bytes"], "probes": metadata["probes"],
            "generator_commands_sent": record.get("generator_commands_sent"),
            "high_sample_histogram_json": json.dumps(measured["paired_high_samples"]["histogram"]),
            "low_sample_histogram_json": json.dumps(measured["paired_low_samples"]["histogram"]),
            "period_sample_histogram_json": json.dumps(measured["period_samples"]["histogram"]),
            "metadata": metadata, "provenance": provenance,
            "high_ns": high, "low_ns": low, "period_ns": period,
            "paired_measurements": measured,
            "paired_cycle_quantization_reference": quantization_reference(rate, frequency, nominal_duty),
        }
        rows.append(row)
    return rows


def scalar_range(values):
    values = [value for value in values if value is not None]
    return {"minimum": min(values), "maximum": max(values),
            "span": max(values) - min(values), "unweighted_mean": sum(values) / len(values)} if values else None


def aggregate(rows):
    grouped = defaultdict(list)
    keys = ("device", "mode", "node", "channel", "sampling_rate_hz", "threshold_volts",
            "source_frequency_hz", "source_nominal_duty_percent", "native_application")
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    groups = []
    for group_key, members in grouped.items():
        group = dict(zip(keys, group_key))
        group.update({"captures": len(members),
                      "capture_paths": [member["capture"] for member in members],
                      "actual_samples": [member["actual_samples"] for member in members],
                      "actual_duration_ms": [member["actual_duration_ms"] for member in members],
                      "complete_paired_cycles": sum(member["complete_paired_cycles"] for member in members),
                      "strict_results": [member["strict_passed"] for member in members]})
        for scalar in ("high_mean_ns", "low_mean_ns", "period_mean_ns", "duty_percent",
                       "frequency_hz", "high_minus_low_mean_ns"):
            group["between_capture_" + scalar] = scalar_range([member[scalar] for member in members])
        for kind in ("paired_high_samples", "paired_low_samples", "period_samples"):
            hist = Counter()
            for member in members:
                hist.update({int(width): count for width, count in
                             member["paired_measurements"][kind]["histogram"].items()})
            group["pooled_" + kind] = histogram_summary(hist)
        high = group["pooled_paired_high_samples"]["mean"]
        low = group["pooled_paired_low_samples"]["mean"]
        group["pooled_time_weighted_duty_percent"] = 100 * high / (high + low) if high is not None else None
        groups.append(group)
    return groups


def matched_comparisons(groups):
    comparisons = []
    match_keys = ("node", "channel", "sampling_rate_hz", "threshold_volts",
                  "source_frequency_hz", "source_nominal_duty_percent", "native_application")
    for fn in (group for group in groups if group["device"] == "FNIRSI-Buffer"):
        for kingst in (group for group in groups if group["device"] == "Kingst"):
            if all(fn[key] == kingst[key] for key in match_keys):
                def difference(name):
                    a, b = fn["between_capture_" + name], kingst["between_capture_" + name]
                    return a["unweighted_mean"] - b["unweighted_mean"] if a and b else None
                comparisons.append({**{key: fn[key] for key in match_keys},
                                    "fnirsi_captures": fn["captures"], "kingst_captures": kingst["captures"],
                                    "fnirsi_minus_kingst_high_mean_ns": difference("high_mean_ns"),
                                    "fnirsi_minus_kingst_low_mean_ns": difference("low_mean_ns"),
                                    "fnirsi_minus_kingst_duty_percentage_points": difference("duty_percent"),
                                    "simultaneous_capture_established": False,
                                    "common_declared_threshold_is_not_comparator_calibration": True,
                                    "actual_duration_ms_fnirsi": fn["actual_duration_ms"],
                                    "actual_duration_ms_kingst": kingst["actual_duration_ms"]})
    return comparisons


def build_report(records, base, channels, manifest=None):
    rows = []
    for record in records:
        rows.extend(analyze_record(record, base, channels))
    groups = aggregate(rows)
    return {
        "analysis": "buffer_comparison_paired_cycles_offline",
        "classification": "diagnostic_only_no_new_pass_criteria",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest.resolve()) if manifest else None,
        "manifest_sha256": capture_sha256(manifest) if manifest else None,
        "analysis_tool_sha256": capture_sha256(Path(__file__)),
        "paired_analyzer_sha256": capture_sha256(Path(__file__).with_name("analyze_duty_capture.py")),
        "capture_count": len(records), "channel_row_count": len(rows),
        "firmware_fault_established": False, "independent_clock_calibration": False,
        "whole_period_loss_detectable_from_periodic_pwm_alone": False,
        "native_application_capture_count": sum(record.get("native_application") is True for record in records),
        "native_reproduction_status": "pending" if not any(record.get("native_application") is True
                                                              for record in records) else "data_present_not_automatically_validated",
        "sampling_rate_gaps_interpretation": "Period outliers are diagnostic; regular PWM cannot certify absence of whole-period loss.",
        "termination_interpretation": "Actual saved samples/duration are retained; exit zero does not certify exact requested count or a trigger.",
        "source_nodes_provenance": "Default wiring map from this experiment: physical bit0 ESP32 GPIO32, bit1 FNIRSI PWM0; optional manifest channel_nodes overrides it.",
        "rows": rows, "groups": groups, "matched_device_comparisons": matched_comparisons(groups),
    }


def self_test():
    expected = b"\0" + (b"\1" * 50 + b"\0" * 50) * 8 + b"\1"
    with tempfile.TemporaryDirectory(prefix="buffer-comparison-offline-") as directory:
        base = Path(directory)
        for unitsize in (1, 2, 4):
            highest = unitsize * 8 - 1
            path = base / f"known{unitsize}.sr"
            text = ("[device 1]\ncapturefile=logic-1\n"
                    f"unitsize={unitsize}\ntotal probes={unitsize * 8}\nsamplerate=100 MHz\n")
            payload = b"".join(((1 | 1 << highest) if value else 0).to_bytes(unitsize, "little")
                               for value in expected)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("metadata", text)
                split = 100 * unitsize
                archive.writestr("logic-1-2", payload[split:])
                archive.writestr("logic-1-1", payload[:split])
            record = {"device": "fixture", "output": str(path), "samplerate_hz": 100000000,
                      "requested_samples": 400, "source_frequency_hz": 1000000,
                      "native_application": False}
            rows = analyze_record(record, base, [0, highest])
            for row in rows:
                assert row["actual_samples"] == 802 and row["sample_count_difference"] == 402
                assert row["complete_paired_cycles"] == 8
                assert row["high_mean_ns"] == row["low_mean_ns"] == 500
                assert row["period_mean_ns"] == 1000 and row["duty_percent"] == 50
                assert row["metadata"]["sample_word_bytes"] == unitsize
                assert row["strict_passed"] is None
                assert row["paired_cycle_quantization_reference"]["strict_verdict_changed"] is False
                assert row["paired_measurements"] == analyze_levels(expected, 100000000, 1000000, 0)
            duplicate = aggregate(rows + rows)
            assert all(group["captures"] == 2 and group["complete_paired_cycles"] == 16 for group in duplicate)
            invalid = dict(record, samplerate_hz=50000000)
            try:
                analyze_record(invalid, base, [0])
            except ValueError:
                pass
            else:
                raise AssertionError("Manifest rate mismatch was accepted")
        reference = quantization_reference(50000000, 1000000, 50)
        assert reference["high_reference_ns"] == [480, 520]
        assert reference["high_minus_low_quantization_error_bound_ns"] == 40
        reference = quantization_reference(250000000, 1000000, 25)
        assert reference["nominal_high_ns"] == 250
        assert reference["conservative_50_percent_duty_error_bound_percentage_points"] is None
    return {"self_test": "PASS", "hardware_access": False,
            "fixture_word_bits": [8, 16, 32], "count_overshoot_retained": True,
            "paired_analyzer_equivalence": True, "rate_mismatch_rejected": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--directory", type=Path)
    source.add_argument("--manifest", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--channels", default="0,1")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    manifest = args.manifest or (args.directory / "acquisition-manifest.json" if args.directory else None)
    if manifest is None:
        parser.error("Specify --directory or --manifest, or --self-test")
    output_json = args.json or manifest.parent / "buffer-comparison-analysis.json"
    output_csv = args.csv or manifest.parent / "buffer-comparison-analysis.csv"
    if output_json.resolve() == output_csv.resolve():
        parser.error("JSON and CSV output paths must differ")
    for output in (output_json, output_csv):
        if output.exists():
            parser.error(f"Refusing to overwrite existing output: {output}")
    channels = [int(value) for value in args.channels.split(",")]
    report = build_report(manifest_records(manifest), manifest.parent, channels, manifest)
    with output_json.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    with output_csv.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["rows"])
    print(json.dumps({"captures": report["capture_count"], "rows": report["channel_row_count"],
                      "groups": len(report["groups"]), "json": str(output_json), "csv": str(output_csv),
                      "native_reproduction_status": report["native_reproduction_status"]}))


if __name__ == "__main__":
    main()
