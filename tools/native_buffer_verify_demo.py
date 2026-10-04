"""Verify finalized official FNIRSI CSV/.demo pairs, without hardware access.

Only explicit .complete.json markers authorize reading/comparing a case. Files
without a completion marker are pending. A marker must anchor finalized CSV and
demo hashes directly or through its finished native-analysis report. This tool
does not change the existing native CSV parser or any captured artifact.
"""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from analyze_duty_capture import analyze_levels, capture_sha256
from native_buffer_analyze import inspect_demo, read_native_csv


BLOCK_BYTES = 1048576
CHANNELS = (0, 1)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def local_path(value, parent):
    path = Path(value)
    return path if path.is_absolute() else parent / path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def marker_inputs(marker_path):
    marker = read_json(marker_path)
    require(marker.get("completed") is True or marker.get("native_analysis_finished") is True,
            "Completion marker does not confirm finished exports")
    csv_path = local_path(marker["csv"], marker_path.parent)
    demo_path = local_path(marker["demo"], marker_path.parent)
    expected_csv = marker.get("csv_sha256")
    expected_demo = marker.get("demo_sha256")
    report_provenance = None
    if (expected_csv is None or expected_demo is None) and marker.get("report"):
        report_path = local_path(marker["report"], marker_path.parent)
        report = read_json(report_path)
        require(report.get("analysis") == "official_FNIRSI_waveform_event_CSV_offline_paired_cycles",
                "Unexpected completion analysis report format")
        require(Path(report["csv"]).resolve() == csv_path.resolve(),
                "Completion report CSV path differs from marker")
        require(Path(report["demo"]["path"]).resolve() == demo_path.resolve(),
                "Completion report demo path differs from marker")
        expected_csv = expected_csv or report["csv_sha256"]
        expected_demo = expected_demo or report["demo"]["sha256"]
        report_provenance = {"path": str(report_path.resolve()), "sha256": capture_sha256(report_path)}
    require(isinstance(expected_csv, str) and len(expected_csv) == 64 and
            isinstance(expected_demo, str) and len(expected_demo) == 64,
            "Completion marker lacks anchored CSV/demo hashes")
    require(csv_path.is_file() and demo_path.is_file(), "Completed CSV/demo files are missing")
    require(capture_sha256(csv_path).lower() == expected_csv.lower(),
            "CSV hash differs from completed export; no sample comparison performed")
    require(capture_sha256(demo_path).lower() == expected_demo.lower(),
            "Demo hash differs from completed export; no sample comparison performed")
    return marker, csv_path, demo_path, expected_csv.lower(), expected_demo.lower(), report_provenance


def validate_configuration(metadata, demo, rate):
    session = demo["session_metadata"]
    config = demo["saved_configuration"]
    settings = config["settingData"]
    samples = metadata["sample_count"]
    require(int(session["SamplingFrequency"]) * 1000 == rate,
            "CSV Fs differs from demo SamplingFrequency (native kHz units)")
    require(int(session["SamplingDepth"]) == samples, "CSV N differs from demo logical SamplingDepth")
    require(settings["setHz"] == rate, "CSV Fs differs from saved configuration setHz")
    require(Decimal(str(settings["setTime"])) * rate / 1000 == samples,
            "Saved rate/duration do not produce CSV logical N")
    require(settings["isBuffer"] is True and settings["RLE"] is False,
            "Expected Buffer without RLE for this comparison")
    require(config["isOne"] is True and config["isInstantly"] is False and config["isRoll"] is False,
            "Expected Single acquisition, not Immediate or Roll")
    require(config["isSimpleTrigger"] is True and config["isAdvancedTrigger"] is False,
            "Expected Simple trigger configuration")
    require(config["glitchRemoval"] == [], "Glitch removal is present in saved configuration")
    channel_settings = config["channelsSet"]
    require(len(channel_settings) == 32 and all(item["id"] == index and item["enable"] is True
            for index, item in enumerate(channel_settings)), "Expected all 32 physical input channels enabled")
    require(channel_settings[0]["triggerType"] == 1 and all(
        item["triggerType"] == 0 for item in channel_settings[1:]),
        "Expected D0 rising trigger only")
    position = Decimal(str(settings["triggerPosition"]))
    require(Decimal(session["TriggerSamplingDepth"]) == samples * position / 100,
            "Saved trigger depth differs from configured percentage")
    return {"session_name": session["SessionName"], "mode": "Buffer", "Fs_hz": rate,
            "logical_samples": samples, "duration_ms": settings["setTime"],
            "threshold_volts_saved": settings["thresholdLevel"],
            "trigger": "D0 rising", "trigger_position_percent": settings["triggerPosition"],
            "saved_trigger_depth_samples": int(session["TriggerSamplingDepth"]),
            "all_32_channels_enabled": True, "glitch_removal_disabled": True,
            "RLE": False, "acquisition": "Single",
            "saved_PWM_values_not_hardware_start_readback": config.get("pwmData")}


