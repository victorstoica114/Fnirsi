"""Check unchanged all32 RAW prefixes against same-acquisition logic.bin files.

The 32 lane bytes in each frame expand into eight little-endian uint32 samples.
This implementation uses independent Python bit placement, no DLL decoder,
guessed start offset, rotation or remapping. Only complete saved sample frames
are compared; read overshoot and incomplete RAW tails are retained and hashed.
Lane diagnostics do not establish framing origin or lossless physical capture.
"""
from argparse import ArgumentParser
from array import array
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

CHUNK_FRAMES = 16384  # 512 KiB input, 512 KiB expected, 131072 samples.
_SCATTER = tuple(sum(((value >> sample) & 1) << (sample * 32)
                     for sample in range(8)) for value in range(256))
_LANE_TABLES = tuple(tuple(value << lane for value in _SCATTER) for lane in range(32))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_block(raw):
    """Bounded bitplane expansion; raw byte lane i always means physical Di."""
    if len(raw) % 32:
        raise ValueError("Independent expansion requires complete 32-byte RAW frames")
    out = bytearray(len(raw))
    tables = _LANE_TABLES
    for offset in range(0, len(raw), 32):
        value = 0
        for lane in range(32):
            value |= tables[lane][raw[offset + lane]]
        out[offset:offset + 32] = value.to_bytes(32, "little")
    return out


def words(block):
    result = array("I")
    if result.itemsize != 4:
        raise ValueError("This Python platform has a non-32-bit unsigned int array")
    result.frombytes(block)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def compare_files(wire_path, logic_path, *, chunk_frames=CHUNK_FRAMES):
    if chunk_frames < 1:
        raise ValueError("chunk_frames must be positive")
    logic_bytes = logic_path.stat().st_size
    wire_bytes = wire_path.stat().st_size
    if not logic_bytes or logic_bytes % 32:
        raise ValueError("Saved logic must contain a positive complete multiple of eight uint32 samples")
    if wire_bytes < logic_bytes:
        raise ValueError("RAW file is shorter than the complete saved logic prefix")
    samples = logic_bytes // 4
    lane_histograms = [Counter() for _ in range(32)]
    mismatch_counts = [0] * 32
    first_mismatch = [None] * 32
    window_diagnostics = []
    wire_hash, wire_prefix_hash, logic_hash = (hashlib.sha256() for _ in range(3))
    observed_logic_bits = 0
    samples_outside_fixture = 0
    consumed = 0
    differing_words = 0
    with wire_path.open("rb") as wire, logic_path.open("rb") as logic:
        while consumed < logic_bytes:
            length = min(chunk_frames * 32, logic_bytes - consumed)
            raw = wire.read(length)
            stored = logic.read(length)
            if len(raw) != length or len(stored) != length:
                raise ValueError("Input file changed or was truncated during comparison")
            wire_hash.update(raw)
            wire_prefix_hash.update(raw)
            logic_hash.update(stored)
            expected = decode_block(raw)
            sample_offset = consumed // 4
            window_lane_or = 0
            for lane in range(32):
                histogram = Counter(raw[lane::32])
                lane_histograms[lane].update(histogram)
                if any(value for value in histogram):
                    window_lane_or |= 1 << lane
            stored_words = words(stored)
            for word in stored_words:
                observed_logic_bits |= word
                samples_outside_fixture += bool(word & 0xfffffffc)
            if expected != stored:
                expected_words = words(expected)
                for index, (want, actual) in enumerate(zip(expected_words, stored_words)):
                    difference = want ^ actual
                    if not difference:
                        continue
                    differing_words += 1
                    while difference:
                        lowest = difference & -difference
                        lane = lowest.bit_length() - 1
                        mismatch_counts[lane] += 1
                        if first_mismatch[lane] is None:
                            first_mismatch[lane] = sample_offset + index
                        difference ^= lowest
            window_diagnostics.append({
                "first_sample": sample_offset,
                "samples": length // 4,
                "raw_lanes_seen_high": [i for i in range(32) if window_lane_or & (1 << i)],
                "raw_lanes_seen_high_mask": f"{window_lane_or:08x}",
            })
            consumed += length
        for tail in iter(lambda: wire.read(1024 * 1024), b""):
            wire_hash.update(tail)
        if logic.read(1):
            raise ValueError("Logic file grew during comparison")
    if wire_path.stat().st_size != wire_bytes or logic_path.stat().st_size != logic_bytes:
        raise ValueError("Input size changed during comparison")
    lanes = []
    for lane, histogram in enumerate(lane_histograms):
        high = sum(value.bit_count() * count for value, count in histogram.items())
        lanes.append({
            "physical_lane": lane,
            "samples": samples,
            "high_samples": high,
            "low_samples": samples - high,
            "seen_high": high > 0,
            "seen_low": high < samples,
            "seen_both_levels": 0 < high < samples,
            "raw_lane_byte_histogram": {f"{value:02x}": count for value, count in sorted(histogram.items())},
            "raw_to_logic_mismatching_samples": mismatch_counts[lane],
            "first_raw_to_logic_mismatch_sample": first_mismatch[lane],
            "raw_to_logic_match": mismatch_counts[lane] == 0,
        })
    raw_or = sum(1 << row["physical_lane"] for row in lanes if row["seen_high"])
    return {
        "comparison": "unchanged_raw_prefix_vs_same_acquisition_logic_all32",
        "wire_file": str(wire_path),
        "logic_file": str(logic_path),
        "wire_sha256": wire_hash.hexdigest(),
        "compared_wire_prefix_sha256": wire_prefix_hash.hexdigest(),
        "logic_sha256": logic_hash.hexdigest(),
        "wire_bytes": wire_bytes,
        "logic_bytes": logic_bytes,
        "saved_samples": samples,
        "compared_wire_prefix_bytes": logic_bytes,
        "extra_wire_bytes_after_saved_samples": wire_bytes - logic_bytes,
        "wire_tail_mod32": wire_bytes % 32,
        "chunk_frames": chunk_frames,
        "all32_raw_to_logic_match": not any(mismatch_counts),
        "raw_to_logic_bit_comparisons": samples * 32,
        "raw_to_logic_differing_words": differing_words,
        "observed_raw_lane_bits_or": f"{raw_or:08x}",
        "observed_logic_sample_bits_or": f"{observed_logic_bits:08x}",
        "raw_lanes_seen_high": [row["physical_lane"] for row in lanes if row["seen_high"]],
        "raw_lanes_seen_both_levels": [row["physical_lane"] for row in lanes if row["seen_both_levels"]],
        "raw_lanes_constant_low": [row["physical_lane"] for row in lanes if not row["seen_high"]],
        "raw_lanes_constant_high": [row["physical_lane"] for row in lanes if not row["seen_low"]],
        "raw_lanes_seen_high_outside_known_fixture_D0_D1": [row["physical_lane"] for row in lanes if row["seen_high"] and row["physical_lane"] > 1],
        "logic_samples_with_bits_outside_known_fixture_D0_D1": samples_outside_fixture,
        "lanes": lanes,
        "activity_windows": window_diagnostics,
        "independent_bitplane_expansion": True,
        "raw_prefix_skipped_bytes": 0,
        "lane_remapping_applied": False,
        "lane_diagnostics_are_acceptance_verdict": False,
        "frame_origin_validated": False,
        "signal_integrity_verified": False,
    }


