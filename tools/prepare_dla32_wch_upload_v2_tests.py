"""Copy the frozen real transport fixture; model measured enabled/disabled states."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wch-upload-v2-tests"
directory.mkdir(exist_ok=False)
source = (root / "artifacts/dla32-wch-upload-tests/test_dla32_wch_upload_unit.c").read_text()
source = source.replace("/dla32-wch-upload-source/", "/dla32-wch-upload-v2-source/")
source = source.replace("static gboolean sdk_fail_disable_all, sdk_fail_clear_all, sdk_mock_enabled;",
                        "static gboolean sdk_fail_disable_all, sdk_fail_clear_all, sdk_mock_enabled;\nstatic gboolean sdk_fail_enable_side_effect;")
source = source.replace("    if (failed) { SetLastError(ERROR_GEN_FAILURE); return FALSE; }",
                        "    if (failed) { if (enable && sdk_fail_enable_side_effect) sdk_mock_enabled = TRUE;\n        SetLastError(ERROR_GEN_FAILURE); return FALSE; }", 1)
source = source.replace("    sdk_record(SDK_CLEAR); sdk_clear_calls++;", "    sdk_record(SDK_CLEAR); sdk_clear_calls++;\n    if (!sdk_mock_enabled) { SetLastError(ERROR_NO_SYSTEM_RESOURCES); return FALSE; }", 1)
source = source.replace("    sdk_fail_disable_all = sdk_fail_clear_all = sdk_mock_enabled = FALSE;",
                        "    sdk_fail_disable_all = sdk_fail_clear_all = sdk_mock_enabled = FALSE;\n    sdk_fail_enable_side_effect = FALSE;", 1)
start = source.index("static gboolean sdk_sequence")
end = source.index("int main(void)", start)
source = source[:start] + (root / "tools/dla32_wch_upload_v2_cases.c.inc").read_text() + "\n" + source[end:]
source = source.replace("    test_upload_close_only_false(); test_upload_public_exit_dirty();",
                        "    test_upload_close_only_false(); test_upload_public_exit_dirty();\n    test_upload_enable_false_unknown_side_effect();", 1)
assert "SDK_SETUP, SDK_ENABLE, SDK_CLEAR, SDK_ARM" in source
assert "ERROR_NO_SYSTEM_RESOURCES" in source
(directory / "test_dla32_wch_upload_v2_unit.c").write_text(source, encoding="utf-8", newline="\n")
print(directory / "test_dla32_wch_upload_v2_unit.c")
