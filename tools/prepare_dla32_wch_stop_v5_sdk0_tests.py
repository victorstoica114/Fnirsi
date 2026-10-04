"""Create a NEW SDK0 fixture; reuse frozen lifecycle checks with real transport helpers."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
destination = root / "artifacts/dla32-wch-stop-v5-tests/test_dla32_wch_stop_v5_sdk0_unit.c"
if destination.exists():
    raise SystemExit("Refusing to replace existing SDK0 fixture")
source = (root / "artifacts/dla32-wch-timeout-tests/test_dla32_wch_timeout_lifecycle.c").read_text()
start = source.index("#define LIBSIGROK_FNIRSI_DLA32_WCH_H")
stop = source.index("static int LIBUSB_CALL audit_bulk_transfer", start)
mock = r'''
/* All SDK symbols below are fake. The actual V5 header performs timeout and
 * read/write validation; no WCH DLL loads or hardware functions are called. */
static unsigned int sdk_write_calls, sdk_read_calls, sdk_setter_calls;
static unsigned int sdk_unknown_exports, sdk_timeout_bad, sdk_unprepared_io;
static unsigned int sdk_timeout_fail_nth, sdk_last_timeout, sdk_last_write_ms, sdk_last_read_ms;
static unsigned int sdk_short_stop_count;
static gboolean sdk_timeout_fail_all, sdk_timeout_prepared, sdk_rollback_stop_fail;
static const char *sdk_missing_export;
static BOOL sdk_prepare_timeout(ULONG index, ULONG a, ULONG b, ULONG c, ULONG d, gboolean ex)
{
    sdk_setter_calls++; sdk_timeout_prepared = FALSE;
    if (index != 0 || a != b || (ex && (a != c || a != d))) sdk_timeout_bad++;
    sdk_last_timeout = a;
    if (sdk_timeout_fail_all || sdk_setter_calls == sdk_timeout_fail_nth) {
        SetLastError(ERROR_GEN_FAILURE); return FALSE;
    }
    sdk_timeout_prepared = TRUE; return TRUE;
}
static BOOL WINAPI sdk_timeout(ULONG index, ULONG a, ULONG b)
{ return sdk_prepare_timeout(index, a, b, 0, 0, FALSE); }
static BOOL WINAPI sdk_timeout_ex(ULONG index, ULONG a, ULONG b, ULONG c, ULONG d)
{ return sdk_prepare_timeout(index, a, b, c, d, TRUE); }
static void sdk_note_io(gboolean write)
{
    if (!sdk_timeout_prepared) sdk_unprepared_io++;
    sdk_timeout_prepared = FALSE;
    if (write) { sdk_last_write_ms = sdk_last_timeout; if (sdk_last_timeout != 1000) sdk_timeout_bad++; }
    else sdk_last_read_ms = sdk_last_timeout;
}
static HANDLE WINAPI sdk_open(ULONG index)
{ (void)index; return (HANDLE)(uintptr_t)42; }
static VOID WINAPI sdk_close(ULONG index)
{ (void)index; close_calls++; }
static ULONG WINAPI sdk_usb_id(ULONG index)
{ (void)index; return 0x55371a86; }
static BOOL WINAPI sdk_write(ULONG index, PVOID data, PULONG length)
{
    int ret; unsigned int opcode = *length > 9 ? ((uint8_t *)data)[9] : 0;
    (void)index; sdk_note_io(TRUE); sdk_write_calls++;
    ret = audit_command(data, *length);
    if (ret != SR_OK) { *length = 0; SetLastError(ERROR_GEN_FAILURE); return FALSE; }
    if (opcode == 0x15 && sdk_rollback_stop_fail && header_attempts) {
        *length = 0; SetLastError(ERROR_GEN_FAILURE); return FALSE;
    }
    if (opcode == 0x15 && sdk_short_stop_count) { sdk_short_stop_count--; (*length)--; return TRUE; }
    return TRUE;
}
static BOOL WINAPI sdk_read(ULONG index, ULONG pipe, PVOID data, PULONG length)
{
    int actual = 0, ret;
    (void)index; (void)pipe; sdk_note_io(FALSE); sdk_read_calls++;
    ret = audit_read(data, *length, &actual); *length = actual;
    SetLastError(ret == LIBUSB_SUCCESS ? ERROR_SUCCESS :
        ret == LIBUSB_ERROR_TIMEOUT ? ERROR_TIMEOUT : ERROR_GEN_FAILURE);
    return ret == LIBUSB_SUCCESS;
}
static HMODULE WINAPI sdk_load_library(LPCWSTR filename, HANDLE file, DWORD flags)
{ (void)filename; (void)file; (void)flags; return (HMODULE)(uintptr_t)1; }
static BOOL WINAPI sdk_free_library(HMODULE module)
{ (void)module; return TRUE; }
static FARPROC WINAPI sdk_get_proc_address(HMODULE module, LPCSTR name)
{
    FARPROC address = NULL; (void)module;
    if (sdk_missing_export && !strcmp(name, sdk_missing_export)) return NULL;
#define SDK_EXPORT(symbol, function) do { \
    if (!strcmp(name, symbol)) { \
        __typeof__(&function) pointer = &function; \
        G_STATIC_ASSERT(sizeof(pointer) == sizeof(address)); \
        memcpy(&address, &pointer, sizeof(address)); return address; \
    } \
} while (0)
    SDK_EXPORT("CH375OpenDevice", sdk_open);
    SDK_EXPORT("CH375CloseDevice", sdk_close);
    SDK_EXPORT("CH375GetUsbID", sdk_usb_id);
    SDK_EXPORT("CH375WriteData", sdk_write);
    SDK_EXPORT("CH375ReadEndP", sdk_read);
    SDK_EXPORT("CH375SetTimeout", sdk_timeout);
    SDK_EXPORT("CH375SetTimeoutEx", sdk_timeout_ex);
#undef SDK_EXPORT
    sdk_unknown_exports++; return NULL;
}
#define LoadLibraryExW sdk_load_library
#define GetProcAddress sdk_get_proc_address
#define FreeLibrary sdk_free_library
#define LOG_PREFIX "fnirsi-dla32-sdk0-test"
#include "V5_HEADER_PATH"
#undef LOG_PREFIX
#undef LoadLibraryExW
#undef GetProcAddress
#undef FreeLibrary
G_STATIC_ASSERT(DLA_WCH_UPLOAD_DIAGNOSTIC == 0);
'''
header = root / "artifacts/dla32-wch-stop-v5-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h"
source = source[:start] + mock.replace("V5_HEADER_PATH", header.as_posix()) + "\n" + source[stop:]
source = source.replace("/dla32-wch-timeout-source/", "/dla32-wch-stop-v5-source/")
needle = "    delivered = accepted = 0; fake_trigger = NULL; f->use_wch = use_wch;"
assert source.count(needle) == 1
source = source.replace(needle, needle + r'''
    memset(&wch_api, 0, sizeof(wch_api)); sdk_missing_export = NULL;
    sdk_write_calls = sdk_read_calls = sdk_setter_calls = sdk_unknown_exports = 0;
    sdk_timeout_bad = sdk_unprepared_io = sdk_timeout_fail_nth = 0;
    sdk_last_timeout = sdk_last_write_ms = sdk_last_read_ms = sdk_short_stop_count = 0;
    sdk_timeout_fail_all = sdk_timeout_prepared = sdk_rollback_stop_fail = FALSE;
    if (use_wch) expect("SDK0 fixture binds only fake standard SDK exports", dla_wch_load() && !sdk_unknown_exports);
''')
# Binding is fixture setup, not a new inherited assertion: validate again in
# the authored SDK0 tests and abort immediately if the fake loader is broken.
source = source.replace('if (use_wch) expect("SDK0 fixture binds only fake standard SDK exports", dla_wch_load() && !sdk_unknown_exports);',
                        'if (use_wch && (!dla_wch_load() || sdk_unknown_exports)) { fprintf(stderr, "Fake SDK binding failed\\n"); abort(); }')
needle = "        queued_count() || sources || unsafe_frees) return FALSE;"
assert source.count(needle) == 1
source = source.replace(needle, "        queued_count() || sources || unsafe_frees || (f->use_wch && f->devc->upload_stop_pending)) return FALSE;", 1)
needle = "        opcode_failures = read_fail_call = 0; endless_drain = FALSE;"
source = source.replace(needle, needle + "\n        sdk_timeout_fail_all = sdk_rollback_stop_fail = FALSE; sdk_timeout_fail_nth = sdk_short_stop_count = 0;", 1)
needle = "    f->devc->samplerate = SR_MHZ(50); f->devc->threshold = 1;"
source = source.replace(needle, needle + "\n    f->devc->pwm[0].frequency = 1000000; f->devc->pwm[0].duty = 50;", 1)
needle = "        if (sr_dev_clear(&f->driver) != SR_OK) {"
source = source.replace(needle, "        if (f->use_wch && f->devc->upload_stop_pending) sr_dev_acquisition_stop(f->sdi);\n" + needle, 1)
needle = '    expect_transport(&f, "failed STOP cannot retain source or queued ownership", ends == 1 && resources_clean(&f)); fixture_clear(&f);'
assert source.count(needle) == 1
source = source.replace(needle, r'''
    expect_transport(&f, "failed STOP releases application callbacks but retains WCH pending ownership",
        ends == 1 && !has_application_resources(f.devc) &&
        (wch ? (f.devc->upload_stop_pending && !resources_clean(&f)) : resources_clean(&f)));
    if (wch) expect_transport(&f, "explicit failed-STOP retry clears pending before teardown",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f));
    fixture_clear(&f);''', 1)
cases = (root / "tools/dla32_wch_stop_v5_sdk0_cases.c.inc").read_text()
source = source.replace("int main(void)", cases + "\nint main(void)", 1)
needle = '    printf("Audit: %u checks, %u pending behavioral failures.'
assert source.count(needle) == 1
extra = r'''    printf("SDK0 inherited checks: %u (443 source checks plus one explicit retry).\n", checks);
    if (checks != 444) { failures++; printf("FAIL: inherited SDK0 coverage changed.\n"); }
    for (transport = 0; transport < 2; transport++) {
        gboolean legacy = transport != 0;
        test_sdk0_v5_prearm_failure(legacy);
        test_sdk0_v5_terminal_failure(legacy, FALSE);
        test_sdk0_v5_terminal_failure(legacy, TRUE);
        test_sdk0_v5_timeout_failure(legacy);
        test_sdk0_v5_rollback_failure(legacy, FALSE);
        test_sdk0_v5_rollback_failure(legacy, TRUE);
        test_sdk0_v5_exit_retry(legacy);
    }
    test_sdk0_v5_legacy_binding();
'''
source = source.replace(needle, extra + needle, 1)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(source, encoding="utf-8", newline="\n")
print(destination)
