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
    if (opcode == 0x12) reads_at_arm = read_calls;
    if ((int)opcode == fail_opcode && opcode_failures) {
        opcode_failures--; return SR_ERR_IO;
    }
    return SR_OK;
}
static int audit_read(uint8_t *data, int length, int *actual)
{
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
#include "D:/Documente/analizor logic/artifacts/dla32-wch-stop-v5-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h"
#undef LOG_PREFIX
#undef LoadLibraryExW
#undef GetProcAddress
#undef FreeLibrary
G_STATIC_ASSERT(DLA_WCH_UPLOAD_DIAGNOSTIC == 0);

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
#include "D:/Documente/analizor logic/artifacts/dla32-wch-stop-v5-source/libsigrok/src/hardware/fnirsi-dla32/api.c"

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
    fail_opcode = -1; opcode_failures = 0;
    source_result = header_result = logic_result = end_result = SR_OK;
    headers = header_attempts = ends = end_attempts = sources = read_count = 0;
    read_calls = read_fail_call = submit_calls = fail_submit_call = event_calls = 0;
    close_calls = unsafe_frees = fail_alloc_call = alloc_calls = cancel_calls = 0;
    event_result = LIBUSB_SUCCESS;
    endless_drain = consume_logic = real_sources = FALSE;
    nested_header = stop_in_header = FALSE; nested_safe = TRUE; removed_stop_check_id = 0;
    delivered = accepted = 0; fake_trigger = NULL; f->use_wch = use_wch;
    memset(&wch_api, 0, sizeof(wch_api)); sdk_missing_export = NULL;
    sdk_write_calls = sdk_read_calls = sdk_setter_calls = sdk_unknown_exports = 0;
    sdk_timeout_bad = sdk_unprepared_io = sdk_timeout_fail_nth = 0;
    sdk_last_timeout = sdk_last_write_ms = sdk_last_read_ms = sdk_short_stop_count = 0;
    sdk_timeout_fail_all = sdk_timeout_prepared = sdk_rollback_stop_fail = FALSE;
    if (use_wch && (!dla_wch_load() || sdk_unknown_exports)) { fprintf(stderr, "Fake SDK binding failed\n"); abort(); }

    f->sdi = g_malloc0(sizeof(*f->sdi)); f->drvc = g_malloc0(sizeof(*f->drvc));
    f->devc = g_malloc0(sizeof(*f->devc));
    f->devc->use_wch = use_wch; f->devc->wch.opened = use_wch;
    f->devc->usb3 = f->devc->logic_only = TRUE;
    f->devc->samplerate = SR_MHZ(50); f->devc->threshold = 1;
    f->devc->pwm[0].frequency = 1000000; f->devc->pwm[0].duty = 50;
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
        queued_count() || sources || unsafe_frees || (f->use_wch && f->devc->upload_stop_pending)) return FALSE;
    for (i = 0; i < ARRAY_SIZE(f->devc->transfers); i++)
        if (f->devc->transfers[i] || f->devc->transfer_submitted[i]) return FALSE;
    return TRUE;
}
static void fixture_clear(struct fixture *f)
{
    if (f->sdi) {
        source_result = header_result = logic_result = end_result = SR_OK;
        opcode_failures = read_fail_call = 0; endless_drain = FALSE;
        sdk_timeout_fail_all = sdk_rollback_stop_fail = FALSE; sdk_timeout_fail_nth = sdk_short_stop_count = 0;
        event_result = LIBUSB_SUCCESS;
        if (f->devc->acquiring) { sr_dev_acquisition_stop(f->sdi); fixture_pump(f); }
        if (f->use_wch && f->devc->upload_stop_pending) sr_dev_acquisition_stop(f->sdi);
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

    expect_transport(&f, "failed STOP releases application callbacks but retains WCH pending ownership",
        ends == 1 && !has_application_resources(f.devc) &&
        (wch ? (f.devc->upload_stop_pending && !resources_clean(&f)) : resources_clean(&f)));
    if (wch) expect_transport(&f, "explicit failed-STOP retry clears pending before teardown",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f));
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

/* Additional SDK0 checks use the real V5 API and real transport helpers. */
static void sdk0_mode(gboolean legacy) { if (legacy) wch_api.timeout_ex = NULL; }
static void sdk0_check(const char *name, gboolean value)
{
    char label[240]; snprintf(label, sizeof(label), "SDK0 V5 %s: %s", wch_api.timeout_ex ? "Ex" : "legacy", name);
    expect(label, value);
}
static void sdk0_config_guard(struct fixture *f)
{
    uint64_t saved_rate = f->devc->samplerate, saved_limit = f->devc->limits.limit_samples;
    gboolean saved_stream = f->devc->stream; unsigned int saved_threshold = f->devc->threshold;
    struct pwm_setting saved_pwm = f->devc->pwm[0]; unsigned int writes = sdk_write_calls;
    struct sr_channel_group group = { .name = "PWM0" };
    GSList *saved_groups = f->sdi->channel_groups;
    GVariant *rate = g_variant_ref_sink(g_variant_new_uint64(SR_MHZ(100)));
    GVariant *limit = g_variant_ref_sink(g_variant_new_uint64(100));
    GVariant *threshold = g_variant_ref_sink(g_variant_new("(dd)", 2.5, 2.5));
    GVariant *mode = g_variant_ref_sink(g_variant_new_string(saved_stream ? "Buffer" : "Stream"));
    GVariant *frequency = g_variant_ref_sink(g_variant_new_double(2000000));
    GVariant *duty = g_variant_ref_sink(g_variant_new_double(60));
    GVariant *enable = g_variant_ref_sink(g_variant_new_boolean(TRUE));
    f->sdi->channel_groups = g_slist_append(NULL, &group);
    sdk0_check("pending STOP rejects rate/count/threshold/mode configuration before cache mutation",
        sr_config_set(f->sdi, NULL, SR_CONF_SAMPLERATE, rate) != SR_OK &&
        sr_config_set(f->sdi, NULL, SR_CONF_LIMIT_SAMPLES, limit) != SR_OK &&
        sr_config_set(f->sdi, NULL, SR_CONF_VOLTAGE_THRESHOLD, threshold) != SR_OK &&
        sr_config_set(f->sdi, NULL, SR_CONF_DEVICE_MODE, mode) != SR_OK &&
        f->devc->samplerate == saved_rate && f->devc->limits.limit_samples == saved_limit &&
        f->devc->threshold == saved_threshold && f->devc->stream == saved_stream);
    sdk0_check("pending STOP rejects PWM frequency/duty/enable without packets or cached changes",
        sr_config_set(f->sdi, &group, SR_CONF_OUTPUT_FREQUENCY, frequency) != SR_OK &&
        sr_config_set(f->sdi, &group, SR_CONF_DUTY_CYCLE, duty) != SR_OK &&
        sr_config_set(f->sdi, &group, SR_CONF_ENABLED, enable) != SR_OK &&
        !memcmp(&saved_pwm, &f->devc->pwm[0], sizeof(saved_pwm)) && sdk_write_calls == writes);
    g_slist_free(f->sdi->channel_groups); f->sdi->channel_groups = saved_groups;
    g_variant_unref(rate); g_variant_unref(limit); g_variant_unref(threshold); g_variant_unref(mode);
    g_variant_unref(frequency); g_variant_unref(duty); g_variant_unref(enable);
}
static void test_sdk0_v5_prearm_failure(gboolean legacy)
{
    struct fixture f; unsigned int reads;
    fixture_init(&f, TRUE); sdk0_mode(legacy); fail_opcode = 0x15; opcode_failures = 20;
    sdk0_check("persistent initial STOP failure rejects before any SETUP/ARM/HEADER/read",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && commands[0x15] == 2 &&
        !commands[0x11] && !commands[0x12] && !headers && !ends && !read_calls &&
        f.devc->upload_stop_pending && !has_application_resources(f.devc));
    reads = read_calls; sdk0_config_guard(&f);
    sdk0_check("initial pending STOP refuses start/clear/inactive-stop/close without free or drain",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK &&
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO && sr_dev_close(f.sdi) == SR_ERR_IO &&
        f.sdi->status == SR_ST_ACTIVE && f.devc->wch.opened && f.devc->upload_stop_pending &&
        !close_calls && read_calls == (int)reads);
    opcode_failures = 0;
    sdk0_check("initial inactive-stop recovery accepts STOP then two20ms reads and restores ownership",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f) && read_calls == (int)reads + 2 &&
        sdk_last_write_ms == 1000 && sdk_last_read_ms == 20 && !sdk_unprepared_io && !sdk_timeout_bad);
    sdk0_check("positive capture can start after initial pending recovery", start(&f)); feed(&f, 200000);
    sdk0_check("recovered capture has exact data/END and no SDK upload dependency",
        delivered == 50000 && headers == 1 && ends == 1 && resources_clean(&f) && !sdk_unknown_exports);
    fixture_clear(&f);
}
static void test_sdk0_v5_terminal_failure(gboolean legacy, gboolean short_write)
{
    struct fixture f; unsigned int reads, stops;
    fixture_init(&f, TRUE); sdk0_mode(legacy);
    sdk0_check("terminal STOP fixture begins", start(&f)); feed(&f, 32);
    reads = read_calls; stops = commands[0x15];
    if (short_write) sdk_short_stop_count = 20; else { fail_opcode = 0x15; opcode_failures = 20; }
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    sdk0_check(short_write ? "short successful STOP still retains pending beyond END" : "false STOP retains pending beyond END",
        delivered == 8 && ends == 1 && !has_application_resources(f.devc) && f.devc->upload_stop_pending &&
        !resources_clean(&f) && read_calls == (int)reads && commands[0x15] == stops + 1);
    sdk0_config_guard(&f);
    sdk0_check("terminal pending refuses new capture/clear and failed close retains ACTIVE handle",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK &&
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE && f.devc->wch.opened &&
        !close_calls && read_calls == (int)reads);
    opcode_failures = sdk_short_stop_count = 0;
    sdk0_check("public inactive-stop recovery writes exactly one accepted STOP then drains two reads",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && commands[0x15] == stops + 3 &&
        read_calls == (int)reads + 2 && resources_clean(&f) && !close_calls);
    sdk0_check("capture after terminal recovery starts", start(&f)); feed(&f, 200000);
    sdk0_check("capture after terminal recovery has exact fresh accepted sample count", delivered == 50008 && ends == 2 && resources_clean(&f));
    sdk0_check("normal close releases once after pending recovered", sr_dev_close(f.sdi) == SR_OK && close_calls == 1);
    fixture_clear(&f);
}
static void test_sdk0_v5_timeout_failure(gboolean legacy)
{
    struct fixture f; unsigned int writes, reads;
    fixture_init(&f, TRUE); sdk0_mode(legacy); sdk_timeout_fail_all = TRUE;
    sdk0_check("false prearm timeout setter blocks all WriteData/ReadEndP with pending retained",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !sdk_write_calls && !sdk_read_calls &&
        !commands[0x15] && !headers && !ends && f.devc->upload_stop_pending);
    sdk_timeout_fail_all = FALSE;
    sdk0_check("prearm timeout failure close retry restores1000 before STOP and releases once",
        sr_dev_close(f.sdi) == SR_OK && commands[0x15] == 1 && read_calls == 2 && close_calls == 1 &&
        sdk_last_write_ms == 1000 && sdk_last_read_ms == 20 && !sdk_unprepared_io && !sdk_timeout_bad);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk0_mode(legacy); sdk0_check("terminal setter-failure capture starts", start(&f));
    writes = sdk_write_calls; reads = read_calls; sdk_timeout_fail_all = TRUE;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    sdk0_check("failed terminal setter retains pending without stale USB calls despite END",
        sdk_write_calls == writes && read_calls == (int)reads && f.devc->upload_stop_pending && ends == 1);
    sdk0_config_guard(&f); sdk_timeout_fail_all = FALSE;
    sdk0_check("terminal setter close retry accepts STOP/drains/releases exactly once",
        sr_dev_close(f.sdi) == SR_OK && sdk_write_calls == writes + 1 && read_calls == (int)reads + 2 &&
        close_calls == 1 && resources_clean(&f) && !sdk_unprepared_io && !sdk_timeout_bad);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk0_mode(legacy); sdk_timeout_fail_nth = 2;
    sdk0_check("false read-timeout setter leaves actual0 and calls no ReadEndP or decoder",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && sdk_read_calls == 0 && !accepted && !delivered &&
        !headers && !ends && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    fixture_clear(&f);
}
static void test_sdk0_v5_rollback_failure(gboolean legacy, gboolean arm)
{
    struct fixture f; unsigned int reads;
    fixture_init(&f, TRUE); sdk0_mode(legacy);
    if (arm) { fail_opcode = 0x12; opcode_failures = 1; } else header_result = SR_ERR;
    /* Fail rollback STOP only after HEADER was attempted, independently of
     * the HEADER/ARM fault; the fake SDK writer performs that injection. */
    sdk_rollback_stop_fail = TRUE;
    sdk0_check(arm ? "ARM failure plus rollback STOP failure returns IO and retains pending" : "HEADER failure plus rollback STOP failure returns IO and retains pending",
        sr_dev_acquisition_start(f.sdi) != SR_OK && f.devc->upload_stop_pending &&
        !has_application_resources(f.devc) && !resources_clean(&f) && header_attempts == 1 &&
        ends == headers);
    reads = read_calls; sdk0_config_guard(&f);
    sdk0_check("rollback pending blocks retry/clear without transport reads",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK && read_calls == (int)reads);
    opcode_failures = 0; sdk_rollback_stop_fail = FALSE; header_result = SR_OK;
    sdk0_check("rollback explicit STOP recovery accepts one command and drains before a new capture",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f) && read_calls == (int)reads + 2);
    sdk0_check("new capture after rollback recovery starts", start(&f)); feed(&f, 200000);
    sdk0_check("new capture after rollback contains exact fresh data", delivered == 50000 && resources_clean(&f));
    fixture_clear(&f);
}
static void test_sdk0_v5_exit_retry(gboolean legacy)
{
    struct fixture f; struct sr_context *ctx = NULL; libusb_context *saved; int ret;
    fixture_init(&f, TRUE); sdk0_mode(legacy); ret = sr_init(&ctx);
    sdk0_check("pending-exit fixture initializes library context without device scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(ctx->driver_list); ctx->driver_list = g_new0(struct sr_dev_driver *, 2);
    ctx->driver_list[0] = &f.driver; f.drvc->sr_ctx = ctx; saved = ctx->libusb_ctx;
    sdk0_check("pending-exit fake capture starts", start(&f));
    fail_opcode = 0x15; opcode_failures = 20; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    ret = sr_exit(ctx);
    sdk0_check("failed sr_exit retains USB/private/pending ownership after END and no handle free",
        ret != SR_OK && ctx->libusb_ctx == saved && f.driver.context == f.drvc &&
        f.sdi->priv == f.devc && f.devc->upload_stop_pending && !close_calls && !sources && ends == 1);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    opcode_failures = 0;
    sdk0_check("explicit close retries pending STOP and releases handle once before sr_exit retry",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && close_calls == 1);
    ret = sr_exit(ctx);
    sdk0_check("sr_exit retry frees remaining context without duplicate handle close",
        ret == SR_OK && !f.driver.context && !f.session.devs && close_calls == 1);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}
static void test_sdk0_v5_legacy_binding(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); memset(&wch_api, 0, sizeof(wch_api)); sdk_missing_export = "CH375SetTimeoutEx";
    expect("SDK0 V5 real export loader selects legacy fallback without upload dependencies",
        dla_wch_load() && !wch_api.timeout_ex && wch_api.timeout && !sdk_unknown_exports);
    sdk_missing_export = NULL; expect("SDK0 V5 legacy-bound positive capture starts", start(&f)); feed(&f, 200000);
    expect("SDK0 V5 legacy-bound positive capture completes with correct setter/IO ordering",
        delivered == 50000 && ends == 1 && resources_clean(&f) && !sdk_timeout_bad && !sdk_unprepared_io && !sdk_unknown_exports);
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
    test_wire_trace();
    test_guards_and_capabilities();
    test_real_session_rollback(FALSE); test_real_session_rollback(TRUE);
    test_public_exit_retry(TRUE); test_public_exit_retry(FALSE);
    printf("SDK0 inherited checks: %u (443 source checks plus one explicit retry).\n", checks);
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
    printf("Audit: %u checks, %u pending behavioral failures. No hardware accessed.\n", checks, failures);
    return failures ? 1 : 0;
}
