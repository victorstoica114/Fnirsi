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
    if ((int)opcode == fail_opcode && opcode_failures) {
        opcode_failures--; return SR_ERR_IO;
    }
    return SR_OK;
}
static int audit_read(uint8_t *data, int length, int *actual)
{
    read_calls++; fake_now += 1000; *actual = 0;
    if (read_calls == read_fail_call) return LIBUSB_ERROR_IO;
    *actual = MIN(endless_drain ? 32 : read_count, length);
    if (*actual > 0) {
        memset(data, 0, *actual);
        if (!endless_drain) read_count -= *actual;
    }
    return LIBUSB_SUCCESS;
}

#define LIBSIGROK_FNIRSI_DLA32_WCH_H
struct dla_wch { ULONG index; gboolean opened; };
static gboolean dla_wch_load(void) { return TRUE; }
static int dla_wch_open(struct dla_wch *wch) { wch->opened = TRUE; return SR_OK; }
static void dla_wch_close(struct dla_wch *wch)
{ if (wch->opened) close_calls++; wch->opened = FALSE; }
static gboolean dla_wch_model32(struct dla_wch *wch) { (void)wch; return TRUE; }
static int dla_wch_write(struct dla_wch *wch, const uint8_t *data, ULONG length)
{ (void)wch; return audit_command(data, length); }
static int dla_wch_read(struct dla_wch *wch, ULONG pipe, uint8_t *data,
    ULONG length, int *actual, unsigned int timeout)
{ (void)wch; (void)pipe; (void)timeout; return audit_read(data, length, actual); }
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
#include "../src/libsigrok/src/hardware/fnirsi-dla32/api.c"

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
        queued_count() || sources || unsafe_frees) return FALSE;
    for (i = 0; i < ARRAY_SIZE(f->devc->transfers); i++)
        if (f->devc->transfers[i] || f->devc->transfer_submitted[i]) return FALSE;
    return TRUE;
}
static void fixture_clear(struct fixture *f)
{
    if (f->sdi) {
        source_result = header_result = logic_result = end_result = SR_OK;
        opcode_failures = read_fail_call = 0; endless_drain = FALSE;
        event_result = LIBUSB_SUCCESS;
        if (f->devc->acquiring) { sr_dev_acquisition_stop(f->sdi); fixture_pump(f); }
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
    expect_transport(&f, "failed STOP cannot retain source or queued ownership", ends == 1 && resources_clean(&f)); fixture_clear(&f);
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
int main(void)
{
    unsigned int transport, fault, nth; gboolean wch;
    setvbuf(stdout, NULL, _IONBF, 0); sr_log_loglevel_set(SR_LOG_NONE);
    for (transport = 0; transport < 2; transport++) {
        wch = transport == 0;
        test_normal_capture(wch); test_watchdog(wch); test_finite_limits(wch); test_frontend_failure(wch);
        for (fault = FAULT_STOP; fault <= FAULT_ARM; fault++) test_startup_failure(wch, fault, 0);
        if (!wch) for (nth = 1; nth <= NUM_TRANSFERS; nth++) {
            test_startup_failure(FALSE, FAULT_SUBMIT, nth); test_startup_failure(FALSE, FAULT_ALLOC, nth);
        }
        test_nested_header(wch); test_stop_and_transport_failure(wch); test_lifecycle(wch); test_atomic_clear(wch);
    }
    test_guards_and_capabilities();
    test_real_session_rollback(FALSE); test_real_session_rollback(TRUE);
    test_public_exit_retry(TRUE); test_public_exit_retry(FALSE);
    printf("Audit: %u checks, %u pending behavioral failures. No hardware accessed.\n", checks, failures);
    return failures ? 1 : 0;
}
