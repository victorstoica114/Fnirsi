"""Root-owned finite V6 CLI captures; no signal-generator or ESP32 operations.

Reuse the frozen cancellation runner's ownership, preflight and watchdog. Keep
original wire bytes and binary SR_DF_LOGIC separately. A clean count/lifecycle
result is not a channel-placement, trigger-timing or lossless verdict.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
GUARDS = ROOT / "tools/run_dla32_stop_worker_v6_cancel.py"
GUARDS_SHA = "7679133b79db88fcc746c8a49fa548e2ea26b7b8430fe73596cec2515b6b4fb0"
BASE = ROOT / "captures/wch-worker-v6-sdk0-2026-10-04-r1"
RATES = (1000000, 2000000, 4000000, 5000000, 10000000, 20000000,
         25000000, 40000000, 50000000, 100000000, 125000000, 200000000,
         250000000, 500000000, 1000000000)


def guards():
    if hashlib.sha256(GUARDS.read_bytes()).hexdigest() != GUARDS_SHA:
        raise RuntimeError("Frozen runner differs")
    spec = importlib.util.spec_from_file_location("frozen_v6_capture_guards", GUARDS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def no_active_dla_etw():
    """Read-only query; never starts or stops a tracing session."""
    result = subprocess.run(["logman.exe", "query", "-ets"], capture_output=True,
                            text=True, check=True, timeout=10)
    if re.search(r"\bDLA32_ETW_[A-Za-z0-9_-]+\b", result.stdout + result.stderr, re.I):
        raise RuntimeError("DLA32 ETW session is active; generic capture must not overlap")
    return {"query": ["logman.exe", "query", "-ets"], "exit_code": result.returncode,
            "active_DLA32_ETW_session": False}


def settings(mode, rate, samples, channels, threshold, trigger):
    if mode not in ("Buffer", "Stream") or type(rate) is not int or rate not in RATES:
        raise ValueError("Unsupported mode/rate")
    if type(samples) is not int or not 1 <= samples <= 100000000:
        raise ValueError("Samples must be 1..100000000")
    if threshold not in ("0.6", "1.6", "2.5") or trigger not in ("rising", "falling", "none"):
        raise ValueError("Unsupported threshold/trigger")
    if channels == "all":
        indexes = list(range(32))
    else:
        names = channels.split(",")
        if not names or any(not re.fullmatch(r"D(?:[0-9]|[12][0-9]|3[01])", s) for s in names):
            raise ValueError("Use all or comma-separated physical names D0..D31")
        indexes = sorted(int(s[1:]) for s in names)
        if len(indexes) != len(set(indexes)):
            raise ValueError("Duplicate channel")
    if trigger != "none" and 0 not in indexes:
        raise ValueError("D0 must be enabled for D0 trigger")
    count = len(indexes)
    frame = 1 << (count - 1).bit_length()
    if mode == "Buffer" and 1 < frame < 8:
        frame = 8
    maximum = ((1000000000 if count <= 8 else 500000000 if count <= 16 else 250000000)
        if mode == "Buffer" else
        (1000000000 if count <= 2 else 500000000 if count <= 4 else 250000000 if count <= 8
         else 125000000 if count <= 16 else 50000000))
    if rate > maximum:
        raise ValueError("Rate exceeds selected mode/channel limit")
    if mode == "Buffer" and samples > 4294967296 // frame:
        raise ValueError("Sample count exceeds Buffer capacity")
    if samples / rate > 30:
        raise ValueError("Requested duration exceeds this bounded 60-second probe")
    return {"mode": mode, "samplerate_hz": rate, "requested_samples": samples,
        "channels": ["D" + str(i) for i in indexes], "physical_channel_mask": f"{sum(1 << i for i in indexes):08x}",
        "threshold_volts": float(threshold), "threshold_text": threshold, "trigger": trigger,
        "trigger_channel": "D0" if trigger != "none" else None,
        "trigger_position_raw": 500 if trigger != "none" else 0,
        "unitsize": 1 if indexes[-1] < 8 else 2 if indexes[-1] < 16 else 4,
        "wire_frame_bytes": frame, "requested_duration_seconds": samples / rate}


def paths(block):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", block):
        raise ValueError("Block must be a simple fresh name")
    if not BASE.resolve().is_relative_to(ROOT):
        raise ValueError("Capture directory resolves outside workspace")
    directory = BASE / block
    outputs = {"directory": directory, "start": BASE / (block + ".acquisition-start.json"),
        "record": BASE / (block + ".acquisition.json"), "stdout": BASE / (block + ".stdout.log"),
        "stderr": BASE / (block + ".stderr.log"), "wire": directory / "capture.wire.bin",
        "logic": directory / "capture.logic.bin", "unconsumed": directory / "capture.wire.bin.worker-unconsumed.bin"}
    if any(p.exists() for p in outputs.values()):
        raise ValueError("Refusing to overwrite acquisition evidence")
    return outputs


def command(g, config, output):
    threshold = config["threshold_text"]
    cmd = [str(g.PACKAGE / "sigrok-cli.exe"), "--driver", "fnirsi-dla32", "--config",
        f"data_source={config['mode']}:samplerate={config['samplerate_hz']}:voltage_threshold={threshold}-{threshold}",
        "--channels", ",".join(config["channels"]), "--samples", str(config["requested_samples"]),
        "--output-format", "binary", "--output-file", str(output), "--loglevel", "4"]
    if config["trigger"] != "none":
        cmd.extend(["--triggers", "D0=" + ("r" if config["trigger"] == "rising" else "f")])
    return cmd


def verify(config, output, stderr, sha):
    """Bounded CLI count/terminal gate. Full worker/copy/physical audit is separate."""
    problems, files = [], {}
    for role in ("wire", "logic", "unconsumed"):
        try:
            path = output[role]
            files[role] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
        except OSError as exc:
            problems.append(role + ": " + repr(exc))
    text = stderr.decode("utf-8", errors="replace")
    lines = text.splitlines()
    actual = files.get("logic", {}).get("bytes", 0) // config["unitsize"]
    if files.get("logic", {}).get("bytes") != config["requested_samples"] * config["unitsize"]:
        problems.append("Binary LOGIC byte count differs from requested sample count")
    units = re.findall(r"session: bus: Received SR_DF_LOGIC packet \((\d+) bytes, unitsize = (\d+)\)", text)
    if not units or any(int(unit) != config["unitsize"] for _, unit in units) or sum(int(n) for n, _ in units) != actual * config["unitsize"]:
        problems.append("Driver LOGIC packet count/unitsize differs")
    if text.count("session: bus: Received SR_DF_HEADER packet") != 1 or text.count("session: bus: Received SR_DF_END packet") != 1:
        problems.append("Missing/duplicate HEADER or END")
    enabled = re.findall(r"WIRE trace enabled: '.+', frame (\d+) bytes, mask ([0-9a-f]{8}), (Buffer|Stream), (\d+) Hz\.", text)
    if enabled != [(str(config["wire_frame_bytes"]), config["physical_channel_mask"], config["mode"], str(config["samplerate_hz"]))]:
        problems.append("Saved driver mode/rate/mask/frame differs")
    terminal = re.findall(r"WCH WORKER terminal: generation (\d+), reads (\d+), stop_status (-?\d+), pending ([01]), stop_start_us (\d+), stop_duration_us (\d+), drain_bytes (\d+), drain_reads (\d+), empty_reads (\d+), quiet ([01]), diagnostics_failed ([01]), unconsumed_bytes (\d+); native thread exited\.", text)
    if len(terminal) != 1 or any(terminal[0][i] != value for i, value in {2:"0",3:"0",9:"1",10:"0",11:"0"}.items()) or int(terminal[0][8]) < 2:
        problems.append("Worker STOP/quiet/native-exit gate failed")
    for phase in ("pre-arm", "stop"):
        if text.count(f"WCH capture STOP complete: phase {phase}; command accepted.") != 1:
            problems.append("Missing/duplicate accepted " + phase + " STOP")
    markers = {name: [i for i, line in enumerate(lines) if marker in line] for name, marker in {
        "stop": "WCH WORKER command:", "accepted": "WCH capture STOP complete: phase stop;", "drain": "WCH WORKER drain read:",
        "terminal": "WCH WORKER terminal:", "END": "session: bus: Received SR_DF_END packet"}.items()}
    if any(not markers[k] for k in markers) or not (markers["stop"][0] < markers["accepted"][0] < min(markers["drain"]) <= max(markers["drain"]) < markers["terminal"][0] < markers["END"][0]):
        problems.append("STOP/drain/terminal/END ordering failed")
    closed = re.findall(r"WIRE trace closed: (\d+) decoder-input bytes, tail (\d+), failed ([01])\.", text)
    rawbytes = files.get("wire", {}).get("bytes", -1)
    if closed != [(str(rawbytes), str(rawbytes % config["wire_frame_bytes"]), "0")]:
        problems.append("RAW close/count/tail differs")
    side = re.findall(r"WCH WORKER unconsumed trace closed: (\d+) bytes, failed ([01]); these bytes were not decoded\.", text)
    if side != [("0", "0")] or files.get("unconsumed", {}).get("bytes") != 0:
        problems.append("Unexpected unconsumed data in finite capture")
    if rawbytes // config["wire_frame_bytes"] * 8 < actual:
        problems.append("Insufficient RAW decoder-input frames")
    # Default CLI logger omits severity. Preserve logs and label this screening;
    # never claim an exhaustive warning-level audit from these strings alone.
    dangerous = []
    for line in lines:
        if "fnirsi-dla32:" not in line:
            continue
        cleaned = re.sub(r"(?:diagnostics_)?failed 0", "", line)
        if re.search(r"\b(?:failed|cannot|stalled|refused|deferred|overflow|truncation|discontinuity|skipped)\b", cleaned, re.I):
            dangerous.append(line)
    if dangerous:
        problems.append("Driver failure/warning text present")
    return {"passed": not problems, "issues": problems, "actual_samples": actual,
        "logic_unitsize": config["unitsize"], "files": files, "terminal": terminal,
        "driver_failure_lines": dangerous, "CLI_close_and_sr_exit_return_codes_exposed": False,
        "CLI_log_severity_exposed": False, "complete_worker_log_audit_performed": False,
        "RAW_to_LOGIC_verified": False, "physical_integrity_verified": False}


def execute(g, config, output, protected, etw):
    reserve = config["requested_samples"] * config["unitsize"] + ((config["requested_samples"] + 7) // 8) * config["wire_frame_bytes"] + 268435456
    if shutil.disk_usage(ROOT).free < reserve:
        raise RuntimeError("Insufficient disk space to preserve all original evidence")
    BASE.mkdir(parents=True, exist_ok=True)
    output["directory"].mkdir()
    cmd = command(g, config, output["logic"])
    report = {"schema": 1, "policy": "wch-worker-v6-sdk0-finite-cli", "settings": config,
        "command": cmd, "output": str(output["directory"]), "started_utc": datetime.now(timezone.utc).isoformat(),
        "dll_sha256": g.EXPECTED_DLL, "frozen_guard_sha256": GUARDS_SHA,
        "runner_sha256": g.sha(Path(__file__)), "protected_files_sha256": protected,
        "ETW_preflight": etw,
        "generator_commands_sent": False, "ESP32_accessed": False, "hardware_accessed": True,
        "disk_IO_changes_capture_timing": True, "performance_benchmark": False}
    g.write_json(output["start"], report)
    environment = dict(os.environ)
    environment["PATH"] = str(g.PACKAGE) + os.pathsep + environment.get("PATH", "")
    environment.pop("FNIRSI_WCH_DLL", None)
    environment["FNIRSI_DLA32_WIRE_TRACE"] = str(output["wire"].resolve())
    result, stdout, stderr = g.bounded_process(cmd, environment, 60)
    report.update(result)
    report["ended_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        report["ETW_postflight"] = {"passed": True, **no_active_dla_etw()}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        report["ETW_postflight"] = {"passed": False, "error": repr(exc)}
    report["evidence_write_errors"] = []
    for role, data in (("stdout", stdout), ("stderr", stderr)):
        try:
            with output[role].open("xb") as stream:
                stream.write(data)
        except OSError as exc:
            report["evidence_write_errors"].append(role + ": " + repr(exc))
    changed = []
    for name, digest in protected.items():
        try:
            if g.sha(Path(name)) != digest:
                changed.append(name)
        except OSError:
            changed.append(name)
    report["protected_files_changed"] = changed
    report["forced_termination_precludes_cleanup_claim"] = result["kill_attempted"]
    try:
        report["capture_verification"] = verify(config, output, stderr, g.sha)
    except (OSError, ValueError, TypeError) as exc:
        report["capture_verification"] = {"passed": False, "issues": ["Evidence verification exception: " + repr(exc)]}
    report["harness_process_passed"] = (result["passed"] and not changed and
        not report["evidence_write_errors"] and report["capture_verification"]["passed"] and
        report["ETW_postflight"]["passed"])
    report["physical_integrity_verdict"] = "PENDING separate original RAW/LOGIC and waveform/placement analysis"
    g.write_json(output["record"], report)
    print(json.dumps({"process_passed": result["passed"], "count_terminal_passed": report["capture_verification"]["passed"],
        "outer_gate_passed": report["harness_process_passed"], "record": str(output["record"])}))
    return 0 if report["harness_process_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", required=True)
    parser.add_argument("--mode", required=True, choices=("Buffer", "Stream"))
    parser.add_argument("--rate", required=True, type=int)
    parser.add_argument("--samples", required=True, type=int)
    parser.add_argument("--channels", required=True)
    parser.add_argument("--threshold", required=True, choices=("0.6", "1.6", "2.5"))
    parser.add_argument("--trigger", required=True, choices=("rising", "falling", "none"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    config = settings(args.mode, args.rate, args.samples, args.channels, args.threshold, args.trigger)
    output = paths(args.block)
    g = guards()
    with g.exclusive_runner():
        protected = g.preflight()
        protected[str(GUARDS)] = GUARDS_SHA
        protected[str(Path(__file__))] = g.sha(Path(__file__))
        etw = no_active_dla_etw()
        if args.preflight_only:
            print(json.dumps({"preflight_passed": True, "hardware_accessed": False,
                "settings": config, "ETW_preflight": etw, "command": command(g, config, output["logic"])}))
            return 0
        return execute(g, config, output, protected, etw)


if __name__ == "__main__":
    raise SystemExit(main())
