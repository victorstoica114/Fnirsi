"""Autonomous V6 GUI validation, with optional offline file loading and ownership guards."""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
import run_dla32_stop_worker_v6_cancel as guard
import run_dla32_worker_v7_r2_probes as probes

ROOT = guard.ROOT
PACKAGE = ROOT / "artifacts/pulseview-dla32-v7-r2-cxx"
MANIFEST_SHA = "9517438b839bfc7d3b251e49d9f9a7b01d9f0f696816e6cc1db69a685e08f1cf"
GUARD_SHA = "7679133b79db88fcc746c8a49fa548e2ea26b7b8430fe73596cec2515b6b4fb0"


def package_preflight():
    if guard.sha(Path(guard.__file__)) != GUARD_SHA:
        raise RuntimeError("Frozen guard differs")
    checked, core = probes.core_preflight(guard)
    manifest_path = PACKAGE / "package-manifest.json"
    if guard.sha(manifest_path) != MANIFEST_SHA:
        raise RuntimeError("Candidate manifest differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("variant") != "pulseview-dla32-v7-r2-cxx" or manifest.get("core_DLL_sha256") != core["core_dll_sha256"]:
        raise RuntimeError("Candidate policy differs")
    checked[str(manifest_path)] = MANIFEST_SHA
    for name, item in manifest["files"].items():
        path = (PACKAGE / name).resolve(strict=True)
        if not path.is_relative_to(PACKAGE) or path.stat().st_size != item["bytes"] or guard.sha(path) != item["sha256"]:
            raise RuntimeError("Candidate file differs: " + name)
        checked[str(path)] = item["sha256"]
    return checked, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--input-file", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.block):
        parser.error("Block must be a fresh simple name")
    directory = ROOT / "captures/pulseview-v7-r2-2026-10-06" / args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):
        raise RuntimeError("Fresh workspace output required")
    with guard.exclusive_runner():
        protected, manifest = package_preflight()
        protected[str(Path(__file__))] = guard.sha(Path(__file__))
        if args.input_file:
            args.input_file = args.input_file.resolve(strict=True)
            if not args.input_file.is_relative_to(ROOT):
                raise RuntimeError("Input must stay inside workspace")
            protected[str(args.input_file)] = guard.sha(args.input_file)
        if args.preflight_only:
            print(json.dumps({"preflight_passed": True, "candidate_files": len(manifest["files"]), "hardware_accessed": False}))
            return 0
        directory.mkdir(parents=True, exist_ok=False)
        environment = dict(os.environ)
        environment.update(PATH=str(PACKAGE) + os.pathsep + environment.get("PATH", ""),
                           PYTHONHOME=str(PACKAGE), SIGROKDECODE_DIR=str(PACKAGE / "decoders"),
                           QT_PLUGIN_PATH=str(PACKAGE))
        for key in ("FNIRSI_WCH_DLL", "FNIRSI_DLA32_WIRE_TRACE", "QT_QPA_PLATFORM"):
            environment.pop(key, None)
        command = ([str(PACKAGE / "pulseview.exe"), "-c", "-D", "-l", "4", "-i", str(args.input_file)]
                   if args.input_file else [str(PACKAGE / "pulseview.exe"), "-c", "-d", "fnirsi-dla32", "-l", "4"])
        report = {"schema": 1, "block": args.block, "started_utc": datetime.now(timezone.utc).isoformat(),
                  "command": command, "manifest_sha256": MANIFEST_SHA, "DLL_sha256": manifest["core_DLL_sha256"],
                  "mutex_name": guard.MUTEX_NAME, "ESP32_accessed": False, "generator_commands_sent_by_runner": False,
                  "hardware_scan_requested": not bool(args.input_file),
                  "input_file": str(args.input_file) if args.input_file else None,
                  "GUI_capture_requires_separate_evidence": True, "GUI_watchdog_seconds": 180,
                  "timed_out": False, "kill_attempted": False, "native_exit_code": None,
                  "process_exited": False, "retirement_errors": []}
        guard.write_json(directory / "launch-start.json", report)
        process = None
        started = time.monotonic()
        def retire_failed_child():
            report["kill_attempted"] = True
            try:
                process.kill()
            except Exception as error:
                report["retirement_errors"].append("kill: " + repr(error))
            try:
                report["native_exit_code"] = process.wait(timeout=5)
            except Exception as error:
                report["retirement_errors"].append("wait: " + repr(error))
                report["native_exit_code"] = process.poll()
        try:
            with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
                process = subprocess.Popen(command, cwd=PACKAGE, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
                (directory / "pulseview-pid.txt").write_text(str(process.pid), encoding="ascii")
                print(json.dumps({"launched_PID": process.pid, "output": str(directory)}), flush=True)
                try:
                    report["native_exit_code"] = process.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    report["timed_out"] = True
                    retire_failed_child()
        except Exception as error:
            report["process_error"] = repr(error)
            if process is not None and process.poll() is None and not report["kill_attempted"]:
                retire_failed_child()
        report["ended_utc"] = datetime.now(timezone.utc).isoformat()
        report["elapsed_seconds"] = round(time.monotonic() - started, 6)
        report["process_exited"] = process is not None and process.poll() is not None
        report["forced_termination_precludes_cleanup_claim"] = report["kill_attempted"]
        report["automatic_hardware_reuse_allowed"] = False
        report["protected_files_changed"] = []
        for name, digest in protected.items():
            try:
                if guard.sha(Path(name)) != digest:
                    report["protected_files_changed"].append(name)
            except OSError:
                report["protected_files_changed"].append(name)
        report["clean_process_exit"] = (report["native_exit_code"] == 0 and not report["timed_out"]
                                        and not report["kill_attempted"] and not report.get("process_error")
                                        and report["process_exited"] and not report["retirement_errors"]
                                        and not report["protected_files_changed"])
        guard.write_json(directory / "launch-result.json", report)
        print(json.dumps({"native_exit_code": report["native_exit_code"], "clean_process_exit": report["clean_process_exit"]}))
        return 0 if report["clean_process_exit"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
