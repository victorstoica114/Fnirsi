"""Package the two isolated diagnostic DLLs without replacing any runtime."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
reference = root / "artifacts/pulseview-la1010-reference"
source = root / "artifacts/dla32-drain-ab-source/libsigrok"
source_manifest = json.loads((source.parent / "source-manifest.json").read_text())
runtime_files = {
    "DLA32": root / "artifacts/pulseview-dla32-reliability/libsigrok-4.dll",
    "Kingst": reference / "libsigrok-4.dll",
    "diagnostic": root / "artifacts/pulseview-dla32-wiretrace/libsigrok-4.dll",
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

assert {key: sha(path) for key, path in runtime_files.items()} == source_manifest["existing_runtime_dll_sha256"]
original = Path(source_manifest["source_original"])
changed = []
source_hashes = {}
for path in sorted(source.rglob("*")):
    if not path.is_file():
        continue
    name = path.relative_to(source).as_posix()
    source_hashes[name] = sha(path)
    if sha(path) != sha(original / name):
        changed.append(name)
assert changed == source_manifest["changed_files"], changed
(source.parent / "source-sha256.json").write_text(json.dumps(source_hashes, indent=2) + "\n", encoding="utf-8")

variants = {"baseline": 1, "two-empty": 2}
requested = sys.argv[1:] or list(variants)
assert requested and len(requested) == len(set(requested)) and all(v in variants for v in requested)
results = []
for variant in requested:
    policy = variants[variant]
    destination = root / f"artifacts/dla32-drain-ab-{variant}"
    destination.mkdir(exist_ok=False)
    prefix = msys / f"tmp/dla32-drain-ab-prefix/{variant}"
    build = msys / f"tmp/dla32-drain-ab-build/{variant}/libsigrok"
    for path in reference.iterdir():
        if path.is_file() and (path.suffix.lower() == ".dll" or path.name in {
            "sigrok-cli.exe", "LICENSE-libsigrok.txt", "LICENSE-sigrok-cli.txt", "sources.lock.json"
        }) and path.name != "libsigrok-4.dll":
            shutil.copy2(path, destination / path.name)
    shutil.copy2(prefix / "bin/libsigrok-4.dll", destination / "libsigrok-4.dll")
    shutil.copy2(source.parent / "prearm-drain-ab.patch", destination / "prearm-drain-ab.patch")
    shutil.copy2(source.parent / "source-manifest.json", destination / "source-manifest.json")
    shutil.copy2(source.parent / "source-sha256.json", destination / "source-sha256.json")
    config_status = (build / "config.status").read_text(encoding="utf-8")
    assert f"-DDLA_PREARM_EMPTY_READS={policy}" in config_status
    (destination / "Sigrok-DLA32-Drain-AB.ps1").write_text(
        "$env:PATH=$PSScriptRoot+';'+$env:PATH\n"
        "& (Join-Path $PSScriptRoot 'sigrok-cli.exe') @args\n"
        "exit $LASTEXITCODE\n", encoding="utf-8")
    subprocess.run([
        str(msys / "usr/bin/bash.exe"), "-lc",
        "bash /tmp/dla32-project/tools/build_dla32_drain_ab.sh "
        f"/tmp/dla32-drain-ab-prefix/{variant} "
        f"/tmp/dla32-project/artifacts/dla32-drain-ab-{variant}",
    ], check=True, env=dict(os.environ, MSYSTEM="UCRT64", CHERE_INVOKING="1"))
    (destination / "README.md").write_text(f"""# DLA32 pre-arm drain A/B diagnostic: {variant}

Compile-time `DLA_PREARM_EMPTY_READS={policy}` requires {policy} consecutive empty
reads after STOP and before SETUP/ARM. Policy 1 reproduces the existing driver
criterion. Policy 2 is an experiment, not a demonstrated channel alignment fix.
Both variants have identical instrumentation and otherwise identical sources.
Post-stop draining, decoder, channel masks and duty handling are unchanged.

The 64-read limit, 3-second deadline and read-error rollback are retained. An
empty read that completes the criterion still wins before the deadline check,
preserving baseline behavior. A nonempty read resets the consecutive count.
Zero-byte success and timeout results count as empty; timeout with data does
not. Other errors reject startup before SETUP/ARM.

This separate CLI runtime never replaces the installed DLA32/PulseView, Kingst
reference, or diagnostic wiretrace runtime. Only `--version` and
`--list-supported` were run during packaging; no hardware was accessed.
Hardware captures require separate authorization and a stable physical setup.

