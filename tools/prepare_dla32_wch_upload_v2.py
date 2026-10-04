"""Create a new queue-order diagnostic from the frozen first upload candidate."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent.parent
original = root / "artifacts/dla32-wch-upload-source/libsigrok"
directory = root / "artifacts/dla32-wch-upload-v2-source"
source = directory / "libsigrok"
if directory.exists():
    raise SystemExit(f"Refusing to overwrite {directory}")
shutil.copytree(original, source)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
provenance = {
    "original_source": str(original), "isolated_source": str(source),
    "original_source_sha256": {p.relative_to(original).as_posix(): sha(p)
                               for p in sorted(original.rglob("*")) if p.is_file()},
    "protected_dll_sha256": {name: sha(root / name) for name in [
        "artifacts/pulseview-dla32-reliability/libsigrok-4.dll",
        "artifacts/pulseview-la1010-reference/libsigrok-4.dll",
        "artifacts/pulseview-dla32-wiretrace/libsigrok-4.dll",
        "artifacts/dla32-drain-ab-baseline/libsigrok-4.dll",
        "artifacts/dla32-drain-ab-two-empty/libsigrok-4.dll",
        "artifacts/dla32-wch-upload/libsigrok-4.dll",
    ]},
    "kernel_sys_sha256": sha(Path(r"C:\Windows\System32\drivers\CH375W64.SYS")),
    "hardware_accessed": False,
}
(directory / "original-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
header = source / "src/hardware/fnirsi-dla32/transport-wch.h"
before = header.read_text()
after = before.replace(
    '    ret = dla_wch_upload_clear(wch, phase);\n    if (ret != SR_OK)\n        return ret;\n    wch->upload_dirty = FALSE;',
    '    /* The observed kernel disables/cancels/retires the SDK queue here.\n'
    '     * Clear on a disabled queue is rejected with ERROR_NO_SYSTEM_RESOURCES;\n'
    '     * it is not an additional retirement requirement. */\n'
    '    wch->upload_dirty = FALSE;', 1)
after = after.replace(
    '    ret = dla_wch_upload_clear(wch, "before-arm");\n    if (ret != SR_OK)\n        return ret;\n    return dla_wch_upload_set(wch, 1, "before-arm");',
    '    ret = dla_wch_upload_set(wch, 1, "before-arm");\n    if (ret != SR_OK)\n        return ret;\n'
    '    /* Required clear is performed only while the SDK upload path is enabled. */\n'
    '    return dla_wch_upload_clear(wch, "before-arm");', 1)
assert after != before and 'return dla_wch_upload_clear(wch, "before-arm");' in after
header.write_text(after, encoding="utf-8", newline="\n")
relative = header.relative_to(source).as_posix()
patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  fromfile="a/" + relative, tofile="b/" + relative))
(directory / "wch-upload-v2-diagnostic.patch").write_text(patch, encoding="utf-8")
hashes = {p.relative_to(source).as_posix(): sha(p) for p in sorted(source.rglob("*")) if p.is_file()}
changed = [name for name in hashes if hashes[name] != provenance["original_source_sha256"][name]]
assert changed == [relative]
(directory / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
record = {
    "variant": "wch-upload-v2", "source_directory": str(source), "changed_files_vs_v1": changed,
    "source_sha256": {name: hashes[name] for name in ["src/hardware/fnirsi-dla32/api.c", relative]},
    "source_patch_sha256": sha(directory / "wch-upload-v2-diagnostic.patch"),
    "prearm_empty_reads": 1, "upload_diagnostic_enabled": True,
    "read_size_bytes": 1048576, "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20,
    "upload_pipe": 1, "upload_length_bytes": 1048576,
    "start_order": "STOP -> disable -> baseline drain -> SETUP/source/HEADER -> enable -> enabled Clear -> ARM",
    "retire_order": "STOP -> successful disable -> post-stop drain; no disabled Clear",
    "upload_clear_erased_byte_count": None, "alignment_fix_established": False,
    "api_unchanged_vs_v1": hashes["src/hardware/fnirsi-dla32/api.c"] == provenance["original_source_sha256"]["src/hardware/fnirsi-dla32/api.c"],
    "protocol_unchanged": True, "hardware_accessed": False,
    "protected_dll_sha256": provenance["protected_dll_sha256"],
}
(directory / "source-manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
