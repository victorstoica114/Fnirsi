"""Offline V6 candidate: reuse proven V5 C++/PulseView binaries after ABI checks.

Does not load any DLL or invoke PulseView, Context, sigrok-cli, scan, or hardware.
Only objdump inspects PE files. Every original source/runtime stays unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
V5 = ROOT / "artifacts/pulseview-dla32-v5-cxx"
V6 = ROOT / "artifacts/dla32-wch-worker-v6-sdk0"
DEST = ROOT / "artifacts/pulseview-dla32-v6-cxx"
spec = importlib.util.spec_from_file_location("frozen_v5_package_helpers",
    ROOT / "tools/package_dla32_pulseview_v5_cxx.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
sha = helpers.sha


def require_digest(path, expected):
    if sha(path) != expected:
        raise RuntimeError("Frozen input differs: " + str(path))


def main():
    if DEST.exists():
        raise RuntimeError("Refusing to overwrite V6 candidate")
    require_digest(V5 / "package-manifest.json", "61b5202cf4f0cec56ced6ecabe29d1747a3147f373179d8960fe854e8a0de1ef")
    require_digest(V6 / "package-manifest.json", "0288c1f6fbb62a26a43c678d74796081e1a2c481165325a5cb3443a89b6db254")
    require_digest(V6 / "preflight-validation.json", "d40c31833ca42501df5c185f650a6d03190c4db8586732373dacb079ad92dba6")
    require_digest(ROOT / "tools/package_dla32_pulseview_v5_cxx.py",
        "95a89dba067caa148376caf9e480c422184f3e6383f55fe9abd5221057b769af")
    v5 = json.loads((V5 / "package-manifest.json").read_text())
    v6 = json.loads((V6 / "package-manifest.json").read_text())
    proof = json.loads((V6 / "preflight-validation.json").read_text())
    if proof["limited_hardware_gate_ready"] is not True or not all(c["passed"] is True for c in proof["checks"]):
        raise RuntimeError("V6 offline/review gate failed")
    protected = {ROOT / "artifacts/pulseview-dla32-reliability/libsigrok-4.dll":
        "76bfe1e98b37a5af01dcd39f453125978a5bfa86d622d49b5098ba4b226b7049"}
    for package, manifest in ((V5, v5), (V6, v6)):
        for name, entry in manifest["files"].items():
            path = package / name
            require_digest(path, entry["sha256"])
            if path.stat().st_size != entry["bytes"]:
                raise RuntimeError("Frozen input size differs")
            protected[path] = entry["sha256"]
    for row in proof["files"]:
        path = ROOT / row["path"]
        require_digest(path, row["sha256"])
        protected[path] = row["sha256"]

    old_source = ROOT / "artifacts/dla32-wch-stop-v5-source/libsigrok"
    new_source = ROOT / "artifacts/dla32-wch-worker-v6-source/libsigrok"
    old_files, new_files = helpers.tree(old_source), helpers.tree(new_source)
    changed = sorted(name for name in old_files.keys() & new_files.keys() if old_files[name] != new_files[name])
    added, removed = sorted(new_files.keys() - old_files.keys()), sorted(old_files.keys() - new_files.keys())
    if changed != ["src/hardware/fnirsi-dla32/api.c", "src/hardware/fnirsi-dla32/transport-wch.h"] or added != ["src/hardware/fnirsi-dla32/worker-wch.h"] or removed:
        raise RuntimeError("V6 changed source outside the proven private worker scope")
    public = sorted(name for name in old_files if name.startswith(("include/", "bindings/cxx/")) or name == "configure.ac")
    if any(old_files[name] != new_files[name] for name in public):
        raise RuntimeError("Public headers/C++ source/version declarations differ; fresh C++ build required")
    versions = [helpers.MSYS / "tmp" / name / "include/libsigrok/version.h" for name in (
        "dla32-pulseview-v5-cxx-prefix", "dla32-wch-worker-v6-prefix")]
    if versions[0].read_bytes() != versions[1].read_bytes():
        raise RuntimeError("Generated public package/library version headers differ")
    version_macros = {m.group(1): m.group(2) for m in re.finditer(
        r"^#define\s+(SR_(?:PACKAGE|LIB)_VERSION\w*)\s+(.+)$", versions[1].read_text(), re.MULTILINE)}

    objdump = helpers.UCRT / "bin/objdump.exe"
    dumps = {name: helpers.command([objdump, "-p", V5 / name]) for name in (
        "libsigrok-4.dll", "libsigrokcxx-4.dll", "pulseview.exe", "sigrok-cli.exe")}
    new_dump = helpers.command([objdump, "-p", V6 / "libsigrok-4.dll"])
    old_exports = helpers.exported_symbols(dumps["libsigrok-4.dll"])
    new_exports = helpers.exported_symbols(new_dump)
    if old_exports != new_exports:
        raise RuntimeError("Complete named core export set differs; fresh compatibility review required")
    dumps["libsigrok-4.dll"] = new_dump
    abi = {}
    for consumer, provider in (("pulseview.exe", "libsigrokcxx-4.dll"),
            ("pulseview.exe", "libsigrok-4.dll"), ("libsigrokcxx-4.dll", "libsigrok-4.dll"),
            ("sigrok-cli.exe", "libsigrok-4.dll")):
        imports = helpers.imported_symbols(dumps[consumer]).get(provider, [])
        exports = helpers.exported_symbols(dumps[provider])
        missing = sorted(set(imports) - exports)
        if not imports or missing:
            raise RuntimeError("Named-export binding/application ABI closure failed")
        abi[consumer + "->" + provider] = {"named_imports": len(imports), "missing": missing,
            "provider_exports": len(exports), "all_named_imports_resolved": True}

    excluded = {"package-manifest.json", "README.md", "Launch-PulseView-V5.ps1", "cxx-version.exe",
                "offline-cxx-ABI.log", "offline-pulseview-help-invalid.log",
                "offline-pulseview-help-valid.log", "offline-version.log"}
    shutil.copytree(V5, DEST, ignore=lambda directory, names: [n for n in names if n in excluded])
    shutil.copy2(V6 / "libsigrok-4.dll", DEST / "libsigrok-4.dll")
    retained = {}
    for name, entry in v5["files"].items():
        if name in excluded or name == "libsigrok-4.dll":
            continue
        require_digest(DEST / name, entry["sha256"])
        retained[name] = entry["sha256"]
    # Every unchanged PE's complete dependency list was proved in the V5
    # package. Its bytes and all providers are rechecked here; only core changed.
    dependency_imports = dict(v5["PE_dependency_imports"])
    dependency_imports.pop("cxx-version.exe", None)
    dependency_imports["libsigrok-4.dll"] = re.findall(r"DLL Name:\s*(\S+)", new_dump)
    system = Path(os.environ["SystemRoot"]) / "System32"
    for consumer, libraries in dependency_imports.items():
        if not (DEST / consumer).is_file():
            raise RuntimeError("Missing copied dependency consumer: " + consumer)
        for library in libraries:
            if not ((DEST / library).is_file() or (system / library).is_file() or
                    library.lower().startswith(("api-ms-", "ext-ms-"))):
                raise RuntimeError("Unresolved PE dependency: " + library)
    for name in ("LICENSE-libsigrok.txt", "LICENSE-libsigrokdecode.txt", "LICENSE-PulseView.txt"):
        if not (DEST / name).is_file():
            raise RuntimeError("Missing upstream license")

    launcher = f'''# Separate experimental V6 candidate; manual root/user launch only.
$ErrorActionPreference='Stop'
foreach ($pair in @(@('libsigrok-4.dll','{sha(V6 / "libsigrok-4.dll")}'),@('libsigrokcxx-4.dll','{sha(V5 / "libsigrokcxx-4.dll")}'),@('pulseview.exe','{sha(V5 / "pulseview.exe")}'))) {{
    if ((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $pair[0]) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pair[1]) {{ throw 'Candidate binary differs from frozen identity' }}
}}
$env:PYTHONHOME=$PSScriptRoot
$env:SIGROKDECODE_DIR=Join-Path $PSScriptRoot 'decoders'
$env:QT_PLUGIN_PATH=$PSScriptRoot
$env:PATH=$PSScriptRoot+';'+$env:PATH
Remove-Item Env:FNIRSI_WCH_DLL -ErrorAction SilentlyContinue
& (Join-Path $PSScriptRoot 'pulseview.exe') @args
exit $LASTEXITCODE
'''
    (DEST / "Launch-PulseView-V6.ps1").write_text(launcher, encoding="utf-8")
    (DEST / "README.md").write_text(
        "# Experimental PulseView DLA-32 V6 candidate\n\n"
        "This separate package uses the frozen V6 SDK0 worker core. PulseView and the C++ binding are reused byte for byte from the verified V5 C++ package. Public C headers, the complete C++ binding source, generated package/library version headers, the complete named core export set and all four application/binding import closures match. The existing copied-PulseView early log-level fix is retained.\n\n"
        "Launch manually with Launch-PulseView-V6.ps1 after exclusive analyzer ownership is established. The launcher verifies the three candidate binaries and selects the installed official SDK. Preparation invoked no application, Context, scan, SDK, or hardware.\n\n"
        "This package is a GUI experiment, with separate root hardware gates. Channel-shift and Buffer-content failure investigations remain open. No physical lossless Stream, trigger accuracy, 200 MB/s throughput, mid-payload cancellation, or complete GUI capture/stop reliability is claimed. Main and V5 runtimes remain unchanged.\n\n"
        "Original upstream GPL licenses remain in this package. The manifest records every file and the V5 binding provenance separately from the V6 core provenance.\n", encoding="utf-8")
    for path, digest in protected.items():
        require_digest(path, digest)
    provenance = {"created_utc": datetime.now(timezone.utc).isoformat(), "variant": "pulseview-dla32-v6-cxx",
        "core_origin": "isolated V6 SDK0 worker package", "core_DLL_sha256": sha(DEST / "libsigrok-4.dll"),
        "CXX_binding_origin": "frozen V5 CXX build; reused without rebuild", "PulseView_origin": "frozen copied V5 build with early log-level main.cpp patch",
        "core_built_with_cxx": False, "CXX_binding_reused": True,
        "V5_package_manifest_sha256": sha(V5 / "package-manifest.json"),
        "V6_package_manifest_sha256": sha(V6 / "package-manifest.json"),
        "V6_final_preflight_proof_sha256": sha(V6 / "preflight-validation.json"),
        "source_private_changes": changed, "source_private_additions": added,
        "source_removals": removed, "public_header_CXX_source_and_configure_anchors": {n: old_files[n] for n in public},
        "generated_version_headers": {str(p): sha(p) for p in versions}, "public_version_macros": version_macros,
        "complete_named_core_exports_identical": True, "named_core_exports": sorted(new_exports),
        "PE_name_export_ABI_checks": abi, "PE_dependency_imports": dependency_imports,
        "unchanged_V5_copied_file_sha256": retained,
        "source_tool_dependency_sha256": sha(ROOT / "tools/package_dla32_pulseview_v5_cxx.py"),
        "packager_script_sha256": sha(Path(__file__)), "originals_verified_unchanged": True,
        "hardware_accessed": False, "application_executed": False, "Context_created": False,
        "SDK_called": False, "scan_executed": False, "visible_or_interactive_GUI_shown": False,
        "GUI_hardware_validation_complete": False, "physical_lossless_capture_verified": False,
        "channel_or_byte_compensation_applied": False,
        "files": {p.relative_to(DEST).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
                  for p in sorted(DEST.rglob("*")) if p.is_file()}}
    helpers.save(DEST / "package-manifest.json", provenance)
    print(json.dumps({"package": str(DEST), "core_DLL_sha256": provenance["core_DLL_sha256"],
        "CXX_DLL_sha256": sha(DEST / "libsigrokcxx-4.dll"), "PulseView_sha256": sha(DEST / "pulseview.exe"),
        "manifest_sha256": sha(DEST / "package-manifest.json"), "ABI_checks": abi,
        "copied_V5_files": len(retained), "core_named_exports": len(new_exports),
        "hardware_accessed": False, "application_executed": False}, indent=2))


if __name__ == "__main__":
    main()