def read_log_evidence(path):
    text = path.read_text(encoding="utf-8")
    reads = []
    pattern = re.compile(r"WIRE read: phase ([a-z-]+), start_us (-?\d+), duration_us (-?\d+), "
                         r"status (-?\d+), requested (\d+), timeout_ms (\d+), count (-?\d+), "
                         r"count_mod32 (-?\d+), payload_offset (\d+), prefix32 ([0-9a-f]*)\.")
    for match in pattern.finditer(text):
        values = match.groups()
        reads.append({
            "phase": values[0],
            **dict(zip(("start_us", "duration_us", "status", "requested", "timeout_ms", "count", "count_mod32", "payload_offset"), map(int, values[1:9]))),
            "prefix32": values[9],
        })
    prearm = [row for row in reads if row["phase"] == "pre-arm-drain"]
    empty_streak = 0
    for row in reversed(prearm):
        if row["status"] in (0, -7) and row["count"] == 0:
            empty_streak += 1
        else:
            break
    closed = re.findall(r"WIRE trace closed: (\d+) decoder-input bytes, tail (\d+), failed (\d+)\.", text)
    error_lines = [line for line in text.splitlines() if re.search(r"\blevel=1\s", line)]
    unsafe_warning_lines = [line for line in text.splitlines() if re.search(r"\blevel=2\s", line) and any(
        warning in line for warning in ("Cannot allocate the post-stop drain buffer.",
                                       "Post-stop drain failed:", "Post-stop endpoint did not become quiet",
                                       "Failed to cancel USB transfer:"))]
    poststop = re.findall(r"WIRE post-stop drain summary: discarded (\d+), mod32 (\d+), empty_reads (\d+)\.", text)
    return {
        "file": str(path), "sha256": sha256(path),
        "wire_reads": reads,
        "prearm_reads": len(prearm),
        "prearm_discarded_bytes_from_reads": sum(row["count"] for row in prearm if row["count"] > 0),
        "prearm_final_consecutive_empty_reads": empty_streak,
        "prearm_final_empty_statuses": [row["status"] for row in prearm[-empty_streak:]] if empty_streak else [],
        "commands": [{"opcode_hex": op, "elapsed_us": int(elapsed), "prefix54": prefix}
                     for op, elapsed, prefix in re.findall(r"WIRE command: opcode ([0-9a-f]+), elapsed_us (-?\d+), prefix54 ([0-9a-f]+)", text)],
        "wire_trace_close_records": [{"bytes": int(count), "tail": int(tail), "failed": int(failed)} for count, tail, failed in closed],
        "driver_error_log_lines": error_lines,
        "driver_error_log_count": len(error_lines),
        "unsafe_transport_warning_log_lines": unsafe_warning_lines,
        "poststop_drain_summaries": [{"discarded_bytes": int(count), "mod32": int(mod), "empty_reads": int(empty)}
                                    for count, mod, empty in poststop],
        "transport_read_failures": [row for row in reads if row["status"] not in (0, -7) or row["count"] < 0 or row["count"] > row["requested"]],
        "summary_lines": [line for line in text.splitlines() if "drain summary:" in line],
    }


