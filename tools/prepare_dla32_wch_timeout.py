"""Prepare an isolated SDK0 operation-timeout correction from frozen A/B source."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent.parent
OLD = ROOT / "artifacts/dla32-drain-ab-source/libsigrok"
OUT = ROOT / "artifacts/dla32-wch-timeout-source"
NEW = OUT / "libsigrok"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


expected = json.loads((OLD.parent / "source-sha256.json").read_text())
old_hashes = {p.relative_to(OLD).as_posix(): sha(p) for p in OLD.rglob("*") if p.is_file()}
assert old_hashes == expected, "Frozen A/B source changed"
if OUT.exists():
    raise SystemExit(f"Refusing to overwrite {OUT}")
protected_names = [
    "pulseview-dla32-reliability", "pulseview-la1010-reference", "pulseview-dla32-wiretrace",
    "dla32-drain-ab-baseline", "dla32-drain-ab-two-empty", "dla32-wch-upload", "dla32-wch-upload-v2",
]
protected = {f"artifacts/{name}/libsigrok-4.dll": sha(ROOT / f"artifacts/{name}/libsigrok-4.dll")
             for name in protected_names}
shutil.copytree(OLD, NEW)
header = NEW / "src/hardware/fnirsi-dla32/transport-wch.h"
before = header.read_text()
helper = '''/* ReadEndP and WriteData both use the installed SDK/kernel's first timeout
 * field. Set every field before each operation; a short drain must not leave
 * the next command with its short read deadline. No SDK upload is enabled.
 * Keep setter and I/O adjacent on success: log callbacks cannot intervene. */
static gboolean dla_wch_operation_timeout(struct dla_wch *wch,
    unsigned int timeout_ms, const char *operation)
{
    BOOL ok;
    DWORD error;

    SetLastError(ERROR_SUCCESS);
    ok = wch_api.timeout_ex ?
        wch_api.timeout_ex(wch->index, timeout_ms, timeout_ms, timeout_ms, timeout_ms) :
        wch_api.timeout(wch->index, timeout_ms, timeout_ms);
    error = GetLastError();
    if (!ok)
        sr_err("WCH %s timeout configuration failed (%u ms, Windows error %lu).",
            operation, timeout_ms, error);
    return ok != FALSE;
}

'''
needle = 'static int dla_wch_write(struct dla_wch *wch, const uint8_t *data, ULONG length)'
after = before.replace(needle, helper + needle, 1)
needle = '    if (!wch->opened)\n        return SR_ERR_DEV_CLOSED;\n    if (!wch_api.write('
replacement = '    if (!wch->opened)\n        return SR_ERR_DEV_CLOSED;\n    if (!dla_wch_operation_timeout(wch, 1000, "write"))\n        return SR_ERR_IO;\n    if (!wch_api.write('
assert needle in after
after = after.replace(needle, replacement, 1)
needle = '''    if (!(wch_api.timeout_ex ?
        wch_api.timeout_ex(wch->index, 1000, timeout_ms, 1000, timeout_ms) :
        wch_api.timeout(wch->index, 1000, timeout_ms)))'''
assert needle in after
after = after.replace(needle, '    if (!dla_wch_operation_timeout(wch, timeout_ms, "read"))', 1)
header.write_text(after, encoding="utf-8", newline="\n")
relative = header.relative_to(NEW).as_posix()
patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  fromfile='a/' + relative, tofile='b/' + relative))
(OUT / "wch-timeout.patch").write_text(patch, encoding="utf-8")
hashes = {p.relative_to(NEW).as_posix(): sha(p) for p in NEW.rglob("*") if p.is_file()}
changed = sorted(name for name in hashes if hashes[name] != old_hashes[name])
assert changed == [relative]
provenance = {"original_source": str(OLD), "original_source_sha256": old_hashes,
              "protected_dll_sha256": protected, "hardware_accessed": False}
(OUT / "original-provenance.json").write_text(json.dumps(provenance, indent=2) + '\n', encoding="utf-8")
(OUT / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + '\n', encoding="utf-8")
record = {
    "variant": "wch-timeout", "source_directory": str(NEW), "changed_files": changed,
    "source_patch_sha256": sha(OUT / "wch-timeout.patch"),
    "prearm_empty_reads": 1, "upload_diagnostic_enabled": False, "read_size_bytes": 1048576,
    "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20, "command_write_timeout_ms": 1000,
    "operation_timeout_policy": "before every ReadEndP: Ex(t,t,t,t) or legacy(t,t); before every WriteData: Ex(1000,1000,1000,1000) or legacy(1000,1000)",
    "timeout_policy_unchanged": False, "api_unchanged_vs_baseline": True,
    "decoder_or_lane_correction": False, "trigger_or_readsize_or_queue_changes": False,
    "protocol_sha256": hashes["src/hardware/fnirsi-dla32/protocol.h"],
    "primary_timeout_evidence_sha256": sha(ROOT / "downloads/dla32-trigger-research/WCH_TIMEOUT_FIELD_MAPPING.md"),
    "direct_timeout_probe_sha256": sha(ROOT / "captures/wch-upload-v2-2026-10-03-r1/SDK-direct-timeouts-r1.json"),
    "hardware_accessed": False,
}
(OUT / "source-manifest.json").write_text(json.dumps(record, indent=2) + '\n', encoding="utf-8")
print(json.dumps(record, indent=2))
