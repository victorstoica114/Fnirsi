/* SPDX-License-Identifier: GPL-3.0-only */
/* Compile the actual driver against fake WCH/libusb transports. No hardware
 * is opened. Queued libusb transfers stay owned until callbacks, including
 * cancellation callbacks during synchronous failed-start rollback. */
#include <config.h>
#include <glib.h>
#include <libsigrok/libsigrok.h>
#include "libsigrok-internal.h"
#include <windows.h>

#define AUDIT_QUEUE_CAPACITY 32
struct queued_transfer { struct libusb_transfer *transfer; gboolean canceled; };
static struct queued_transfer usb_queue[AUDIT_QUEUE_CAPACITY];
static gint64 fake_now;
static int fail_opcode, opcode_failures, source_result, header_result;
static int logic_result, end_result, headers, header_attempts, ends, end_attempts;
static int sources, read_count, read_calls, read_fail_call, submit_calls;
static int fail_submit_call, event_calls, close_calls, unsafe_frees;
static int event_result, fail_alloc_call, alloc_calls, cancel_calls;
static gboolean endless_drain, consume_logic, real_sources;
static gboolean nested_header, stop_in_header, nested_safe;
static guint removed_stop_check_id;
static unsigned int commands[256], checks, failures;
static unsigned int before_cleanup_count, after_cleanup_count;
static uint64_t delivered, accepted;
static struct sr_trigger *fake_trigger;
struct scripted_read { int count, status; gint64 elapsed_us; };
static struct scripted_read prearm_script[64];
static unsigned int prearm_script_length, prearm_script_position;
static unsigned int reads_at_arm;
enum sdk_event { SDK_STOP = 0x15, SDK_SETUP = 0x11, SDK_ARM = 0x12,
    SDK_DISABLE = 100, SDK_CLEAR, SDK_ENABLE, SDK_READ };
static int sdk_events[4096];
static unsigned int sdk_event_count, sdk_disable_calls, sdk_clear_calls, sdk_enable_calls;
static unsigned int sdk_fail_disable_nth, sdk_fail_clear_nth, sdk_fail_enable_nth;
static unsigned int sdk_free_calls, sdk_bad_arguments, sdk_open_calls, sdk_write_calls;
static gboolean sdk_fail_disable_all, sdk_fail_clear_all, sdk_mock_enabled;
static gboolean sdk_fail_enable_side_effect;
static gboolean sdk_reject_stop_when_enabled;
static unsigned int sdk_active_stop_attempts;
static const char *sdk_missing_export;
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

static void sdk_record(int event)
{ if (sdk_event_count < ARRAY_SIZE(sdk_events)) sdk_events[sdk_event_count++] = event; }


static void audit_header_reenter(const struct sr_dev_inst *sdi);

static gint64 audit_time(void) { return fake_now; }
static void audit_sleep(gulong usec) { fake_now += usec; }
static void audit_real_sleep(gulong usec) { g_usleep(usec); }
static unsigned int queued_count(void)
{
    unsigned int i, n = 0;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++) n += usb_queue[i].transfer != NULL;
    return n;
}
static struct sr_trigger *audit_trigger_get(struct sr_session *session)
{ (void)session; return fake_trigger; }
/* These wrappers are defined BEFORE driver macro substitutions, so their
 * optional real path exercises the library's actual GLib event sources. */
static int audit_source_add(struct sr_session *session, int fd, int events,
    int timeout, sr_receive_data_callback cb, void *data)
{
    int ret = source_result;
    if (ret == SR_OK && real_sources)
        ret = sr_session_source_add(session, fd, events, timeout, cb, data);
    if (ret == SR_OK) sources++;
    return ret;
}
static int audit_source_remove(struct sr_session *session, int fd)
{
    int ret = real_sources ? sr_session_source_remove(session, fd) : SR_OK;
    if (real_sources && ret == SR_OK) removed_stop_check_id = session->stop_check_id;
    if (ret == SR_OK) sources--;
    return ret;
}
static int audit_usb_source_add(struct sr_session *session, struct sr_context *ctx,
    int timeout, sr_receive_data_callback cb, void *data)
{ (void)ctx; return audit_source_add(session, -2, 0, timeout, cb, data); }
static int audit_usb_source_remove(struct sr_session *session, struct sr_context *ctx)
{ (void)ctx; return audit_source_remove(session, -2); }
static int audit_header(const struct sr_dev_inst *sdi)
{
    header_attempts++;
    if (nested_header) audit_header_reenter(sdi);
    if (header_result == SR_OK) headers++;
    return header_result;
}
static int audit_end(const struct sr_dev_inst *sdi)
{
    (void)sdi; end_attempts++;
    if (end_result == SR_OK) ends++;
    return end_result;
}
static int audit_send(const struct sr_dev_inst *sdi, const struct sr_datafeed_packet *packet)
{
    const struct sr_datafeed_logic *logic = packet->payload;
    uint64_t samples;
    (void)sdi;
    if (logic_result != SR_OK) return logic_result;
    if (packet->type == SR_DF_LOGIC) {
        samples = logic->length / logic->unitsize;
        accepted += samples;
        if (!consume_logic) delivered += samples;
    }
    return SR_OK;
}
static void audit_limits_start(struct sr_sw_limits *limits)
{
    limits->samples_read = limits->frames_read = 0;
    limits->start_time = fake_now;
}
static int audit_command(const uint8_t *data, unsigned int length)
{
    unsigned int opcode = length > 9 ? data[9] : 0;
    commands[opcode]++;
    if (opcode == 0x15 || opcode == 0x11 || opcode == 0x12) sdk_record(opcode);
    if (opcode == 0x12) reads_at_arm = read_calls;
    if ((int)opcode == fail_opcode && opcode_failures) {
        opcode_failures--; return SR_ERR_IO;
    }
    return SR_OK;
}
static int audit_read(uint8_t *data, int length, int *actual)
{
    sdk_record(SDK_READ);
    if (prearm_script_position < prearm_script_length) {
        const struct scripted_read *item = &prearm_script[prearm_script_position++];
        read_calls++; fake_now += item->elapsed_us;
        *actual = MIN(item->count, length);
        if (*actual > 0) memset(data, 0, *actual);
        return item->status;
    }
    read_calls++; fake_now += 1000; *actual = 0;
    if (read_calls == read_fail_call) return LIBUSB_ERROR_IO;
    *actual = MIN(endless_drain ? 32 : read_count, length);
    if (*actual > 0) {
        memset(data, 0, *actual);
        if (!endless_drain) read_count -= *actual;
    }
    return LIBUSB_SUCCESS;
}


/* Real transport-WCH implementation resolves only fake exports in this fixture.
 * No LoadLibrary, WCH DLL, device open, or USB function is executed. */
static HANDLE WINAPI sdk_open(ULONG index)
{ (void)index; sdk_open_calls++; return (HANDLE)(uintptr_t)42; }
static VOID WINAPI sdk_close(ULONG index)
{ (void)index; close_calls++; }
static ULONG WINAPI sdk_usb_id(ULONG index)
{ (void)index; return 0x55371a86; }
static BOOL WINAPI sdk_timeout(ULONG index, ULONG write_ms, ULONG read_ms)
{ return sdk_prepare_timeout(index, write_ms, read_ms, 0, 0, FALSE); }
static BOOL WINAPI sdk_timeout_ex(ULONG index, ULONG a, ULONG b, ULONG c, ULONG d)
{ return sdk_prepare_timeout(index, a, b, c, d, TRUE); }
static BOOL WINAPI sdk_write(ULONG index, PVOID data, PULONG length)
{
    int ret;
    sdk_timeout_io(TRUE);
    (void)index; sdk_write_calls++; ret = audit_command(data, *length);
    if (*length > 9 && ((const uint8_t *)data)[9] == 0x15 && sdk_mock_enabled) {
        sdk_active_stop_attempts++;
        /* Inject the observed case, without assuming a universal SDK rule. */
        if (sdk_reject_stop_when_enabled) { *length = 0; SetLastError(ERROR_GEN_FAILURE); return FALSE; }
    }
    if (ret == SR_OK) return TRUE;
    *length = 0; SetLastError(ERROR_GEN_FAILURE); return FALSE;
}
static BOOL WINAPI sdk_read(ULONG index, ULONG pipe, PVOID data, PULONG length)
{
    int actual = 0, ret;
    sdk_timeout_io(FALSE);
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
    if (failed) { if (enable && sdk_fail_enable_side_effect) sdk_mock_enabled = TRUE;
        SetLastError(ERROR_GEN_FAILURE); return FALSE; }
    sdk_mock_enabled = enable != 0;
    return TRUE;
}
static BOOL WINAPI sdk_clear_upload(ULONG index, ULONG pipe)
{
    if (index != 0 || pipe != 1) sdk_bad_arguments++;
    sdk_record(SDK_CLEAR); sdk_clear_calls++;
    if (!sdk_mock_enabled) { SetLastError(ERROR_NO_SYSTEM_RESOURCES); return FALSE; }
    if (sdk_fail_clear_all || sdk_clear_calls == sdk_fail_clear_nth) {
        SetLastError(ERROR_GEN_FAILURE); return FALSE;
    }
    if (timeout_fail_after_clear) timeout_fail_nth = timeout_calls + 1;
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
#include "../dla32-wch-worker-v8-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h"
#undef LoadLibraryExW
#undef GetProcAddress
#undef FreeLibrary
#undef g_get_monotonic_time
#undef LOG_PREFIX
static int LIBUSB_CALL audit_bulk_transfer(libusb_device_handle *handle,
    unsigned char endpoint, unsigned char *data, int length,
    int *actual, unsigned int timeout)
{
    int ret;
    (void)handle; (void)timeout;
    if (endpoint == 0x81) return audit_read(data, length, actual);
    ret = audit_command(data, length);
    *actual = ret == SR_OK ? length : 0;
    return ret == SR_OK ? LIBUSB_SUCCESS : LIBUSB_ERROR_IO;
}
static struct libusb_transfer *LIBUSB_CALL audit_alloc_transfer(int iso_packets)
{
    alloc_calls++;
    return alloc_calls == fail_alloc_call ? NULL : libusb_alloc_transfer(iso_packets);
}
static void LIBUSB_CALL audit_free_transfer(struct libusb_transfer *transfer)
{
    unsigned int i;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++) {
        if (usb_queue[i].transfer != transfer) continue;
        /* Record the ownership error and remove the callback before freeing;
         * a driver regression must not cause a UAF in the test itself. */
        unsafe_frees++; memset(&usb_queue[i], 0, sizeof(usb_queue[i]));
    }
    libusb_free_transfer(transfer);
}
static int LIBUSB_CALL audit_submit_transfer(struct libusb_transfer *transfer)
{
    unsigned int i;
    submit_calls++;
    if (submit_calls == fail_submit_call) return LIBUSB_ERROR_IO;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++)
        if (usb_queue[i].transfer == transfer) return LIBUSB_ERROR_BUSY;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++) {
        if (usb_queue[i].transfer) continue;
        usb_queue[i].transfer = transfer; usb_queue[i].canceled = FALSE;
        return LIBUSB_SUCCESS;
    }
    return LIBUSB_ERROR_NO_MEM;
}
static int LIBUSB_CALL audit_cancel_transfer(struct libusb_transfer *transfer)
{
    unsigned int i;
    cancel_calls++;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++) {
        if (usb_queue[i].transfer != transfer) continue;
        usb_queue[i].canceled = TRUE; return LIBUSB_SUCCESS;
    }
    return LIBUSB_ERROR_NOT_FOUND;
}
static int LIBUSB_CALL audit_handle_events(libusb_context *ctx, struct timeval *tv)
{
    struct libusb_transfer *snapshot[AUDIT_QUEUE_CAPACITY], *transfer;
    unsigned int i, j;
    int count;
    (void)ctx; (void)tv; event_calls++; fake_now += 1000;
    if (event_result != LIBUSB_SUCCESS) return event_result;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++) snapshot[i] = usb_queue[i].transfer;
    for (i = 0; i < AUDIT_QUEUE_CAPACITY; i++) {
        transfer = snapshot[i];
        if (!transfer) continue;
        for (j = 0; j < AUDIT_QUEUE_CAPACITY; j++)
            if (usb_queue[j].transfer == transfer) break;
        if (j == AUDIT_QUEUE_CAPACITY || (!usb_queue[j].canceled && !read_count)) continue;
        transfer->status = usb_queue[j].canceled ? LIBUSB_TRANSFER_CANCELLED : LIBUSB_TRANSFER_COMPLETED;
        count = usb_queue[j].canceled ? 0 : MIN(read_count, transfer->length);
        transfer->actual_length = count;
        if (count) { memset(transfer->buffer, 0, count); read_count -= count; }
        memset(&usb_queue[j], 0, sizeof(usb_queue[j]));
        transfer->callback(transfer);
    }
    return LIBUSB_SUCCESS;
}
static int LIBUSB_CALL audit_release_interface(libusb_device_handle *handle, int interface)
{ (void)handle; (void)interface; return LIBUSB_SUCCESS; }
static void LIBUSB_CALL audit_usb_close(libusb_device_handle *handle)
{ (void)handle; close_calls++; }

