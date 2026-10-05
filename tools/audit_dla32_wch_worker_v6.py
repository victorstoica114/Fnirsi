"""Offline audit of the isolated three-stage V6 cancellation experiment.

Checks lifecycle/byte accounting; does not decode, remap lanes, establish USB
origin, judge trigger behavior, or certify signal continuity. Main logger time
and original worker I/O time are deliberately separate clocks in the report.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

READ = re.compile(r"WCH WORKER read: generation (\d+), read_id (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+), Windows error (\d+), payload_offset (\d+), prefix32 ([0-9a-f]*)\.")
UNCONSUMED = re.compile(r"WCH WORKER unconsumed read: generation (\d+), read_id (\d+), offset (\d+), count (\d+), prefix32 ([0-9a-f]+); stop requested, no LOGIC\.")
PAYLOAD = re.compile(r"WIRE payload: offset (\d+), count (\d+), offset_mod (\d+), count_mod (\d+), pending (\d+), elapsed_us \d+, prefix32 ([0-9a-f]+)\.")
COMMAND = re.compile(r"WIRE command: opcode ([0-9a-f]{2}), elapsed_us \d+, prefix54 ([0-9a-f]{108})\.")
STOP = re.compile(r"WCH WORKER command: generation (\d+), opcode 15, io_start_us (\d+), prefix54 ([0-9a-f]{108})\.")
TERMINAL = re.compile(r"WCH WORKER terminal: generation (\d+), reads (\d+), stop_status (-?\d+), pending ([01]), stop_start_us (\d+), stop_duration_us (\d+), drain_bytes (\d+), drain_reads (\d+), empty_reads (\d+), quiet ([01]), diagnostics_failed ([01]), unconsumed_bytes (\d+); native thread exited\.")
DRAIN = re.compile(r"WCH WORKER drain read: generation (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms 20, count (\d+)\.")
TRACE_CLOSE = re.compile(r"WIRE trace closed: (\d+) decoder-input bytes, tail (\d+), failed ([01])\.")
UNCONSUMED_CLOSE = re.compile(r"WCH WORKER unconsumed trace closed: (\d+) bytes, failed ([01]); these bytes were not decoded\.")
STAGES = ("cancel001-Buffer", "rep001-step1-Buffer", "rep001-step2-Stream")

def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON key: " + key)
        out[key] = value
    return out

def read_json(data):
    out = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_object)
    if not isinstance(out, dict):
        raise ValueError("Expected JSON object")
    return out

def valid_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name, re.ASCII):
        raise ValueError("Evidence must use a relative ASCII basename")
    return name

def stamp(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Evidence must be a regular non-reparse file: " + path.name)
    return (info.st_size, info.st_mtime_ns, info.st_ino)

def stage(metadata, log, wire, logic, discarded, expected_rate, expected_limit):
    """Pure audit, enabling meaningful offline mutations without SDK access."""
    issues, details = [], {}
    def require(condition, message):
        if not condition:
            issues.append(message)
    def eq(key, value):
        require(type(metadata.get(key)) is type(value) and metadata.get(key) == value, "metadata " + key)
    def number(key):
        value = metadata.get(key)
        require(type(value) is int and value >= 0, "metadata nonnegative integer " + key)
        return value if type(value) is int and value >= 0 else 0
    def parsed(pattern, tag):
        out = []
        for idx, line in enumerate(log.splitlines()):
            if tag not in line:
                continue
            match = pattern.search(line)
            require(match is not None, "malformed " + tag)
            if match:
                out.append((idx, match.groups()))
        return out
    for key, value in {"samplerate_hz": expected_rate, "configured_sample_limit": expected_limit,
        "unitsize": 4, "physical_channel_mask": "ffffffff", "headers": 1, "ends": 1,
        "threshold_volts": 1.6, "trigger": "D0 rising hardware request", "raw_trigger_position": 500,
        "stopped_callbacks": 1, "lifecycle_and_count_pass": True,
        "worker_unconsumed_bytes_are_decoder_input": False,
        "frame_origin_validated": False, "trigger_behavior_validated": False,
        "signal_integrity_verified": False}.items():
        eq(key, value)
    for key in ("logic_after_stop", "context_mismatches", "run_status", "running_after_run", "destroy_status",
                "invalid_packet", "guard_expired", "logic_close_failed", "driver_error_log_count",
                "driver_log_write_failures", "driver_log_close_failed", "stop_status"):
        eq(key, 0)
    actual = number("actual_samples")
    eq("written_logic_bytes", len(logic)); require(len(logic) == actual * 4, "LOGIC actual-N byte count")
    eq("wire_trace_bytes", len(wire)); eq("wire_trace_tail_mod32", len(wire) % 32)
    eq("worker_unconsumed_bytes", len(discarded)); eq("worker_unconsumed_tail_mod32", len(discarded) % 32)
    cancel = metadata.get("capture_kind") == "programmatic-cancel"
    if cancel:
        require(0 <= actual < expected_limit, "cancellation partial count")
        for k, v in {"stop_requested": True, "stop_call_count": 1, "cancel_timer_fired": 1,
                     "cancel_timer_nominal_ms": 100, "stop_reason": "cancel-timer", "samples_at_stop": actual}.items():
            eq(k, v)
        stop_begin, stop_end = number("stop_begin_us"), number("stop_end_us")
        fired, attached = number("timer_fired_us"), number("timer_attached_us")
        require(attached > 0 and attached <= fired <= stop_begin <= stop_end, "timer/public-stop time order")
        eq("stop_call_duration_us", stop_end - stop_begin); eq("actual_timer_delay_us", fired - attached)
    else:
        eq("capture_kind", "finite-recovery"); require(actual == expected_limit, "finite exact-N count")
        for k, v in {"stop_requested": False, "stop_call_count": 0, "cancel_timer_fired": 0}.items(): eq(k, v)
    lines = log.splitlines()
    require(not any(re.search(r"\blevel=[12]\b", line) for line in lines), "driver error/warning log")
    require("WCH UPLOAD" not in log and "WIRE read: phase samples" not in log, "unexpected synchronous/SDK1 path")
    headers = [i for i, line in enumerate(lines) if "SR_DF_HEADER" in line]
    ends = [i for i, line in enumerate(lines) if "SR_DF_END" in line]
    commands = parsed(COMMAND, "WIRE command:")
    stops = parsed(STOP, "WCH WORKER command:")
    terminals = parsed(TERMINAL, "WCH WORKER terminal:")
    reads = parsed(READ, "WCH WORKER read:")
    payloads = parsed(PAYLOAD, "WIRE payload:")
    extras = parsed(UNCONSUMED, "WCH WORKER unconsumed read:")
    drains = parsed(DRAIN, "WCH WORKER drain read:")
    closes = parsed(TRACE_CLOSE, "WIRE trace closed:")
    extra_closes = parsed(UNCONSUMED_CLOSE, "WCH WORKER unconsumed trace closed:")
    require(len(headers) == len(ends) == len(stops) == len(terminals) == len(closes) == len(extra_closes) == 1,
            "unique HEADER/END/worker STOP/terminal/trace-close records")
    require([row[1][0] for row in commands] == ["15", "11", "12"], "unchanged pre-arm STOP/SETUP/ARM sequence")
    pre_markers = [i for i, line in enumerate(lines) if "WCH capture STOP complete: phase pre-arm; command accepted." in line]
    stop_markers = [i for i, line in enumerate(lines) if "WCH capture STOP complete: phase stop; command accepted." in line]
    require(len(pre_markers) == 1, "unique accepted pre-arm STOP marker")
    require(len(stop_markers) == 1, "unique accepted terminal STOP marker")
    if not all(len(items) == 1 for items in (headers, ends, stops, terminals, closes, extra_closes)) or len(commands) != 3:
        return {"pass": False, "issues": issues, "details": details}
    ti, tg = terminals[0]; si, sg = stops[0]; ci, cg = closes[0]; xi, xg = extra_closes[0]
    term = dict(zip(("generation", "reads", "stop_status", "pending", "stop_start_us", "stop_duration_us", "drain_bytes",
                    "drain_reads", "empty_reads", "quiet", "diagnostics_failed", "unconsumed_bytes"), map(int, tg)))
    details["terminal"] = term
    require(commands[0][0] < commands[1][0] < headers[0] < commands[2][0] < ti < xi < ci < ends[0], "main startup/terminal/close/END order")
    if len(pre_markers) == 1: require(commands[0][0] < pre_markers[0] < commands[1][0], "pre-arm accepted marker order")
    if len(stop_markers) == 1:
        require(si < stop_markers[0] < ti and all(stop_markers[0] < di < ti for di, _ in drains), "worker STOP/accepted/drain/terminal FIFO order")
    require(all(lineidx < ti for lineidx, _ in reads), "worker read result before terminal")
    require(int(sg[0]) == term["generation"] and int(sg[1]) == term["stop_start_us"] and sg[2] == commands[0][1][1], "worker STOP fingerprint/generation/time")
    require(term["stop_status"] == term["pending"] == term["diagnostics_failed"] == 0 and term["quiet"] == 1 and term["empty_reads"] >= 2,
            "terminal accepted STOP/native exit/clean diagnostics/quiet")
    require(tuple(map(int, cg)) == (len(wire), len(wire) % 32, 0), "decoder trace-close byte count")
    require(tuple(map(int, xg)) == (len(discarded), 0) and term["unconsumed_bytes"] == len(discarded), "unconsumed sidecar-close byte count")
    require(len(reads) == term["reads"] and [int(g[1]) for _, g in reads] == list(range(1, len(reads) + 1)), "all worker read IDs covered exactly")
    require(all(int(g[0]) == term["generation"] for _, g in reads), "read generation")
    data_records = {"wire": payloads, "unconsumed": extras}
    positions = {"wire": 0, "unconsumed": 0}; indexes = {"wire": 0, "unconsumed": 0}
    previous_end = 0
    for ri, (lineidx, group) in enumerate(reads):
        gen, rid, start, duration, status, requested, timeout, count, winerr, offset = map(int, group[:-1]); prefix = group[-1]
        require(previous_end <= start <= start + duration <= term["stop_start_us"], "original read/STOP I/O order")
        previous_end = start + duration
        require(requested == 1048576 and timeout == 1000 and 0 <= count <= requested and status in (0, -7), "sample read tuple/status")
        require(offset == positions["wire"] and len(prefix) == min(count, 32) * 2, "read RAW offset/prefix size")
        if not count: continue
        following = reads[ri + 1][0] if ri + 1 < len(reads) else ti
        options = [(kind, record) for kind in data_records for record in data_records[kind]
                   if lineidx < record[0] < following]
        require(len(options) == 1, "one causal payload or unconsumed record for positive read")
        if len(options) != 1: continue
        kind, (pi, pg) = options[0]
        indexes[kind] += 1
        if kind == "wire":
            po, pc, pom, pcm, pending = map(int, pg[:-1]); pp = pg[-1]
            require((pom, pcm, pending) == (po % 32, pc % 32, po % 32), "frame carry accounting without per-read alignment")
            data = wire
        else:
            pgen, prid, po, pc = map(int, pg[:-1]); pp = pg[-1]; data = discarded
            require(pgen == gen and prid == rid and cancel, "unconsumed read identity/cancel scope")
        require(po == positions[kind] and pc == count and pp == prefix, "payload count/offset/prefix correspondence")
        require(data[po:po + min(count, 32)].hex() == prefix and po + count <= len(data), "saved payload prefix/span")
        positions[kind] += count
    require(all(indexes[kind] == len(data_records[kind]) for kind in indexes), "no unmapped payload records")
    require(positions == {"wire": len(wire), "unconsumed": len(discarded)}, "complete decoder-input/unconsumed byte accounting")
    drain_total = drain_empty = last_end = 0
    for di, dg in drains:
        gen, start, duration, status, requested, count = map(int, dg)
        require(gen == term["generation"] and term["stop_start_us"] + term["stop_duration_us"] <= start and last_end <= start,
                "STOP-before-drain original I/O order")
        require(status in (0, -7) and 0 <= count <= requested <= min(1048576, 67108864 - drain_total), "bounded drain tuple")
        last_end = start + duration; drain_total += count; drain_empty = drain_empty + 1 if count == 0 else 0
    require(len(drains) == term["drain_reads"] and drain_total == term["drain_bytes"] and drain_empty == term["empty_reads"] and drain_empty >= 2,
            "all drain reads/bytes and consecutive empties covered")
    header_time, end_time, stopped_time = number("HEADER_us"), number("END_us"), number("stopped_callback_us")
    require(header_time > 0 and end_time >= last_end and stopped_time >= end_time, "HEADER/END/stopped-callback original time order")
    if reads: require(header_time <= int(reads[0][1][2]), "HEADER precedes worker read")
    if cancel: require(metadata.get("stop_begin_us", 0) <= term["stop_start_us"] and metadata.get("stop_end_us", 0) <= end_time, "cancel request precedes hardware STOP/END")
    details.update(actual_samples=actual, decoder_input_bytes=len(wire), unconsumed_bytes=len(discarded), cancellation=cancel,
        public_stop_duration_us=metadata.get("stop_call_duration_us"), actual_timer_delay_us=metadata.get("actual_timer_delay_us"))
    return {"pass": not issues, "issues": issues, "details": details}

def audit(directory, outer_path, expected_dll):
    anchors, stamps, problems, cases = {}, {}, [], []
    def load(path):
        before = stamp(path); data = path.read_bytes()
        if stamp(path) != before or len(data) != before[0]: raise ValueError("Evidence changed during read: " + path.name)
        key = str(path.resolve()); anchors[key] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}; stamps[path] = before
        return data
    try:
        dir_info = directory.lstat()
        if not stat.S_ISDIR(dir_info.st_mode) or stat.S_ISLNK(dir_info.st_mode) or getattr(dir_info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Acquisitions directory cannot be a reparse point")
        seq = read_json(load(directory / "sequence-result.json")); outer = read_json(load(outer_path))
        for k, v in {"harness": "separate_v6_worker_cancel_recovery", "expected_loaded_DLL_SHA256": expected_dll,
            "completed_capture_stages": 3, "expected_capture_stages": 3, "device_close_attempted": True,
            "device_close_status": 0, "sr_exit_attempted": True, "sr_exit_status": 0, "outer_lifecycle_and_count_pass": True}.items():
            if type(seq.get(k)) is not type(v) or seq.get(k) != v: problems.append("sequence " + k)
        for k, v in {"process_started": True, "process_exited": True, "native_exit_code": 0, "exit_code": 0,
            "timed_out": False, "kill_attempted": False, "output_drained": True, "process_error": None,
            "kill_succeeded": False,
            "evidence_write_errors": [], "protected_files_changed": [], "harness_process_passed": True}.items():
            if type(outer.get(k)) is not type(v) or outer.get(k) != v: problems.append("outer " + k)
        if outer.get("launch_error") is not None: problems.append("outer launch_error")
        expected_files = {stem + ".json" for stem in STAGES} | {"sequence-result.json"}
        for idx, stem in enumerate(STAGES):
            metadata = read_json(load(directory / (stem + ".json")))
            if metadata.get("capture") != stem: problems.append("capture identity " + stem)
            if metadata.get("mode") != ("Stream" if idx == 2 else "Buffer"): problems.append("capture mode " + stem)
            for key, name in {"driver_log_file": stem + ".driver.log", "wire_file": stem + ".wire.bin",
                              "logic_file": stem + ".logic.bin", "worker_unconsumed_file": stem + ".wire.bin.worker-unconsumed.bin"}.items():
                if metadata.get(key) != name: problems.append("capture role filename " + stem + " " + key)
                expected_files.add(name)
            evidence = {k: load(directory / valid_name(metadata.get(k))) for k in ("driver_log_file", "wire_file", "logic_file", "worker_unconsumed_file")}
            result = stage(metadata, evidence["driver_log_file"].decode("utf-8"), evidence["wire_file"], evidence["logic_file"], evidence["worker_unconsumed_file"],
                           1000000 if idx == 0 else 50000000, 10000000 if idx == 0 else 5000000)
            result["capture"] = stem; cases.append(result)
        actual_files = {p.name for p in directory.iterdir() if p.name.endswith((".json", ".bin", ".driver.log"))}
        if actual_files != expected_files: problems.append("unexpected/missing capture evidence files")
        for path, before in stamps.items():
            if stamp(path) != before: problems.append("evidence changed: " + path.name)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        problems.append(str(exc))
    return {"schema": 1, "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "verdict": "PASS" if not problems and len(cases) == 3 and all(c["pass"] for c in cases) else "FAIL",
        "issues": problems, "captures": cases, "anchored_files": anchors,
        "scope": "software lifecycle and returned-byte accounting only; original I/O timestamps are distinct from deferred logger timestamps",
        "RAW_to_LOGIC_comparison_performed": False, "SDK_to_RAW_full_comparison_performed": False,
        "physical_origin_or_integrity_verified": False, "lane_remapping": False, "bytes_removed": False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path); parser.add_argument("--outer-record", type=Path, required=True)
    parser.add_argument("--expected-dll-sha256", required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{64}", args.expected_dll_sha256): parser.error("Expected lowercase SHA256")
    if args.output.resolve().is_relative_to(args.directory.resolve()): parser.error("Report must be outside acquisitions directory")
    report = audit(args.directory, args.outer_record, args.expected_dll_sha256)
    with args.output.open("x", encoding="utf-8") as out: json.dump(report, out, indent=2); out.write("\n")
    print(json.dumps({"verdict": report["verdict"], "captures": len(report["captures"]), "issues": report["issues"]}))
    return 0 if report["verdict"] == "PASS" else 1

if __name__ == "__main__": raise SystemExit(main())
