"""Verify and package the independent SDK0 operation-timeout diagnostic."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
msys = Path((root / "tools/toolchain-path.txt").read_text().strip())
directory = root / "artifacts/dla32-wch-timeout-source"
source = directory / "libsigrok"
package = root / "artifacts/dla32-wch-timeout"
prefix = msys / "tmp/dla32-wch-timeout-prefix"
build = msys / "tmp/dla32-wch-timeout-build/libsigrok"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(path):
    return {p.relative_to(path).as_posix(): sha(p) for p in path.rglob("*") if p.is_file()}


provenance = json.loads((directory / "original-provenance.json").read_text())
settings = json.loads((directory / "source-manifest.json").read_text())
hashes = json.loads((directory / "source-sha256.json").read_text())
assert source_hashes(source) == hashes
assert source_hashes(Path(provenance["original_source"])) == provenance["original_source_sha256"]
assert {name: sha(root / name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
changed = sorted(name for name in hashes if hashes[name] != provenance["original_source_sha256"][name])
assert changed == ["src/hardware/fnirsi-dla32/transport-wch.h"]
status_path = root / "logs/build-dla32-wch-timeout.status.json"
status = json.loads(status_path.read_text())
assert status["native_bash_exit_code"] == 0
assert '-DDLA_PREARM_EMPTY_READS=1' in (build / "config.status").read_text()
assert '-DDLA_WCH_UPLOAD_DIAGNOSTIC=1' not in (build / "config.status").read_text()
lifecycle_path = root / "logs/dla32-wch-timeout-lifecycle.log"
helper_path = root / "logs/dla32-wch-timeout-helper.log"
lifecycle_text, helper_text = lifecycle_path.read_text(), helper_path.read_text()
lifecycle = re.search(r"Audit: (\d+) checks, (\d+) pending behavioral failures", lifecycle_text)
helper = re.search(r"Timeout audit: (\d+) checks, (\d+) failures", helper_text)
assert lifecycle and lifecycle.group(1) == '443' and lifecycle.group(2) == '0'
assert helper and int(helper.group(1)) >= 40 and helper.group(2) == '0'
assert lifecycle_text.count('PASS:') == 443 and 'FAIL:' not in lifecycle_text
assert helper_text.count('PASS:') == int(helper.group(1)) and 'FAIL:' not in helper_text
make_text = (build / "tests/main.log").read_text()
make = re.search(r"100%: Checks: (\d+), Failures: (\d+), Errors: (\d+)", make_text)
assert make and make.group(1) == '92' and make.group(2) == make.group(3) == '0'
assert 'PASS:' in (root / "logs/dla32-wch-timeout-sdk0-guard.log").read_text()
vendor = Path(r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll")
assert sha(vendor) == 'db19ce17fe9b731cc82b9c40f2c7da23b55f19661c46ce1cfba9952b8a8621f7'
baseline = root / "artifacts/dla32-drain-ab-baseline"
baseline_manifest = json.loads((baseline / "package-manifest.json").read_text())
for name, record in baseline_manifest["files"].items():
    assert sha(baseline / name) == record["sha256"] and (baseline / name).stat().st_size == record["bytes"]
package.mkdir(exist_ok=False)
for p in baseline.iterdir():
    if p.is_file() and (p.suffix.lower() == '.dll' or p.name in {
        'sigrok-cli.exe', 'LICENSE-libsigrok.txt', 'LICENSE-sigrok-cli.txt', 'sources.lock.json'
    }) and p.name != 'libsigrok-4.dll':
        shutil.copy2(p, package / p.name)
shutil.copy2(prefix / "bin/libsigrok-4.dll", package / "libsigrok-4.dll")
dll_data = (package / "libsigrok-4.dll").read_bytes()
assert b'CH375SetBufUploadEx' not in dll_data and b'CH375ClearBufUpload' not in dll_data
for name in ['wch-timeout.patch', 'source-manifest.json', 'source-sha256.json', 'original-provenance.json']:
    shutil.copy2(directory / name, package / name)
assert sha(root / "artifacts/test_dla32_drain_ab.exe") == '65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82'
shutil.copy2(root / "artifacts/test_dla32_drain_ab.exe", package / "test_dla32_drain_ab.exe")
for name in ['test_dla32_drain_ab.c', 'build_dla32_drain_ab.sh']:
    shutil.copy2(root / 'tools' / name, package / name)
assert sha(package / "test_dla32_drain_ab.exe") == sha(root / "artifacts/test_dla32_drain_ab.exe")
(package / "README.md").write_text('''# DLA32 independent direct-operation timeout correction (A07)

Isolated SDK0 runtime, frozen A/B baseline API, one-empty pre-arm policy, 1MiB
ReadEndP, original decoder/masks/trigger and capture limits. No SDK upload queue
exports, hardware reset, generator or ESP32 operation is introduced.

The installed SDK/kernel uses the first Ex DWORD for both direct ReadEndP and
legacy WriteData. Before every read, all four Ex fields are the requested read
deadline (legacy fallback: both fields). Before every command write, all four
are restored to 1000ms (legacy fallback: both fields1000). Timeout setter failure
prevents its USB I/O operation; failed reads initialize actual count to zero.
Existing partial timeout, overflow and write-length/error handling is retained.
No success log callback is inserted between a setter and its I/O call.

Sample read request1000ms, drain request20ms, identification read500ms; actual
timing needs measurement and includes driver/USB scheduling overhead. Open-time
legacy initialization is unchanged, then each actual read/write reapplies its
own tuple. This intentionally changes timeout routing, independently of the
V2/V3 queue experiments. It does not establish channel alignment, duty accuracy,
lossless transport or USB throughput. The complete wire prefix remains intact.

Primary evidence: downloads/dla32-trigger-research/WCH_TIMEOUT_FIELD_MAPPING.md
and captures/wch-upload-v2-2026-10-03-r1/SDK-direct-timeouts-r1.json.
All baseline/primary/reference/queue packages and installed DLL/SYS are preserved.
Build and package validation are hardware-free; root controls hardware testing.
''', encoding='utf-8')
cli_checks = {}
for executable, argument, label in [
    ('sigrok-cli.exe', '--version', 'version'),
    ('sigrok-cli.exe', '--list-supported', 'drivers'),
    ('test_dla32_drain_ab.exe', '--self-test', 'harness-self-test'),
]:
    result = subprocess.run([str(package / executable), argument], capture_output=True, text=True)
    log = root / f'logs/dla32-wch-timeout-{label}.log'
    with log.open('x', encoding='utf-8') as handle:
        handle.write(result.stdout + result.stderr)
    assert result.returncode == 0, result.stderr
    if argument == '--self-test':
        assert 'PASS self-test:' in result.stdout and 'no hardware' in result.stdout
    cli_checks[argument] = {'exit_code': result.returncode, 'log_sha256': sha(log)}
manifest = dict(settings)
manifest.update({
    'unit_checks': 443, 'unit_failures': 0, 'timeout_mock_checks': int(helper.group(1)), 'timeout_mock_failures': 0,
    'unit_fixture_sha256': sha(root / 'artifacts/dla32-wch-timeout-tests/test_dla32_wch_timeout_lifecycle.c'),
    'unit_log_sha256': sha(lifecycle_path), 'timeout_mock_source_sha256': sha(root / 'tools/test_dla32_wch_timeout_unit.c'),
    'timeout_mock_log_sha256': sha(helper_path),
    'make_check_cases': 92, 'make_check_failures': 0, 'make_check_errors': 0,
    'make_check_log_sha256': sha(build / 'tests/main.log'),
    'native_bash_exit_code': 0, 'native_build_status_sha256': sha(status_path),
    'hardware_accessed_during_build_or_packaging': False, 'cli_checks': cli_checks,
    'source_manifest': settings, 'protocol_unchanged': True,
    'build_directory': str(build), 'prefix_directory': str(prefix),
    'config_status_sha256': sha(build / 'config.status'),
    'vendor_sdk_path': str(vendor), 'vendor_sdk_sha256': sha(vendor),
    'hardware_harness_source_sha256': sha(root / 'tools/test_dla32_drain_ab.c'),
    'hardware_harness_build_script_sha256': sha(root / 'tools/build_dla32_drain_ab.sh'),
    'hardware_harness_reused_frozen_binary': True,
    'protected_dll_sha256': provenance['protected_dll_sha256'],
    'files': {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in sorted(package.iterdir()) if p.is_file()},
})
(package / 'package-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
assert {name: sha(root / name) for name in provenance['protected_dll_sha256']} == provenance['protected_dll_sha256']
validation = {
    'variant': 'wch-timeout', 'hardware_accessed': False,
    'native_bash_exit_code': 0, 'make_check_checks': 92, 'make_check_failures': 0,
    'mock_checks': 443, 'mock_failures': 0, 'timeout_mock_checks': int(helper.group(1)), 'timeout_mock_failures': 0,
    'harness_selftest_passed': True, 'package_directory': str(package),
    'dll_sha256': sha(package / 'libsigrok-4.dll'),
    'package_manifest_sha256': sha(package / 'package-manifest.json'),
    'source_files_verified': len(hashes), 'package_files_verified': len(manifest['files']),
    'changed_files': changed, 'source_patch_sha256': settings['source_patch_sha256'],
    'protected_dll_sha256': provenance['protected_dll_sha256'],
    'timeout_policy_changed_intentionally': True, 'alignment_fix_established': False,
}
with (directory / 'build-validation.json').open('x', encoding='utf-8') as handle:
    json.dump(validation, handle, indent=2); handle.write('\n')
print(json.dumps(validation, indent=2))