#define g_get_monotonic_time audit_time
#define g_usleep audit_sleep
#define sr_session_trigger_get audit_trigger_get
#define sr_session_source_add audit_source_add
#define sr_session_source_remove audit_source_remove
#define usb_source_add audit_usb_source_add
#define usb_source_remove audit_usb_source_remove
#define std_session_send_df_header audit_header
#define std_session_send_df_end audit_end
#define sr_session_send audit_send
#define sr_sw_limits_acquisition_start audit_limits_start
#define libusb_bulk_transfer audit_bulk_transfer
#define libusb_alloc_transfer audit_alloc_transfer
#define libusb_free_transfer audit_free_transfer
#define libusb_submit_transfer audit_submit_transfer
#define libusb_cancel_transfer audit_cancel_transfer
#define libusb_handle_events_timeout audit_handle_events
#define libusb_release_interface audit_release_interface
#define libusb_close audit_usb_close
#undef SR_REGISTER_DEV_DRIVER
#define SR_REGISTER_DEV_DRIVER(name)
#include "../dla32-wch-worker-v8-source/libsigrok/src/hardware/fnirsi-dla32/api.c"

struct fixture {
    struct sr_dev_inst *sdi;
    struct dev_context *devc;
    struct drv_context *drvc;
    struct sr_context ctx;
    struct sr_dev_driver driver;
    struct sr_session session;
    struct sr_trigger trigger;
    struct sr_trigger_stage stage;
    struct sr_trigger_match match;
    gboolean use_wch;
};
static void expect(const char *name, gboolean pass)
{
    checks++; if (!pass) failures++;
    printf("%s: %s\n", pass ? "PASS" : "FAIL", name);
}
static void expect_transport(const struct fixture *f, const char *name, gboolean pass)
{
    char label[240];
    snprintf(label, sizeof(label), "%s: %s", f->use_wch ? "WCH" : "libusb", name);
    expect(label, pass);
}
static void fixture_init(struct fixture *f, gboolean use_wch)
{
    unsigned int i;
    memset(f, 0, sizeof(*f)); memset(usb_queue, 0, sizeof(usb_queue));
    memset(commands, 0, sizeof(commands)); fake_now = 1000000;
    prearm_script_length = prearm_script_position = reads_at_arm = 0;
    sdk_event_count = sdk_disable_calls = sdk_clear_calls = sdk_enable_calls = 0;
    sdk_fail_disable_nth = sdk_fail_clear_nth = sdk_fail_enable_nth = 0;
    sdk_free_calls = sdk_bad_arguments = sdk_open_calls = sdk_write_calls = 0;
    sdk_fail_disable_all = sdk_fail_clear_all = sdk_mock_enabled = FALSE;
    sdk_fail_enable_side_effect = sdk_reject_stop_when_enabled = FALSE;
    sdk_active_stop_attempts = 0;
    timeout_calls = timeout_fail_nth = timeout_bad_arguments = 0;
    timeout_io_without_success = timeout_last_ms = 0;
    timeout_last_read_ms = timeout_last_write_ms = 0;
    timeout_fail_all = timeout_success_ready = timeout_fail_after_clear = FALSE;
    sdk_missing_export = NULL;
    memset(&wch_api, 0, sizeof(wch_api));
    if (!dla_wch_load()) { expect("Mock SDK exports load", FALSE); return; }

    fail_opcode = -1; opcode_failures = 0;
    source_result = header_result = logic_result = end_result = SR_OK;
    headers = header_attempts = ends = end_attempts = sources = read_count = 0;
    read_calls = read_fail_call = submit_calls = fail_submit_call = event_calls = 0;
    close_calls = unsafe_frees = fail_alloc_call = alloc_calls = cancel_calls = 0;
    event_result = LIBUSB_SUCCESS;
    endless_drain = consume_logic = real_sources = FALSE;
    nested_header = stop_in_header = FALSE; nested_safe = TRUE; removed_stop_check_id = 0;
    delivered = accepted = 0; fake_trigger = NULL; f->use_wch = use_wch;
    f->sdi = g_malloc0(sizeof(*f->sdi)); f->drvc = g_malloc0(sizeof(*f->drvc));
    f->devc = g_malloc0(sizeof(*f->devc));
    f->devc->use_wch = use_wch; f->devc->wch.opened = use_wch;
    f->devc->usb3 = f->devc->logic_only = TRUE;
    f->devc->samplerate = SR_MHZ(50); f->devc->threshold = 1;
    f->devc->limits.limit_samples = 50000;
    f->driver = fnirsi_dla32_driver_info; f->driver.context = f->drvc;
    f->drvc->sr_ctx = &f->ctx; f->drvc->instances = g_slist_append(NULL, f->sdi);
    f->sdi->driver = &f->driver; f->sdi->inst_type = SR_INST_USB;
    f->sdi->status = SR_ST_ACTIVE; f->sdi->priv = f->devc;
    f->sdi->conn = sr_usb_dev_inst_new(1, 1, use_wch ? NULL : (libusb_device_handle *)(uintptr_t)1);
    f->sdi->session = &f->session; f->session.devs = g_slist_append(NULL, f->sdi);
    for (i = 0; i < 32; i++) sr_channel_new(f->sdi, i, SR_CHANNEL_LOGIC, TRUE, "test");
}
static void fixture_trigger(struct fixture *f)
{
    f->match.channel = f->sdi->channels->data; f->match.match = SR_TRIGGER_RISING;
    f->stage.matches = g_slist_append(NULL, &f->match);
    f->trigger.stages = g_slist_append(NULL, &f->stage); fake_trigger = &f->trigger;
}
static void fixture_pump(struct fixture *f)
{
    unsigned int i;
    for (i = 0; i < 16 && f->devc->acquiring; i++) {
        receive_events(-1, 0, f->sdi);
        if (!f->devc->stopping) break;
    }
}
static gboolean resources_clean(const struct fixture *f)
{
    unsigned int i;
    if (f->devc->acquiring || f->devc->starting || f->devc->source_added || f->devc->header_sent ||
        f->devc->decoded || f->devc->wch_buffer || f->devc->submitted_transfers ||
        queued_count() || sources || unsafe_frees ||
        (f->use_wch && (f->devc->wch.upload_dirty || f->devc->upload_stop_pending))) return FALSE;
    for (i = 0; i < ARRAY_SIZE(f->devc->transfers); i++)
        if (f->devc->transfers[i] || f->devc->transfer_submitted[i]) return FALSE;
    return TRUE;
}
static void fixture_clear(struct fixture *f)
{
    if (f->sdi) {
        source_result = header_result = logic_result = end_result = SR_OK;
        opcode_failures = read_fail_call = 0; endless_drain = FALSE;
        sdk_fail_disable_nth = sdk_fail_clear_nth = sdk_fail_enable_nth = 0;
        sdk_fail_disable_all = sdk_fail_clear_all = FALSE;
        timeout_fail_all = timeout_fail_after_clear = FALSE; timeout_fail_nth = 0;

        event_result = LIBUSB_SUCCESS;
        if (f->devc->acquiring) { sr_dev_acquisition_stop(f->sdi); fixture_pump(f); }
        if (f->use_wch && (f->devc->wch.upload_dirty || f->devc->upload_stop_pending) && sr_dev_close(f->sdi) != SR_OK) {
            expect("Fixture teardown retires dirty fake SDK ownership", FALSE); return;
        }
        if (sr_dev_clear(&f->driver) != SR_OK) {
            expect_transport(f, "teardown can clear inactive instance", FALSE); return;
        }
        f->sdi = NULL; f->devc = NULL;
    }
    if (f->driver.context) { f->driver.cleanup(&f->driver); f->driver.context = NULL; }
    g_slist_free(f->trigger.stages); g_slist_free(f->stage.matches); fake_trigger = NULL;
}
static gboolean start(struct fixture *f) { return sr_dev_acquisition_start(f->sdi) == SR_OK; }
static void feed(struct fixture *f, int bytes) { read_count = bytes; fixture_pump(f); }
static void audit_header_reenter(const struct sr_dev_inst *sdi)
{
    struct dev_context *devc = sdi->priv;
    int before = read_calls;
    fake_now += 5000000;
    receive_events(-1, 0, (void *)sdi);
    if (stop_in_header) {
        sr_dev_acquisition_stop((struct sr_dev_inst *)sdi);
        receive_events(-1, 0, (void *)sdi);
    }
    nested_safe = nested_safe && read_calls == before && !commands[0x12]
        && !end_attempts && devc->acquiring && devc->decoded && sources == 1
        && !queued_count() && !unsafe_frees;
}

