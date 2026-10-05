#!/usr/bin/env python3
"""Prepare copied sources/package an isolated PulseView V5 SDK0 build, offline."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "artifacts/pulseview-dla32-v5-cxx-source"
DEST = ROOT / "artifacts/pulseview-dla32-v5-cxx"
ORIGINAL = ROOT / "artifacts/dla32-wch-stop-v5-source"
REFERENCE = ROOT / "artifacts/pulseview-dla32-reliability"
MSYS = Path((ROOT / "tools/toolchain-path.txt").read_text().strip())
UCRT = MSYS / "ucrt64"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree(path):
    return {p.relative_to(path).as_posix(): sha(p) for p in sorted(path.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(path).parts}


def save(path, record):
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(record, output, indent=2)
        output.write("\n")


def checked_originals(provenance):
    assert tree(ORIGINAL / "libsigrok") == provenance["original_libsigrok_sha256"]
    assert tree(ROOT / "src/pulseview") == provenance["original_pulseview_sha256"]
    for name, digest in provenance["protected_dll_sha256"].items():
        assert sha(ROOT / name) == digest, name


def prepare(prefix, build):
    assert not SOURCES.exists(), "Refusing to overwrite copied-source provenance"
    assert not DEST.exists(), "Refusing to overwrite an existing runtime"
    old = json.loads((ORIGINAL / "source-manifest.json").read_text())
    hashes = json.loads((ORIGINAL / "source-sha256.json").read_text())
    assert tree(ORIGINAL / "libsigrok") == hashes
    provenance = {"created_utc": datetime.now(timezone.utc).isoformat(),
                  "original_libsigrok_sha256": hashes,
                  "original_pulseview_sha256": tree(ROOT / "src/pulseview"),
                  "protected_dll_sha256": old["protected_dll_sha256"],
                  "prefix": str(prefix), "build": str(build), "SDK_upload_default": 0,
                  "prearm_empty_reads": 1, "hardware_accessed": False}
    # Include frozen CLI/harness identities in the protected set as well.
    for name in ("artifacts/dla32-wch-stop-v5-sdk0/libsigrok-4.dll",
                 "artifacts/test_dla32_drain_ab.exe"):
        provenance["protected_dll_sha256"][name] = sha(ROOT / name)
    checked_originals(provenance)
    SOURCES.mkdir()
    shutil.copytree(ORIGINAL / "libsigrok", SOURCES / "libsigrok")
    shutil.copytree(ROOT / "src/pulseview", SOURCES / "pulseview", ignore=shutil.ignore_patterns(".git"))
    assert tree(SOURCES / "libsigrok") == hashes
    assert tree(SOURCES / "pulseview") == provenance["original_pulseview_sha256"]
    save(SOURCES / "original-provenance.json", provenance)
    print("Prepared isolated byte-identical V5 SDK0/PulseView sources; no .git copied.")


def command(arguments, cwd=None, environment=None):
    result = subprocess.run([str(value) for value in arguments], cwd=cwd, env=environment,
                            capture_output=True, text=True, errors="replace", timeout=60)
    if result.returncode:
        raise RuntimeError(f"Offline command failed ({result.returncode}): {arguments[0]}\n" + result.stdout + result.stderr)
    return result.stdout + result.stderr


def imported_symbols(text):
    imports = {}
    current = None
    for line in text.splitlines():
        library = re.search(r"DLL Name:\s*(\S+)", line)
        if library:
            current = library[1].lower()
            imports.setdefault(current, [])
        elif current:
            entry = re.match(r"^\s*[0-9a-fA-F]+\s+(?:<none>\s+)?[0-9a-fA-F]+\s+(\S+)", line)
            if entry:
                imports[current].append(entry[1])
            elif not line.strip():
                current = None
    return imports


def exported_symbols(text):
    table = text.split("[Ordinal/Name Pointer] Table", 1)
    if len(table) != 2:
        raise RuntimeError("Missing PE export name table")
    return set(re.findall(r"^\s*\[\s*\d+\]\s+(?:\+base\[\s*\d+\]\s+[0-9a-fA-F]+\s+)?(\S+)", table[1], re.MULTILINE))


def package(prefix, build):
    provenance = json.loads((SOURCES / "original-provenance.json").read_text())
    checked_originals(provenance)
    assert not (DEST / "package-manifest.json").exists(), "Refusing to overwrite a finalized runtime"
    if DEST.exists():
        assert DEST.is_dir() and not DEST.is_symlink(), "Partial candidate must be our real fixed directory"
    change = json.loads((SOURCES / "pulseview-source-change.json").read_text())
    original_pv = provenance["original_pulseview_sha256"]
    current_pv = tree(SOURCES / "pulseview")
    assert set(current_pv) == set(original_pv)
    assert [name for name in current_pv if current_pv[name] != original_pv[name]] == ["main.cpp"]
    assert current_pv["main.cpp"] == change["new_main_cpp_sha256"]
    assert change["original_main_cpp_sha256"] == original_pv["main.cpp"]
    assert sha(SOURCES / "pulseview-early-loglevel.patch") == change["patch_sha256"]
    # The first full compile may have completed main.cpp before the authorized
    # copied-source fix landed. Rebuild this one dependency and install it here.
    incremental = command([UCRT / "bin/cmake.exe", "--build", build / "pulseview", "--parallel", "4"])
    incremental += command([UCRT / "bin/cmake.exe", "--install", build / "pulseview"])
    (build / "validation/early-loglevel-incremental.log").write_text(incremental, encoding="utf-8")
    for name in ("libsigrok-4.dll", "libsigrokcxx-4.dll", "pulseview.exe"):
        assert (prefix / "bin" / name).is_file(), name
    for name in ("api.c", "transport-wch.h", "protocol.h"):
        rel = Path("src/hardware/fnirsi-dla32") / name
        assert sha(SOURCES / "libsigrok" / rel) == sha(ORIGINAL / "libsigrok" / rel)
    fixtures = {name: sha(ROOT / "artifacts/dla32-wch-stop-v5-tests" / name) for name in (
        "test_dla32_wch_stop_v5_sdk0_unit.c", "test_dla32_wch_stop_v5_timeout_unit.c")}
    frozen_validation = json.loads((ROOT / "artifacts/dla32-wch-stop-v5-sdk0/package-manifest.json").read_text())
    assert fixtures["test_dla32_wch_stop_v5_sdk0_unit.c"] == frozen_validation["unit_fixture_sha256"]
    config = (build / "libsigrok/config.status").read_text(errors="replace")
    assert "--enable-cxx" in config and "-DDLA_PREARM_EMPTY_READS=1" in config
    assert "DLA_WCH_UPLOAD_DIAGNOSTIC=" not in config
    general_log = build / "libsigrok/tests/main.log"
    assert re.search(r"100%: Checks: 92, Failures: 0, Errors: 0", general_log.read_text())
    driver_log = build / "validation/sdk0.log"
    driver_text = driver_log.read_text()
    driver_checks = re.search(r"Audit: (\d+) checks, 0 pending behavioral failures", driver_text)
    assert driver_checks and int(driver_checks[1]) == 551 and driver_text.count("PASS:") == 551 and "FAIL:" not in driver_text
    helper_log = build / "validation/timeout.log"
    helper_text = helper_log.read_text()
    assert "Timeout audit: 47 checks, 0 failures." in helper_text and helper_text.count("PASS:") == 47
    # Reuse complete decoder/Python/Qt dependencies, then replace our fresh binaries.
    shutil.copytree(REFERENCE, DEST, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "package-manifest.json", "Porneste-PulseView.*"))
    for name in ("libsigrok-4.dll", "libsigrokcxx-4.dll", "pulseview.exe"):
        shutil.copy2(prefix / "bin" / name, DEST / name)
    shutil.copy2(ROOT / "artifacts/dla32-wch-stop-v5-sdk0/sigrok-cli.exe", DEST / "sigrok-cli.exe")
    shutil.copy2(build / "validation/cxx-version.exe", DEST / "cxx-version.exe")
    objdump = UCRT / "bin/objdump.exe"
    queue = list(DEST.rglob("*.dll")) + list(DEST.rglob("*.pyd")) + list(DEST.glob("*.exe"))
    system = Path(os.environ["SystemRoot"]) / "System32"
    seen, dependency_imports = set(), {}
    while queue:
        binary = queue.pop()
        if binary in seen:
            continue
        seen.add(binary)
        text = command([objdump, "-p", binary])
        libraries = re.findall(r"DLL Name:\s*(\S+)", text)
        dependency_imports[binary.relative_to(DEST).as_posix()] = libraries
        for name in libraries:
            if (DEST / name).is_file() or (system / name).is_file() or name.lower().startswith(("api-ms-", "ext-ms-")):
                continue
            source = next((place / name for place in (prefix / "bin", UCRT / "bin") if (place / name).is_file()), None)
            if source is None:
                raise RuntimeError("Unresolved dependency: " + name)
            shutil.copy2(source, DEST / name)
            queue.append(DEST / name)
    # Name-level ABI closure for the application's C++ imports and the binding's C imports.
    abi = {}
    for consumer, provider in (("pulseview.exe", "libsigrokcxx-4.dll"),
                               ("pulseview.exe", "libsigrok-4.dll"),
                               ("libsigrokcxx-4.dll", "libsigrok-4.dll"),
                               ("sigrok-cli.exe", "libsigrok-4.dll")):
        imports = imported_symbols(command([objdump, "-p", DEST / consumer])).get(provider, [])
        exports = exported_symbols(command([objdump, "-p", DEST / provider]))
        assert imports, "Missing named imports for ABI audit: " + consumer
        missing = sorted(set(imports) - exports)
        assert not missing, (consumer, provider, missing)
        abi[consumer + " -> " + provider] = {"named_imports": len(imports), "missing_exports": missing}
    environment = dict(os.environ)
    environment["PATH"] = str(DEST) + os.pathsep + environment.get("PATH", "")
    environment["QT_PLUGIN_PATH"] = str(DEST)
    environment["PYTHONHOME"] = str(DEST)
    environment["SIGROKDECODE_DIR"] = str(DEST / "decoders")
    version = command([DEST / "sigrok-cli.exe", "--version"], DEST, environment)
    cxx_version = command([DEST / "cxx-version.exe"], DEST, environment)
    for name in ("libsigrok-4.dll", "libsigrokcxx-4.dll"):
        match = re.search(r"^" + re.escape(name) + r"=(.+)$", cxx_version, re.MULTILINE)
        assert match and os.path.samefile(Path(match[1].strip()), DEST / name), cxx_version
    (DEST / "offline-version.log").write_text(version, encoding="utf-8")
    (DEST / "offline-cxx-ABI.log").write_text(cxx_version, encoding="utf-8")
    help_tests = {}
    for level, label in (("5", "valid"), ("6", "invalid")):
        output = command([DEST / "pulseview.exe", "-l", level, "--help"], DEST, environment)
        assert "Usage:" in output, output
        path = DEST / ("offline-pulseview-help-" + label + ".log")
        path.write_text(output, encoding="utf-8")
        help_tests[label] = {"arguments": ["-l", level, "--help"], "exit_code": 0,
                             "Context_created": False, "device_scan": False, "SDK_called": False,
                             "visible_GUI_launched": False, "log_sha256": sha(path),
                             "invalid_Qt_debug_message_captured_in_stdio": "invalid log level" in output if label == "invalid" else None,
                             "Windows_Qt_debug_output_is_not_required_in_stdio": True}
    (DEST / "README.md").write_text("""# Isolated PulseView V5 SDK0/C++ candidate

