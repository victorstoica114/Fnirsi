"""Isolate A07 per-operation deadlines on the frozen V3 SDK lifecycle."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent.parent
original = root / "artifacts/dla32-wch-upload-v3-source/libsigrok"
directory = root / "artifacts/dla32-wch-upload-v4-source"
source = directory / "libsigrok"
assert not directory.exists(), f"Refusing to overwrite {directory}"
shutil.copytree(original, source)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path):
    return {p.relative_to(path).as_posix(): sha(p) for p in sorted(path.rglob("*")) if p.is_file()}
protected = json.loads((root / "artifacts/dla32-wch-upload-v3-source/original-provenance.json").read_text())["protected_dll_sha256"]
for name in ["artifacts/dla32-wch-upload-v3/libsigrok-4.dll", "artifacts/dla32-wch-timeout/libsigrok-4.dll"]:
    protected[name] = sha(root / name)
provenance = {
    "created_utc": datetime.now(timezone.utc).isoformat(), "original_source": str(original),
    "isolated_source": str(source), "original_source_sha256": tree(original),
    "protected_dll_sha256": protected,
    "frozen_hardware_harness_sha256": sha(root / "artifacts/test_dla32_drain_ab.exe"),
    "timeout_reference_header_sha256": sha(root / "artifacts/dla32-wch-timeout-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h"),
    "timeout_reference_patch_sha256": sha(root / "artifacts/dla32-wch-timeout-source/wch-timeout.patch"),
    "kernel_sys_sha256": sha(Path(r"C:\Windows\System32\drivers\CH375W64.SYS")),
    "hardware_accessed": False,
}
assert provenance["frozen_hardware_harness_sha256"] == "65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82"
header = source / "src/hardware/fnirsi-dla32/transport-wch.h"
before = header.read_text()
reference = (root / "artifacts/dla32-wch-timeout-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h").read_text()
start = reference.index("static gboolean dla_wch_operation_timeout(")
end = reference.index("static int dla_wch_write(", start)
helper = reference[start:end]
after = before.replace("static int dla_wch_write(",
    "/* The installed SDK/kernel uses the first timeout field for these I/O\n"
    " * operations. Set all fields per operation; each write restores 1000 ms.\n"
    " * SDK upload ownership/order is unchanged from V3. Keep setter and I/O\n"
    " * adjacent on success so no log callback can alter the global deadline. */\n"
    + helper + "static int dla_wch_write(", 1)
old = "    if (!wch->opened)\n        return SR_ERR_DEV_CLOSED;\n"
assert after.count(old) == 1
after = after.replace(old, old + '    if (!dla_wch_operation_timeout(wch, 1000, "write"))\n        return SR_ERR_IO;\n', 1)
old = "    if (!(wch_api.timeout_ex ?\n        wch_api.timeout_ex(wch->index, 1000, timeout_ms, 1000, timeout_ms) :\n        wch_api.timeout(wch->index, 1000, timeout_ms)))\n"
assert after.count(old) == 1
after = after.replace(old, '    if (!dla_wch_operation_timeout(wch, timeout_ms, "read"))\n', 1)
header.write_text(after, encoding="utf-8", newline="\n")
relative = header.relative_to(source).as_posix()
patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/"+relative, tofile="b/"+relative))
(directory / "wch-upload-v4-diagnostic.patch").write_text(patch, encoding="utf-8")
hashes = tree(source)
changed = [name for name in hashes if hashes[name] != provenance["original_source_sha256"][name]]
assert changed == [relative]
api = "src/hardware/fnirsi-dla32/api.c"
assert hashes[api] == "e62b4d37029533ff0be04e3446d030897d28c241fd3a9d6d7d677cccc730d67d"
record = {
    "variant": "wch-upload-v4", "created_utc": datetime.now(timezone.utc).isoformat(),
    "source_directory": str(source), "changed_files_vs_v3": changed,
    "source_sha256": {name: hashes[name] for name in [api, relative, "src/hardware/fnirsi-dla32/protocol.h"]},
    "source_patch_sha256": sha(directory / "wch-upload-v4-diagnostic.patch"),
    "api_unchanged_vs_v3": True, "protocol_unchanged": True, "decoder_unchanged_vs_v3": True,
    "prearm_empty_reads": 1, "upload_diagnostic_enabled": True, "read_size_bytes": 1048576,
    "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20, "command_write_timeout_ms": 1000,
    "timeout_policy_unchanged": False,
    "timeout_policy": "before each read Ex(t,t,t,t) or legacy(t,t); before each write all fields1000",
    "timeout_helper_byte_identical_to_a07": True, "upload_pipe": 1, "upload_length_bytes": 1048576,
    "start_order": "disable -> STOP -> baseline drain -> SETUP/source/HEADER -> enable -> enabled Clear -> ARM",
    "retire_order": "successful disable -> successful STOP -> bounded post-stop drain",
    "sdk_disable_precedes_hardware_stop": True, "pending_stop_survives_application_end": True,
    "upload_clear_erased_byte_count": None, "upload_retirement_erased_byte_count": None,
    "alignment_fix_established": False, "hardware_accessed": False,
    "sdk0_pending_stop_generalization_included": False, "protected_dll_sha256": protected,
}
for name, value in [("original-provenance.json", provenance), ("source-sha256.json", hashes), ("source-manifest.json", record)]:
    (directory / name).write_text(json.dumps(value, indent=2)+"\n", encoding="utf-8")
for name in ["build_dla32_wch_upload_v3.sh", "run_dla32_wch_upload_v3_build_with_status.sh", "test_dla32_wch_upload_v3_mock.sh", "test_dla32_wch_upload_v3_compile_guards.sh"]:
    text = (root / "tools" / name).read_text().replace("dla32-wch-upload-v3", "dla32-wch-upload-v4").replace("dla32_wch_upload_v3", "dla32_wch_upload_v4").replace("V3", "V4")
    if name.startswith("build_"):
        text += '\nbash "$project/tools/test_dla32_wch_upload_v4_timeout.sh"\nbash "$project/tools/test_dla32_wch_upload_v4_compile_guards.sh"\n'
    target = root / "tools" / name.replace("_v3", "_v4")
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
print(json.dumps(record, indent=2))
