"""Copy real V2 fixture and isolate STOP ordering / deferred device ownership."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wch-upload-v3-tests"
if directory.exists():
    assert sys.argv[1:] == ["--refresh-fixture"]
    assert not (root / "artifacts/dla32-wch-upload-v3").exists(), "V3 already packaged"
else:
    directory.mkdir()
source = (root / "artifacts/dla32-wch-upload-v2-tests/test_dla32_wch_upload_v2_unit.c").read_text()
source = source.replace("/dla32-wch-upload-v2-source/", "/dla32-wch-upload-v3-source/")
source = source.replace("UPLOAD V2:", "UPLOAD V3:")
source = source.replace("static gboolean sdk_fail_enable_side_effect;", "static gboolean sdk_fail_enable_side_effect;\nstatic gboolean sdk_reject_stop_when_enabled;\nstatic unsigned int sdk_active_stop_attempts;")
source = source.replace("    (void)index; sdk_write_calls++; ret = audit_command(data, *length);",
    "    (void)index; sdk_write_calls++; ret = audit_command(data, *length);\n"
    "    if (*length > 9 && ((const uint8_t *)data)[9] == 0x15 && sdk_mock_enabled) {\n"
    "        sdk_active_stop_attempts++;\n"
    "        /* Inject the observed case, without assuming a universal SDK rule. */\n"
    "        if (sdk_reject_stop_when_enabled) { *length = 0; SetLastError(ERROR_GEN_FAILURE); return FALSE; }\n"
    "    }", 1)
source = source.replace("    sdk_fail_enable_side_effect = FALSE;", "    sdk_fail_enable_side_effect = sdk_reject_stop_when_enabled = FALSE;\n    sdk_active_stop_attempts = 0;", 1)
source = source.replace("(f->use_wch && f->devc->wch.upload_dirty)", "(f->use_wch && (f->devc->wch.upload_dirty || f->devc->upload_stop_pending))")
source = source.replace("if (f->use_wch && f->devc->wch.upload_dirty && sr_dev_close(f->sdi) != SR_OK)", "if (f->use_wch && (f->devc->wch.upload_dirty || f->devc->upload_stop_pending) && sr_dev_close(f->sdi) != SR_OK)")
old = '    expect_transport(&f, "failed STOP cannot retain source or queued ownership", ends == 1 && resources_clean(&f)); fixture_clear(&f);'
new = '''    if (wch) {
        expect_transport(&f, "failed STOP retires application END but retains pending hardware ownership",
            ends == 1 && !has_application_resources(f.devc) && f.devc->upload_stop_pending
            && !f.devc->wch.upload_dirty && !resources_clean(&f));
        expect_transport(&f, "explicit inactive STOP retry retires pending hardware ownership",
            sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f));
    } else
        expect_transport(&f, "failed STOP cannot retain source or queued ownership", ends == 1 && resources_clean(&f));
    fixture_clear(&f);'''
assert source.count(old) == 1
source = source.replace(old, new, 1)
source = source.replace("{SDK_STOP, SDK_DISABLE, SDK_READ,", "{SDK_DISABLE, SDK_STOP, SDK_READ,")
source = source.replace("        SDK_STOP, SDK_DISABLE, SDK_READ, SDK_READ};", "        SDK_DISABLE, SDK_STOP, SDK_READ, SDK_READ};")
source = source.replace("exact STOP-disable-drain-SETUP-enable-clear-ARM order", "exact disable-STOP-drain-SETUP-enable-clear-ARM order")
source = source.replace("exact read-STOP-disable-poststopdrain without disabled clear", "exact read-disable-STOP-poststopdrain without disabled clear")
source = source.replace("enabled clear false sends rollbackSTOP and disable before returningIO", "enabled clear false sends disable then rollbackSTOP before returningIO")
old = '''        && !commands[0x11] && !commands[0x12] && commands[0x15] == 2
        && sdk_disable_calls == 2 && !sdk_clear_calls);'''
new = '''        && !commands[0x11] && !commands[0x12] && commands[0x15] == 1
        && sdk_disable_calls == 2 && !sdk_clear_calls);'''
assert source.count(old) == 1
source = source.replace(old, new, 1)
cases = (root / "tools/dla32_wch_upload_v3_cases.c.inc").read_text()
begin = source.index("static void test_upload_public_exit_dirty(void)")
end = source.index("static void test_upload_enable_false_unknown_side_effect(void)", begin)
pending_case = source[begin:end].replace("test_upload_public_exit_dirty", "test_upload_public_exit_pending_stop")
pending_case = pending_case.replace("SDKdirty", "pending hardware STOP").replace("SDK ownership", "pending hardware ownership")
pending_case = pending_case.replace("sdk_fail_disable_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);", "fail_opcode = 0x15; opcode_failures = 3; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);")
pending_case = pending_case.replace("f.devc->wch.upload_dirty", "f.devc->upload_stop_pending && !f.devc->wch.upload_dirty")
pending_case = pending_case.replace("!f.devc->upload_stop_pending && !f.devc->wch.upload_dirty", "!f.devc->upload_stop_pending && !f.devc->wch.upload_dirty")
pending_case = pending_case.replace("sdk_fail_disable_all = FALSE;", "opcode_failures = 0;")
cases += "\n" + pending_case
source = source.replace("int main(void)", cases + "\nint main(void)", 1)
source = source.replace("    test_upload_enable_false_unknown_side_effect();", "    test_upload_enable_false_unknown_side_effect();\n    test_upload_v3_observed_active_stop();\n    test_upload_v3_disable_before_stop_failure();\n    test_upload_v3_pending_stop_close_retry();\n    test_upload_v3_prearm_stop_failures();\n    test_upload_public_exit_pending_stop();", 1)
(directory / "test_dla32_wch_upload_v3_unit.c").write_text(source, encoding="utf-8", newline="\n")
for name in ["build_dla32_wch_upload_v2.sh", "run_dla32_wch_upload_v2_build_with_status.sh", "test_dla32_wch_upload_v2_mock.sh", "test_dla32_wch_upload_v2_compile_guards.sh"]:
    contents = (root / "tools" / name).read_text().replace("dla32-wch-upload-v2", "dla32-wch-upload-v3").replace("dla32_wch_upload_v2", "dla32_wch_upload_v3").replace("V2", "V3")
    target = root / "tools" / name.replace("_v2", "_v3")
    if target.exists():
        assert target.read_text() == contents
    else:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
print(directory / "test_dla32_wch_upload_v3_unit.c")
