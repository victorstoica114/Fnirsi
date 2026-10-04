"""Record the exact local audit inputs and results without modifying sources."""
import hashlib
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime", default="artifacts/pulseview-dla32-reliability")
args = parser.parse_args()
runtime = (root / args.runtime).resolve()
if (root / "artifacts").resolve() not in runtime.parents:
    parser.error("Runtime must be inside workspace artifacts.")
paths = [
    "src/libsigrok/src/hardware/fnirsi-dla32/api.c",
    "src/libsigrok/src/hardware/fnirsi-dla32/protocol.h",
    "src/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h",
    "src/libsigrok/src/session.c",
    "src/libsigrok/src/std.c",
    "src/libsigrok/src/hwdriver.c",
    "src/libsigrok/src/backend.c",
    "src/libsigrok/src/libsigrok-internal.h",
    "src/pulseview/pv/binding/device.cpp",
    "tools/audit_dla32_driver.c",
    "tools/run_driver_audit.sh",
    "tools/test_dla32_protocol.c",
    "tools/run_protocol_audit.sh",
    "tools/snapshot_driver_audit.py",
    "tools/test_dla32_reliability.c",
    "tools/build_reliability_test.sh",
    "tools/rebuild_reliability.sh",
    "tools/package_windows.py",
    "logs/driver-audit-behavior.log",
    "logs/driver-audit-protocol.log",
    str((runtime / "libsigrok-4.dll").relative_to(root).as_posix()),
    "logs/libsigrok-reliability-tests.log",
    "logs/driver-reliability-hardware.log",
    "captures/2026-10-03-reliability-ESP32-PWM100kHz-D0-32ch-1MHz-10M-buffer.analysis.json",
    "captures/2026-10-03-reliability-ESP32-PWM100kHz-D0-32ch-50MHz-500k-buffer.analysis.json",
    "captures/2026-10-03-reliability-ESP32-PWM100kHz-D0-32ch-50MHz-500k-stream.analysis.json",
    "artifacts/pulseview-reliability-preview.png",
    "Porneste-PulseView-DLA32.cmd",
    "artifacts/source-changes/libsigrok.patch",
    "artifacts/source-changes/pulseview.patch",
    "AUDIT_DRIVER_DLA32.md",
    "PROIECT_DLA32_PULSEVIEW.md",
    "TESTE_ESP32_DLA32.md",
    "CUM_FOLOSIM_PULSEVIEW_DLA32.md",
]
behavior = (root / "logs/driver-audit-behavior.log").read_text(encoding="utf-8")
protocol = (root / "logs/driver-audit-protocol.log").read_text(encoding="utf-8")
summary = re.search(r"Audit: (\d+) checks, (\d+) pending behavioral failures", behavior)
protocol_counts = re.findall(r"PASS DLA-32 protocol: (\d+) checks", protocol)
if summary is None or len(protocol_counts) != 2 or len(set(protocol_counts)) != 1:
    raise SystemExit("Audit logs are incomplete; no successful manifest can be recorded.")
manifest = {
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "source_bases": json.loads((root / "sources.lock.json").read_text(encoding="utf-8"))["sources"],
    "behavior_checks": int(summary[1]),
    "behavior_failures": int(summary[2]),
    "protocol_checks_per_path": int(protocol_counts[0]),
    "protocol_paths": ["simd", "table"],
    "ubsan_available": "UBSan unavailable" not in protocol,
    "phase": "reliability_corrections",
    "hardware_accessed_during_behavior_simulation": False,
    "hardware_regressions_executed_separately": True,
    "driver_modified": True,
    "hardware_result_scope": "See hardware log and independent waveform analysis; a completed capture is not proof of losslessness.",
    "waveform_analysis_results": {
        name: json.loads((root / name).read_text(encoding="utf-8"))["passed"]
        for name in paths if name.endswith(".analysis.json")
    },
    "files": {
        name: {"bytes": (root / name).stat().st_size,
               "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
        for name in paths
    },
}
destination = root / "artifacts/driver-audit-manifest.json"
destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Reliability manifest: {len(paths)} inputs/results recorded; simulated and hardware evidence kept distinct.")