Fresh copied libsigrok V5 sources compiled with C++ bindings and a fresh PulseView
application. API/transport/decoder files remain byte-identical to frozen V5 SDK0.
Only copied PulseView main.cpp changes: the validated early -l option calls
sr_log_loglevel_set directly, checks its result, and avoids calling a method on
a null Context before Context::create. The original source remains preserved.
The upload macro is omitted, pre-arm policy is one empty read, and operation
timeouts remain writes1000ms/samples1000ms/drain20ms. No lane remapping or prefix
stripping is introduced. All existing runtimes and frozen sources are retained.

This is an experimental package. Previous channel shifts and failed Buffer
captures remain unresolved; this packaging step establishes no alignment fix,
physical continuity or complete hardware validation. No visible GUI, scan, SDK call,
generator command or ESP32 access occurred during build/package verification.

Offline validation includes general makecheck, frozen V5 actual-driver/timeout
fixtures, PE dependency/name-export closure, CLI --version and static C++ version
getters with resolved DLL paths. Valid/invalid -l followed by --help exits before
Context creation and device scanning; Qt application initialization still occurs.
GUI startup/capture/cancel/close behavior requires
separate root-controlled manual/hardware tests. Protocol tests are deferred.

Decoder/Python/Qt dependencies and their licenses are reused from the existing
protected PulseView package; sources and exact hashes are in package-manifest.json.
The official WCH SDK remains in its installed location and is dynamically loaded
only when the root/user opens a device. The launcher is not run by the build.
""", encoding="utf-8")
    (DEST / "Launch-PulseView-V5.ps1").write_text("""$env:PYTHONHOME=$PSScriptRoot
