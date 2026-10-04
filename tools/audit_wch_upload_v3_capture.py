"""Offline audit of recorded WCH V3 SDK calls, ordering and trace counts.

This audits completed capture logs and metadata. It never loads an SDK or opens
USB, and never treats accepted SDK calls as proof of physical FIFO alignment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

LINE = re.compile(r"^time_us=(\d+) level=(\d+) (.*)$")
SET = re.compile(
    r"WCH UPLOAD SetBufUploadEx: phase ([\w-]+), index (\d+), enable (\d+), "
    r"pipe (\d+), length (\d+), start_us (\d+), duration_us (\d+), "
    r"BOOL (\d+), Windows error (\d+)\.")
CLEAR = re.compile(
    r"WCH UPLOAD ClearBufUpload: phase ([\w-]+), index (\d+), pipe (\d+), "
    r"start_us (\d+), duration_us (\d+), BOOL (\d+), Windows error (\d+); "
    r"erased queue byte count unknown\.")
READ = re.compile(
    r"WIRE read: phase ([\w-]+), start_us (\d+), duration_us (\d+), "
    r"status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+), "
    r"count_mod32 (\d+), payload_offset (\d+), prefix32 ([0-9a-f]*)\.")
STOP_COMPLETION = re.compile(
    r"WCH UPLOAD capture STOP complete: phase ([\w-]+); disable precedes STOP\.")
OWNERSHIP_OR_DRAIN_FAILURE = re.compile(
    r"WCH write failed|Failed to send the acquisition stop command|"
    r"capture STOP (?:deferred|failed):|pending ownership retained|"
    r"close refused:|failed-start retains|post-stop drain skipped:|"
    r"Post-stop drain failed:|Post-stop endpoint did not become quiet|"
    r"Cannot allocate the post-stop drain buffer|Timed out draining|"
    r"Device still streaming after stop")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    state = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            state.update(block)
    return state.hexdigest()


def audit_text(log: str, metadata: dict, sizes: dict, profile: str) -> dict:
    """Pure function for audits and deliberately faulty offline fixtures."""
    events, errors, warnings, violations = [], [], [], []

    def require(condition: bool, detail: str) -> None:
        if not condition:
            violations.append(detail)

    require(profile in ("queued-v3", "direct-baseline"), "unsupported audit profile")

    for number, line in enumerate(log.splitlines(), 1):
        m = LINE.fullmatch(line)
        if not m:
            require(not line.strip(), f"unparsed log line {number}")
            continue
        timestamp, level, body = int(m[1]), int(m[2]), m[3]
        event = {"line": number, "time_us": timestamp, "level": level, "body": body}
        require(not OWNERSHIP_OR_DRAIN_FAILURE.search(body),
                f"SDK/pending STOP ownership or drain failure evidence at line {number}")
        if level == 1:
            errors.append(event)
        elif level == 2:
            warnings.append(event)
        if "WCH UPLOAD SetBufUploadEx:" in body:
            match = SET.search(body)
            require(match is not None, f"unparsed SDK Set at line {number}")
            if match:
                phase, *values = match.groups()
                fields = dict(zip(("index", "enable", "pipe", "length", "start_us",
                                   "duration_us", "BOOL", "windows_error"), map(int, values)))
                event.update(kind="set", phase=phase, **fields)
        elif "WCH UPLOAD ClearBufUpload:" in body:
            match = CLEAR.search(body)
            require(match is not None, f"unparsed SDK Clear at line {number}")
            if match:
                phase, *values = match.groups()
                fields = dict(zip(("index", "pipe", "start_us", "duration_us", "BOOL",
                                   "windows_error"), map(int, values)))
                event.update(kind="clear", phase=phase, **fields)
        elif "WCH UPLOAD capture STOP complete:" in body:
            match = STOP_COMPLETION.search(body)
            require(match is not None, f"unparsed capture STOP completion at line {number}")
            if match:
                event.update(kind="stop-complete", phase=match[1])
        elif "WIRE command:" in body:
            match = re.search(r"WIRE command: opcode ([0-9a-f]{2}),", body)
            require(match is not None, f"unparsed command at line {number}")
            if match:
                event.update(kind="command", opcode=int(match[1], 16))
        elif "WIRE read:" in body:
            match = READ.search(body)
            require(match is not None, f"unparsed read at line {number}")
            if match:
                phase, *values, prefix = match.groups()
                fields = dict(zip(("start_us", "duration_us", "status", "requested",
                                   "timeout_ms", "count", "count_mod32", "payload_offset"),
                                  map(int, values)))
                event.update(kind="read", phase=phase, prefix32=prefix, **fields)
        elif "WIRE payload:" in body:
            match = re.search(r"WIRE payload: offset (\d+), count (\d+),", body)
            require(match is not None, f"unparsed payload at line {number}")
            if match:
                event.update(kind="payload", offset=int(match[1]), count=int(match[2]))
        elif "WIRE post-stop drain summary:" in body:
            match = re.search(r"discarded (\d+), mod32 (\d+), empty_reads (\d+)\.", body)
            require(match is not None, f"unparsed post-stop summary at line {number}")
            if match:
                event.update(kind="post-summary", discarded=int(match[1]),
                             mod32=int(match[2]), empty_reads=int(match[3]))
        elif "WIRE trace closed:" in body:
            match = re.search(r"WIRE trace closed: (\d+) decoder-input bytes, tail (\d+), failed (\d+)\.", body)
            require(match is not None, f"unparsed trace close at line {number}")
            if match:
                event.update(kind="trace-close", count=int(match[1]), tail=int(match[2]), failed=int(match[3]))
        elif "Received SR_DF_HEADER packet" in body:
            event["kind"] = "header"
        elif "Received SR_DF_END packet" in body:
            event["kind"] = "end"
        elif "Received SR_DF_LOGIC packet" in body:
            match = re.search(r"SR_DF_LOGIC packet \((\d+) bytes, unitsize = (\d+)\)", body)
            require(match is not None, f"unparsed LOGIC packet at line {number}")
            if match:
                event.update(kind="logic", count=int(match[1]), unitsize=int(match[2]))
        else:
            event["kind"] = "other"
        events.append(event)

    require(all(a["time_us"] <= b["time_us"] for a, b in zip(events, events[1:])),
            "log timestamps go backwards")
    require(not errors, "driver/session error lines present")
    require(metadata.get("driver_error_log_count") == len(errors), "metadata error count differs from log")
    require(metadata.get("lifecycle_and_count_pass") is True, "capture lifecycle/count verdict is not true")
    for field in ("guard_expired", "invalid_packet", "logic_close_failed", "driver_log_write_failures",
                  "driver_log_close_failed", "context_mismatches", "running_after_run", "run_status",
                  "destroy_status", "stop_status", "logic_after_stop"):
        require(metadata.get(field) == 0, f"metadata {field} is not zero")
    require(metadata.get("headers") == 1 and metadata.get("ends") == 1, "metadata HEADER/END counts differ")
    require(metadata.get("stopped_callbacks") == 1, "metadata stopped callback count differs")
    require(type(metadata.get("stop_requested")) is bool, "metadata stop_requested is not a boolean")
    require(metadata.get("mode") in ("Stream", "Buffer"), "metadata capture mode is unsupported")
    require(metadata.get("context") == "single-default", "metadata capture context differs")
    require(metadata.get("trigger") == "D0 rising hardware request" and
            metadata.get("raw_trigger_position") == 500, "metadata trigger request differs")
    if metadata.get("stop_requested") is True:
        require(0 < metadata.get("actual_samples", 0) == metadata.get("samples_at_stop"),
                "early-stop actual sample count differs from samples at stop")
    else:
        require(metadata.get("actual_samples") == metadata.get("configured_sample_limit"),
                "actual sample count differs from configured full-capture limit")
    require(metadata.get("unitsize") == 4 and metadata.get("physical_channel_mask") == "ffffffff",
            "capture is not the full-32-channel, 4-byte fixture")
    require(metadata.get("samplerate_hz") == 50000000 and metadata.get("threshold_volts") == 1.6,
            "capture rate/threshold differs from V3 comparison")
    for kind in ("header", "end", "post-summary", "trace-close"):
        require(sum(e.get("kind") == kind for e in events) == 1, f"expected one {kind} event")
    commands = [e for e in events if e.get("kind") == "command"]
    require([e["opcode"] for e in commands] == [0x15, 0x11, 0x12, 0x15],
            "command order is not initial STOP, SETUP, ARM, final STOP")
    sets = [e for e in events if e.get("kind") == "set"]
    clears = [e for e in events if e.get("kind") == "clear"]
    sdk = [e for e in events if e.get("kind") in ("set", "clear")]
    completions = [e for e in events if e.get("kind") == "stop-complete"]
    headers = [e for e in events if e.get("kind") == "header"]
    if len(commands) == 4 and len(headers) == 1:
        require(commands[1]["line"] < headers[0]["line"] < commands[2]["line"],
                "HEADER is outside SETUP-to-ARM interval")
    if profile == "queued-v3":
        require([(e["phase"], e["enable"]) for e in sets] ==
                [("pre-arm", 0), ("before-arm", 1), ("stop", 0)], "SDK Set tuple sequence differs from V3")
        require(len(clears) == 1 and clears[0]["phase"] == "before-arm", "expected one before-arm Clear")
        require([e["phase"] for e in completions] == ["pre-arm", "stop"],
                "expected pre-arm and final successful capture STOP completion records")
        active = False
        for e in sdk:
            require(e["index"] == 0 and e["pipe"] == 1, f"SDK index/pipe differs at line {e['line']}")
            require(e["BOOL"] == 1 and e["windows_error"] == 0, f"SDK result/error differs at line {e['line']}")
            require(e["start_us"] + e["duration_us"] <= e["time_us"],
                    f"SDK call completes after its log timestamp at line {e['line']}")
            if e["kind"] == "set":
                require(e["length"] == 1048576, f"SDK upload length differs at line {e['line']}")
                if e["BOOL"] == 1:
                    active = bool(e["enable"])
            else:
                require(active, f"Clear without an accepted active upload state at line {e['line']}")
        require(not active, "SDK upload state not retired at capture end")
        if len(commands) == 4 and len(sets) == 3 and len(clears) == 1:
            require(sets[0]["line"] < commands[0]["line"] < commands[1]["line"] <
                    sets[1]["line"] < clears[0]["line"] < commands[2]["line"] <
                    sets[2]["line"] < commands[3]["line"], "SDK calls cross required disable-before-STOP boundaries")
            if len(headers) == 1:
                require(headers[0]["line"] < sets[1]["line"], "before-arm Enable precedes HEADER")
            if len(completions) == 2:
                require(commands[0]["line"] < completions[0]["line"] < commands[1]["line"],
                        "initial STOP success record is outside STOP-to-SETUP interval")
                require(commands[3]["line"] < completions[1]["line"],
                        "final STOP success record precedes hardware STOP attempt")
    else:
        require(not sdk, "direct baseline contains upload SDK calls")
        require(not completions, "direct baseline contains diagnostic STOP completion records")

    reads = [e for e in events if e.get("kind") == "read"]
    require(all(e["status"] in (0, -7) and e["count"] <= e["requested"] and
                e["count_mod32"] == e["count"] % 32 for e in reads), "read status/count metadata invalid")
    require(all(e["requested"] == 1048576 for e in reads), "read size differs from unchanged 1 MiB policy")
    pre = [e for e in reads if e["phase"] == "pre-arm-drain"]
    post = [e for e in reads if e["phase"] == "post-stop-drain"]
    sample_reads = [e for e in reads if e["phase"] == "samples"]
    require(len(pre) + len(post) + len(sample_reads) == len(reads), "unexpected read phase")
    require(bool(pre) and pre[-1]["count"] == 0, "pre-arm drain lacks a final empty read")
    require(all(e["count"] > 0 for e in pre[:-1]),
            "pre-arm drain continues past its first empty read despite policy1")
    require(len(post) >= 2 and post[-1]["count"] == 0 and post[-2]["count"] == 0,
            "post-stop drain lacks two consecutive empty reads")
    require(bool(sample_reads), "no sample reads recorded")
    require(all(e["timeout_ms"] == 20 for e in pre + post) and
            all(e["timeout_ms"] == 1000 for e in sample_reads),
            "logged timeout request arguments differ from unchanged V3 policy")
    if len(commands) == 4:
        initial_stop, setup, arm, stop = commands
        require(all(initial_stop["line"] < e["line"] < setup["line"] for e in pre),
                "pre-arm read is outside STOP-to-SETUP interval")
        if profile == "queued-v3" and sets:
            require(all(e["line"] > sets[0]["line"] for e in pre), "pre-arm read precedes initial queue retirement")
        if profile == "queued-v3" and len(completions) == 2:
            require(all(e["line"] > completions[0]["line"] for e in pre),
                    "pre-arm read precedes accepted hardware STOP completion")
        require(all(arm["line"] < e["line"] < stop["line"] for e in sample_reads),
                "sample read is outside ARM-to-STOP interval")
        if profile == "queued-v3" and len(sets) == 3:
            require(all(e["line"] < sets[2]["line"] for e in sample_reads),
                    "sample read follows terminal SDK retirement")
        retired = completions[-1]["line"] if profile == "queued-v3" and completions else stop["line"]
        require(all(e["line"] > retired for e in post), "post-stop read precedes queue retirement/STOP")
    summaries = [e for e in events if e.get("kind") == "post-summary"]
    if len(summaries) == 1:
        summary = summaries[0]
        require(summary["empty_reads"] == 2 and summary["discarded"] == sum(e["count"] for e in post)
                and summary["mod32"] == summary["discarded"] % 32,
                "post-stop summary disagrees with recorded reads")
        require(bool(post) and summary["line"] > post[-1]["line"], "post-stop summary precedes reads")
    payloads = [e for e in events if e.get("kind") == "payload"]
    cursor = 0
    for e in payloads:
        require(e["offset"] == cursor, f"wire payload gap/overlap at line {e['line']}")
        cursor += e["count"]
    positive_reads = [e for e in sample_reads if e["count"] > 0]
    require(len(positive_reads) == len(payloads), "positive sample reads/payload event counts differ")
    logic_intervals = []
    for read, payload in zip(positive_reads, payloads):
        require(read["line"] < payload["line"] and read["count"] == payload["count"] and
                read["payload_offset"] == payload["offset"], "sample read does not match its following wire payload")
        next_read = next((e["line"] for e in sample_reads if e["line"] > read["line"]), None)
        if next_read is None:
            if profile == "queued-v3" and len(sets) == 3:
                next_read = sets[2]["line"]
            elif len(commands) == 4:
                next_read = commands[3]["line"]
            else:
                next_read = len(events) + 1
        require(payload["line"] < next_read,
                "sample read is not traced synchronously before the next sample read/retirement")
        logic_intervals.append((payload["line"], next_read))
    if len(commands) == 4:
        require(all(commands[2]["line"] < e["line"] < commands[3]["line"] for e in payloads),
                "wire payload is outside ARM-to-STOP interval")
        if profile == "queued-v3" and len(sets) == 3:
            require(all(e["line"] < sets[2]["line"] for e in payloads),
                    "wire payload follows terminal SDK retirement")
    require(cursor == sum(e["count"] for e in sample_reads), "sample read bytes differ from traced payload bytes")
    closes = [e for e in events if e.get("kind") == "trace-close"]
    if len(closes) == 1:
        close = closes[0]
        require(close["count"] == cursor == sizes.get("wire") == metadata.get("wire_trace_bytes"),
                "wire count differs between reads/payload/log/file/metadata")
        require(close["tail"] == cursor % 32 == metadata.get("wire_trace_tail_mod32") == 0 and close["failed"] == 0,
                "trace tail/failure metadata invalid")
        if summaries:
            require(close["line"] > summaries[-1]["line"], "trace closes before post-stop summary")
        ends = [e for e in events if e.get("kind") == "end"]
        require(not ends or ends[0]["line"] > close["line"], "END precedes trace close")
    require(sizes.get("logic") == metadata.get("written_logic_bytes") == metadata.get("actual_samples", -1) * 4,
            "logic file/count metadata differs")
    logic = [e for e in events if e.get("kind") == "logic"]
    require(all(any(start < e["line"] < end for start, end in logic_intervals) for e in logic),
            "LOGIC precedes traced decoder input or follows its next sample read/retirement")
    require(bool(logic) and sum(e["count"] for e in logic) == sizes.get("logic") and
            all(e["unitsize"] == 4 and e["count"] % 4 == 0 for e in logic),
            "LOGIC packet totals/units differ from saved logic")
    require(len(logic) == metadata.get("logic_packets"), "LOGIC packet count differs from metadata")
    if len(commands) == 4:
        require(all(commands[2]["line"] < e["line"] < commands[3]["line"] for e in logic),
                "LOGIC packet is outside ARM-to-STOP interval")
        if profile == "queued-v3" and len(sets) == 3:
            require(all(e["line"] < sets[2]["line"] for e in logic),
                    "LOGIC packet follows terminal SDK retirement")
    require(metadata.get("written_logic_bytes", -1) <= cursor, "saved logic exceeds received wire bytes")
    return {"status": "LOGGED_SDK_ORDER_AND_CAPTURE_COUNTS_MATCH" if not violations else "AUDIT_FAIL",
            "audit_pass": not violations, "profile": profile, "capture": metadata.get("capture"),
            "violations": violations, "SDK_calls": sdk, "capture_STOP_completions": completions,
            "command_opcodes": [f"{e['opcode']:02x}" for e in commands],
            "actual_samples": metadata.get("actual_samples"), "requested_samples": metadata.get("configured_sample_limit"),
            "mode": metadata.get("mode"), "observed_bits": metadata.get("observed_sample_bits_or"),
            "pre_arm_reads": pre, "post_stop_reads": post, "post_stop_summary": summaries,
            "sample_read_count": len(sample_reads), "sample_bytes_received": cursor, "trace_close": closes,
            "error_lines": errors, "warning_lines": warnings,
            "limits": {"lane_origin_verified_by_this_audit": False, "physical_FIFO_reset_proven": False,
                       "alignment_proven_by_SDK_success": False, "queue_clear_discarded_bytes_known": False,
                       "disable_retirement_discarded_bytes_known": False,
                       "saved_timeout_arguments_prove_effective_SDK_timeout": False,
                       "hardware_STOP_write_success_has_diagnostic_completion_record": profile == "queued-v3",
                       "post_stop_direct_read_discarded_bytes": sum(e["count"] for e in post)}}


def audit_capture(path: Path, profile: str, expected_samples: int = 5000000) -> dict:
    raw = path.read_bytes()
    meta = json.loads(raw)
    if meta.get("configured_sample_limit") != expected_samples:
        raise ValueError("configured sample limit differs from requested audit expectation")
    for field in ("driver_log_file", "logic_file", "wire_file"):
        name = meta.get(field)
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError(f"{field} must name one file in the capture directory")
    log_path = path.parent / meta["driver_log_file"]
    logic_path = path.parent / meta["logic_file"]
    wire_path = path.parent / meta["wire_file"]
    log_raw = log_path.read_bytes()
    initial_stats = {"logic": logic_path.stat(), "wire": wire_path.stat()}
    sizes = {"logic": logic_path.stat().st_size, "wire": wire_path.stat().st_size}
    payload_hashes = {"wire": file_digest(wire_path), "logic": file_digest(logic_path)}
    result = audit_text(log_raw.decode("utf-8"), meta, sizes, profile)
    result["provenance"] = {"metadata": str(path), "metadata_sha256": digest(raw),
                            "driver_log": str(log_path), "driver_log_sha256": digest(log_raw),
                            "wire_bytes": sizes["wire"], "logic_bytes": sizes["logic"],
                            "wire_sha256": payload_hashes["wire"], "logic_sha256": payload_hashes["logic"],
                            "wire_logic_payloads_not_redecoded_by_this_audit": True}
    if path.read_bytes() != raw or log_path.read_bytes() != log_raw or sizes != {
            "logic": logic_path.stat().st_size, "wire": wire_path.stat().st_size} or any(
            (initial_stats[key].st_mtime_ns, initial_stats[key].st_ctime_ns) !=
            (file.stat().st_mtime_ns, file.stat().st_ctime_ns) for key, file in
            (("logic", logic_path), ("wire", wire_path))):
        result["audit_pass"] = False
        result["status"] = "INPUTS_CHANGED_DURING_AUDIT"
        result["violations"].append("capture inputs changed during audit")
    return result


def self_test() -> dict:
    bodies = [
        "fnirsi-dla32: WCH UPLOAD SetBufUploadEx: phase pre-arm, index 0, enable 0, pipe 1, length 1048576, start_us 0, duration_us 0, BOOL 1, Windows error 0.",
        "fnirsi-dla32: WIRE command: opcode 15, elapsed_us 0, prefix54 .",
        "fnirsi-dla32: WCH UPLOAD capture STOP complete: phase pre-arm; disable precedes STOP.",
        "fnirsi-dla32: WIRE read: phase pre-arm-drain, start_us 0, duration_us 0, status 0, requested 1048576, timeout_ms 20, count 0, count_mod32 0, payload_offset 0, prefix32 .",
        "fnirsi-dla32: WIRE command: opcode 11, elapsed_us 0, prefix54 .",
        "session: bus: Received SR_DF_HEADER packet.",
        "fnirsi-dla32: WCH UPLOAD SetBufUploadEx: phase before-arm, index 0, enable 1, pipe 1, length 1048576, start_us 0, duration_us 0, BOOL 1, Windows error 0.",
        "fnirsi-dla32: WCH UPLOAD ClearBufUpload: phase before-arm, index 0, pipe 1, start_us 0, duration_us 0, BOOL 1, Windows error 0; erased queue byte count unknown.",
        "fnirsi-dla32: WIRE command: opcode 12, elapsed_us 0, prefix54 .",
        "fnirsi-dla32: WIRE read: phase samples, start_us 0, duration_us 0, status 0, requested 1048576, timeout_ms 1000, count 64, count_mod32 0, payload_offset 0, prefix32 03000000.",
        "fnirsi-dla32: WIRE payload: offset 0, count 64, offset_mod 0, count_mod 0, pending 0, elapsed_us 0, prefix32 .",
        "session: bus: Received SR_DF_LOGIC packet (32 bytes, unitsize = 4).",
        "fnirsi-dla32: WCH UPLOAD SetBufUploadEx: phase stop, index 0, enable 0, pipe 1, length 1048576, start_us 0, duration_us 0, BOOL 1, Windows error 0.",
        "fnirsi-dla32: WIRE command: opcode 15, elapsed_us 0, prefix54 .",
        "fnirsi-dla32: WCH UPLOAD capture STOP complete: phase stop; disable precedes STOP.",
        "fnirsi-dla32: WIRE read: phase post-stop-drain, start_us 0, duration_us 0, status 0, requested 1048576, timeout_ms 20, count 0, count_mod32 0, payload_offset 64, prefix32 .",
        "fnirsi-dla32: WIRE read: phase post-stop-drain, start_us 0, duration_us 0, status 0, requested 1048576, timeout_ms 20, count 0, count_mod32 0, payload_offset 64, prefix32 .",
        "fnirsi-dla32: WIRE post-stop drain summary: discarded 0, mod32 0, empty_reads 2.",
        "fnirsi-dla32: WIRE trace closed: 64 decoder-input bytes, tail 0, failed 0.",
        "session: bus: Received SR_DF_END packet.",
    ]
    metadata = {field: 0 for field in ("guard_expired", "invalid_packet", "logic_close_failed", "driver_log_write_failures",
                "driver_log_close_failed", "context_mismatches", "running_after_run", "run_status", "destroy_status",
                "driver_error_log_count", "wire_trace_tail_mod32")}
    metadata.update(capture="fixture", lifecycle_and_count_pass=True, headers=1, ends=1, unitsize=4,
                    physical_channel_mask="ffffffff", samplerate_hz=50000000, threshold_volts=1.6,
                    actual_samples=8, configured_sample_limit=8, written_logic_bytes=32, wire_trace_bytes=64,
                    mode="Stream", observed_sample_bits_or="00300000", stopped_callbacks=1, logic_packets=1,
                    stop_status=0, logic_after_stop=0, stop_requested=False, context="single-default",
                    trigger="D0 rising hardware request", raw_trigger_position=500)
    sizes = {"logic": 32, "wire": 64}
    def run(lines: list[str], meta: dict = metadata, file_sizes: dict = sizes, profile: str = "queued-v3") -> dict:
        return audit_text("\n".join(f"time_us={i+1} level=4 {b}" for i, b in enumerate(lines)), meta, file_sizes, profile)
    checks = []
    assert run(bodies)["audit_pass"]
    checks.append("valid_order_with_overshoot_and_shifted_lane_diagnostics")
    def move_before(lines: list[str], needle: str, target: str) -> list[str]:
        index = next(i for i, s in enumerate(lines) if needle in s)
        moved = lines[index]
        result = lines[:index] + lines[index+1:]
        place = next(i for i, s in enumerate(result) if target in s)
        return result[:place] + [moved] + result[place:]
    def move_after_all(lines: list[str], needle: str) -> list[str]:
        index = next(i for i, s in enumerate(lines) if needle in s)
        return lines[:index] + lines[index+1:] + [lines[index]]
    def move_after(lines: list[str], needle: str, target: str, last: bool = False) -> list[str]:
        index = next(i for i, s in enumerate(lines) if needle in s)
        moved = lines[index]
        result = lines[:index] + lines[index+1:]
        matches = [i for i, s in enumerate(result) if target in s]
        place = (matches[-1] if last else matches[0]) + 1
        return result[:place] + [moved] + result[place:]
    def split_sample(lines: list[str], counts: tuple[int, int], trace_each_before_next_read: bool) -> list[str]:
        start = next(i for i, s in enumerate(lines) if "WIRE read: phase samples," in s)
        end = next(i for i, s in enumerate(lines) if "Received SR_DF_LOGIC" in s)
        a, b = counts
        read1 = lines[start].replace("count 64, count_mod32 0,", f"count {a}, count_mod32 {a % 32},")
        read2 = lines[start].replace("count 64, count_mod32 0, payload_offset 0,", f"count {b}, count_mod32 {b % 32}, payload_offset {a},")
        payload1 = lines[start + 1].replace("count 64,", f"count {a},")
        payload2 = lines[start + 1].replace("offset 0, count 64,", f"offset {a}, count {b},")
        group = [read1, payload1, read2, payload2] if trace_each_before_next_read else [read1, read2, payload1, payload2]
        return lines[:start] + group + lines[end:]
    negative = {
        "clear_before_enable": lambda b: move_before(b, "ClearBufUpload:", "SetBufUploadEx: phase before-arm"),
        "clear_after_disable": lambda b: move_after_all(b, "ClearBufUpload:"),
        "enable_rejected": lambda b: [s.replace("BOOL 1", "BOOL 0") if "enable 1," in s else s for s in b],
        "initial_disable_rejected": lambda b: [s.replace("BOOL 1", "BOOL 0") if "SetBufUploadEx: phase pre-arm" in s else s for s in b],
        "required_clear_rejected": lambda b: [s.replace("BOOL 1", "BOOL 0") if "ClearBufUpload:" in s else s for s in b],
        "terminal_disable_rejected": lambda b: [s.replace("BOOL 1", "BOOL 0") if "SetBufUploadEx: phase stop" in s else s for s in b],
        "wrong_pipe": lambda b: [s.replace("pipe 1,", "pipe 2,") if "WCH UPLOAD" in s else s for s in b],
        "wrong_upload_size": lambda b: [s.replace("length 1048576,", "length 4194304,") for s in b],
        "missing_stop_disable": lambda b: [s for s in b if "SetBufUploadEx: phase stop" not in s],
        "missing_ARM": lambda b: [s for s in b if "opcode 12," not in s],
        "extra_ARM": lambda b: b + [next(s for s in b if "opcode 12," in s)],
        "one_empty_read_despite_summary_two": lambda b: b[:-5] + b[-4:],
        "missing_post_stop_summary": lambda b: [s for s in b if "post-stop drain summary:" not in s],
        "post_drain_before_retirement": lambda b: move_before(b, "phase post-stop-drain,", "SetBufUploadEx: phase stop"),
        "pre_drain_before_initial_disable": lambda b: move_before(b, "phase pre-arm-drain,", "SetBufUploadEx: phase pre-arm"),
        "HEADER_before_STOP": lambda b: move_before(b, "SR_DF_HEADER", "opcode 15,"),
        "HEADER_after_ARM": lambda b: move_after_all(b, "SR_DF_HEADER"),
        "payload_gap": lambda b: [s.replace("WIRE payload: offset 0,", "WIRE payload: offset 32,") for s in b],
        "payload_after_END": lambda b: move_after_all(b, "WIRE payload:"),
        "logic_packet_count_disagreement": lambda b: [s.replace("packet (32 bytes", "packet (28 bytes") for s in b],
        "trace_count_disagreement": lambda b: [s.replace("64 decoder-input bytes", "32 decoder-input bytes") for s in b],
        "trace_write_failure": lambda b: [s.replace("tail 0, failed 0.", "tail 0, failed 1.") for s in b],
        "missing_END": lambda b: b[:-1],
        "success_with_nonzero_recorded_error": lambda b: [s.replace("Windows error 0.", "Windows error 1450.") for s in b],
        "V2_initial_STOP_before_SDK_disable": lambda b: move_before(b, "WIRE command: opcode 15,", "SetBufUploadEx: phase pre-arm"),
        "V2_terminal_STOP_before_SDK_disable": lambda b: move_after(b, "SetBufUploadEx: phase stop", "WIRE command: opcode 15,", last=True),
        "initial_STOP_attempt_without_success_record": lambda b: [s for s in b if "STOP complete: phase pre-arm" not in s],
        "final_STOP_attempt_without_success_record": lambda b: [s for s in b if "STOP complete: phase stop" not in s],
        "STOP_success_record_before_write_attempt": lambda b: move_before(b, "STOP complete: phase pre-arm", "WIRE command: opcode 15,"),
        "pre_drain_before_STOP_success": lambda b: move_before(b, "phase pre-arm-drain,", "STOP complete: phase pre-arm"),
        "post_drain_before_STOP_success": lambda b: move_before(b, "phase post-stop-drain,", "STOP complete: phase stop"),
        "sample_read_after_terminal_disable": lambda b: move_after(b, "WIRE read: phase samples,", "SetBufUploadEx: phase stop"),
        "payload_after_terminal_disable": lambda b: move_after(b, "WIRE payload:", "SetBufUploadEx: phase stop"),
        "LOGIC_after_terminal_disable": lambda b: move_after(b, "Received SR_DF_LOGIC", "SetBufUploadEx: phase stop"),
        "changed_read_size": lambda b: [s.replace("requested 1048576,", "requested 524288,") for s in b],
        "changed_timeout_argument": lambda b: [s.replace("timeout_ms 20,", "timeout_ms 50,") for s in b],
        "SDKdirty_STOP_deferred_even_if_logged_not_as_error": lambda b: b + ["fnirsi-dla32: WCH UPLOAD capture STOP deferred: phase stop; SDK retirement failed."],
        "STOP_pending_even_if_exactN_and_END": lambda b: b + ["fnirsi-dla32: WCH UPLOAD capture STOP failed: phase stop; pending ownership retained."],
        "close_refused_even_if_exactN_and_END": lambda b: b + ["fnirsi-dla32: WCH UPLOAD close refused: SDK or pending capture STOP retirement failed."],
        "quiet_warning_not_accepted_with_forged_summary": lambda b: b + ["fnirsi-dla32: Post-stop endpoint did not become quiet after 0 bytes within the drain bound."],
        "write_failure_even_if_logged_not_as_error": lambda b: b + ["fnirsi-dla32: WCH write failed (0/2048 bytes, Windows error 31)."],
        "LOGIC_before_its_sample_read_and_payload": lambda b: move_before(b, "Received SR_DF_LOGIC", "WIRE read: phase samples,"),
        "policy1_second_prearm_empty_read": lambda b: b[:3] + [next(s for s in b if "phase pre-arm-drain," in s)] + b[3:],
        "READ2_before_READ1_payload_trace": lambda b: split_sample(b, (32, 32), False),
    }
    for name, mutate in negative.items():
        assert not run(mutate(copy.copy(bodies)))["audit_pass"], name
        checks.append(name)
    fragmented = split_sample(bodies, (16, 16), True)
    fragmented = [s.replace("payload_offset 64,", "payload_offset 32,").replace("64 decoder-input bytes", "32 decoder-input bytes") for s in fragmented]
    assert run(fragmented, meta=dict(metadata, wire_trace_bytes=32), file_sizes={"wire": 32, "logic": 32})["audit_pass"]
    checks.append("valid_fragmented_reads_carry_frame_and_emit_no_LOGIC_for_first_payload")
    assert not run(bodies, file_sizes={"logic": 32, "wire": 63})["audit_pass"]
    checks.append("on_disk_wire_truncation")
    assert not run(bodies, profile="direct-baseline")["audit_pass"]
    checks.append("unexpected_SDK_calls_in_baseline")
    altered_metadata = dict(metadata, actual_samples=9, written_logic_bytes=36)
    assert not run(bodies, meta=altered_metadata, file_sizes={"logic": 36, "wire": 64})["audit_pass"]
    checks.append("metadata_full_count_differs_from_requested_limit")
    error_fixture = "\n".join(f"time_us={i+1} level=4 {b}" for i, b in enumerate(bodies))
    error_fixture += "\ntime_us=999 level=1 fnirsi-dla32: SDK retirement failed."
    assert not audit_text(error_fixture, dict(metadata, driver_error_log_count=1), sizes, "queued-v3")["audit_pass"]
    checks.append("logged_error_rejected_even_when_metadata_agrees")
    baseline = [s for s in bodies if "WCH UPLOAD" not in s]
    assert run(baseline, profile="direct-baseline")["audit_pass"]
    checks.append("valid_direct_baseline")
    return {"status": "PASS", "checks": checks, "count": len(checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--profile", choices=("queued-v3", "direct-baseline"), default="queued-v3")
    parser.add_argument("--expected-samples", type=int, default=5000000)
    parser.add_argument("--expected-captures", type=int)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0
    if not args.directory or not args.json:
        parser.error("--directory and --json are required")
    paths = sorted(args.directory.glob("rep*-step*.json"))
    reports = []
    for path in paths:
        try:
            reports.append(audit_capture(path, args.profile, args.expected_samples))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reports.append({"metadata": str(path), "status": "INCOMPLETE_OR_UNSUPPORTED_INPUT",
                            "audit_pass": False, "error": str(exc)})
    count_matches = args.expected_captures is None or len(reports) == args.expected_captures
    result = {"tool": str(Path(__file__).resolve()), "tool_sha256": digest(Path(__file__).read_bytes()),
              "directory": str(args.directory.resolve()), "profile": args.profile,
              "expected_captures": args.expected_captures, "expected_samples": args.expected_samples,
              "captures_found": len(reports),
              "capture_count_matches": count_matches,
              "all_log_and_count_audits_pass": bool(reports) and count_matches and all(r["audit_pass"] for r in reports),
              "captures": reports, "hardware_integrity_or_lane_origin_verdict": "NOT_EVALUATED_BY_THIS_AUDIT"}
    with args.json.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "captures"}, indent=2))
    return 0 if result["all_log_and_count_audits_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