static void test_normal_capture(gboolean wch)
{
    struct fixture f;
    fixture_init(&f, wch);
    expect_transport(&f, "baseline capture starts", start(&f)); feed(&f, 200000);
    expect_transport(&f, "exact 50000 samples with single HEADER/END",
        delivered == 50000 && accepted == 50000 && headers == 1 && ends == 1);
    expect_transport(&f, "completion releases source and transfer ownership", resources_clean(&f));
    expect_transport(&f, "stop after END is harmless",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && ends == 1 && resources_clean(&f));
    expect_transport(&f, "second capture starts without reopening", start(&f)); feed(&f, 200000);
    expect_transport(&f, "second capture has exact count and no stale tail",
        delivered == 100000 && ends == 2 && resources_clean(&f)); fixture_clear(&f);
}
static void test_watchdog(gboolean wch)
{
    struct fixture f; unsigned int i;
    fixture_init(&f, wch); f.devc->samplerate = SR_MHZ(1); f.devc->limits.limit_samples = 10000000;
    expect_transport(&f, "ten-second Buffer starts", start(&f));
    fake_now += 4000000; fixture_pump(&f);
    expect_transport(&f, "Buffer still waits at second four", f.devc->acquiring && !ends);
    fake_now += 8000000; fixture_pump(&f);
    expect_transport(&f, "Buffer still waits at second twelve", f.devc->acquiring && !ends);
    fake_now += 1100000; fixture_pump(&f);
    expect_transport(&f, "Buffer expires beyond duration plus three-second margin",
        ends == 1 && delivered == 0 && resources_clean(&f)); fixture_clear(&f);

    fixture_init(&f, wch); f.devc->stream = TRUE;
    expect_transport(&f, "Stream starts for no-data stall test", start(&f));
    fake_now += 3100000; fixture_pump(&f);
    expect_transport(&f, "Stream without trigger detects no-data stall", ends == 1 && resources_clean(&f));
    fixture_clear(&f);

    fixture_init(&f, wch); fixture_trigger(&f);
    expect_transport(&f, "triggered capture starts", start(&f));
    fake_now += 60000000; fixture_pump(&f);
    expect_transport(&f, "trigger waits with no data beyond initial deadline", f.devc->acquiring && !ends && !delivered);
    feed(&f, 1);
    expect_transport(&f, "incomplete first byte does not manufacture samples", f.devc->acquiring && !delivered);
    fake_now += 3100000; fixture_pump(&f);
    expect_transport(&f, "trigger cannot suppress stall after incomplete first byte", ends == 1 && resources_clean(&f));
    fixture_clear(&f);

    fixture_init(&f, wch); fixture_trigger(&f);
    expect_transport(&f, "capture starts for repeated progress test", start(&f));
    for (i = 0; i < 4; i++) { fake_now += 2500000; feed(&f, 32); }
    expect_transport(&f, "repeated USB progress resets inactivity deadline", f.devc->acquiring && !ends && delivered == 32);
    fake_now += 3100000; fixture_pump(&f);
    expect_transport(&f, "stall after progress releases resources", ends == 1 && resources_clean(&f)); fixture_clear(&f);
}
static void test_finite_limits(gboolean wch)
{
    struct fixture f;
    fixture_init(&f, wch); f.devc->stream = TRUE;
    f.devc->limits.limit_samples = 0; f.devc->limits.limit_msec = 1000; /* 1 ms stored in us. */
    expect_transport(&f, "internal timed Stream starts", start(&f)); fake_now += 2000; feed(&f, 1);
    expect_transport(&f, "timed Stream ignores host wall-clock expiry", f.devc->acquiring && !ends && !delivered);
    feed(&f, 200703);
    expect_transport(&f, "timed Stream fragments/padding clip to 50000", delivered == 50000 && ends == 1 && resources_clean(&f));
    fixture_clear(&f);

    fixture_init(&f, wch); fixture_trigger(&f); f.devc->stream = TRUE;
    f.devc->limits.limit_samples = 0; f.devc->limits.limit_msec = 1000;
    expect_transport(&f, "timed Stream with trigger starts", start(&f)); fake_now += 60000000; fixture_pump(&f);
    expect_transport(&f, "timed Stream waits for delayed trigger", f.devc->acquiring && !ends && !delivered);
    feed(&f, 200704);
    expect_transport(&f, "timed Stream completes exact count after delayed trigger", delivered == 50000 && ends == 1 && resources_clean(&f));
    fixture_clear(&f);

    fixture_init(&f, wch); f.devc->stream = TRUE;
    f.devc->limits.limit_samples = 10000; f.devc->limits.limit_msec = 1000;
    expect_transport(&f, "combined sample/time limits start", start(&f)); feed(&f, 200704);
    expect_transport(&f, "smaller sample limit wins over timed limit", delivered == 10000 && ends == 1 && resources_clean(&f));
    fixture_clear(&f);

    fixture_init(&f, wch); f.devc->limits.limit_samples = 50001;
    expect_transport(&f, "non-frame-aligned Buffer starts", start(&f)); feed(&f, 200032);
    expect_transport(&f, "Buffer padded final frame clips exactly to 50001", delivered == 50001 && ends == 1 && resources_clean(&f));
    fixture_clear(&f);
}
static void test_frontend_failure(gboolean wch)
{
    struct fixture f;
    fixture_init(&f, wch);
    expect_transport(&f, "capture starts before LOGIC rejection", start(&f)); logic_result = SR_ERR; feed(&f, 200000);
    expect_transport(&f, "rejected LOGIC not counted as accepted", !f.devc->limits.samples_read && !accepted && !delivered);
    expect_transport(&f, "LOGIC rejection stops once and releases resources", end_attempts == 1 && resources_clean(&f)); fixture_clear(&f);

    fixture_init(&f, wch); consume_logic = TRUE;
    expect_transport(&f, "capture starts with consuming transform", start(&f)); feed(&f, 200000);
    expect_transport(&f, "SR_OK-consuming transform counts accepted samples",
        !delivered && accepted == 50000 && f.devc->limits.samples_read == 50000 && ends == 1 && resources_clean(&f)); fixture_clear(&f);

    fixture_init(&f, wch);
    expect_transport(&f, "capture starts before END rejection", start(&f)); end_result = SR_ERR; feed(&f, 200000);
    expect_transport(&f, "END rejection attempted once and still frees resources", end_attempts == 1 && !ends && delivered == 50000 && resources_clean(&f));
    end_result = SR_OK;
    expect_transport(&f, "capture restarts after END rejection", start(&f)); feed(&f, 200000);
    expect_transport(&f, "restart after END rejection completes", end_attempts == 2 && ends == 1 && delivered == 100000 && resources_clean(&f)); fixture_clear(&f);
}
enum startup_fault { FAULT_STOP, FAULT_SETUP, FAULT_DRAIN, FAULT_ENDLESS_DRAIN,
    FAULT_SOURCE, FAULT_HEADER, FAULT_ARM, FAULT_SUBMIT, FAULT_ALLOC };
static const char *fault_name(enum startup_fault fault)
{
    static const char *names[] = { "STOP", "SETUP", "pre-arm read", "drain bound",
        "source add", "HEADER", "ARM", "nth USB submit", "nth USB allocation" };
    return names[fault];
}
static void test_startup_failure(gboolean wch, enum startup_fault fault, int nth)
{
    struct fixture f; char label[160]; int ret;
    fixture_init(&f, wch);
    switch (fault) {
    case FAULT_STOP: case FAULT_SETUP: case FAULT_ARM:
        fail_opcode = fault == FAULT_STOP ? 0x15 : fault == FAULT_SETUP ? 0x11 : 0x12; opcode_failures = 1; break;
    case FAULT_DRAIN: read_fail_call = 1; break;
    case FAULT_ENDLESS_DRAIN: endless_drain = TRUE; break;
    case FAULT_SOURCE: source_result = SR_ERR; break;
    case FAULT_HEADER: header_result = SR_ERR; break;
    case FAULT_SUBMIT: fail_submit_call = nth; break;
    case FAULT_ALLOC: fail_alloc_call = nth; break;
    }
    ret = sr_dev_acquisition_start(f.sdi);
    snprintf(label, sizeof(label), "%s fault %d returns start error", fault_name(fault), nth);
    expect_transport(&f, label, ret != SR_OK);
    snprintf(label, sizeof(label), "%s fault %d cleans before start returns", fault_name(fault), nth);
    expect_transport(&f, label, resources_clean(&f) && header_attempts <= 1 && end_attempts == headers);
    if (fault == FAULT_SUBMIT && nth > 1) {
        snprintf(label, sizeof(label), "submit fault %d retires owned callbacks before return", nth);
        expect_transport(&f, label, cancel_calls >= nth - 1 && event_calls > 0 && !unsafe_frees);
    }
    opcode_failures = read_fail_call = fail_submit_call = fail_alloc_call = 0;
    endless_drain = FALSE; source_result = header_result = SR_OK;
    expect_transport(&f, "retry after start fault succeeds", start(&f)); feed(&f, 200000);
    expect_transport(&f, "retry after start fault receives exact data", delivered == 50000 && resources_clean(&f)); fixture_clear(&f);
}
static void test_nested_header(gboolean wch)
{
    struct fixture f;
    fixture_init(&f, wch); nested_header = TRUE;
    expect_transport(&f, "start survives nested event dispatch inside HEADER", start(&f));
    expect_transport(&f, "HEADER nested events neither read/arm nor free before return", nested_safe);
    feed(&f, 200000);
    expect_transport(&f, "capture after delayed HEADER has fresh ARM deadline", delivered == 50000 && ends == 1 && resources_clean(&f)); fixture_clear(&f);
    fixture_init(&f, wch); nested_header = stop_in_header = TRUE;
    expect_transport(&f, "consumer stop during HEADER is accepted", start(&f));
    expect_transport(&f, "nested stop cannot END or free before HEADER returns", nested_safe && !commands[0x12]);
    fixture_pump(&f);
    expect_transport(&f, "consumer stop during HEADER ends once without arming", ends == 1 && !delivered && !commands[0x12] && resources_clean(&f)); fixture_clear(&f);
}
static void test_stop_and_transport_failure(gboolean wch)
{
    struct fixture f;
    fixture_init(&f, wch);
    expect_transport(&f, "capture starts for manual stop", start(&f)); feed(&f, 32);
    expect_transport(&f, "manual stop accepted before sample limit", sr_dev_acquisition_stop(f.sdi) == SR_OK); fixture_pump(&f);
    expect_transport(&f, "manual stop keeps partial accepted count", delivered == 8 && ends == 1 && resources_clean(&f));
    expect_transport(&f, "fresh capture starts after manual stop", start(&f)); feed(&f, 200000);
    expect_transport(&f, "fresh capture after stop has no stale samples", delivered == 50008 && ends == 2 && resources_clean(&f)); fixture_clear(&f);
    fixture_init(&f, wch);
    expect_transport(&f, "capture starts before STOP rejection", start(&f)); fail_opcode = 0x15; opcode_failures = 1;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    if (wch) {
        expect_transport(&f, "failed STOP retires application END but retains pending hardware ownership",
            ends == 1 && !has_application_resources(f.devc) && f.devc->upload_stop_pending
            && !f.devc->wch.upload_dirty && !resources_clean(&f));
        expect_transport(&f, "explicit inactive STOP retry retires pending hardware ownership",
            sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f));
    } else
        expect_transport(&f, "failed STOP cannot retain source or queued ownership", ends == 1 && resources_clean(&f));
    fixture_clear(&f);
    fixture_init(&f, wch);
    expect_transport(&f, "capture starts before transport failure", start(&f));
    if (wch) { read_fail_call = read_calls + 1; fixture_pump(&f); }
    else { fail_submit_call = submit_calls + 1; feed(&f, 32); }
    expect_transport(&f, "read/resubmit failure retires every queued transfer", ends == 1 && resources_clean(&f)); fixture_clear(&f);
}
static gboolean handle_open(const struct fixture *f)
{
    return f->use_wch ? f->devc->wch.opened : ((struct sr_usb_dev_inst *)f->sdi->conn)->devhdl != NULL;
}
static void test_lifecycle(gboolean wch)
{
    struct fixture f; struct drv_context *saved_context; struct sr_dev_inst *saved_sdi; int ret;
    fixture_init(&f, wch);
    expect_transport(&f, "lifecycle test starts capture", start(&f)); saved_context = f.driver.context; saved_sdi = f.sdi;
    ret = sr_dev_close(f.sdi);
    expect_transport(&f, "public close refuses capture and preserves ACTIVE status",
        ret != SR_OK && f.sdi->status == SR_ST_ACTIVE && handle_open(&f) && f.devc->acquiring && sources == 1 && !close_calls);
    ret = sr_dev_clear(&f.driver);
    expect_transport(&f, "clear refusal preserves instance ownership",
        ret != SR_OK && f.driver.context == saved_context && f.drvc->instances && f.drvc->instances->data == saved_sdi && handle_open(&f) && sources == 1 && !close_calls);
    ret = f.driver.cleanup(&f.driver);
    expect_transport(&f, "cleanup refusal preserves driver context",
        ret != SR_OK && f.driver.context == saved_context && f.drvc->instances->data == saved_sdi && handle_open(&f) && sources == 1);
    expect_transport(&f, "public stop works after refused lifecycle calls", sr_dev_acquisition_stop(f.sdi) == SR_OK); fixture_pump(&f);
    expect_transport(&f, "stop after refusals releases acquisition", ends == 1 && resources_clean(&f));
    expect_transport(&f, "public close succeeds after stop",
        sr_dev_close(f.sdi) == SR_OK && f.sdi->status == SR_ST_INACTIVE && !handle_open(&f) && close_calls == 1);
    ret = f.driver.cleanup(&f.driver);
    expect_transport(&f, "cleanup retry succeeds and removes session instance", ret == SR_OK && !f.session.devs && close_calls == 1 && !sources);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}
