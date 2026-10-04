"""Copy the frozen 534-check driver fixture and inject per-operation failures."""
from pathlib import Path
root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wch-upload-v4-tests"
assert not directory.exists()
directory.mkdir()
source = (root / "artifacts/dla32-wch-upload-v3-tests/test_dla32_wch_upload_v3_unit.c").read_text()
source = source.replace("/dla32-wch-upload-v3-source/", "/dla32-wch-upload-v4-source/")
old = "static const char *sdk_missing_export;"
new = old + '''
static unsigned int timeout_calls, timeout_fail_nth, timeout_bad_arguments;
static unsigned int timeout_io_without_success, timeout_last_ms;
static unsigned int timeout_last_read_ms, timeout_last_write_ms;
static gboolean timeout_fail_all, timeout_success_ready, timeout_fail_after_clear;
static BOOL sdk_prepare_timeout(ULONG index, ULONG a, ULONG b, ULONG c, ULONG d, gboolean ex)
{
    timeout_calls++; timeout_success_ready = FALSE;
    if (index != 0 || a != b || (ex && (a != c || a != d))) timeout_bad_arguments++;
    timeout_last_ms = a;
    if (timeout_fail_all || timeout_calls == timeout_fail_nth) {
        SetLastError(ERROR_GEN_FAILURE); return FALSE;
    }
    timeout_success_ready = TRUE; return TRUE;
}
static void sdk_timeout_io(gboolean write)
{
    if (!timeout_success_ready) timeout_io_without_success++;
    timeout_success_ready = FALSE;
    if (write) {
        timeout_last_write_ms = timeout_last_ms;
        if (timeout_last_ms != 1000) timeout_bad_arguments++;
    } else timeout_last_read_ms = timeout_last_ms;
}
'''
assert source.count(old) == 1
source = source.replace(old,new,1)
old = "{ (void)index; (void)write_ms; (void)read_ms; return TRUE; }"
assert source.count(old) == 1
source = source.replace(old,"{ return sdk_prepare_timeout(index, write_ms, read_ms, 0, 0, FALSE); }",1)
old = "{ (void)index; (void)a; (void)b; (void)c; (void)d; return TRUE; }"
assert source.count(old) == 1
source = source.replace(old,"{ return sdk_prepare_timeout(index, a, b, c, d, TRUE); }",1)
source = source.replace("    (void)index; sdk_write_calls++;", "    sdk_timeout_io(TRUE);\n    (void)index; sdk_write_calls++;",1)
source = source.replace("    (void)index; (void)pipe;\n    ret = audit_read", "    sdk_timeout_io(FALSE);\n    (void)index; (void)pipe;\n    ret = audit_read",1)
old = "    return TRUE;\n}\nstatic HMODULE WINAPI sdk_load_library"
assert source.count(old) == 1
source = source.replace(old,"    if (timeout_fail_after_clear) timeout_fail_nth = timeout_calls + 1;\n" + old,1)
old = "    sdk_active_stop_attempts = 0;"
source = source.replace(old,old+'''
    timeout_calls = timeout_fail_nth = timeout_bad_arguments = 0;
    timeout_io_without_success = timeout_last_ms = 0;
    timeout_last_read_ms = timeout_last_write_ms = 0;
    timeout_fail_all = timeout_success_ready = timeout_fail_after_clear = FALSE;''',1)
old = "        sdk_fail_disable_all = sdk_fail_clear_all = FALSE;"
source = source.replace(old,old+"\n        timeout_fail_all = timeout_fail_after_clear = FALSE; timeout_fail_nth = 0;",1)
cases = (root / "tools/dla32_wch_upload_v4_cases.c.inc").read_text()
source = source.replace("int main(void)", cases+"\nint main(void)",1)
old = '    printf("Audit: %u checks, %u pending behavioral failures.'
extra = '''    printf("V3 inherited checks: %u (expected534).\\n", checks);
    if (checks != 534) { failures++; printf("FAIL: inherited534-check coverage changed.\\n"); }
    for (transport = 0; transport < 2; transport++) {
        gboolean legacy = transport != 0;
        test_v4_prearm_setter_failure(legacy);
        test_v4_terminal_setter_failure(legacy);
        test_v4_arm_setter_failure(legacy);
        test_v4_read_setter_failure(legacy);
        test_v4_timeout_exit_retry(legacy);
    }
    test_v4_legacy_export_fallback();
'''
assert source.count(old) == 1
source = source.replace(old,extra+old,1)
(directory / "test_dla32_wch_upload_v4_unit.c").write_text(source, encoding="utf-8", newline="\n")
timeout = (root / "tools/test_dla32_wch_timeout_unit.c").read_text()
timeout = timeout.replace('#include "../artifacts/dla32-wch-timeout-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h"',
    '#include "'+(root / "artifacts/dla32-wch-upload-v4-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h").as_posix()+'"')
(directory / "test_dla32_wch_upload_v4_timeout_unit.c").write_text(timeout, encoding="utf-8", newline="\n")
print(directory)
