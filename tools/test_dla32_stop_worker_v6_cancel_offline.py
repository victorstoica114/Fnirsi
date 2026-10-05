"""Offline entry-point tests only; never invokes sr_init, scan, or a device."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "artifacts/test_dla32_stop_worker_v6_cancel.exe"
SOURCE = ROOT / "tools/test_dla32_stop_worker_v6_cancel.c"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve(strict=True)
    report = args.report.resolve()
    log = args.log.resolve()
    for path in (report, log):
        if path.exists() or not path.is_relative_to(ROOT) or not path.parent.is_dir():
            raise RuntimeError("Offline outputs must be fresh existing-parent workspace paths")
    expected = sha(runtime / "libsigrok-4.dll")
    fresh = ROOT / ("worker-cancel-offline-" + uuid.uuid4().hex)
    outside = ROOT.parent / ("worker-cancel-offline-outside-" + uuid.uuid4().hex)
    environment = dict(os.environ)
    environment["PATH"] = str(runtime) + os.pathsep + environment.get("PATH", "")
    # These arguments return before sr_init, scan, open, or output creation.
    cases = [
        ("compiled_self_test", ["--self-test"], 0),
        ("valid_DLL_and_fresh_path", ["--validate-only", ROOT, fresh, expected], 0),
        ("wrong_DLL_SHA", ["--validate-only", ROOT, fresh, "0" * 64], 2),
        ("existing_output", ["--validate-only", ROOT, ROOT, expected], 2),
        ("outside_workspace", ["--validate-only", ROOT, outside, expected], 2),
        ("relative_output", ["--validate-only", ROOT, "worker-cancel-relative", expected], 2),
        ("malformed_SHA", ["--validate-only", ROOT, fresh, "wrong"], 2),
        ("missing_arguments", [], 2),
    ]
    results = []
    with log.open("x", encoding="utf-8", newline="\n") as stream:
        for name, arguments, code in cases:
            completed = subprocess.run([str(HARNESS), *map(str, arguments)], cwd=ROOT,
                                       env=environment, capture_output=True, timeout=10)
            stdout = completed.stdout.decode("utf-8", "replace")
            stderr = completed.stderr.decode("utf-8", "replace")
            passed = completed.returncode == code
            if name == "compiled_self_test":
                passed &= "PASS offline self-test: 42 checks, 0 failures" in stdout
            if name == "valid_DLL_and_fresh_path":
                passed &= "no sr_init/scan/open" in stdout
            passed &= not fresh.exists() and not outside.exists()
            results.append({"case": name, "exit_code": completed.returncode,
                            "expected_exit_code": code, "passed": bool(passed),
                            "stdout": stdout, "stderr": stderr})
            stream.write(f"[{name}] exit={completed.returncode} passed={passed}\n")
            stream.write(stdout + stderr)
    data = {"created_utc": datetime.now(timezone.utc).isoformat(),
            "hardware_accessed": False, "sr_init_scan_open_executed": False,
            "compiled_self_test_checks": 42,
            "compiled_self_test_failures": 0 if results[0]["passed"] else 1,
            "CLI_SHA_fresh_path_entrypoint_checks": len(results),
            "CLI_SHA_fresh_path_entrypoint_failures": sum(not r["passed"] for r in results),
            "offline_runtime_DLL_sha256": expected, "results": results,
            "files_sha256": {str(path.relative_to(ROOT)): sha(path) for path in
                (HARNESS, SOURCE, ROOT / "tools/build_dla32_stop_worker_v6_cancel.sh",
                 Path(__file__).resolve(), log)},
            "old_V5_source_sha256": sha(ROOT / "tools/test_dla32_stop_v5_cancel.c"),
            "old_V5_EXE_sha256": sha(ROOT / "artifacts/test_dla32_stop_v5_cancel.exe")}
    with report.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: data[key] for key in (
        "compiled_self_test_checks", "compiled_self_test_failures",
        "CLI_SHA_fresh_path_entrypoint_checks", "CLI_SHA_fresh_path_entrypoint_failures",
        "offline_runtime_DLL_sha256", "hardware_accessed")}, indent=2))
    return int(any(not r["passed"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
