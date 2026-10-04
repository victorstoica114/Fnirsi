"""Record isolated queue diagnostic changes and original-runtime immutability."""
from pathlib import Path
import difflib
import hashlib
import json

root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wch-upload-source"
original = root / "artifacts/dla32-drain-ab-source/libsigrok"
source = directory / "libsigrok"
provenance = json.loads((directory / "original-provenance.json").read_text())
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
assert {p.relative_to(original).as_posix(): sha(p) for p in sorted(original.rglob("*")) if p.is_file()} == provenance["original_source_sha256"]
assert {name: sha(root / name) for name in provenance["protected_dll_sha256"]} == provenance["protected_dll_sha256"]
hashes = {p.relative_to(source).as_posix(): sha(p) for p in sorted(source.rglob("*")) if p.is_file()}
changed = [name for name in hashes if hashes[name] != provenance["original_source_sha256"][name]]
assert changed == ["src/hardware/fnirsi-dla32/api.c", "src/hardware/fnirsi-dla32/transport-wch.h"], changed
patch = []
for name in changed:
    patch += difflib.unified_diff((original / name).read_text().splitlines(True),
                                (source / name).read_text().splitlines(True),
                                fromfile="a/" + name, tofile="b/" + name)
(directory / "wch-upload-diagnostic.patch").write_text("".join(patch), encoding="utf-8")
(directory / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
record = {
    "purpose": "one diagnostic variable: capture-scoped WCH SDK upload queue lifecycle",
    "source_directory": str(source), "changed_files": changed,
    "source_sha256": {name: hashes[name] for name in changed},
    "patch_sha256": sha(directory / "wch-upload-diagnostic.patch"),
    "source_sha256_map_sha256": sha(directory / "source-sha256.json"),
    "prearm_empty_reads": 1, "read_size_bytes": 1048576,
    "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20,
    "upload_length_bytes": 1048576, "upload_pipe": 1,
    "upload_clear_erased_byte_count": None,
    "upload_length_is_known_total_queue_capacity": False,
    "decoder_changed": False, "frame_or_channel_correction": False,
    "hardware_accessed": False,
    "abi_note_sha256": sha(root / "downloads/dla32-trigger-research/CH375_DLL_UPLOAD_ABI.md"),
    "abi_machine_record_sha256": sha(root / "downloads/dla32-trigger-research/ch375dll64-upload-abi.json"),
    "protected_dll_sha256": provenance["protected_dll_sha256"],
}
(directory / "source-manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
