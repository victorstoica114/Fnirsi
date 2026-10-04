"""Copy the frozen drain A/B source for one controlled WCH queue diagnostic."""
from pathlib import Path
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent.parent
original = root / "artifacts/dla32-drain-ab-source/libsigrok"
destination = root / "artifacts/dla32-wch-upload-source/libsigrok"
if destination.exists():
    raise SystemExit(f"Refusing to overwrite {destination}")
shutil.copytree(original, destination)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
record = {
    "original_source": str(original), "isolated_source": str(destination),
    "original_source_sha256": {p.relative_to(original).as_posix(): sha(p)
                               for p in sorted(original.rglob("*")) if p.is_file()},
    "protected_dll_sha256": {name: sha(root / name) for name in [
        "artifacts/pulseview-dla32-reliability/libsigrok-4.dll",
        "artifacts/pulseview-la1010-reference/libsigrok-4.dll",
        "artifacts/pulseview-dla32-wiretrace/libsigrok-4.dll",
        "artifacts/dla32-drain-ab-baseline/libsigrok-4.dll",
        "artifacts/dla32-drain-ab-two-empty/libsigrok-4.dll",
    ]},
    "hardware_accessed": False,
}
(destination.parent / "original-provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(destination)
