"""Retain explicit statuses for existing hardware-free binaries after shell noise.

This creates new logs only; no source/build/package/provenance file is changed.
The original PowerShell wrapper returned 1 without retaining Bash's exit status.
The original transcript completed all suites and includes GCC warnings wrapped
as NativeCommandError. Replay makes the test processes' statuses explicit.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
destination = root / "artifacts/dla32-drain-ab-status-recheck"
destination.mkdir(exist_ok=False)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

protected = [root / "artifacts/dla32-drain-ab-source/source-manifest.json",
             root / "artifacts/dla32-drain-ab-source/build-validation.json"]
protected += [p for directory in ["artifacts/dla32-drain-ab-baseline",
                                  "artifacts/dla32-drain-ab-two-empty",
                                  "artifacts/dla32-drain-ab-source/libsigrok"]
              for p in (root / directory).rglob("*") if p.is_file()]
before = {str(path): sha(path) for path in protected}
tests = []
for variant in ["baseline", "two-empty"]:
    build = msys / f"tmp/dla32-drain-ab-build/{variant}/libsigrok"
    package = root / f"artifacts/dla32-drain-ab-{variant}"
    main = build / "tests/.libs/main.exe"
    assert main.is_file()
    tests += [
        (f"make-check-{variant}", main, package),
        (f"unit-{variant}", root / f"artifacts/dla32-drain-ab-tests/test_dla32_drain_ab_unit-{variant}.exe", package),
    ]
for mode in ["simd", "table"]:
    tests.append((f"protocol-{mode}", root / f"artifacts/dla32-drain-ab-tests/test_dla32_protocol-{mode}.exe",
                  root / "artifacts/dla32-drain-ab-baseline"))
results = []
for name, executable, package in tests:
    environment = dict(os.environ)
    environment["PATH"] = str(package) + os.pathsep + str(msys / "ucrt64/bin") + os.pathsep + environment.get("PATH", "")
    log = destination / f"{name}.log"
    with log.open("xb") as stream:
        result = subprocess.run([str(executable)], cwd=destination, env=environment,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=60)
    output = log.read_text(encoding="utf-8")
    assert result.returncode == 0, (name, result.returncode, output)
    assert "FAIL:" not in output
    expected = "100%: Checks: 92, Failures: 0, Errors: 0" if name.startswith("make-check") else \
               "Audit: 443 checks, 0 pending behavioral failures" if name.startswith("unit") else \
               "PASS DLA-32 protocol: 134930 checks"
    assert expected in output, (name, output)
    results.append({"test": name, "executable": str(executable), "executable_sha256": sha(executable),
                    "exit_code": result.returncode, "summary": expected, "log_sha256": sha(log)})
after = {str(path): sha(path) for path in protected}
assert before == after
record = {
    "purpose": "explicit independent process exit statuses for existing hardware-free binaries",
    "original_outer_powershell_exit_code": 1,
    "original_native_bash_exit_code": None,
    "original_log_completed_all_suites": True,
    "original_log_contains_native_command_error_from_compiler_warnings": True,
    "original_outer_exit_cause": "not established retrospectively; original Bash exit status was not stored",
    "test_processes": results,
    "all_replayed_process_exit_codes_zero": True,
    "sources_and_packages_unchanged": True,
    "protected_file_count": len(protected),
    "hardware_accessed": False,
    "recheck_tool_sha256": sha(Path(__file__)),
}
(destination / "validation.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
