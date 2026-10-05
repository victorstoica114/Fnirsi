"""Root-owned V6 cancellation/recovery probe with exclusive ownership and watchdog.

Preparation agents only use offline tests or --preflight-only. All hardware is in
the separately compiled harness. No PWM, reset, SDK upload, or ESP32 calls occur
in this runner. Preserve every output, including failed or killed acquisitions.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "artifacts/dla32-wch-worker-v6-sdk0"
HARNESS = ROOT / "artifacts/test_dla32_stop_worker_v6_cancel.exe"
BASE = ROOT / "captures/wch-worker-v6-sdk0-2026-10-04-r1"
VENDOR = Path(r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll")
MUTEX_NAME = "Local\\FNIRSI_DLA32_BOUNDARY_RUNNER"
EXPECTED_DLL = "3d65045715233816584a8a0cfc3bd25e72a2032517b95deb488311ed7960f3dc"
# Root's final interaction/guards/review evidence is pinned separately from
# the already frozen initial runtime manifest. Never infer readiness from r2.
FINAL_PROOF = ROOT / "artifacts/dla32-wch-worker-v6-sdk0/preflight-validation.json"
FINAL_PROOF_SHA = "d40c31833ca42501df5c185f650a6d03190c4db8586732373dacb079ad92dba6"
EXPECTED = {
    PACKAGE / "package-manifest.json": "0288c1f6fbb62a26a43c678d74796081e1a2c481165325a5cb3443a89b6db254",
    PACKAGE / "libsigrok-4.dll": EXPECTED_DLL,
    HARNESS: "19f78d80929be052c31b5940f445a9c43af062b721a154abea80b6abc47e2785",
    ROOT / "tools/test_dla32_stop_worker_v6_cancel.c": "5ee37826a9cfb2cbfa1abab209a4ec3f6f837e1b35cd08c83ac6a022347d8619",
    ROOT / "artifacts/test_dla32_stop_worker_v6_cancel.validation.json":
        "b69747be840c829d35b2cba593f21e2d18bc2a9cfde1aee9492e2b7d1e4ab2c4",
    VENDOR: "db19ce17fe9b731cc82b9c40f2c7da23b55f19661c46ce1cfba9952b8a8621f7",
    ROOT / "artifacts/pulseview-dla32-reliability/libsigrok-4.dll":
        "76bfe1e98b37a5af01dcd39f453125978a5bfa86d622d49b5098ba4b226b7049",
    ROOT / "tools/test_dla32_stop_v5_cancel.c":
        "ac5e0995c9a111a1df97c172ac67563543e784e20f9e8fd4f9427796286fc216",
    ROOT / "artifacts/test_dla32_stop_v5_cancel.exe":
        "6ad73ed94261ed8f3f8d2b10d199f54ab3e425561f88046ca91baa24f3816763",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_file(relative: str) -> Path:
    if not isinstance(relative, str) or not relative or ":" in relative:
        raise RuntimeError("Invalid relative file path")
    normalized = relative.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or any(p in ("", "..", ".") for p in normalized.split("/")):
        raise RuntimeError("Invalid relative file path")
    resolved = (ROOT / path).resolve(strict=True)
    if not resolved.is_relative_to(ROOT):
        raise RuntimeError("File resolves outside workspace")
    return resolved


def verified_files(expected) -> dict:
    checked = {}
    for path, digest in expected.items():
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("Missing or invalid frozen SHA")
        if sha(path) != digest:
            raise RuntimeError(f"Frozen file differs: {path}")
        checked[str(path)] = digest
    return checked


def final_proof_files() -> dict:
    if not FINAL_PROOF_SHA:
        raise RuntimeError("Final V6 build/review proof is not frozen; no hardware may start")
    verified_files({FINAL_PROOF: FINAL_PROOF_SHA})
    proof = json.loads(FINAL_PROOF.read_text(encoding="utf-8"))
    if (proof.get("schema_version") != 1 or proof.get("limited_hardware_gate_ready") is not True or
            proof.get("package_manifest_sha256") != EXPECTED[PACKAGE / "package-manifest.json"] or
            proof.get("core_dll_sha256") != EXPECTED_DLL):
        raise RuntimeError("Final V6 preflight proof failed")
    checks = proof.get("checks")
    if (not isinstance(checks, list) or not checks or
            any(not isinstance(row, dict) or row.get("passed") is not True or not row.get("name") for row in checks) or
            len({row["name"] for row in checks}) != len(checks)):
        raise RuntimeError("Final V6 proof contains failed/missing/duplicate checks")
    files = proof.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("Final proof lacks file anchors")
    expected = {}
    for row in files:
        if not isinstance(row, dict):
            raise RuntimeError("Final proof file row invalid")
        path = workspace_file(row.get("path"))
        if path in expected:
            raise RuntimeError("Final proof duplicate file anchor")
        expected[path] = row.get("sha256")
    for key in ("owner_offline_validation_sha256", "independent_review_sha256"):
        digest = proof.get(key)
        if not isinstance(digest, str) or digest not in expected.values():
            raise RuntimeError("Final proof lacks owner/reviewer file anchor")
    return expected


def preflight() -> dict:
    expected = dict(EXPECTED)
    checked = verified_files(expected)
    manifest = json.loads((PACKAGE / "package-manifest.json").read_text(encoding="utf-8"))
    required = {"variant": "wch-worker-v6-sdk0", "worker_enabled": True,
                "SDK_upload_enabled": False, "prearm_empty_reads": 1,
                "sample_read_timeout_ms": 1000, "command_write_timeout_ms": 1000,
                "drain_read_timeout_ms": 20, "read_size_bytes": 1048576,
                "general_checks": 92, "general_failures": 0,
                "channel_or_byte_compensation_applied": False}
    if any(manifest.get(key) != value for key, value in required.items()):
        raise RuntimeError("V6 runtime policy differs")
    for name, entry in manifest["files"].items():
        if Path(name).name != name or name in ("", ".", ".."):
            raise RuntimeError("Unsafe package manifest path")
        expected[PACKAGE / name] = entry["sha256"]
        if (PACKAGE / name).stat().st_size != entry["bytes"]:
            raise RuntimeError("Package file size differs")
    source = ROOT / "artifacts/dla32-wch-worker-v6-source/libsigrok/src/hardware/fnirsi-dla32"
    for name, digest in manifest["source_SHA256"].items():
        if Path(name).name != name:
            raise RuntimeError("Unsafe source manifest path")
        expected[source / name] = digest
    validation = json.loads((ROOT / "artifacts/test_dla32_stop_worker_v6_cancel.validation.json").read_text())
    for key, value in {"compiled_self_test_checks": 42, "compiled_self_test_failures": 0,
                       "CLI_SHA_fresh_path_entrypoint_checks": 8,
                       "CLI_SHA_fresh_path_entrypoint_failures": 0,
                       "hardware_accessed": False, "sr_init_scan_open_executed": False,
                       "offline_runtime_DLL_sha256": EXPECTED_DLL}.items():
        if validation.get(key) != value:
            raise RuntimeError("Cancellation offline validation differs")
    for name, digest in validation["files_sha256"].items():
        expected[workspace_file(name)] = digest
    expected.update(final_proof_files())
    checked.update(verified_files(expected))
    checked[str(FINAL_PROOF)] = FINAL_PROOF_SHA
    if os.environ.get("FNIRSI_WCH_DLL"):
        raise RuntimeError("SDK observer/override environment is set; direct V6 only")
    command = ("Get-Process | Where-Object { $_.ProcessName -match "
               "'^(pulseview|DLA[-_]Logic|KingstVIS|sigrok-cli|test_dla32_(drain_ab|stop_v5_cancel|stop_worker_v6_cancel))$' } "
               "| Select-Object ProcessName,Id | ConvertTo-Json -Compress")
    owners = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if owners:
        raise RuntimeError("Analyzer process is running: " + owners)
    return checked


@contextmanager
def exclusive_runner():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
    kernel.CreateMutexW.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_ulong]
    kernel.WaitForSingleObject.restype = C.c_ulong
    kernel.ReleaseMutex.argtypes = [C.c_void_p]
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise C.WinError(C.get_last_error())
    held = False
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        held = status in (0, 0x80)
        if status != 0:
            raise RuntimeError(f"Runner mutex unavailable or abandoned: {status}")
        yield
    finally:
        if held:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def bounded_process(command, environment, timeout):
    result = {"exit_code": None, "native_exit_code": None,
              "process_started": False, "process_exited": False,
              "timed_out": False, "kill_attempted": False, "kill_succeeded": False,
              "output_drained": False, "process_error": None,
              "watchdog_seconds": timeout}
    process = None
    stdout = stderr = b""
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, cwd=ROOT, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        result["process_started"] = True
        result["process_id"] = process.pid
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            result["output_drained"] = True
        except subprocess.TimeoutExpired as error:
            result["timed_out"] = True
            stdout, stderr = error.stdout or b"", error.stderr or b""
        except Exception as error:
            result["process_error"] = repr(error)
    except Exception as error:
        result["process_error"] = repr(error)
    finally:
        if process is not None:
            if process.poll() is None:
                result["kill_attempted"] = True
                try:
                    process.kill()
                except Exception as error:
                    result["process_error"] = str(result["process_error"] or "") + "; kill: " + repr(error)
            if not result["output_drained"]:
                try:
                    stdout, stderr = process.communicate(timeout=5)
                    result["output_drained"] = True
                except subprocess.TimeoutExpired as error:
                    stdout, stderr = error.stdout or stdout, error.stderr or stderr
                    result["process_error"] = str(result["process_error"] or "") + "; output drain timed out"
                except Exception as error:
                    result["process_error"] = str(result["process_error"] or "") + "; output drain: " + repr(error)
            result["native_exit_code"] = process.poll()
            result["process_exited"] = result["native_exit_code"] is not None
            result["kill_succeeded"] = result["kill_attempted"] and result["process_exited"]
        result["exit_code"] = 124 if result["timed_out"] else result["native_exit_code"]
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    result["passed"] = (result["process_started"] and result["process_exited"] and
                        result["exit_code"] == 0 and result["output_drained"] and
                        not result["timed_out"] and not result["kill_attempted"] and
                        result["process_error"] is None)
    return result, stdout, stderr


def new_paths(block):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", block):
        raise RuntimeError("Block must be a simple fresh name")
    if not BASE.resolve().is_relative_to(ROOT):
        raise RuntimeError("Capture parent resolves outside workspace")
    paths = {"directory": BASE / block,
             "start": BASE / (block + ".acquisition-start.json"),
             "record": BASE / (block + ".acquisition.json"),
             "stdout": BASE / (block + ".stdout.log"),
             "stderr": BASE / (block + ".stderr.log")}
    if any(path.exists() for path in paths.values()):
        raise RuntimeError("Refusing to overwrite any cancellation evidence")
    return paths


def write_json(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def verify_sequence(directory):
    sequence = json.loads((directory / "sequence-result.json").read_text(encoding="utf-8"))
    expected = {"harness": "separate_v6_worker_cancel_recovery",
                "expected_loaded_DLL_SHA256": EXPECTED_DLL, "completed_capture_stages": 3,
                "expected_capture_stages": 3, "device_close_attempted": True,
                "device_close_status": 0, "sr_exit_attempted": True, "sr_exit_status": 0,
                "outer_lifecycle_and_count_pass": True}
    if any(sequence.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Cancel/recovery sequence or close/exit failed")
    stages = []
    for stem, kind, mode, count, rate in (
        ("cancel001-Buffer", "programmatic-cancel", "Buffer", 10000000, 1000000),
        ("rep001-step1-Buffer", "finite-recovery", "Buffer", 5000000, 50000000),
        ("rep001-step2-Stream", "finite-recovery", "Stream", 5000000, 50000000)):
        stage = json.loads((directory / (stem + ".json")).read_text(encoding="utf-8"))
        criteria = {"capture": stem, "capture_kind": kind, "mode": mode,
                    "samplerate_hz": rate, "configured_sample_limit": count,
                    "threshold_volts": 1.6, "physical_channel_mask": "ffffffff",
                    "raw_trigger_position": 500, "unitsize": 4,
                    "lifecycle_and_count_pass": True, "headers": 1, "ends": 1,
                    "stopped_callbacks": 1, "logic_after_stop": 0, "context_mismatches": 0,
                    "run_status": 0, "running_after_run": 0, "stop_status": 0,
                    "destroy_status": 0, "invalid_packet": 0, "guard_expired": 0,
                    "logic_close_failed": 0,
                    "driver_error_log_count": 0, "driver_log_write_failures": 0,
                    "driver_log_close_failed": 0, "worker_unconsumed_bytes_are_decoder_input": False}
        if any(stage.get(key) != value for key, value in criteria.items()):
            raise RuntimeError("Stage lifecycle/settings failed: " + stem)
        samples = stage.get("actual_samples")
        if type(samples) is not int or samples < 0:
            raise RuntimeError("Invalid stage sample count")
        if kind == "programmatic-cancel":
            if not (samples < count and stage.get("samples_at_stop") == samples and
                    stage.get("stop_call_count") == 1 and stage.get("cancel_timer_fired") == 1 and
                    stage.get("stop_reason") == "cancel-timer" and stage.get("stop_requested") is True):
                raise RuntimeError("Partial cancellation criterion failed")
        elif samples != count or stage.get("stop_requested") is not False:
            raise RuntimeError("Finite recovery count failed")
        entries = {}
        for key, expected_bytes in (("logic_file", samples * 4),
                ("wire_file", stage.get("wire_trace_bytes")),
                ("worker_unconsumed_file", stage.get("worker_unconsumed_bytes"))):
            name = stage.get(key)
            wanted = {"logic_file": stem + ".logic.bin", "wire_file": stem + ".wire.bin",
                      "worker_unconsumed_file": stem + ".wire.bin.worker-unconsumed.bin"}[key]
            if not isinstance(name, str) or name != wanted:
                raise RuntimeError("Unsafe stage output filename")
            path = directory / name
            if type(expected_bytes) is not int or expected_bytes < 0 or path.stat().st_size != expected_bytes:
                raise RuntimeError("Stage output byte count failed")
            entries[name] = {"bytes": expected_bytes, "sha256": sha(path)}
        if stage["wire_trace_bytes"] < samples * 4:
            raise RuntimeError("Insufficient original decoder input")
        if (stage.get("written_logic_bytes") != samples * 4 or
                stage.get("wire_trace_tail_mod32") != stage["wire_trace_bytes"] % 32 or
                stage.get("worker_unconsumed_tail_mod32") != stage["worker_unconsumed_bytes"] % 32 or
                stage.get("driver_log_file") != stem + ".driver.log"):
            raise RuntimeError("Stage metadata/file accounting differs")
        log_path = directory / stage["driver_log_file"]
        entries[log_path.name] = {"bytes": log_path.stat().st_size, "sha256": sha(log_path)}
        stages.append({"capture": stem, "samples": samples, "files": entries,
                       "metadata_sha256": sha(directory / (stem + ".json"))})
    return {"passed": True, "sequence_sha256": sha(directory / "sequence-result.json"),
            "stages": stages, "physical_integrity_verified": False,
            "worker_log_audit_required": True}


def execute(block, protected):
    paths = new_paths(block)
    if shutil.disk_usage(ROOT).free < 1_400_000_000:
        raise RuntimeError("Insufficient disk space for all original cancellation/recovery bytes")
    BASE.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PATH"] = str(PACKAGE) + os.pathsep + environment.get("PATH", "")
    environment.pop("FNIRSI_WCH_DLL", None)
    started = time.monotonic()
    report = {"schema": 1, "policy": "wch-worker-v6-sdk0", "block": block,
        "capture_kind": "timer_cancel_then_same_open_device_finite_Buffer_Stream",
        "output": str(paths["directory"]), "started_utc": datetime.now(timezone.utc).isoformat(),
        "cancel_samplerate_hz": 1000000, "cancel_configured_sample_limit": 10000000,
        "cancel_nominal_timer_ms": 100, "cancel_sample_count_criterion": "0 <= N < 10000000",
        "recovery_samplerate_hz": 50000000, "recovery_samples_per_capture": 5000000,
        "recovery_capture_count": 2, "threshold_volts": 1.6, "trigger": "D0 rising",
        "trigger_position_raw": 500, "physical_channel_mask": "ffffffff",
        "context": "single default GLib context; same open sdi for all3 stages",
        "dll_sha256": EXPECTED_DLL, "protected_files_sha256": protected,
        "generator_commands_sent": False, "ESP32_accessed": False, "SDK_upload_enabled": False,
        "timer_deadline_is_not_hardware_timing_guarantee": True,
        "mutex_name": MUTEX_NAME, "hardware_accessed": True}
    write_json(paths["start"], report)
    result, stdout, stderr = bounded_process(
        [str(HARNESS), str(ROOT), str(paths["directory"]), EXPECTED_DLL], environment, 60)
    report.update(result)
    report["ended_utc"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    report["evidence_write_errors"] = []
    for key, payload in (("stdout", stdout), ("stderr", stderr)):
        try:
            with paths[key].open("xb") as stream:
                stream.write(payload)
        except OSError as error:
            report["evidence_write_errors"].append(key + ": " + str(error))
    changed = []
    for name, digest in protected.items():
        try:
            if sha(Path(name)) != digest:
                changed.append(name)
        except OSError:
            changed.append(name)
    report["protected_files_changed"] = changed
    report["forced_termination_precludes_cleanup_claim"] = result["kill_attempted"]
    report["sequence_verification"] = {"passed": False}
    if result["passed"]:
        try:
            report["sequence_verification"] = verify_sequence(paths["directory"])
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            report["sequence_verification"] = {"passed": False, "error": repr(error)}
    report["harness_process_passed"] = (result["passed"] and not changed and
        not report["evidence_write_errors"] and report["sequence_verification"]["passed"])
    report["physical_integrity_verdict"] = "requires separate original RAW/LOGIC and worker-log analysis"
    write_json(paths["record"], report)
    print(json.dumps({key: report[key] for key in (
        "block", "exit_code", "native_exit_code", "elapsed_seconds", "timed_out",
        "kill_attempted", "output_drained", "harness_process_passed")}, indent=2))
    return 0 if report["harness_process_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    new_paths(args.block)
    with exclusive_runner():
        protected = preflight()
        if args.preflight_only:
            print(json.dumps({"preflight_passed": True, "files_checked": len(protected),
                              "hardware_accessed": False, "mutex_name": MUTEX_NAME}))
            return 0
        return execute(args.block, protected)


if __name__ == "__main__":
    raise SystemExit(main())
