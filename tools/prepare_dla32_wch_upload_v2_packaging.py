"""Create new V2 packaging/guard/verification tools, preserving all V1 files."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
tools = root / "tools"
package = (tools / "package_dla32_wch_upload.py").read_text()
package = package.replace("dla32-wch-upload", "dla32-wch-upload-v2")
package = package.replace('"wch-upload-diagnostic.patch"', '"wch-upload-v2-diagnostic.patch"')
package = package.replace('"variant": "wch-upload"', '"variant": "wch-upload-v2"')
package = package.replace('"test_dla32_wch_upload_unit.c"', '"test_dla32_wch_upload_v2_unit.c"')
package = package.replace('/test_dla32_wch_upload_unit.c"', '/test_dla32_wch_upload_v2_unit.c"')
package = package.replace(
    '    "upload_pipe": 1, "upload_length_bytes": 1048576,',
    '    "sample_read_timeout_ms_is_request_argument": True,\n'
    '    "drain_read_timeout_ms_is_request_argument": True,\n'
    '    "timeout_policy_unchanged": True,\n'
    '    "upload_pipe": 1, "upload_length_bytes": 1048576,', 1)
package = package.replace(
    '    "source_patch_sha256": sha(source_directory / "wch-upload-v2-diagnostic.patch"),',
    '    "source_patch_sha256": sha(source_directory / "wch-upload-v2-diagnostic.patch"),\n'
    '    "kernel_sys_sha256": provenance["kernel_sys_sha256"],\n'
    '    "kernel_note_sha256": sha(root / "downloads/dla32-trigger-research/CH375_KERNEL_UPLOAD_STATE.md"),\n'
    '    "upload_retirement_erased_byte_count": None,', 1)
begin = package.index('(destination / "README.md").write_text(')
end = package.index("cli_checks = {}", begin)
readme = '''# DLA32 WCH upload V2: enabled Clear and disable-only retirement

Separate diagnostic, DLA_WCH_UPLOAD_DIAGNOSTIC=1. Baseline pre-arm criterion is
one empty read. ReadEndP remains 1 MiB; timeout calls keep the existing requested
1000 ms sample and 20 ms drain arguments. Static SDK field routing shows that the
effective direct-read timeout differs from the drain argument. This diagnostic
keeps that timeout policy unchanged; a separate isolated change will address it.
API, decoder, wire trace, trigger, mask and acquisition limits match frozenV1.
Only transport-wch.h differs: upload enable precedes required Clear; successful
disable retires SDK ownership without a disabled Clear call.

Chosen start sequence: STOP -> disable0 -> baseline drain -> SETUP/source/HEADER
-> enable1 -> required Clear while enabled -> ARM -> unchanged ReadEndP.
Stop/failedARM: STOP -> disable0 -> post-stop drain. Beforeclose, disable is retried.
SDK Enable/Clear false causes startupIO beforeARM, even if rollback succeeds.
Disable false retains SDKdirty, blocks start/dev_clear/std_cleanup/sr_exit and
refuses close until an explicit successful retirement retry. Enabled Clear false
is always a capture failure; it is never ignored.

The measured SDK probe and installed kernel disassembly show that Clear/Query
on a disabled upload path return false/1450. The kernel disable path clears its
active flag, waits/cancels work and frees completed queue entries. This establishes
SDK/kernel retirement, not physical endpoint quiet, sample integrity or FPGA frame
alignment. Enable BOOLtrue alone does not prove read URBs or received samples.
Evidence: downloads/dla32-trigger-research/CH375_KERNEL_UPLOAD_STATE.md.

Clear and disable can erase internal queue data whose count is UNKNOWN. A logged
post-stop drain count covers only bytes read afterward; it is not a full accounting
of all discarded SDK data. Upload length1MiB is not known totalqueue capacity.
The wire file preserves every byte actually supplied to the decoder, including
data beyond the softwarelimit. No prefix is dropped, channel rotated or duty changed.
These diagnostics do not establish lossless transport or throughput performance.

The SDK DLL, kernel driver, primary, Kingst, A/B and frozenV1 runtimes are unchanged.
No hardware scan/capture or generator/ESP action occurred during build/packaging.
Newsource: artifacts/dla32-wch-upload-v2-source/libsigrok.
Newbuild: /tmp/dla32-wch-upload-v2-build/libsigrok.
Newprefix: /tmp/dla32-wch-upload-v2-prefix.
Nativeexit: logs/build-dla32-wch-upload-v2.status.json.
Realtransport mocked SDK tests: logs/dla32-wch-upload-v2-mock-final.log.
Set FNIRSI_DLA32_WIRE_TRACE to an absolute newfilename and retain -l4 logs.
'''
package = package[:begin] + '(destination / "README.md").write_text(' + repr(readme) + ', encoding="utf-8")\n' + package[end:]
guards = (tools / "test_dla32_wch_upload_compile_guards.sh").read_text().replace("dla32-wch-upload", "dla32-wch-upload-v2")
verify = (tools / "verify_dla32_wch_upload_package.py").read_text().replace("dla32-wch-upload", "dla32-wch-upload-v2")
verify = verify.replace('assert changed == ["src/hardware/fnirsi-dla32/api.c", "src/hardware/fnirsi-dla32/transport-wch.h"]',
                        'assert changed == ["src/hardware/fnirsi-dla32/transport-wch.h"]')
verify = verify.replace('== 497', '== 501').replace('Audit: 497 checks', 'Audit: 501 checks').replace('"mock_checks": 497', '"mock_checks": 501')
begin = verify.index('    "tools_sha256":')
end = verify.index('\n}', begin)
verify = verify[:begin] + '''    "tools_sha256": {name: sha(root / "tools" / name) for name in [
        "prepare_dla32_wch_upload_v2.py", "prepare_dla32_wch_upload_v2_tests.py",
        "dla32_wch_upload_v2_cases.c.inc", "test_dla32_wch_upload_v2_mock.sh",
        "test_dla32_wch_upload_v2_compile_guards.sh", "build_dla32_wch_upload_v2.sh",
        "run_dla32_wch_upload_v2_build_with_status.sh",
        "prepare_dla32_wch_upload_v2_packaging.py", "package_dla32_wch_upload_v2.py",
        "verify_dla32_wch_upload_v2_package.py",
    ]},''' + verify[end:]
verify = verify.replace('"upload_clear_erased_byte_count": None,', '"upload_clear_erased_byte_count": None, "upload_retirement_erased_byte_count": None,')
for name, contents in [
    ("package_dla32_wch_upload_v2.py", package),
    ("test_dla32_wch_upload_v2_compile_guards.sh", guards),
    ("verify_dla32_wch_upload_v2_package.py", verify),
]:
    target = tools / name
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(contents)
    print(target)