def compare_memory(payload, levels):
    require(len(payload) == BLOCK_BYTES, "Unexpected native channel allocation block length")
    mismatch_count = 0
    first = None
    last = None
    for index, csv_level in enumerate(levels):
        native_level = (payload[index // 8] >> (index % 8)) & 1
        if native_level != csv_level:
            mismatch_count += 1
            if first is None:
                first = {"sample": index, "CSV": csv_level, "demo": native_level}
            last = index
    return {"bit_order": "lsb_first", "compared_samples": len(levels),
            "mismatch_count": mismatch_count, "first_mismatch": first,
            "last_mismatch_sample": last}


def width_result(levels, rate, frequency, start, end):
    measured = analyze_levels(levels[start:end], rate, frequency, 0)
    high, low = measured["paired_high_samples"], measured["paired_low_samples"]
    return {"start_sample": start, "end_sample_exclusive": end,
            "start_ms": start * 1000 / rate, "end_ms_exclusive": end * 1000 / rate,
            "window_samples": end - start, "endpoint_partial_cycles_excluded": True,
            "complete_paired_cycles": measured["complete_paired_cycles"],
            "high_mean_ns": high["mean"] * 1e9 / rate if high["mean"] is not None else None,
            "low_mean_ns": low["mean"] * 1e9 / rate if low["mean"] is not None else None,
            "duty_percent": measured["paired_time_weighted_duty_percent"],
            "high_samples_histogram": high["histogram"], "low_samples_histogram": low["histogram"],
            "period_samples_histogram": measured["period_samples"]["histogram"]}


def verify_case(marker_path, frequency):
    marker, csv_path, demo_path, csv_sha, demo_sha, analysis_provenance = marker_inputs(marker_path)
    marker_sha = capture_sha256(marker_path)
    levels, rate, metadata = read_native_csv(csv_path, CHANNELS)
    demo = inspect_demo(demo_path)
    config = validate_configuration(metadata, demo, rate)
    results = []
    with zipfile.ZipFile(demo_path) as archive:
        for channel in CHANNELS:
            storage = demo["channel_storage_metadata"][str(channel)]
            require(storage["start_sample"] == 0, "Nonzero native channel start requires an explicit supported mapping")
            count = storage["stored_samples"]
            require(metadata["sample_count"] <= count <= BLOCK_BYTES * 8,
                    "Stored count is shorter than logical CSV or exceeds the verified single-block format")
            allocation_lines = archive.read(f"{channel}/channel.ini").decode("utf-8-sig").splitlines()[3:]
            require(allocation_lines and allocation_lines[0] == "18446744073709551614,0",
                    "Unexpected initial native block allocation mapping")
            name = f"{channel}/0-0.bin"
            payload = archive.read(name)  # zipfile checks this member's CRC on full read.
            comparison = compare_memory(payload, levels[channel])
            samples = len(levels[channel])
            halves = [width_result(levels[channel], rate, frequency,
                                   samples * part // 2, samples * (part + 1) // 2) for part in range(2)]
            tenths = [width_result(levels[channel], rate, frequency,
                                   samples * part // 10, samples * (part + 1) // 10) for part in range(10)]
            results.append({"channel": channel, "member": name,
                            "member_sha256": hashlib.sha256(payload).hexdigest(),
                            "member_CRC32": f"{archive.getinfo(name).CRC:08x}",
                            "allocation_bytes": len(payload), "stored_samples": count,
                            "stored_tail_samples_beyond_logical_CSV": count - samples,
                            "stored_bytes_ceil": (count + 7) // 8,
                            "allocation_bytes_beyond_stored_extent": len(payload) - (count + 7) // 8,
                            "stored_tail_not_used_to_force_alignment": True,
                            "exact_full_logical_prefix_comparison": comparison,
                            "full_capture_paired_measurement": width_result(levels[channel], rate, frequency, 0, samples),
                            "diagnostic_halves": halves, "diagnostic_tenths": tenths})
    require(capture_sha256(csv_path) == csv_sha and capture_sha256(demo_path) == demo_sha and
            capture_sha256(marker_path) == marker_sha, "An artifact changed during verification; result not accepted")
    equal = all(item["exact_full_logical_prefix_comparison"]["mismatch_count"] == 0 for item in results)
    return {"status": "METADATA_AND_ALL_LOGICAL_SAMPLES_EQUAL" if equal else "SAMPLE_MISMATCH",
            "scope_of_status": "export_format_equality_only_not_firmware_or_physical_timing_certification",
            "marker": str(marker_path.resolve()), "marker_sha256": marker_sha,
            "completion_analysis_report": analysis_provenance,
            "csv": str(csv_path.resolve()), "csv_sha256": csv_sha,
            "demo": str(demo_path.resolve()), "demo_sha256": demo_sha,
            "configuration": config, "CSV_metadata": metadata, "channels": results,
            "whole_capture_preserved": True,
            "segment_analysis": "halves_and_tenths_are_diagnostic_views_no_capture_samples_discarded",
            "physical_threshold_change_during_capture_established": False,
            "firmware_fault_established": False}


def scan(directory, frequency):
    cases = []
    markers = sorted(directory.glob("*.complete.json"))
    claimed_csv_paths = set()
    for marker in markers:
        try:
            claim = read_json(marker)
            if claim.get("csv"):
                claimed_csv_paths.add(local_path(claim["csv"], marker.parent).resolve())
            cases.append(verify_case(marker, frequency))
        except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
            cases.append({"status": "VERIFICATION_ERROR", "marker": str(marker.resolve()), "error": str(error)})
    pending = [{"csv": str(path.resolve()), "status": "PENDING_NO_EXPLICIT_COMPLETION_MARKER",
                "sample_comparison_performed": False} for path in sorted(directory.glob("*.csv"))
               if path.resolve() not in claimed_csv_paths]
    return {"analysis": "completed_native_FNIRSI_CSV_demo_strict_full_logical_prefix",
            "hardware_access": False, "diagnostic_only": True,
            "tool_sha256": capture_sha256(Path(__file__)),
            "frozen_CSV_parser_sha256": capture_sha256(Path(__file__).with_name("native_buffer_analyze.py")),
            "paired_analyzer_sha256": capture_sha256(Path(__file__).with_name("analyze_duty_capture.py")),
            "source_frequency_hz_nominal": frequency, "directory": str(directory.resolve()),
            "completed_marker_count": len(markers), "cases": cases, "pending": pending,
            "all_completed_cases_equal": bool(cases) and all(
                case["status"] == "METADATA_AND_ALL_LOGICAL_SAMPLES_EQUAL" for case in cases)}


def self_test():
    with tempfile.TemporaryDirectory(prefix="native-demo-prefix-offline-") as directory:
        base = Path(directory)
        csv_path = base / "known.csv"
        demo_path = base / "known.demo"
        marker_path = base / "known.complete.json"
        samples, rate = 500, 50000000
        csv_path.write_text("; Sample rate: 50 MHz\n; Sample count: 500\n"
                            "SystemTime, Time(s), channel 0, channel 1\n"
                            "t,0,0,1\n" + "".join(
                                f"t,{Decimal(index)/rate},{level},{1-level}\n"
                                for cycle in range(9) for index, level in
                                ((1+50*cycle, 1), (26+50*cycle, 0))) +
                            "t,0.000009020,1,0\n", encoding="utf-8")
        levels, _, _ = read_native_csv(csv_path, CHANNELS)
        config = {"settingData": {"setHz": rate, "setTime": 0.01, "isBuffer": True,
                                  "RLE": False, "thresholdLevel": 2.5, "triggerPosition": 50},
                  "isOne": True, "isInstantly": False, "isRoll": False,
                  "isSimpleTrigger": True, "isAdvancedTrigger": False, "glitchRemoval": [],
                  "channelsSet": [{"id": index, "enable": True, "triggerType": 1 if index == 0 else 0}
                                  for index in range(32)]}
        def make_demo(flip_sample=None, wrong_rate=False):
            with zipfile.ZipFile(demo_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("channel.ini", "SessionName=DLA32_Plus\nSamplingFrequency=" +
                                 ("100000" if wrong_rate else "50000") +
                                 "\nSamplingDepth=500\nTriggerSamplingDepth=250\n")
                archive.writestr("set.ini", "\n".join(key+"="+json.dumps(value) for key, value in config.items()))
                for channel in CHANNELS:
                    archive.writestr(f"{channel}/channel.ini", f"channel {channel}\n0\n508\n18446744073709551614,0\n")
                    payload = bytearray(BLOCK_BYTES)
                    for index, level in enumerate(levels[channel]):
                        payload[index//8] |= level << (index%8)
                    if channel == 0 and flip_sample is not None:
                        payload[flip_sample//8] ^= 1 << (flip_sample%8)
                    archive.writestr(f"{channel}/0-0.bin", payload)
        def marker():
            marker_path.write_text(json.dumps({"completed": True, "csv": csv_path.name,
                                   "demo": demo_path.name, "csv_sha256": capture_sha256(csv_path),
                                   "demo_sha256": capture_sha256(demo_path)}))
        make_demo()
        marker()
        result = verify_case(marker_path, 1000000)
        assert result["status"] == "METADATA_AND_ALL_LOGICAL_SAMPLES_EQUAL"
        assert all(row["stored_tail_samples_beyond_logical_CSV"] == 8 for row in result["channels"])
        assert all(row["exact_full_logical_prefix_comparison"]["compared_samples"] == 500 for row in result["channels"])
        make_demo(flip_sample=499)
        marker()
        result = verify_case(marker_path, 1000000)
        mismatch = result["channels"][0]["exact_full_logical_prefix_comparison"]
        assert result["status"] == "SAMPLE_MISMATCH" and mismatch["mismatch_count"] == 1
        assert mismatch["first_mismatch"]["sample"] == 499
        make_demo(wrong_rate=True)
        marker()
        try:
            verify_case(marker_path, 1000000)
        except ValueError as error:
            assert "Fs differs" in str(error)
        else:
            raise AssertionError("Mismatched Fs was accepted")
        make_demo()
        marker()
        csv_path.write_text(csv_path.read_text()+"\n")
        try:
            verify_case(marker_path, 1000000)
        except ValueError as error:
            assert "CSV hash differs" in str(error)
        else:
            raise AssertionError("Changed completed CSV was accepted")
        marker_path.unlink()
        result = scan(base, 1000000)
        assert result["cases"] == [] and len(result["pending"]) == 1
        assert result["pending"][0]["sample_comparison_performed"] is False
    return {"self_test": "PASS", "hardware_access": False,
            "checks": ["entire_logical_prefix_two_channels", "stored_tail_retained_as_metadata",
                       "last_logical_sample_corruption_detected", "Fs_mismatch_rejected",
                       "hash_changed_before_comparison_rejected", "unmarked_CSV_pending_without_comparison"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--frequency", type=int, default=1000000)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    if args.directory is None or args.frequency <= 0:
        parser.error("Specify --directory and a positive --frequency, or --self-test")
    if args.json and args.json.exists():
        parser.error(f"Refusing to overwrite existing output: {args.json}")
    report = scan(args.directory, args.frequency)
    if args.json:
        with args.json.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
    print(json.dumps(report if args.json is None else {
        "json": str(args.json), "completed_marker_count": report["completed_marker_count"],
        "all_completed_cases_equal": report["all_completed_cases_equal"],
        "pending_count": len(report["pending"]),
        "cases": [{"csv": case.get("csv"), "status": case["status"], "error": case.get("error"),
                   "config": case.get("configuration"),
                   "channels": [{"channel": row["channel"],
                                 "sample_comparison": row["exact_full_logical_prefix_comparison"],
                                 "stored_tail_samples": row["stored_tail_samples_beyond_logical_CSV"],
                                 "full": {key: row["full_capture_paired_measurement"][key] for key in
                                          ("complete_paired_cycles", "high_mean_ns", "low_mean_ns", "duty_percent")}}
                                for row in case.get("channels", [])]}
                  for case in report["cases"]]}))
    if any(case["status"] != "METADATA_AND_ALL_LOGICAL_SAMPLES_EQUAL" for case in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