$env:SIGROKDECODE_DIR=Join-Path $PSScriptRoot 'decoders'
$env:QT_PLUGIN_PATH=$PSScriptRoot
$env:PATH=$PSScriptRoot+';'+$env:PATH
Remove-Item Env:FNIRSI_WCH_DLL -ErrorAction SilentlyContinue
& (Join-Path $PSScriptRoot 'pulseview.exe') @args
exit $LASTEXITCODE
""", encoding="utf-8-sig")
    checked_originals(provenance)
    manifest = {"variant": "pulseview-dla32-v5-cxx", "created_utc": datetime.now(timezone.utc).isoformat(),
                "SDK_upload_enabled": False, "upload_macro_omitted": True, "prearm_empty_reads": 1,
                "read_size_bytes": 1048576, "command_write_timeout_ms": 1000,
                "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20,
                "API_transport_protocol_identical_to_frozen_V5": True,
                "copy_source_provenance_sha256": sha(SOURCES / "original-provenance.json"),
                "built_libsigrok_source_sha256": tree(SOURCES / "libsigrok"),
                "built_pulseview_source_sha256": tree(SOURCES / "pulseview"),
                "PulseView_changed_files": ["main.cpp"], "early_log_level_null_Context_fixed": True,
                "PulseView_source_change": change,
                "PulseView_source_patch_sha256": sha(SOURCES / "pulseview-early-loglevel.patch"),
                "protected_originals_verified_unchanged": True, "protected_dll_sha256": provenance["protected_dll_sha256"],
                "make_check_cases": 92, "driver_checks": int(driver_checks[1]), "timeout_helper_checks": 47,
                "frozen_fixture_source_sha256": fixtures,
                "fixtures_linked_new_core_static_archive_sha256": sha(build / "libsigrok/.libs/libsigrok.a"),
                "fixture_executable_sha256": {name: sha(build / "validation" / (name + ".exe")) for name in ("sdk0", "timeout")},
                "failures": 0, "checks_sha256": {str(path.relative_to(MSYS)): sha(path) for path in (general_log, driver_log, helper_log)},
                "PE_dependency_imports": dependency_imports, "PE_name_export_ABI_checks": abi,
                "CLI_version_exit_code": 0, "CXX_static_version_exit_code": 0,
                "PulseView_early_log_level_help_checks": help_tests,
                "Qt_application_initialized_for_help_checks": True,
                "hardware_accessed": False, "GUI_launched": False, "SDK_called": False,
                "application_CLI_help_executed": True, "visible_or_interactive_GUI_shown": False,
                "build_script_sha256": sha(ROOT / "tools/build_dla32_pulseview_v5_cxx.sh"),
                "packager_script_sha256": sha(Path(__file__)),
                "hardware_GUI_validation_complete": False, "decoder_or_lane_correction": False,
                "files": {p.relative_to(DEST).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
                          for p in sorted(DEST.rglob("*")) if p.is_file()}}
    save(DEST / "package-manifest.json", manifest)
    print(json.dumps({"package": str(DEST), "DLL_sha256": sha(DEST / "libsigrok-4.dll"),
                      "CXX_DLL_sha256": sha(DEST / "libsigrokcxx-4.dll"), "PulseView_sha256": sha(DEST / "pulseview.exe"),
                      "manifest_sha256": sha(DEST / "package-manifest.json"), "ABI_checks": abi,
                      "driver_checks": int(driver_checks[1]), "hardware_accessed": False}, indent=2))


if __name__ == "__main__":
    assert len(sys.argv) == 4 and sys.argv[1] in ("prepare", "package")
    prefix, build = Path(sys.argv[2]), Path(sys.argv[3])
    (prepare if sys.argv[1] == "prepare" else package)(prefix, build)