The `FNIRSI_DLA32_WIRE_TRACE` environment variable retains its existing behavior:
set it to an absolute UTF-8 path for a NEW wire file before acquisition; existing
files reject startup before USB commands. Capture all 32 channels and retain
`-l 4` logs. Wire bytes are exactly decoder inputs, including bytes beyond the
software limit and an incomplete final frame; pre-arm/post-stop drain bytes are
excluded. No prefix removal, lane rotation or duty compensation is performed.
Diagnostic disk writes/logs affect timing and cannot prove USB throughput.

Isolated source: `artifacts/dla32-drain-ab-source/libsigrok`.
Build: `/tmp/dla32-drain-ab-build/{variant}/libsigrok`.
Prefix: `/tmp/dla32-drain-ab-prefix/{variant}`.
Build log: `logs/build-dla32-drain-ab.log`.
Hardware-free unit log: `logs/dla32-drain-ab-unit-{variant}.log`.
Source patch, all source hashes and package hashes accompany this runtime.
""", encoding="utf-8")
    cli_results = {}
    for argument, label in [("--version", "version"), ("--list-supported", "drivers")]:
        result = subprocess.run([str(destination / "sigrok-cli.exe"), argument],
                                text=True, capture_output=True)
        logfile = root / f"logs/dla32-drain-ab-{variant}-{label}.log"
        logfile.write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, (argument, result.returncode, result.stderr)
        cli_results[argument] = {"exit_code": result.returncode, "log": str(logfile), "sha256": sha(logfile)}
    unit_log = root / f"logs/dla32-drain-ab-unit-{variant}.log"
    assert "0 pending behavioral failures. No hardware accessed." in unit_log.read_text()
    unit_checks = re.search(r"Audit: (\d+) checks, (\d+) pending behavioral failures", unit_log.read_text())
    assert unit_checks and unit_checks.group(2) == "0"
    make_check_log = build / "test-suite.log"
    assert "# FAIL:  0" in make_check_log.read_text()
    make_check_cases = re.search(r"100%: Checks: (\d+), Failures: (\d+), Errors: (\d+)",
                                 (build / "tests/main.log").read_text())
    assert make_check_cases and make_check_cases.group(2) == make_check_cases.group(3) == "0"
    manifest = {
        "purpose": "isolate pre-arm consecutive empty-read criterion; not a confirmed fix",
        "variant": variant,
        "prearm_empty_reads": policy,
        "compiler_cppflags": f"-DDLA_PREARM_EMPTY_READS={policy}",
        "drivers_enabled": ["demo", "fnirsi-dla32"],
        "decoder_or_lane_correction": False,
        "hardware_accessed_during_build_or_packaging": False,
        "source_directory": str(source),
        "source_manifest": source_manifest,
        "source_sha256_file": "source-sha256.json",
        "build_directory": str(build),
        "prefix_directory": str(prefix),
        "configure_sha256": sha(source / "configure"),
        "config_status_sha256": sha(build / "config.status"),
        "config_h_sha256": sha(build / "config.h"),
        "test_log_sha256": sha(unit_log),
        "unit_checks": int(unit_checks.group(1)),
        "unit_failures": 0,
        "make_check_log_sha256": sha(make_check_log),
        "make_check_cases": int(make_check_cases.group(1)),
        "make_check_failures": 0,
        "make_check_errors": 0,
        "compile_policy_guards_log_sha256": sha(root / "logs/dla32-drain-ab-compile-policy-guards.log"),
        "hardware_harness_source_sha256": sha(root / "tools/test_dla32_drain_ab.c"),
        "hardware_harness_build_script_sha256": sha(root / "tools/build_dla32_drain_ab.sh"),
        "cli_checks": cli_results,
        "unchanged_reference_cli_sha256": sha(reference / "sigrok-cli.exe"),
        "existing_runtime_dll_sha256": {key: sha(path) for key, path in runtime_files.items()},
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha(path)}
                  for path in sorted(destination.iterdir()) if path.is_file()},
    }
    assert sha(destination / "sigrok-cli.exe") == manifest["unchanged_reference_cli_sha256"]
    (destination / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    results.append({"variant": variant, "directory": str(destination), "prearm_empty_reads": policy,
                    "dll_sha256": sha(destination / "libsigrok-4.dll"), "files": len(manifest["files"])})

assert {key: sha(path) for key, path in runtime_files.items()} == source_manifest["existing_runtime_dll_sha256"]
summary_path = source.parent / "build-package-manifest.json"
if summary_path.exists():
    previous = json.loads(summary_path.read_text())["packages"]
    results = previous + results
summary = {"packages": results, "existing_runtime_dll_sha256": source_manifest["existing_runtime_dll_sha256"]}
summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