def contained_file(directory, name):
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Capture file names must stay in their capture directory")
    path = directory / name
    if not path.is_file():
        raise ValueError(f"Missing acquisition artifact: {name}")
    return path


def analyze_directory(directory):
    rows = []
    for state_path in sorted(directory.glob("rep*-step*-*.json")):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("unitsize") != 4 or state.get("physical_channel_mask") != "ffffffff":
            raise ValueError("Harness state must describe the unchanged all32/4-byte capture")
        result = compare_files(contained_file(directory, state["wire_file"]), contained_file(directory, state["logic_file"]))
        result["capture"] = state["capture"]
        result["harness_state"] = state
        result["harness_state_sha256"] = sha256(state_path)
        result["driver_log_evidence"] = read_log_evidence(contained_file(directory, state["driver_log_file"]))
        result["reported_counts_match_artifacts"] = (state["actual_samples"] == result["saved_samples"] and
            state["written_logic_bytes"] == result["logic_bytes"] and state["wire_trace_bytes"] == result["wire_bytes"])
        closes = result["driver_log_evidence"]["wire_trace_close_records"]
        result["trace_close_matches_artifacts"] = len(closes) == 1 and closes[0]["failed"] == 0 and closes[0]["bytes"] == result["wire_bytes"] and closes[0]["tail"] == result["wire_tail_mod32"]
        poststop = result["driver_log_evidence"]["poststop_drain_summaries"]
        result["poststop_endpoint_became_quiet"] = len(poststop) == 1 and poststop[0]["empty_reads"] >= 2
        result["software_conversion_and_count_pass"] = (result["all32_raw_to_logic_match"] and
            result["reported_counts_match_artifacts"] and result["trace_close_matches_artifacts"] and
            state["lifecycle_and_count_pass"] and not result["driver_log_evidence"]["driver_error_log_count"] and
            not result["driver_log_evidence"]["transport_read_failures"] and
            not result["driver_log_evidence"]["unsafe_transport_warning_log_lines"] and
            result["poststop_endpoint_became_quiet"])
        rows.append(result)
        print(f"{result['capture']}: all32_conversion={result['all32_raw_to_logic_match']} "
              f"samples={result['saved_samples']} raw_active={result['raw_lanes_seen_both_levels']} "
              f"prearm_empty={result['driver_log_evidence']['prearm_final_consecutive_empty_reads']}", flush=True)
    if not rows:
        raise ValueError("No repeated harness capture reports found")
    return {
        "analysis": "DLA32_drain_AB_no_rotation_all32_same_acquisition",
        "directory": str(directory.resolve()),
        "analyzer_sha256": sha256(Path(__file__)),
        "captures": rows,
        "capture_count": len(rows),
        "all_software_conversion_and_count_pass": all(row["software_conversion_and_count_pass"] for row in rows),
        "raw_to_logic_bit_comparisons": sum(row["raw_to_logic_bit_comparisons"] for row in rows),
        "lossless_physical_stream_verified": False,
        "frame_origin_validated": False,
        "lane_remapping_applied": False,
    }


