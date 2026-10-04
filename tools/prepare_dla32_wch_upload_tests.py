"""Extend a NEW fixture with real transport code behind mocked WinAPI exports."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wch-upload-tests"
directory.mkdir(exist_ok=True)
driver = root / "artifacts/dla32-wch-upload-source/libsigrok/src/hardware/fnirsi-dla32/api.c"
transport = driver.with_name("transport-wch.h")
fixture = (root / "artifacts/dla32-drain-ab-tests/test_dla32_drain_ab_unit.c").read_text()
old_driver = root / "artifacts/dla32-drain-ab-source/libsigrok/src/hardware/fnirsi-dla32/api.c"
fixture = fixture.replace(old_driver.as_posix(), driver.as_posix())
assert driver.as_posix() in fixture
fixture = fixture.replace("static unsigned int reads_at_arm;", r'''static unsigned int reads_at_arm;
enum sdk_event { SDK_STOP = 0x15, SDK_SETUP = 0x11, SDK_ARM = 0x12,
    SDK_DISABLE = 100, SDK_CLEAR, SDK_ENABLE, SDK_READ };
static int sdk_events[4096];
static unsigned int sdk_event_count, sdk_disable_calls, sdk_clear_calls, sdk_enable_calls;
static unsigned int sdk_fail_disable_nth, sdk_fail_clear_nth, sdk_fail_enable_nth;
static unsigned int sdk_free_calls, sdk_bad_arguments, sdk_open_calls, sdk_write_calls;
static gboolean sdk_fail_disable_all, sdk_fail_clear_all, sdk_mock_enabled;
static const char *sdk_missing_export;
static void sdk_record(int event)
{ if (sdk_event_count < ARRAY_SIZE(sdk_events)) sdk_events[sdk_event_count++] = event; }
''', 1)
fixture = fixture.replace("    commands[opcode]++;", "    commands[opcode]++;\n    if (opcode == 0x15 || opcode == 0x11 || opcode == 0x12) sdk_record(opcode);", 1)
fixture = fixture.replace("static int audit_read(uint8_t *data, int length, int *actual)\n{", "static int audit_read(uint8_t *data, int length, int *actual)\n{\n    sdk_record(SDK_READ);", 1)
begin = fixture.index("#define LIBSIGROK_FNIRSI_DLA32_WCH_H")
end = fixture.index("static int LIBUSB_CALL audit_bulk_transfer", begin)
mock = r'''
/* Real transport-WCH implementation resolves only fake exports in this fixture.
 * No LoadLibrary, WCH DLL, device open, or USB function is executed. */
static HANDLE WINAPI sdk_open(ULONG index)
{ (void)index; sdk_open_calls++; return (HANDLE)(uintptr_t)42; }
static VOID WINAPI sdk_close(ULONG index)
{ (void)index; close_calls++; }
static ULONG WINAPI sdk_usb_id(ULONG index)
{ (void)index; return 0x55371a86; }
static BOOL WINAPI sdk_timeout(ULONG index, ULONG write_ms, ULONG read_ms)
{ (void)index; (void)write_ms; (void)read_ms; return TRUE; }
static BOOL WINAPI sdk_timeout_ex(ULONG index, ULONG a, ULONG b, ULONG c, ULONG d)
{ (void)index; (void)a; (void)b; (void)c; (void)d; return TRUE; }
static BOOL WINAPI sdk_write(ULONG index, PVOID data, PULONG length)
{
    int ret;
    (void)index; sdk_write_calls++; ret = audit_command(data, *length);
    if (ret == SR_OK) return TRUE;
    *length = 0; SetLastError(ERROR_GEN_FAILURE); return FALSE;
}
static BOOL WINAPI sdk_read(ULONG index, ULONG pipe, PVOID data, PULONG length)
{
    int actual = 0, ret;
    (void)index; (void)pipe;
    ret = audit_read(data, *length, &actual);
    *length = actual;
    SetLastError(ret == LIBUSB_SUCCESS ? ERROR_SUCCESS :
        ret == LIBUSB_ERROR_TIMEOUT ? ERROR_TIMEOUT : ERROR_GEN_FAILURE);
    return ret == LIBUSB_SUCCESS;
}
static BOOL WINAPI sdk_set_upload(ULONG index, ULONG enable, ULONG pipe, ULONG length)
{
    unsigned int nth;
    gboolean failed;
    if (index != 0 || pipe != 1 || length != 1048576 || enable > 1) sdk_bad_arguments++;
    sdk_record(enable ? SDK_ENABLE : SDK_DISABLE);
    nth = enable ? ++sdk_enable_calls : ++sdk_disable_calls;
    failed = enable ? nth == sdk_fail_enable_nth :
        sdk_fail_disable_all || nth == sdk_fail_disable_nth;
    if (failed) { SetLastError(ERROR_GEN_FAILURE); return FALSE; }
    sdk_mock_enabled = enable != 0;
    return TRUE;
}
static BOOL WINAPI sdk_clear_upload(ULONG index, ULONG pipe)
{
    if (index != 0 || pipe != 1) sdk_bad_arguments++;
    sdk_record(SDK_CLEAR); sdk_clear_calls++;
    if (sdk_fail_clear_all || sdk_clear_calls == sdk_fail_clear_nth) {
        SetLastError(ERROR_GEN_FAILURE); return FALSE;
    }
    return TRUE;
}
static HMODULE WINAPI sdk_load_library(LPCWSTR filename, HANDLE file, DWORD flags)
{ (void)filename; (void)file; (void)flags; return (HMODULE)(uintptr_t)1; }
static BOOL WINAPI sdk_free_library(HMODULE module)
{ (void)module; sdk_free_calls++; return TRUE; }
static FARPROC WINAPI sdk_get_proc_address(HMODULE module, LPCSTR name)
{
    FARPROC address = NULL;
    (void)module;
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
    SDK_EXPORT("CH375SetBufUploadEx", sdk_set_upload);
    SDK_EXPORT("CH375ClearBufUpload", sdk_clear_upload);
#undef SDK_EXPORT
    return NULL;
}
#define LoadLibraryExW sdk_load_library
#define GetProcAddress sdk_get_proc_address
#define FreeLibrary sdk_free_library
#define g_get_monotonic_time audit_time
#define LOG_PREFIX "fnirsi-dla32"
TRANSPORT_INCLUDE
#undef LoadLibraryExW
#undef GetProcAddress
#undef FreeLibrary
#undef g_get_monotonic_time
#undef LOG_PREFIX
'''.replace("TRANSPORT_INCLUDE", f'#include "{transport.as_posix()}"')
fixture = fixture[:begin] + mock + fixture[end:]
fixture = fixture.replace("    prearm_script_length = prearm_script_position = reads_at_arm = 0;", r'''    prearm_script_length = prearm_script_position = reads_at_arm = 0;
    sdk_event_count = sdk_disable_calls = sdk_clear_calls = sdk_enable_calls = 0;
    sdk_fail_disable_nth = sdk_fail_clear_nth = sdk_fail_enable_nth = 0;
    sdk_free_calls = sdk_bad_arguments = sdk_open_calls = sdk_write_calls = 0;
    sdk_fail_disable_all = sdk_fail_clear_all = sdk_mock_enabled = FALSE;
    sdk_missing_export = NULL;
    memset(&wch_api, 0, sizeof(wch_api));
    if (!dla_wch_load()) { expect("Mock SDK exports load", FALSE); return; }
''', 1)
fixture = fixture.replace("        queued_count() || sources || unsafe_frees) return FALSE;", "        queued_count() || sources || unsafe_frees ||\n        (f->use_wch && f->devc->wch.upload_dirty)) return FALSE;", 1)
fixture = fixture.replace("        opcode_failures = read_fail_call = 0; endless_drain = FALSE;", r'''        opcode_failures = read_fail_call = 0; endless_drain = FALSE;
        sdk_fail_disable_nth = sdk_fail_clear_nth = sdk_fail_enable_nth = 0;
        sdk_fail_disable_all = sdk_fail_clear_all = FALSE;
''', 1)
fixture = fixture.replace("        if (sr_dev_clear(&f->driver) != SR_OK) {", r'''        if (f->use_wch && f->devc->wch.upload_dirty && sr_dev_close(f->sdi) != SR_OK) {
            expect("Fixture teardown retires dirty fake SDK ownership", FALSE); return;
        }
        if (sr_dev_clear(&f->driver) != SR_OK) {''', 1)
extra = r'''
static gboolean sdk_sequence(const int *expected, unsigned int count)
{ return sdk_event_count == count && !memcmp(sdk_events, expected, count * sizeof(*expected)); }

static void test_upload_normal_and_repeat(void)
{
    struct fixture f;
    const int start_events[] = {SDK_STOP, SDK_DISABLE, SDK_CLEAR, SDK_READ,
        SDK_SETUP, SDK_CLEAR, SDK_ENABLE, SDK_ARM};
    const int complete_events[] = {SDK_STOP, SDK_DISABLE, SDK_CLEAR, SDK_READ,
        SDK_SETUP, SDK_CLEAR, SDK_ENABLE, SDK_ARM, SDK_READ,
        SDK_STOP, SDK_DISABLE, SDK_CLEAR, SDK_READ, SDK_READ};
    fixture_init(&f, TRUE); f.devc->stream = TRUE; f.devc->limits.limit_samples = 8;
    expect("UPLOAD: starts with active dirty SDK ownership", start(&f)
        && f.devc->wch.upload_dirty && f.devc->wch.upload_enabled && sdk_mock_enabled);
    expect("UPLOAD: exact STOP-disable-clear-drain-SETUP-clear-enable-ARM order",
        sdk_sequence(start_events, ARRAY_SIZE(start_events)));
    expect("UPLOAD: SDK argument widths and pipe1/1MiB tuple", !sdk_bad_arguments);
    feed(&f, 32);
    expect("UPLOAD: normal completion delivers8 and retires SDK/application resources",
        accepted == 8 && ends == 1 && resources_clean(&f)
        && !f.devc->wch.upload_enabled && !sdk_mock_enabled);
    expect("UPLOAD: exact read-STOP-disable-clear-poststopdrain completion order",
        sdk_sequence(complete_events, ARRAY_SIZE(complete_events)));
    expect("UPLOAD: repeated capture starts cleanly", start(&f) && sdk_enable_calls == 2
        && f.devc->wch.upload_dirty && f.devc->wch.upload_enabled);
    feed(&f, 32);
    expect("UPLOAD: repeated capture stops without queue ownership leak", ends == 2
        && resources_clean(&f) && sdk_disable_calls == 4 && sdk_clear_calls == 6
        && !sdk_bad_arguments && !sdk_mock_enabled);
    fixture_clear(&f);
}

static void test_upload_start_failures(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_enable_nth = 1;
    expect("UPLOAD: enable false rejects startup despite successful retirement retry",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x12] && commands[0x15] == 2 && ends == 1
        && sdk_enable_calls == 1 && !sdk_mock_enabled);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_clear_nth = 1;
    expect("UPLOAD: initial clear false sends rollbackSTOP beforeSETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x11] && !commands[0x12] && commands[0x15] == 2
        && !headers && !sdk_enable_calls && sdk_clear_calls == 2);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_clear_nth = 2;
    expect("UPLOAD: beforeARM clear false rolls back HEADER and never enables",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x12] && commands[0x15] == 2 && ends == 1
        && !sdk_enable_calls && sdk_clear_calls == 3);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_disable_nth = 1;
    expect("UPLOAD: initial disable false rejects startup even after retry succeeds",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x11] && !commands[0x12] && commands[0x15] == 2
        && sdk_disable_calls == 2 && sdk_clear_calls == 1);
    fixture_clear(&f);
    fixture_init(&f, TRUE); fail_opcode = 0x12; opcode_failures = 1;
    expect("UPLOAD: failedARM retires enabled SDKqueue before startup returns",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x12] == 1 && commands[0x15] == 2 && ends == 1
        && sdk_enable_calls == 1 && sdk_disable_calls == 2 && sdk_clear_calls == 3
        && !sdk_mock_enabled && !f.devc->wch.upload_enabled);
    fixture_clear(&f);
}

static void test_upload_dirty_ownership(gboolean disable_failure)
{
    struct fixture f;
    unsigned int command_count, count;
    fixture_init(&f, TRUE);
    expect("UPLOAD: persistent cleanup fixture starts", start(&f));
    if (disable_failure) sdk_fail_disable_all = TRUE; else sdk_fail_clear_all = TRUE;
    count = read_calls;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD: cleanup false preserves SDKdirty after application END/resources retire",
        f.devc->wch.upload_dirty && !has_application_resources(f.devc)
        && !f.devc->decoded && !f.devc->wch_buffer && !sources && ends == 1
        && !resources_clean(&f));
    expect("UPLOAD: uncertain SDKqueue prevents poststop read", read_calls == (int)count);
    expect("UPLOAD: disabled flag reflects only successful disable BOOL",
        f.devc->wch.upload_enabled == disable_failure && sdk_mock_enabled == disable_failure);
    expect("UPLOAD: inactive stop reports retained SDKqueue failure",
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO);
    command_count = commands[0x15];
    expect("UPLOAD: newcapture refuses SDKdirty before new USBcommands",
        sr_dev_acquisition_start(f.sdi) != SR_OK && commands[0x15] == command_count);
    expect("UPLOAD: clear refuses to free dirty context", sr_dev_clear(&f.driver) != SR_OK
        && f.driver.context == f.drvc && f.sdi->priv == f.devc && !close_calls);
    expect("UPLOAD: close failure retains active handle/context and SDKdirty",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->wch.upload_dirty && !close_calls);
    sdk_fail_disable_all = sdk_fail_clear_all = FALSE;
    expect("UPLOAD: explicit close retry retires SDKqueue then closes exactly once",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty
        && !f.devc->wch.upload_enabled && !f.devc->wch.opened && close_calls == 1);
    expect("UPLOAD: retired context becomes clearable", resources_clean(&f));
    fixture_clear(&f);
}

static void test_upload_absent_exports(void)
{
    struct fixture f;
    const char *exports[] = {"CH375SetBufUploadEx", "CH375ClearBufUpload"};
    unsigned int i;
    for (i = 0; i < ARRAY_SIZE(exports); i++) {
        struct dla_wch missing = {0};
        memset(&wch_api, 0, sizeof(wch_api)); sdk_free_calls = 0;
        sdk_open_calls = sdk_write_calls = sdk_event_count = 0;
        sdk_missing_export = exports[i];
        expect("UPLOAD: real loader rejects missing required diagnostic export",
            !dla_wch_load() && !wch_api.module && sdk_free_calls == 1);
        expect("UPLOAD: absent export rejects WCHopen before identification/capturecommands",
            dla_wch_open(&missing) == SR_ERR_NA && !sdk_open_calls
            && !sdk_write_calls && !sdk_event_count && !missing.opened);
        sdk_missing_export = NULL;
    }
    fixture_init(&f, TRUE); wch_api.set_buf_upload_ex = NULL;
    expect("UPLOAD: absent Set export rejects capture before STOP/SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    wch_api.set_buf_upload_ex = sdk_set_upload; fixture_clear(&f);
    fixture_init(&f, TRUE); wch_api.clear_buf_upload = NULL;
    expect("UPLOAD: absent Clear export rejects capture before STOP/SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    wch_api.clear_buf_upload = sdk_clear_upload; fixture_clear(&f);
}
'''
fixture = fixture.replace("int main(void)\n{", extra + "\nint main(void)\n{", 1)
fixture = fixture.replace("int main(void)\n{", (root / "tools/dla32_wch_upload_extra_tests.c.inc").read_text() + "\nint main(void)\n{", 1)
fixture = fixture.replace("    test_wire_trace();", "    test_upload_normal_and_repeat();\n    test_upload_start_failures();\n    test_upload_dirty_ownership(FALSE); test_upload_dirty_ownership(TRUE);\n    test_upload_absent_exports();\n    test_upload_close_only_false(); test_upload_public_exit_dirty();\n    test_wire_trace();", 1)
(directory / "test_dla32_wch_upload_unit.c").write_text(fixture, encoding="utf-8", newline="\n")
print(directory / "test_dla32_wch_upload_unit.c")
