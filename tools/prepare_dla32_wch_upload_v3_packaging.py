"""Generate separate V3 packaging/verification tools without changing V2."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tools = root / "tools"
package = (tools / "package_dla32_wch_upload_v2.py").read_text()
package = package.replace("dla32-wch-upload-v2", "dla32-wch-upload-v3").replace("dla32_wch_upload_v2", "dla32_wch_upload_v3").replace("wch-upload-v2", "wch-upload-v3")
package = package.replace("import hashlib\n", "import hashlib\nfrom datetime import datetime, timezone\n", 1)
package = package.replace('    "variant": "wch-upload-v3", "prearm_empty_reads": 1,',
    '    "variant": "wch-upload-v3", "created_utc": datetime.now(timezone.utc).isoformat(),\n'
    '    "prearm_empty_reads": 1, "sdk_disable_precedes_hardware_stop": True,\n'
    '    "pending_hardware_stop_survives_application_end": True,', 1)
begin = package.index('(destination / "README.md").write_text(')
end = package.index("cli_checks = {}", begin)
readme = '''# DLA32 WCH upload V3: disable before capture STOP

Separate diagnostic based on frozen V2. Only api.c differs: SDK disable must
succeed before writing hardware capture STOP in pre-arm, normal completion,
startup rollback and explicit recovery. A failed disable suppresses STOP and
post-stop reads. SDK dirty and deferred hardware STOP ownership survive
application END and block new captures and context cleanup. A failed STOP leaves
pending ownership even when the SDK queue was successfully retired.

An explicit inactive acquisition_stop or close retries disable -> capture STOP
-> unchanged bounded post-stop drain. Close preserves active context/handle on
failure. Pre-arm failure returns IO even if its one cleanup retry succeeds.
Close without a pending capture STOP retires only SDK ownership, as in V2.

The active-upload STOP false/Windows31 occurred in one V2 hardware capture. The
mock injects that observed case; V3 does not assume it is a universal SDK rule.
This build establishes neither a hardware recovery nor a channel alignment fix.

Macro DLA_WCH_UPLOAD_DIAGNOSTIC=1 and one empty pre-arm read remain unchanged.
The V2 transport header, decoder, wire trace, trigger/masks, acquisition limits,
1 MiB reads and timeout call arguments remain byte-identical. Requested sample
timeout is 1000 ms and requested drain argument is 20 ms; effective SDK field
routing differs and is deliberately reserved for a separate isolated change.

The SDK queue enable -> required enabled Clear -> ARM order is unchanged.
Clear/disable can erase queue data with UNKNOWN count. Post-stop drain logs
account only for bytes returned afterward. Bounded drain does not prove physical
endpoint quiet, FPGA alignment, lossless transport or throughput performance.
No prefix is dropped, channels rotated or duty compensated. All decoder input
bytes remain in the wire file, including input beyond the software sample limit.

Primary, Kingst, wiretrace, A/B, upload V1 and V2 runtimes remain unchanged.
No hardware or generator/ESP action occurred during source/build/packaging.
Source: artifacts/dla32-wch-upload-v3-source/libsigrok.
Build: /tmp/dla32-wch-upload-v3-build/libsigrok; a fresh build without object reuse.
Prefix: /tmp/dla32-wch-upload-v3-prefix.
Native status: logs/build-dla32-wch-upload-v3.status.json.
Mock tests: logs/dla32-wch-upload-v3-mock-final.log.
Use a new absolute FNIRSI_DLA32_WIRE_TRACE filename and retain -l4 logs.
UTC creation stamps cross into Oct4 in Europe/Bucharest; existing chain IDs
using Oct3 remain unchanged to preserve experiment provenance.
'''
package = package[:begin] + '(destination / "README.md").write_text(' + repr(readme) + ', encoding="utf-8")\n' + package[end:]
verify = (tools / "verify_dla32_wch_upload_v2_package.py").read_text()
verify = verify.replace("dla32-wch-upload-v2", "dla32-wch-upload-v3").replace("dla32_wch_upload_v2", "dla32_wch_upload_v3").replace("wch-upload-v2", "wch-upload-v3")
verify = verify.replace('assert changed == ["src/hardware/fnirsi-dla32/transport-wch.h"]',
    'assert changed == ["src/hardware/fnirsi-dla32/api.c"]\n'
    'assert hashes["src/hardware/fnirsi-dla32/transport-wch.h"] == provenance["original_source_sha256"]["src/hardware/fnirsi-dla32/transport-wch.h"]')
verify = verify.replace("501", "534")
verify = verify.replace('    "scope":', '    "sdk_disable_precedes_hardware_stop": True,\n    "pending_hardware_stop_survives_application_end": True,\n    "scope":', 1)
verify = verify.replace('"prepare_dla32_wch_upload_v3_tests.py",', '"prepare_dla32_wch_upload_v3_tests.py",', 1)
for name, contents in [
    ("package_dla32_wch_upload_v3.py", package),
    ("verify_dla32_wch_upload_v3_package.py", verify),
]:
    with (tools / name).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(contents)
    print(tools / name)