def self_test():
    # Explicit per-sample/per-lane reference independent of the table expansion.
    raw = bytes((lane * 73 + frame * 41) % 256 for frame in range(5) for lane in range(32))
    reference = b"".join(sum(((raw[frame * 32 + lane] >> sample) & 1) << lane for lane in range(32)).to_bytes(4, "little")
                         for frame in range(5) for sample in range(8))
    assert decode_block(raw) == reference
    with tempfile.TemporaryDirectory(prefix="dla32-drain-ab-selftest-") as temp:
        directory = Path(temp)
        wire, logic = directory / "wire.bin", directory / "logic.bin"
        wire.write_bytes(raw + bytes(range(23)))
        logic.write_bytes(reference)
        for frames in (1, 3, CHUNK_FRAMES):
            result = compare_files(wire, logic, chunk_frames=frames)
            assert result["all32_raw_to_logic_match"] and result["raw_to_logic_bit_comparisons"] == 1280
            assert result["extra_wire_bytes_after_saved_samples"] == 23 and result["wire_tail_mod32"] == 23
            assert result["wire_sha256"] == sha256(wire) and result["logic_sha256"] == sha256(logic)
        changed = bytearray(reference)
        changed[13 * 4 + 3] ^= 0x80
        logic.write_bytes(changed)
        result = compare_files(wire, logic, chunk_frames=1)
        assert not result["all32_raw_to_logic_match"]
        assert result["lanes"][31]["raw_to_logic_mismatching_samples"] == 1
        assert result["lanes"][31]["first_raw_to_logic_mismatch_sample"] == 13
        assert result["raw_to_logic_differing_words"] == 1
        shifted_raw = bytes(0x55 if lane == 20 else 0xaa if lane == 21 else 0 for lane in range(32))
        wire.write_bytes(shifted_raw)
        logic.write_bytes(decode_block(shifted_raw))
        result = compare_files(wire, logic)
        assert result["all32_raw_to_logic_match"] and result["raw_lanes_seen_both_levels"] == [20, 21]
        assert result["logic_samples_with_bits_outside_known_fixture_D0_D1"] == 8
        assert not result["frame_origin_validated"] and not result["signal_integrity_verified"]
        wire.write_bytes(shifted_raw[:-1])
        try:
            compare_files(wire, logic)
        except ValueError:
            pass
        else:
            raise AssertionError("A truncated RAW prefix was accepted")
        wire.write_bytes(shifted_raw[1:] + shifted_raw[:1])
        result = compare_files(wire, logic)
        assert not result["all32_raw_to_logic_match"] and not result["lane_remapping_applied"]
        log = directory / "capture.driver.log"
        log.write_text("time_us=1 level=4 fnirsi-dla32: WIRE read: phase pre-arm-drain, start_us 1, duration_us 2, status 0, requested 1048576, timeout_ms 20, count 0, count_mod32 0, payload_offset 0, prefix32 .\n"
                       "time_us=2 level=4 fnirsi-dla32: WIRE read: phase pre-arm-drain, start_us 3, duration_us 4, status -7, requested 1048576, timeout_ms 20, count 0, count_mod32 0, payload_offset 0, prefix32 .\n"
                       "time_us=3 level=4 fnirsi-dla32: WIRE post-stop drain summary: discarded 0, mod32 0, empty_reads 2.\n"
                       "time_us=3 level=3 fnirsi-dla32: WIRE trace closed: 32 decoder-input bytes, tail 0, failed 0.\n", encoding="utf-8")
        evidence = read_log_evidence(log)
        assert evidence["prearm_final_consecutive_empty_reads"] == 2
        assert evidence["prearm_final_empty_statuses"] == [0, -7]
        assert evidence["wire_trace_close_records"][0]["bytes"] == 32
        assert evidence["poststop_drain_summaries"][0]["empty_reads"] == 2
        with log.open("a", encoding="utf-8") as target:
            target.write("time_us=4 level=2 fnirsi-dla32: Post-stop endpoint did not become quiet after 64 bytes within the drain bound.\n")
        assert len(read_log_evidence(log)["unsafe_transport_warning_log_lines"]) == 1
    print("PASS no-hardware analyzer self-test: all32, bounded chunks, corruption, tails, shifted lanes, no realignment, truncation, drain logs")


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        if args.directory is None:
            return 0
    if args.directory is None or args.report is None:
        parser.error("Provide directory and --report, or --self-test")
    result = analyze_directory(args.directory)
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(f"Software conversion/count: {result['all_software_conversion_and_count_pass']}; "
          f"{result['capture_count']} captures, {result['raw_to_logic_bit_comparisons']} bit comparisons. "
          "Lane origin and lossless physical Stream remain unvalidated.")
    return 0 if result["all_software_conversion_and_count_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