static void test_atomic_clear(gboolean wch)
{
    struct fixture f; struct sr_dev_inst *idle; struct dev_context *idle_devc; int ret;
    fixture_init(&f, wch); idle = g_malloc0(sizeof(*idle)); idle_devc = g_malloc0(sizeof(*idle_devc));
    idle->driver = &f.driver; idle->status = SR_ST_ACTIVE; idle->inst_type = SR_INST_USB;
    idle->priv = idle_devc; idle->conn = sr_usb_dev_inst_new(1, 2, wch ? NULL : (libusb_device_handle *)(uintptr_t)2);
    idle->session = &f.session; idle_devc->use_wch = idle_devc->wch.opened = wch;
    /* Place idle FIRST to detect destructive partial clearing. */
    f.drvc->instances = g_slist_prepend(f.drvc->instances, idle); f.session.devs = g_slist_prepend(f.session.devs, idle);
    expect_transport(&f, "atomic clear test starts second instance", start(&f)); ret = sr_dev_clear(&f.driver);
    expect_transport(&f, "clear checks all instances before freeing earlier idle instance",
        ret != SR_OK && g_slist_length(f.drvc->instances) == 2 && f.drvc->instances->data == idle
        && f.drvc->instances->next->data == f.sdi && idle->status == SR_ST_ACTIVE && !close_calls && sources == 1);
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f); ret = sr_dev_clear(&f.driver);
    expect_transport(&f, "clear retry releases both instances after stop",
        ret == SR_OK && !f.drvc->instances && !f.session.devs && close_calls == 2 && !sources && !queued_count() && !unsafe_frees);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; } fixture_clear(&f);
}
static void test_guards_and_capabilities(void)
{
    struct fixture f; GSList *l;
    fixture_init(&f, TRUE);
    for (l = f.sdi->channels; l; l = l->next) ((struct sr_channel *)l->data)->enabled = FALSE;
    expect("Guard: no channels rejected before HEADER/source", sr_dev_acquisition_start(f.sdi) == SR_ERR_ARG && !headers && resources_clean(&f)); fixture_clear(&f);
    fixture_init(&f, TRUE); f.devc->samplerate = SR_GHZ(1);
    expect("Guard: 1 GHz with 32 channels rejected", sr_dev_acquisition_start(f.sdi) == SR_ERR_ARG && !headers && resources_clean(&f)); fixture_clear(&f);
    fixture_init(&f, TRUE); f.devc->stream = TRUE; f.devc->usb3 = FALSE;
    expect("Guard: Stream on USB 2 rejected", sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !headers && resources_clean(&f)); fixture_clear(&f);
    fixture_init(&f, TRUE); f.devc->limits.limit_samples = 200000000;
    expect("Guard: Buffer beyond physical memory rejected", sr_dev_acquisition_start(f.sdi) == SR_ERR_ARG && !headers && resources_clean(&f)); fixture_clear(&f);
    fixture_init(&f, TRUE);
    expect("Capability: public millisecond limit stays unadvertised", sr_config_set(f.sdi, NULL, SR_CONF_LIMIT_MSEC, g_variant_new_uint64(1)) == SR_ERR_ARG);
    expect("Capability: continuous acquisition stays unadvertised", sr_config_set(f.sdi, NULL, SR_CONF_CONTINUOUS, g_variant_new_boolean(TRUE)) == SR_ERR_ARG); fixture_clear(&f);
}
static void audit_stopped(void *data) { (*(unsigned int *)data)++; }
static void expect_context(gboolean nondefault, const char *name, gboolean pass)
{
    char label[240];
    snprintf(label, sizeof(label), "real GLib %s context: %s", nondefault ? "thread-default" : "default", name);
    expect(label, pass);
}
static void test_real_session_rollback(gboolean nondefault)
{
    struct fixture f;
    struct sr_session *session = NULL;
    GMainContext *context;
    unsigned int stopped = 0, i;
    gboolean clean;
    int ret;

    fixture_init(&f, TRUE);
    context = nondefault ? g_main_context_new() : g_main_context_ref_thread_default();
    if (nondefault) g_main_context_push_thread_default(context);
    sr_session_new(&f.ctx, &session);
    sr_session_dev_remove(&f.session, f.sdi);
    sr_session_dev_add(session, f.sdi);
    sr_session_stopped_callback_set(session, audit_stopped, &stopped);
    real_sources = TRUE;
    fail_opcode = 0x12; opcode_failures = 1;
    ret = sr_session_start(session);
    expect_context(nondefault, "public start propagates ARM failure", ret != SR_OK);
    clean = resources_clean(&f) && !session->running && !session->main_context
        && !session->stop_check_id && !g_hash_table_size(session->event_sources);
    expect_context(nondefault, "failed start releases sources/context and idle ID", clean);
    expect_context(nondefault, "rollback idle was queued then destroyed in owning context",
        removed_stop_check_id != 0 && !g_main_context_find_source_by_id(context, removed_stop_check_id));

    opcode_failures = 0;
    ret = sr_session_start(session);
    expect_context(nondefault, "same session retries successfully", ret == SR_OK && session->main_context == context);
    read_count = 200000;
    /* Real source dispatch, not a direct call to receive_events. The timer is
     * initially due, then its destruction schedules the genuine stop idle. */
    for (i = 0; i < 16 && session->running; i++) {
        g_main_context_iteration(context, FALSE);
        if (session->running) audit_real_sleep(1000);
    }
    expect_context(nondefault, "retry capture completes and invokes stopped callback",
        stopped == 1 && !session->running && delivered == 50000 && resources_clean(&f));
    expect_context(nondefault, "normal completion resets context and idle ownership",
        !session->main_context && !session->stop_check_id && !g_hash_table_size(session->event_sources));

    /* Exercise the caller which immediately destroys a failed session, then
     * continues iterating its own retained context. Never execute a known
     * dangling idle if the ownership assertions above expose a regression. */
    opcode_failures = 1;
    ret = sr_session_start(session);
    clean = ret != SR_OK && resources_clean(&f) && !session->main_context
        && !session->stop_check_id && !g_hash_table_size(session->event_sources);
    expect_context(nondefault, "second failed start is safe for immediate destroy", clean);
    if (clean) {
        sr_session_destroy(session); session = NULL;
        for (i = 0; i < 4; i++) g_main_context_iteration(context, FALSE);
        expect_context(nondefault, "destroy then iterate leaves no stale stop callback", stopped == 1 && !sources);
    }
    fixture_clear(&f);
    if (session && clean) sr_session_destroy(session);
    if (nondefault) g_main_context_pop_thread_default(context);
    g_main_context_unref(context);
    real_sources = FALSE;
}
static int audit_stub_cleanup(const struct sr_dev_driver *driver)
{
    if (!strcmp(driver->name, "audit-before")) before_cleanup_count++;
    else after_cleanup_count++;
    g_free(driver->context);
    return SR_OK;
}
static void test_public_exit_retry(gboolean wch)
{
    struct fixture f;
    struct sr_context *context = NULL;
    struct sr_dev_driver before = { .name = "audit-before", .cleanup = audit_stub_cleanup };
    struct sr_dev_driver after = { .name = "audit-after", .cleanup = audit_stub_cleanup };
    libusb_context *saved_usb_context;
    gboolean preserved;
    int ret;

    fixture_init(&f, wch);
    before_cleanup_count = after_cleanup_count = 0;
    ret = sr_init(&context);
    expect_transport(&f, "public exit test initializes real library context", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    /* sr_init creates shared transports and enumerates backend availability;
     * no driver is scanned/opened. Replace only its allocated pointer array,
     * leaving the library's global driver descriptors untouched. */
    g_free(context->driver_list);
    context->driver_list = g_new0(struct sr_dev_driver *, 4);
    before.context = g_malloc0(1); after.context = g_malloc0(1);
    context->driver_list[0] = &before;
    context->driver_list[1] = &f.driver;
    context->driver_list[2] = &after;
    f.drvc->sr_ctx = context;
    saved_usb_context = context->libusb_ctx;
    expect_transport(&f, "public exit test starts fake-transport acquisition", start(&f));
    ret = sr_exit(context);
    expect_transport(&f, "sr_exit propagates active-driver cleanup failure", ret != SR_OK);
    if (ret == SR_OK) {
        /* A broken core may already have freed context and instances. Do not
         * dereference them or run a callback after this ownership failure. */
        f.driver.context = NULL; f.sdi = NULL; f.devc = NULL;
        return;
    }
    preserved = context->libusb_ctx == saved_usb_context && saved_usb_context
        && f.driver.context == f.drvc && f.drvc->instances
        && f.drvc->instances->data == f.sdi && f.devc->acquiring
        && handle_open(&f) && sources == 1 && !close_calls;
    expect_transport(&f, "failed sr_exit preserves library/USB/device/callback ownership", preserved);
    expect_transport(&f, "failed sr_exit keeps earlier cleanup success and defers later driver",
        before_cleanup_count == 1 && !before.context && !after_cleanup_count && after.context);
    expect_transport(&f, "public stop works after sr_exit refusal", sr_dev_acquisition_stop(f.sdi) == SR_OK);
    fixture_pump(&f);
    expect_transport(&f, "stop after sr_exit refusal fully retires acquisition", ends == 1 && resources_clean(&f));
    ret = sr_exit(context);
    expect_transport(&f, "sr_exit retry completes remaining cleanup once",
        ret == SR_OK && !f.driver.context && before_cleanup_count == 1
        && after_cleanup_count == 1 && !after.context && !f.session.devs
        && close_calls == 1 && !sources && !queued_count() && !unsafe_frees);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}

static void test_wire_trace(void)
{
    struct fixture f;
    GError *error = NULL;
    char *directory = g_dir_make_tmp("dla32-wiretrace-test-XXXXXX", &error);
    char *path, *contents = NULL;
    gsize length = 0;
    uint8_t input[69];
    unsigned int i, fault;
    expect("WIRE: temporary directory created", directory != NULL && error == NULL);
    if (!directory) { g_clear_error(&error); return; }
    path = g_build_filename(directory, "exclusive.bin", NULL);
    expect("WIRE: create existing sentinel", g_file_set_contents(path, "sentinel", 8, NULL));
    g_setenv("FNIRSI_DLA32_WIRE_TRACE", path, TRUE);
    fixture_init(&f, TRUE);
    expect("WIRE: existing trace rejects startup before USB commands", !start(&f)
        && !commands[0x15] && !f.devc->wire_trace && resources_clean(&f));
    expect("WIRE: existing trace unchanged", g_file_get_contents(path, &contents, &length, NULL)
        && length == 8 && !memcmp(contents, "sentinel", 8));
    g_free(contents); contents = NULL; fixture_clear(&f);
    expect("WIRE: existing sentinel cleanup", g_unlink(path) == 0);

    fixture_init(&f, TRUE); f.devc->limits.limit_samples = 8;
    expect("WIRE: traced capture starts", start(&f) && f.devc->wire_trace);
    for (i = 0; i < sizeof(input); i++) input[i] = (uint8_t)(i * 17 + 3);
    expect("WIRE: arbitrary short first read stays pending", decode_transfer(f.sdi, input, 7) == 0
        && f.devc->pending == 7 && f.devc->wire_trace_bytes == 7);
    expect("WIRE: full input preserved despite software sample clipping",
        decode_transfer(f.sdi, input + 7, 57) == 8 && f.devc->wire_trace_bytes == 64
        && accepted == 8 && !f.devc->pending);
    expect("WIRE: incomplete final frame preserved", decode_transfer(f.sdi, input + 64, 5) == 0
        && f.devc->wire_trace_bytes == 69 && f.devc->pending == 5);
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("WIRE: normal stop closes stream and clears ownership", !f.devc->wire_trace
        && !f.devc->wire_trace_failed && resources_clean(&f));
    expect("WIRE: disk bytes equal exact concatenated decoder inputs",
        g_file_get_contents(path, &contents, &length, NULL)
        && length == sizeof(input) && !memcmp(contents, input, sizeof(input)));
    g_free(contents); contents = NULL; fixture_clear(&f);
    expect("WIRE: normal trace closed for deletion", g_unlink(path) == 0);

    fixture_init(&f, TRUE);
    expect("WIRE: write-failure fixture starts", start(&f) && f.devc->wire_trace);
    fclose(f.devc->wire_trace); f.devc->wire_trace = fopen(path, "rb");
    expect("WIRE: read-only stream opened for disk-error test", f.devc->wire_trace != NULL);
    expect("WIRE: write failure stops before delivering samples",
        decode_transfer(f.sdi, input, 32) == 0 && f.devc->wire_trace_failed
        && f.devc->stopping && !accepted && !f.devc->wire_trace_bytes);
    fixture_pump(&f);
    expect("WIRE: write failure closes trace and clears ownership", !f.devc->wire_trace
        && f.devc->wire_trace_failed && resources_clean(&f));
    fixture_clear(&f);
    expect("WIRE: failed-write trace closed for deletion", g_unlink(path) == 0);

    for (fault = 0; fault < 6; fault++) {
        fixture_init(&f, TRUE);
        if (fault <= 2) { fail_opcode = fault == 0 ? 0x15 : fault == 1 ? 0x11 : 0x12;
            opcode_failures = 1; }
        else if (fault == 3) source_result = SR_ERR;
        else if (fault == 4) header_result = SR_ERR;
        else read_fail_call = 1;
        expect("WIRE: failed startup closes trace", !start(&f) && !f.devc->wire_trace
            && resources_clean(&f));
        fixture_clear(&f);
        expect("WIRE: failed-start trace closed for deletion", g_unlink(path) == 0);
    }
    g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    g_free(path); expect("WIRE: temporary directory cleanup", g_rmdir(directory) == 0);
    g_free(directory);
}


static void scripted_prearm_case(gboolean wch, const char *name,
    const struct scripted_read *sequence, unsigned int length,
    int expected_result, unsigned int expected_reads)
{
    struct fixture f;
    char label[256];
    int result;
    fixture_init(&f, wch);
    memcpy(prearm_script, sequence, sizeof(*sequence) * length);
    prearm_script_length = length;
    result = sr_dev_acquisition_start(f.sdi);
    snprintf(label, sizeof(label), "PREARM policy %u %s: return code", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, result == expected_result);
    snprintf(label, sizeof(label), "PREARM policy %u %s: exact read count", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, (result == SR_OK ? reads_at_arm : read_calls) == expected_reads
        && prearm_script_position == expected_reads);
    snprintf(label, sizeof(label), "PREARM policy %u %s: ARM only after criterion", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, result == SR_OK ? commands[0x12] == 1 : !commands[0x12]);
    snprintf(label, sizeof(label), "PREARM policy %u %s: setup follows successful drain", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, result == SR_OK ? commands[0x11] == 1 : !commands[0x11]);
    if (result == SR_OK) {
        /* Clear remaining simulated stale bytes before normal post-stop cleanup. */
        prearm_script_position = prearm_script_length;
        sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    }
    snprintf(label, sizeof(label), "PREARM policy %u %s: clean resources", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, resources_clean(&f));
    fixture_clear(&f);
}

static void test_prearm_scripted(gboolean wch)
{
    const unsigned int policy = DLA_PREARM_EMPTY_READS;
    struct scripted_read bound[64];
    unsigned int i;
    const struct scripted_read empty[] = {{0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read data_empty[] = {{32, 0, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read empty_data[] = {{0, 0, 1000}, {32, 0, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read intermittent[] = {{32, 0, 1000}, {0, 0, 1000}, {32, 0, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read timeout_empty[] = {{0, LIBUSB_ERROR_TIMEOUT, 1000}, {0, LIBUSB_ERROR_TIMEOUT, 1000}};
    const struct scripted_read timeout_data[] = {{32, LIBUSB_ERROR_TIMEOUT, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read io_first[] = {{0, LIBUSB_ERROR_IO, 1000}};
    const struct scripted_read io_after_data[] = {{32, 0, 1000}, {0, LIBUSB_ERROR_IO, 1000}};
    const struct scripted_read deadline_data[] = {{32, 0, STOP_DRAIN_USEC}};
    const struct scripted_read deadline_empty[] = {{0, 0, STOP_DRAIN_USEC}};
    scripted_prearm_case(wch, "empty", empty, ARRAY_SIZE(empty), SR_OK, policy);
    scripted_prearm_case(wch, "data then empty", data_empty, ARRAY_SIZE(data_empty), SR_OK, policy + 1);
    scripted_prearm_case(wch, "empty then stale data", empty_data, ARRAY_SIZE(empty_data), SR_OK, policy == 1 ? 1 : 4);
    scripted_prearm_case(wch, "nonconsecutive empties reset", intermittent, ARRAY_SIZE(intermittent), SR_OK, policy == 1 ? 2 : 5);
    scripted_prearm_case(wch, "timeout empty counts", timeout_empty, ARRAY_SIZE(timeout_empty), SR_OK, policy);
    scripted_prearm_case(wch, "timeout with data resets", timeout_data, ARRAY_SIZE(timeout_data), SR_OK, policy + 1);
    scripted_prearm_case(wch, "first read IO", io_first, ARRAY_SIZE(io_first), SR_ERR_IO, 1);
    scripted_prearm_case(wch, "IO after data", io_after_data, ARRAY_SIZE(io_after_data), SR_ERR_IO, 2);
    scripted_prearm_case(wch, "deadline with data", deadline_data, ARRAY_SIZE(deadline_data), SR_ERR_TIMEOUT, 1);
    scripted_prearm_case(wch, "deadline empty preserves baseline", deadline_empty, ARRAY_SIZE(deadline_empty), policy == 1 ? SR_OK : SR_ERR_TIMEOUT, 1);
    for (i = 0; i < ARRAY_SIZE(bound); i++) bound[i] = (struct scripted_read){32, 0, 1000};
    scripted_prearm_case(wch, "64 nonempty reads reject", bound, ARRAY_SIZE(bound), SR_ERR_IO, 64);
    bound[62].count = bound[63].count = 0;
    scripted_prearm_case(wch, "criterion at read bound", bound, ARRAY_SIZE(bound), SR_OK, policy == 1 ? 63 : 64);
}


static gboolean sdk_sequence(const int *expected, unsigned int count)
{ return sdk_event_count == count && !memcmp(sdk_events, expected, count * sizeof(*expected)); }

static void test_upload_normal_and_repeat(void)
{
    struct fixture f;
    const int start_events[] = {SDK_DISABLE, SDK_STOP, SDK_READ,
        SDK_SETUP, SDK_ENABLE, SDK_CLEAR, SDK_ARM};
    const int complete_events[] = {SDK_DISABLE, SDK_STOP, SDK_READ,
        SDK_SETUP, SDK_ENABLE, SDK_CLEAR, SDK_ARM, SDK_READ,
        SDK_DISABLE, SDK_STOP, SDK_READ, SDK_READ};
    fixture_init(&f, TRUE); f.devc->stream = TRUE; f.devc->limits.limit_samples = 8;
    expect("UPLOAD V3: starts with active dirty SDK ownership", start(&f)
        && f.devc->wch.upload_dirty && f.devc->wch.upload_enabled && sdk_mock_enabled);
    expect("UPLOAD V3: exact disable-STOP-drain-SETUP-enable-clear-ARM order",
        sdk_sequence(start_events, ARRAY_SIZE(start_events)));
    expect("UPLOAD V3: SDK argument widths and pipe1/1MiB tuple", !sdk_bad_arguments);
    feed(&f, 32);
    expect("UPLOAD V3: normal completion delivers8 and retires SDK/application resources",
        accepted == 8 && ends == 1 && resources_clean(&f)
        && !f.devc->wch.upload_enabled && !sdk_mock_enabled);
    expect("UPLOAD V3: exact read-disable-STOP-poststopdrain without disabled clear",
        sdk_sequence(complete_events, ARRAY_SIZE(complete_events)));
    expect("UPLOAD V3: repeated capture starts cleanly", start(&f) && sdk_enable_calls == 2
        && f.devc->wch.upload_dirty && f.devc->wch.upload_enabled);
    feed(&f, 32);
    expect("UPLOAD V3: repeated capture stops without queue ownership leak", ends == 2
        && resources_clean(&f) && sdk_disable_calls == 4 && sdk_clear_calls == 2
        && !sdk_bad_arguments && !sdk_mock_enabled);
    fixture_clear(&f);
}

static void test_upload_start_failures(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_enable_nth = 1; sdk_fail_enable_side_effect = TRUE;
    expect("UPLOAD V3: enable false with possible side effect rejects and successful disable retires",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x12] && commands[0x15] == 2 && ends == 1
        && sdk_enable_calls == 1 && !sdk_mock_enabled && !sdk_clear_calls);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_clear_nth = 1;
    expect("UPLOAD V3: enabled clear false sends disable then rollbackSTOP before returningIO",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x11] == 1 && !commands[0x12] && commands[0x15] == 2
        && headers == 1 && ends == 1 && sdk_enable_calls == 1 && sdk_clear_calls == 1);
    fixture_clear(&f);
    fixture_init(&f, TRUE); f.devc->limits.limit_samples = 8;
    expect("UPLOAD V3: second-clear failure fixture starts first capture", start(&f));
    feed(&f, 32); sdk_fail_clear_nth = 2;
    expect("UPLOAD V3: repeated enabled clear false returnsIO with no second ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x12] == 1 && commands[0x15] == 4 && ends == 2
        && sdk_enable_calls == 2 && sdk_clear_calls == 2 && !sdk_mock_enabled);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_disable_nth = 1;
    expect("UPLOAD V3: initial disable false rejects startup even after retry succeeds",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x11] && !commands[0x12] && commands[0x15] == 1
        && sdk_disable_calls == 2 && !sdk_clear_calls);
    fixture_clear(&f);
    fixture_init(&f, TRUE); fail_opcode = 0x12; opcode_failures = 1;
    expect("UPLOAD V3: failedARM retires enabled SDKqueue before startup returns",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x12] == 1 && commands[0x15] == 2 && ends == 1
        && sdk_enable_calls == 1 && sdk_disable_calls == 2 && sdk_clear_calls == 1
        && !sdk_mock_enabled && !f.devc->wch.upload_enabled);
    fixture_clear(&f);
}

static void test_upload_dirty_ownership(gboolean stop_failure)
{
    struct fixture f;
    unsigned int command_count, count;
    fixture_init(&f, TRUE);
    if (stop_failure) {
        expect("UPLOAD V3: persistent disable failure capture starts", start(&f));
        sdk_fail_disable_all = TRUE; count = read_calls;
        sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    } else {
        sdk_fail_clear_all = TRUE; sdk_fail_disable_nth = 2;
        expect("UPLOAD V3: failed enabled clear with failed rollback disable returnsIO",
            sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x12]);
        sdk_fail_disable_all = TRUE; count = read_calls;
    }
    expect("UPLOAD V3: disable false preserves SDKdirty after application END/resources retire",
        f.devc->wch.upload_dirty && !has_application_resources(f.devc)
        && !f.devc->decoded && !f.devc->wch_buffer && !sources && ends == 1
        && !resources_clean(&f));
    expect("UPLOAD V3: uncertain SDKqueue prevents poststop read", read_calls == (int)count);
    expect("UPLOAD V3: failed disable preserves previously successful enabled state",
        f.devc->wch.upload_enabled && sdk_mock_enabled);
    expect("UPLOAD V3: inactive stop reports retained SDKqueue failure",
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO);
    command_count = commands[0x15];
    expect("UPLOAD V3: newcapture refuses SDKdirty before new USBcommands",
        sr_dev_acquisition_start(f.sdi) != SR_OK && commands[0x15] == command_count);
    expect("UPLOAD V3: clear refuses to free dirty context", sr_dev_clear(&f.driver) != SR_OK
        && f.driver.context == f.drvc && f.sdi->priv == f.devc && !close_calls);
    expect("UPLOAD V3: close failure retains active handle/context and SDKdirty",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->wch.upload_dirty && !close_calls);
    sdk_fail_disable_all = sdk_fail_clear_all = FALSE; sdk_fail_disable_nth = 0;
    expect("UPLOAD V3: explicit close retry disables SDKqueue then closes exactly once",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty
        && !f.devc->wch.upload_enabled && !f.devc->wch.opened && close_calls == 1);
    expect("UPLOAD V3: retired context becomes clearable", resources_clean(&f));
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
        expect("UPLOAD V3: real loader rejects missing required diagnostic export",
            !dla_wch_load() && !wch_api.module && sdk_free_calls == 1);
        expect("UPLOAD V3: absent export rejects WCHopen before identification/capturecommands",
            dla_wch_open(&missing) == SR_ERR_NA && !sdk_open_calls
            && !sdk_write_calls && !sdk_event_count && !missing.opened);
        sdk_missing_export = NULL;
    }
    fixture_init(&f, TRUE); wch_api.set_buf_upload_ex = NULL;
    expect("UPLOAD V3: absent Set export rejects capture before STOP/SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    wch_api.set_buf_upload_ex = sdk_set_upload; fixture_clear(&f);
    fixture_init(&f, TRUE); wch_api.clear_buf_upload = NULL;
    expect("UPLOAD V3: absent Clear export rejects capture before STOP/SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    wch_api.clear_buf_upload = sdk_clear_upload; fixture_clear(&f);
}

static void test_upload_close_only_false(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_disable_all = TRUE;
    expect("UPLOAD V3: close-only disablefalse retains handle/context/dirty without capturecommands",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.devc->wch.opened && f.devc->wch.upload_dirty
        && f.sdi->status == SR_ST_ACTIVE && !close_calls && !commands[0x15]
        && !commands[0x11] && !commands[0x12]);
    expect("UPLOAD V3: close-only SDKdirty refuses context clear", sr_dev_clear(&f.driver) != SR_OK
        && f.driver.context == f.drvc && f.sdi->priv == f.devc);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V3: close-only retry disables before exactly one close withoutclear",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1 && !sdk_clear_calls);
    fixture_clear(&f);
    fixture_init(&f, TRUE);
    expect("UPLOAD V3: observed kernel rejects Clear when disabled with1450",
        !sdk_clear_upload(0, 1) && GetLastError() == ERROR_NO_SYSTEM_RESOURCES);
    expect("UPLOAD V3: disabled clear1450 is not a required SDKretirement call",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1 && sdk_clear_calls == 1);
    fixture_clear(&f);
}

static void test_upload_public_exit_dirty(void)
{
    struct fixture f;
    struct sr_context *context = NULL;
    struct sr_dev_driver before = { .name = "audit-before", .cleanup = audit_stub_cleanup };
    struct sr_dev_driver after = { .name = "audit-after", .cleanup = audit_stub_cleanup };
    libusb_context *saved_usb_context;
    int ret;
    fixture_init(&f, TRUE); before_cleanup_count = after_cleanup_count = 0;
    ret = sr_init(&context);
    expect("UPLOAD V3: dirty exit initializes real library context without hardware scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(context->driver_list); context->driver_list = g_new0(struct sr_dev_driver *, 4);
    before.context = g_malloc0(1); after.context = g_malloc0(1);
    context->driver_list[0] = &before; context->driver_list[1] = &f.driver; context->driver_list[2] = &after;
    f.drvc->sr_ctx = context; saved_usb_context = context->libusb_ctx;
    expect("UPLOAD V3: dirty exit starts fake SDK capture", start(&f));
    sdk_fail_disable_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V3: dirty exit retains only SDK ownership after application END", ends == 1
        && !has_application_resources(f.devc) && f.devc->wch.upload_dirty);
    expect("UPLOAD V3: std_cleanup refuses SDKdirty and preserves private context",
        std_cleanup(&f.driver) != SR_OK && f.driver.context == f.drvc
        && f.drvc->instances && f.drvc->instances->data == f.sdi && !close_calls);
    ret = sr_exit(context);
    expect("UPLOAD V3: sr_exit reports SDKdirty cleanup failure", ret != SR_OK);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    expect("UPLOAD V3: sr_exit preserves library/USB/handle/device/SDKdirty without callbacks",
        context->libusb_ctx == saved_usb_context && saved_usb_context
        && f.driver.context == f.drvc && f.sdi->priv == f.devc
        && f.devc->wch.opened && f.devc->wch.upload_dirty && !sources && !close_calls);
    expect("UPLOAD V3: sr_exit defers later drivers after SDKdirty refusal",
        before_cleanup_count == 1 && !before.context && !after_cleanup_count && after.context);
    expect("UPLOAD V3: public close failure remains retryable after sr_exit refusal",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE && !close_calls);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V3: public close retry retires SDKqueue before final exit",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1);
    ret = sr_exit(context);
    expect("UPLOAD V3: sr_exit retry frees remaining contexts once after SDK retirement",
        ret == SR_OK && !f.driver.context && before_cleanup_count == 1
        && after_cleanup_count == 1 && !after.context && !f.session.devs
        && close_calls == 1 && !sources && !queued_count() && !unsafe_frees);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}

static void test_upload_enable_false_unknown_side_effect(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_enable_nth = 1; sdk_fail_enable_side_effect = TRUE;
    sdk_fail_disable_nth = 2;
    expect("UPLOAD V3: enablefalse plus failed retirement remains startupIO beforeARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x12] && ends == 1);
    expect("UPLOAD V3: unknown enablefalse side effect retains dirty despite enabledflagFALSE",
        f.devc->wch.upload_dirty && !f.devc->wch.upload_enabled && sdk_mock_enabled
        && !has_application_resources(f.devc) && !f.devc->decoded && !f.devc->wch_buffer);
    expect("UPLOAD V3: unknown enablefalse SDKdirty refuses newcapture/contextclear",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK);
    sdk_fail_disable_nth = 0;
    expect("UPLOAD V3: unknown enablefalse retirement retry disables actualmockqueue beforeclose",
        sr_dev_close(f.sdi) == SR_OK && !sdk_mock_enabled && !f.devc->wch.upload_dirty && close_calls == 1);
    fixture_clear(&f);
}

static void test_upload_v3_observed_active_stop(void)
{
    struct fixture f;
    uint8_t packet[DLA_COMMAND_SIZE];
    fixture_init(&f, TRUE); f.devc->limits.limit_samples = 8;
    sdk_reject_stop_when_enabled = TRUE;
    expect("UPLOAD V3: observed active-upload STOP fault case starts", start(&f));
    dla_stop(packet);
    expect("UPLOAD V3: mock explicitly reproduces observed active-upload STOP false/Windows31",
        dla_wch_write(&f.devc->wch, packet, sizeof(packet)) == SR_ERR_IO
        && GetLastError() == ERROR_GEN_FAILURE && sdk_active_stop_attempts == 1);
    feed(&f, 32);
    expect("UPLOAD V3: disable-before-STOP completion avoids injected active-upload command failure",
        resources_clean(&f) && accepted == 8 && ends == 1
        && sdk_active_stop_attempts == 1 && !sdk_mock_enabled);
    expect("UPLOAD V3: same injected case permits a repeated capture", start(&f));
    feed(&f, 32);
    expect("UPLOAD V3: repeated capture never attempts STOP with SDK enabled",
        resources_clean(&f) && ends == 2 && sdk_active_stop_attempts == 1);
    fixture_clear(&f);
}

static void test_upload_v3_disable_before_stop_failure(void)
{
    struct fixture f;
    unsigned int stops, reads;
    fixture_init(&f, TRUE);
    expect("UPLOAD V3: deferred STOP fixture starts", start(&f));
    stops = commands[0x15]; reads = read_calls; sdk_fail_disable_all = TRUE;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V3: disable false suppresses hardware STOP and poststop reads",
        commands[0x15] == stops && read_calls == (int)reads && !sdk_active_stop_attempts);
    expect("UPLOAD V3: deferred STOP survives END separately from SDKdirty/application resources",
        f.devc->upload_stop_pending && f.devc->wch.upload_dirty && sdk_mock_enabled
        && ends == 1 && !has_application_resources(f.devc) && !resources_clean(&f));
    expect("UPLOAD V3: new capture and devclear refuse pending ownership",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && commands[0x15] == stops && f.sdi->priv == f.devc);
    expect("UPLOAD V3: explicit inactive STOP retry cannot write while disable still fails",
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO && commands[0x15] == stops
        && f.devc->upload_stop_pending && f.devc->wch.upload_dirty);
    expect("UPLOAD V3: close retry cannot write or free while disable still fails",
        sr_dev_close(f.sdi) == SR_ERR_IO && commands[0x15] == stops && !close_calls
        && f.sdi->status == SR_ST_ACTIVE && f.devc->wch.opened);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V3: inactive STOP retry retires disable then hardwareSTOP then drain",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && commands[0x15] == stops + 1
        && read_calls == (int)reads + 2 && resources_clean(&f)
        && !sdk_mock_enabled && !sdk_active_stop_attempts && !close_calls);
    fixture_clear(&f);
}

static void test_upload_v3_pending_stop_close_retry(void)
{
    struct fixture f;
    unsigned int stops, reads;
    fixture_init(&f, TRUE);
    expect("UPLOAD V3: failed hardware STOP close-retry fixture starts", start(&f));
    stops = commands[0x15]; reads = read_calls;
    fail_opcode = 0x15; opcode_failures = 3;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V3: hardware STOP false retains pending with clean SDK and completed appEND",
        f.devc->upload_stop_pending && !f.devc->wch.upload_dirty && !sdk_mock_enabled
        && !has_application_resources(f.devc) && ends == 1 && commands[0x15] == stops + 1
        && read_calls == (int)reads && !resources_clean(&f));
    expect("UPLOAD V3: pending hardware STOP blocks new start/contextclear/inactive retry reports IO",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty);
    expect("UPLOAD V3: failed close STOP retry preserves active handle/context without reads",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->upload_stop_pending && !close_calls
        && read_calls == (int)reads && commands[0x15] == stops + 3);
    expect("UPLOAD V3: successful close STOP retry drains before exactly one handle release",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && !f.devc->wch.opened
        && close_calls == 1 && read_calls == (int)reads + 2 && commands[0x15] == stops + 4);
    fixture_clear(&f);
}

static void test_upload_v3_prearm_stop_failures(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); fail_opcode = 0x15; opcode_failures = 2;
    expect("UPLOAD V3: persistent initial hardware STOP failure rejects before SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x11] && !commands[0x12]
        && commands[0x15] == 2 && !headers && !ends && !read_calls
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty
        && !f.devc->decoded && !f.devc->wch_buffer && !has_application_resources(f.devc));
    expect("UPLOAD V3: initial pending STOP blocks capture/clear until explicit retry",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK);
    expect("UPLOAD V3: initial pending STOP inactive retry succeeds and drains",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f)
        && commands[0x15] == 3 && read_calls == 2);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_disable_all = TRUE;
    expect("UPLOAD V3: persistent initial disable failure attempts zero hardware commands or reads",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && !read_calls && sdk_disable_calls == 2
        && f.devc->upload_stop_pending && f.devc->wch.upload_dirty);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V3: initial disable failure close retry sends deferredSTOP then drains before close",
        sr_dev_close(f.sdi) == SR_OK && commands[0x15] == 1 && read_calls == 2
        && close_calls == 1 && resources_clean(&f));
    fixture_clear(&f);
}

static void test_upload_public_exit_pending_stop(void)
{
    struct fixture f;
    struct sr_context *context = NULL;
    struct sr_dev_driver before = { .name = "audit-before", .cleanup = audit_stub_cleanup };
    struct sr_dev_driver after = { .name = "audit-after", .cleanup = audit_stub_cleanup };
    libusb_context *saved_usb_context;
    int ret;
    fixture_init(&f, TRUE); before_cleanup_count = after_cleanup_count = 0;
    ret = sr_init(&context);
    expect("UPLOAD V3: dirty exit initializes real library context without hardware scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(context->driver_list); context->driver_list = g_new0(struct sr_dev_driver *, 4);
    before.context = g_malloc0(1); after.context = g_malloc0(1);
    context->driver_list[0] = &before; context->driver_list[1] = &f.driver; context->driver_list[2] = &after;
    f.drvc->sr_ctx = context; saved_usb_context = context->libusb_ctx;
    expect("UPLOAD V3: dirty exit starts fake SDK capture", start(&f));
    fail_opcode = 0x15; opcode_failures = 3; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V3: dirty exit retains only pending hardware ownership after application END", ends == 1
        && !has_application_resources(f.devc) && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty);
    expect("UPLOAD V3: std_cleanup refuses pending hardware STOP and preserves private context",
        std_cleanup(&f.driver) != SR_OK && f.driver.context == f.drvc
        && f.drvc->instances && f.drvc->instances->data == f.sdi && !close_calls);
    ret = sr_exit(context);
    expect("UPLOAD V3: sr_exit reports pending hardware STOP cleanup failure", ret != SR_OK);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    expect("UPLOAD V3: sr_exit preserves library/USB/handle/device/pending hardware STOP without callbacks",
        context->libusb_ctx == saved_usb_context && saved_usb_context
        && f.driver.context == f.drvc && f.sdi->priv == f.devc
        && f.devc->wch.opened && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty && !sources && !close_calls);
    expect("UPLOAD V3: sr_exit defers later drivers after pending hardware STOP refusal",
        before_cleanup_count == 1 && !before.context && !after_cleanup_count && after.context);
    expect("UPLOAD V3: public close failure remains retryable after sr_exit refusal",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE && !close_calls);
    opcode_failures = 0;
    expect("UPLOAD V3: public close retry retires SDKqueue before final exit",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->upload_stop_pending && !f.devc->wch.upload_dirty && close_calls == 1);
    ret = sr_exit(context);
    expect("UPLOAD V3: sr_exit retry frees remaining contexts once after SDK retirement",
        ret == SR_OK && !f.driver.context && before_cleanup_count == 1
        && after_cleanup_count == 1 && !after.context && !f.session.devs
        && close_calls == 1 && !sources && !queued_count() && !unsafe_frees);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}


/* V4 interaction tests execute the real V3 API with the new timeout helper. */
static void v4_timeout_mode(gboolean legacy)
{
    if (legacy) wch_api.timeout_ex = NULL;
}
static void v4_expect(const char *label, gboolean condition)
{
    char text[240];
    snprintf(text, sizeof(text), "UPLOAD V4 %s: %s", wch_api.timeout_ex ? "Ex" : "legacy", label);
    expect(text, condition);
}
static void test_v4_prearm_setter_failure(gboolean legacy)
{
    struct fixture f;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_all = TRUE;
    v4_expect("persistent prearm write setter failure blocks every command/read and retains STOP",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && timeout_calls == 2
        && !sdk_write_calls && !read_calls && !headers && !ends
        && !commands[0x11] && !commands[0x12] && !commands[0x15]
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty
        && !has_application_resources(f.devc));
    v4_expect("prearm pending STOP blocks new capture and context clear",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && f.sdi->priv == f.devc && !sdk_write_calls && !read_calls);
    v4_expect("failed prearm close retry preserves ACTIVE/handle without I/O",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->upload_stop_pending && !close_calls
        && !sdk_write_calls && !read_calls);
    timeout_fail_all = FALSE;
    v4_expect("inactive public STOP retry restores1000 then STOP and two20ms drain reads",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f)
        && commands[0x15] == 1 && read_calls == 2 && !close_calls
        && timeout_last_write_ms == 1000 && timeout_last_read_ms == 20
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_nth = 1;
    v4_expect("one prearm setter failure still returns IO after successful rollback STOP/drain",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && commands[0x15] == 1
        && sdk_write_calls == 1 && read_calls == 2 && resources_clean(&f)
        && !commands[0x11] && !commands[0x12] && !headers && !ends
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
static void test_v4_terminal_setter_failure(gboolean legacy)
{
    struct fixture f;
    unsigned int writes, reads, stops;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("terminal setter failure capture starts", start(&f));
    writes = sdk_write_calls; reads = read_calls; stops = commands[0x15];
    timeout_fail_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    v4_expect("disable succeeds but false STOP setter blocks WriteData/drain and preserves pending across END",
        !sdk_mock_enabled && !f.devc->wch.upload_dirty && f.devc->upload_stop_pending
        && ends == 1 && !has_application_resources(f.devc) && !resources_clean(&f)
        && sdk_write_calls == writes && read_calls == (int)reads && commands[0x15] == stops);
    v4_expect("terminal pending state blocks start/devclear and failed inactive retry sends no I/O",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO && f.devc->upload_stop_pending
        && !f.devc->wch.upload_dirty && sdk_write_calls == writes && read_calls == (int)reads);
    v4_expect("failed close timeout setter preserves ACTIVE handle/context and no reads",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.sdi->priv == f.devc && !close_calls
        && f.devc->upload_stop_pending && sdk_write_calls == writes && read_calls == (int)reads);
    timeout_fail_all = FALSE;
    v4_expect("close retry restores1000 STOP then20ms drain and releases handle exactly once",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && !f.devc->wch.opened
        && close_calls == 1 && sdk_write_calls == writes + 1 && commands[0x15] == stops + 1
        && read_calls == (int)reads + 2 && timeout_last_write_ms == 1000
        && timeout_last_read_ms == 20 && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
static void test_v4_arm_setter_failure(gboolean legacy)
{
    struct fixture f;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_after_clear = TRUE;
    v4_expect("ARM setter failure after enable/Clear sends no ARM and returns IO after disable/STOP cleanup",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && sdk_enable_calls == 1
        && sdk_clear_calls == 1 && sdk_disable_calls == 2 && !commands[0x12]
        && commands[0x11] == 1 && commands[0x15] == 2 && headers == 1 && ends == 1
        && !sdk_mock_enabled && resources_clean(&f) && timeout_last_write_ms == 1000
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
static void test_v4_read_setter_failure(gboolean legacy)
{
    struct fixture f;
    unsigned int before, writes;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_nth = 2;
    v4_expect("prearm20ms setter failure calls no ReadEndP and rejects before SETUP/HEADER/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !read_calls && commands[0x15] == 1
        && !commands[0x11] && !commands[0x12] && !headers && !ends
        && resources_clean(&f) && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("sample setter failure fixture starts", start(&f));
    before = sdk_event_count; writes = sdk_write_calls;
    timeout_fail_nth = timeout_calls + 1; feed(&f, 32);
    v4_expect("false sample setter emits no data/read; cleanup restores1000 before disable/STOP/drain",
        !accepted && !delivered && ends == 1 && resources_clean(&f)
        && sdk_events[before] == SDK_DISABLE && sdk_events[before+1] == SDK_STOP
        && sdk_write_calls == writes + 1 && timeout_last_write_ms == 1000
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("real sample read error fixture starts", start(&f));
    read_fail_call = read_calls + 1; feed(&f, 32);
    v4_expect("SDK read failure preserves actual0/no stale LOGIC and disables before successful STOP",
        !accepted && !delivered && ends == 1 && resources_clean(&f)
        && !sdk_mock_enabled && commands[0x15] == 2 && timeout_last_write_ms == 1000
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("poststop drain setter failure fixture starts", start(&f));
    before = read_calls; timeout_fail_nth = timeout_calls + 2;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    v4_expect("false postdrain setter calls no ReadEndP; existing void-drain cleanup remains warning-only",
        read_calls == (int)before && ends == 1 && resources_clean(&f)
        && commands[0x15] == 2 && !timeout_io_without_success);
    /* This resources_clean result is not a full capture PASS: the logged
     * setter/error/quiet failure must be rejected by the log/outer gates. */
    fixture_clear(&f);
}
static void test_v4_timeout_exit_retry(gboolean legacy)
{
    struct fixture f;
    struct sr_context *context = NULL;
    libusb_context *saved;
    int ret;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    ret = sr_init(&context);
    v4_expect("timeout ownership exit test initializes context without hardware scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(context->driver_list); context->driver_list = g_new0(struct sr_dev_driver *, 2);
    context->driver_list[0] = &f.driver; f.drvc->sr_ctx = context; saved = context->libusb_ctx;
    v4_expect("timeout ownership exit fake capture starts", start(&f));
    timeout_fail_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    v4_expect("std_cleanup refuses pending STOP after setter failure without freeing context",
        std_cleanup(&f.driver) != SR_OK && f.driver.context == f.drvc
        && f.sdi->priv == f.devc && f.devc->upload_stop_pending && !close_calls);
    ret = sr_exit(context);
    v4_expect("sr_exit preserves USB/private/pending context on timeout-induced retirement refusal",
        ret != SR_OK && context->libusb_ctx == saved && f.driver.context == f.drvc
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty && !close_calls);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    timeout_fail_all = FALSE;
    v4_expect("explicit close recovers timeout-induced STOP and releases handle once",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && close_calls == 1
        && !timeout_bad_arguments && !timeout_io_without_success);
    ret = sr_exit(context);
    v4_expect("sr_exit retry releases remaining device/context once after explicit close recovery",
        ret == SR_OK && !f.driver.context && !f.session.devs && close_calls == 1);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}
static void test_v4_legacy_export_fallback(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); memset(&wch_api, 0, sizeof(wch_api));
    sdk_missing_export = "CH375SetTimeoutEx";
    expect("UPLOAD V4: missing optional TimeoutEx selects legacy timeout while required upload exports remain",
        dla_wch_load() && !wch_api.timeout_ex && wch_api.timeout
        && wch_api.set_buf_upload_ex && wch_api.clear_buf_upload);
    sdk_missing_export = NULL;
    v4_expect("legacy fallback capture completes with uniform deadlines and unchanged SDK lifecycle", start(&f));
    feed(&f, 200000);
    v4_expect("legacy fallback exact count/END and no unprepared SDK I/O", delivered == 50000
        && ends == 1 && resources_clean(&f) && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}

static void test_v5_sdk1_configuration_ownership(gboolean dirty_only)
{
    struct fixture f;
    struct sr_channel_group *cg;
    struct pwm_setting saved;
    uint64_t samplerate;
    unsigned int writes;
    fixture_init(&f, TRUE);
    cg = sr_channel_group_new(f.sdi, "PWM0", NULL);
    f.devc->pwm[0].frequency = 1000000; f.devc->pwm[0].duty = 50;
    f.devc->pwm[0].enabled = TRUE;
    saved = f.devc->pwm[0]; samplerate = f.devc->samplerate;
    if (dirty_only) {
        sdk_fail_disable_all = TRUE;
        expect("STOP V5 SDK1: failed close-only retirement retains dirty without pendingSTOP",
            sr_dev_close(f.sdi) == SR_ERR_IO && f.devc->wch.upload_dirty
            && !f.devc->upload_stop_pending && f.sdi->status == SR_ST_ACTIVE);
    } else {
        expect("STOP V5 SDK1: pending configuration fixture starts", start(&f));
        fail_opcode = 0x15; opcode_failures = 1;
        sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
        expect("STOP V5 SDK1: failed STOP ends app but retains only pending hardware ownership",
            !has_application_resources(f.devc) && f.devc->upload_stop_pending
            && !f.devc->wch.upload_dirty && ends == 1);
    }
    writes = sdk_write_calls;
    expect("STOP V5 SDK1: pending/dirty rejects samplerate mutation before cached configuration changes",
        config_set(SR_CONF_SAMPLERATE, g_variant_new_uint64(SR_MHZ(100)), f.sdi, NULL) == SR_ERR
        && f.devc->samplerate == samplerate && sdk_write_calls == writes);
    expect("STOP V5 SDK1: pending/dirty rejects PWM frequency and preserves cached setting without packet",
        config_set(SR_CONF_OUTPUT_FREQUENCY, g_variant_new_double(2000000), f.sdi, cg) == SR_ERR
        && !memcmp(&saved, &f.devc->pwm[0], sizeof(saved)) && sdk_write_calls == writes);
    expect("STOP V5 SDK1: pending/dirty rejects PWM duty/disable writes without touching active generator setting",
        config_set(SR_CONF_DUTY_CYCLE, g_variant_new_double(25), f.sdi, cg) == SR_ERR
        && config_set(SR_CONF_ENABLED, g_variant_new_boolean(FALSE), f.sdi, cg) == SR_ERR
        && !memcmp(&saved, &f.devc->pwm[0], sizeof(saved)) && sdk_write_calls == writes);
    sdk_fail_disable_all = FALSE; opcode_failures = 0;
    expect("STOP V5 SDK1: only explicit STOP/close recovery retires ownership without PWM packets",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f)
        && f.devc->pwm[0].enabled && sdk_write_calls == writes + 1
        && !memcmp(&saved, &f.devc->pwm[0], sizeof(saved)));
    expect("STOP V5 SDK1: configuration mutation is allowed again after explicit retirement",
        config_set(SR_CONF_SAMPLERATE, g_variant_new_uint64(SR_MHZ(100)), f.sdi, NULL) == SR_OK
        && f.devc->samplerate == SR_MHZ(100));
    fixture_clear(&f);
}

int main(void)
{
    unsigned int transport, fault, nth; gboolean wch;
    setvbuf(stdout, NULL, _IONBF, 0); sr_log_loglevel_set(SR_LOG_NONE);
    for (transport = 0; transport < 2; transport++) {
        wch = transport == 0;
        test_prearm_scripted(wch);
        test_normal_capture(wch); test_watchdog(wch); test_finite_limits(wch); test_frontend_failure(wch);
        for (fault = FAULT_STOP; fault <= FAULT_ARM; fault++) test_startup_failure(wch, fault, 0);
        if (!wch) for (nth = 1; nth <= NUM_TRANSFERS; nth++) {
            test_startup_failure(FALSE, FAULT_SUBMIT, nth); test_startup_failure(FALSE, FAULT_ALLOC, nth);
        }
        test_nested_header(wch); test_stop_and_transport_failure(wch); test_lifecycle(wch); test_atomic_clear(wch);
    }
    test_upload_normal_and_repeat();
    test_upload_start_failures();
    test_upload_dirty_ownership(FALSE); test_upload_dirty_ownership(TRUE);
    test_upload_absent_exports();
    test_upload_close_only_false(); test_upload_public_exit_dirty();
    test_upload_enable_false_unknown_side_effect();
    test_upload_v3_observed_active_stop();
    test_upload_v3_disable_before_stop_failure();
    test_upload_v3_pending_stop_close_retry();
    test_upload_v3_prearm_stop_failures();
    test_upload_public_exit_pending_stop();
    test_wire_trace();
    test_guards_and_capabilities();
    test_real_session_rollback(FALSE); test_real_session_rollback(TRUE);
    test_public_exit_retry(TRUE); test_public_exit_retry(FALSE);
    printf("V3 inherited checks: %u (expected534).\n", checks);
    if (checks != 534) { failures++; printf("FAIL: inherited534-check coverage changed.\n"); }
    for (transport = 0; transport < 2; transport++) {
        gboolean legacy = transport != 0;
        test_v4_prearm_setter_failure(legacy);
        test_v4_terminal_setter_failure(legacy);
        test_v4_arm_setter_failure(legacy);
        test_v4_read_setter_failure(legacy);
        test_v4_timeout_exit_retry(legacy);
    }
    test_v4_legacy_export_fallback();
    printf("V4 inherited checks: %u (expected585).\n", checks);
    if (checks != 585) { failures++; printf("FAIL: inherited585-check coverage changed.\n"); }
    test_v5_sdk1_configuration_ownership(FALSE);
    test_v5_sdk1_configuration_ownership(TRUE);
    printf("Audit: %u checks, %u pending behavioral failures. No hardware accessed.\n", checks, failures);
    return failures ? 1 : 0;
}
