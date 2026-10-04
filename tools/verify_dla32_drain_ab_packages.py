"""Verify both diagnostic packages and retained hardware-free test provenance."""
from pathlib import Path
import hashlib
import json
import re

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
source_dir = root / "artifacts/dla32-drain-ab-source"
source = source_dir / "libsigrok"
source_manifest = json.loads((source_dir / "source-manifest.json").read_text())
source_hashes = json.loads((source_dir / "source-sha256.json").read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read_log(path):
    data = path.read_bytes()
    return data.decode("utf-16" if data[:2] in {b"\xff\xfe", b"\xfe\xff"} else "utf-8")

assert len(source_hashes) == source_manifest["source_file_count"]
assert {p.relative_to(source).as_posix(): sha(p) for p in source.rglob("*") if p.is_file()} == source_hashes
original = Path(source_manifest["source_original"])
changed = [name for name, digest in source_hashes.items() if sha(original / name) != digest]
assert changed == source_manifest["changed_files"] == ["src/hardware/fnirsi-dla32/api.c"]
assert sha(original / changed[0]) == source_manifest["original_api_sha256"]

runtime_files = {
    "DLA32": root / "artifacts/pulseview-dla32-reliability/libsigrok-4.dll",
    "Kingst": root / "artifacts/pulseview-la1010-reference/libsigrok-4.dll",
    "diagnostic": root / "artifacts/pulseview-dla32-wiretrace/libsigrok-4.dll",
}
assert {name: sha(path) for name, path in runtime_files.items()} == source_manifest["existing_runtime_dll_sha256"]
reference = root / "artifacts/pulseview-la1010-reference"
results = []
for variant, policy in [("baseline", 1), ("two-empty", 2)]:
    package = root / f"artifacts/dla32-drain-ab-{variant}"
    manifest_file = package / "package-manifest.json"
    manifest = json.loads(manifest_file.read_text())
    assert manifest["prearm_empty_reads"] == policy
    for name, record in manifest["files"].items():
        path = package / name
        assert path.stat().st_size == record["bytes"] and sha(path) == record["sha256"], name
        if path.suffix.lower() in {".exe", ".dll"} and name not in {"libsigrok-4.dll", "test_dla32_drain_ab.exe"}:
            assert sha(path) == sha(reference / name), name
    assert sha(package / "libsigrok-4.dll") == sha(msys / f"tmp/dla32-drain-ab-prefix/{variant}/bin/libsigrok-4.dll")
    build = msys / f"tmp/dla32-drain-ab-build/{variant}/libsigrok"
    make_check = re.search(r"100%: Checks: (\d+), Failures: (\d+), Errors: (\d+)",
                           (build / "tests/main.log").read_text())
    assert make_check and make_check.group(2) == make_check.group(3) == "0"
    unit_path = root / f"logs/dla32-drain-ab-unit-{variant}.log"
    unit_log = unit_path.read_text()
    unit = re.search(r"Audit: (\d+) checks, (\d+) pending behavioral failures", unit_log)
    assert unit and unit.group(2) == "0" and "FAIL:" not in unit_log
    assert unit_log.count("PASS:") == int(unit.group(1))
    selftest_path = root / f"logs/dla32-drain-ab-harness-self-test-{variant}.log"
    assert "PASS self-test:" in read_log(selftest_path) and "no hardware" in read_log(selftest_path)
    results.append({
        "variant": variant, "prearm_empty_reads": policy,
        "package": str(package), "package_manifest_sha256": sha(manifest_file),
        "dll_sha256": sha(package / "libsigrok-4.dll"),
        "make_check": {"checks": int(make_check.group(1)), "failures": 0, "errors": 0},
        "driver_fixture": {"checks": int(unit.group(1)), "failures": 0, "log_sha256": sha(unit_path)},
        "harness_selftest_passed": True, "harness_selftest_log_sha256": sha(selftest_path),
    })
protocol = []
for mode in ["simd", "table"]:
    path = root / f"logs/dla32-drain-ab-protocol-{mode}.log"
    text = path.read_text()
    matches = re.search(r"PASS DLA-32 protocol: (\d+) checks", text)
    assert matches, text
    protocol.append({"mode": mode, "checks": int(matches.group(1)), "passed": True, "log_sha256": sha(path)})
guard_log = root / "logs/dla32-drain-ab-compile-policy-guards.log"
assert read_log(guard_log).count("PASS:") == 3
summary = {
    "scope": "hardware-free build, simulations, package provenance and integrity only",
    "alignment_fix_established": False,
    "changed_source_files": changed,
    "copied_source_files": len(source_hashes),
    "patch_sha256": sha(source_dir / "prearm-drain-ab.patch"),
    "source_manifest_sha256": sha(source_dir / "source-manifest.json"),
    "source_hashes_sha256": sha(source_dir / "source-sha256.json"),
    "packages": results, "decoder_protocol": protocol,
    "compile_policy_guards": {"checks": 3, "passed": True, "log_sha256": sha(guard_log)},
    "existing_runtime_dll_sha256": source_manifest["existing_runtime_dll_sha256"],
    "tools_sha256": {name: sha(root / "tools" / name) for name in [
        "prepare_dla32_drain_ab.py", "build_dla32_drain_ab_variants.sh",
        "test_dla32_drain_ab_guards.sh", "package_dla32_drain_ab.py",
        "verify_dla32_drain_ab_packages.py",
    ]},
}
(source_dir / "build-validation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
