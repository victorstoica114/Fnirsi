/* SPDX-License-Identifier: GPL-3.0-only */
/* Offline interactions with the real V7 API/transport/worker. Every SDK
 * function is fake; there is no sr_init(), device scan or vendor DLL load.
 * Real Win32 threads/events and GLib timers test lifetime and responsiveness. */
#include <config.h>
#include <glib.h>
#include <libsigrok/libsigrok.h>
#include "libsigrok-internal.h"
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#ifndef DLA_WCH_WORKER
#define DLA_WCH_WORKER 1
#endif
#if !DLA_WCH_WORKER
#error "Interaction fixture requires the actual worker"
#endif

static DWORD main_thread;
static volatile LONG commands[256], sdk_reads, sample_reads, sdk_writes;
static volatile LONG io_active, overlapping_io, timeout_wrong, unprepared_io;
static volatile LONG wrong_callback_thread, late_logic, callback_depth, create_calls;
static volatile LONG armed, fail_stop, short_stop, worker_setter_fail;
static volatile LONG fail_create, hold_exit;
static volatile LONG fail_arm, fail_close_worker, worker_close_attempts;
static HANDLE worker_thread_handle, diagnostic_done;
static void (*inject_diagnostics)(void);
static HANDLE read_entered, read_release, terminal_waiting, exit_release;
static unsigned int checks, failures, headers, ends, sources, heartbeats;
static uint64_t delivered;
static gboolean stop_from_header, stop_from_logic, inspect_end, end_guard_ok;
static int header_status, source_status, logic_status;
static gboolean reentrant_source_safe;
static gboolean stop_from_terminal_log, terminal_stop_seen;
static struct sr_dev_inst *current_sdi;
static gboolean diagnostic_failure_seen;
static ULONG prepared_ms;
static unsigned int sdk_closes;
static DWORD prepared_thread;
static unsigned int alloc_count, alloc_fail;
static volatile LONG fail_event, fail_notify, fail_reset, drain_fill_once;
static gboolean distinct_reads;
static gboolean fake_device_present;
static DWORD stop_error, drain_error;
struct script { int count; BOOL ok; DWORD error; gboolean held; };
static struct script scripts[8];
static unsigned int script_length;

static LONG atomic_read(volatile LONG *value)
{ return InterlockedCompareExchange(value, 0, 0); }
static void expect(const char *name, gboolean pass)
{
    checks++; if (!pass) failures++;
    printf("%s: %s\n", pass ? "PASS" : "FAIL", name);
}
static void enter_io(void)
{ if (InterlockedIncrement(&io_active) != 1) InterlockedIncrement(&overlapping_io); }
static void leave_io(void)
{ InterlockedDecrement(&io_active); }
static BOOL WINAPI fake_timeout(ULONG index, ULONG a, ULONG b);
static VOID WINAPI fake_sdk_close(ULONG index)
{ (void)index; sdk_closes++; }
static BOOL WINAPI fake_timeout_ex(ULONG index, ULONG a, ULONG b, ULONG c, ULONG d)
{
    if (index || a != b || a != c || a != d) InterlockedIncrement(&timeout_wrong);
    if (GetCurrentThreadId() != main_thread && atomic_read(&worker_setter_fail)) {
        InterlockedDecrement(&worker_setter_fail);
        SetLastError(ERROR_GEN_FAILURE); return FALSE;
    }
    prepared_ms = a; prepared_thread = GetCurrentThreadId();
    SetLastError(ERROR_SUCCESS); return TRUE;
}
static BOOL WINAPI fake_timeout(ULONG index, ULONG a, ULONG b)
{ return fake_timeout_ex(index, a, b, a, b); }
static HANDLE WINAPI fake_sdk_open(ULONG index)
{ (void)index; return fake_device_present ? (HANDLE)(uintptr_t)1 : INVALID_HANDLE_VALUE; }
static ULONG WINAPI fake_usb_id(ULONG index)
{ (void)index; return 0x55371a86; }
static BOOL WINAPI fake_initial_timeout(ULONG index, ULONG a, ULONG b)
{ return !index && a == 1000 && b == 20; }
static void check_preparation(ULONG timeout)
{
    if (prepared_thread != GetCurrentThreadId()) InterlockedIncrement(&unprepared_io);
    if (prepared_ms != timeout) InterlockedIncrement(&timeout_wrong);
    prepared_thread = 0;
}
static BOOL WINAPI fake_write(ULONG index, PVOID data, PULONG length)
{
    unsigned int opcode = *length > 9 ? ((uint8_t *)data)[9] : 0;
    BOOL result = TRUE;

    (void)index; enter_io(); check_preparation(1000);
    InterlockedIncrement(&sdk_writes); InterlockedIncrement(&commands[opcode]);
    if (opcode == 0x12) InterlockedExchange(&armed, TRUE);
    if (opcode == 0x12 && atomic_read(&fail_arm)) {
        *length = 0; SetLastError(ERROR_GEN_FAILURE); result = FALSE;
    }
    if (opcode == 0x15 && atomic_read(&armed)) {
        if (atomic_read(&fail_stop)) {
            *length = 0; SetLastError(stop_error); result = FALSE;
        } else if (atomic_read(&short_stop)) {
            (*length)--; SetLastError(ERROR_SUCCESS);
        } else {
            InterlockedExchange(&armed, FALSE); SetLastError(ERROR_SUCCESS);
        }
    }
    leave_io(); return result;
}
static BOOL WINAPI fake_read(ULONG index, ULONG pipe, PVOID data, PULONG length)
{
    BOOL ok = TRUE;
    DWORD error = ERROR_SUCCESS;
    unsigned int number = 0;
    int count = 0;

    (void)index; enter_io(); InterlockedIncrement(&sdk_reads);
    if (pipe == 2) {
        uint8_t *reply = data;
        check_preparation(500);
        if (*length < 19) { leave_io(); return FALSE; }
        memset(reply, 0, 19); reply[0] = 0x0a; reply[1] = 2; reply[2] = 0x0d;
        memcpy(reply + 13, "DL32", 4); reply[18] = 0x0b; *length = 19;
        leave_io(); SetLastError(ERROR_SUCCESS); return TRUE;
    }
    if (atomic_read(&armed)) {
        check_preparation(1000);
        number = (unsigned int)InterlockedIncrement(&sample_reads) - 1;
        if (number < script_length) {
            const struct script *s = &scripts[number];
            if (inject_diagnostics) inject_diagnostics();
            if (s->held) {
                SetEvent(read_entered);
                if (WaitForSingleObject(read_release, 5000) != WAIT_OBJECT_0) {
                    leave_io(); *length = 0; SetLastError(ERROR_TIMEOUT); return FALSE;
                }
            }
            count = MIN(s->count, (int)*length); ok = s->ok; error = s->error;
        }
    } else {
        check_preparation(20);
        if (drain_error) { error = drain_error; ok = FALSE; }
        if (atomic_read(&drain_fill_once)) {
            InterlockedDecrement(&drain_fill_once);
            count = MIN(32, (int)*length);
        }
    }
    if (count > 0) memset(data, distinct_reads ?
        (atomic_read(&armed) ? number + 1 : 0xdd) : 0x55, count);
    *length = count;
    leave_io(); SetLastError(error); return ok;
}

struct trampoline { LPTHREAD_START_ROUTINE start; LPVOID data; };
static DWORD WINAPI held_thread(LPVOID context)
{
    struct trampoline *call = context;
    LPTHREAD_START_ROUTINE start = call->start;
    LPVOID data = call->data;
    DWORD result;

    g_free(call); result = start(data);
    if (atomic_read(&hold_exit)) {
        SetEvent(terminal_waiting);
        WaitForSingleObject(exit_release, 5000);
    }
    return result;
}
static HANDLE WINAPI fake_create_thread(LPSECURITY_ATTRIBUTES security,
        SIZE_T size, LPTHREAD_START_ROUTINE start, LPVOID data,
        DWORD flags, LPDWORD id)
{
    struct trampoline *call;
    HANDLE handle;

    InterlockedIncrement(&create_calls);
    if (atomic_read(&fail_create)) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return NULL; }
    call = g_malloc(sizeof(*call)); call->start = start; call->data = data;
    handle = CreateThread(security, size, held_thread, call, flags, id);
    worker_thread_handle = handle;
    if (!handle) g_free(call);
    return handle;
}
static BOOL WINAPI fake_close_handle(HANDLE handle)
{
    if (handle == worker_thread_handle) {
        InterlockedIncrement(&worker_close_attempts);
        if (atomic_read(&fail_close_worker)) {
            InterlockedDecrement(&fail_close_worker);
            SetLastError(ERROR_INVALID_HANDLE); return FALSE;
        }
    }
    return CloseHandle(handle);
}
static void *fake_try_malloc(gsize bytes)
{ alloc_count++; return alloc_count == alloc_fail ? NULL : g_try_malloc(bytes); }
static void *fake_try_malloc0(gsize bytes)
{ alloc_count++; return alloc_count == alloc_fail ? NULL : g_try_malloc0(bytes); }
static void fake_sleep(gulong usec) { (void)usec; }
static struct sr_trigger *fake_trigger_get(struct sr_session *session)
{ (void)session; return NULL; }
static int fake_source_add(struct sr_session *session, int fd, int events,
        int timeout, sr_receive_data_callback cb, void *data)
{
    (void)session; (void)fd; (void)events; (void)timeout; (void)cb; (void)data;
    if (source_status == SR_OK) sources++;
    return source_status;
}
static int fake_source_remove(struct sr_session *session, int fd)
{ (void)session; (void)fd; sources--; return SR_OK; }
static int fake_pollfd_add(struct sr_session *session, GPollFD *pollfd,
        int timeout, sr_receive_data_callback cb, void *data)
{
    if (!pollfd || pollfd->fd <= 0 || pollfd->events != G_IO_IN || timeout != 100)
        return SR_ERR_ARG;
    return fake_source_add(session, -1, 0, timeout, cb, data);
}
static int fake_pollfd_remove(struct sr_session *session, GPollFD *pollfd)
{ (void)pollfd; return fake_source_remove(session, -1); }
static int fake_header(const struct sr_dev_inst *sdi);
static int fake_end(const struct sr_dev_inst *sdi);
static int fake_send(const struct sr_dev_inst *sdi, const struct sr_datafeed_packet *packet);
static HANDLE WINAPI fake_create_event(LPSECURITY_ATTRIBUTES attr, BOOL manual,
        BOOL initial, LPCWSTR name)
{
    if (atomic_read(&fail_event)) {
        InterlockedDecrement(&fail_event);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    return CreateEventW(attr, manual, initial, name);
}
static BOOL WINAPI fake_set_event(HANDLE handle)
{
    if (atomic_read(&fail_notify)) {
        InterlockedDecrement(&fail_notify); SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    return SetEvent(handle);
}
static BOOL WINAPI fake_reset_event(HANDLE handle)
{
    if (atomic_read(&fail_reset)) {
        InterlockedDecrement(&fail_reset); SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    return ResetEvent(handle);
}

#define CreateThread fake_create_thread
#define CloseHandle fake_close_handle
#define CreateEventW fake_create_event
#define SetEvent fake_set_event
#define ResetEvent fake_reset_event
#define g_try_malloc fake_try_malloc
#define g_try_malloc0 fake_try_malloc0
#define g_usleep fake_sleep
#define sr_session_trigger_get fake_trigger_get
#define sr_session_source_add fake_source_add
#define sr_session_source_remove fake_source_remove
#define sr_session_source_add_pollfd fake_pollfd_add
#define sr_session_source_remove_pollfd fake_pollfd_remove
#define std_session_send_df_header fake_header
#define std_session_send_df_end fake_end
#define sr_session_send fake_send
#undef SR_REGISTER_DEV_DRIVER
#define SR_REGISTER_DEV_DRIVER(name)
#include "../dla32-wch-worker-v8-source/libsigrok/src/hardware/fnirsi-dla32/api.c"
#undef CreateThread
#undef CloseHandle
#undef CreateEventW
#undef SetEvent
#undef ResetEvent
#undef g_try_malloc
#undef g_try_malloc0
#undef g_usleep

struct fixture {
    struct sr_dev_inst *sdi;
    struct dev_context *devc;
    struct drv_context drvc;
    struct sr_dev_driver driver;
    struct sr_context ctx;
    struct sr_session session;
};
static gboolean lifecycle_gate(const struct sr_dev_inst *sdi)
{
    struct dev_context *devc = sdi->priv;
    GVariant *value = g_variant_ref_sink(g_variant_new_uint64(1000));
    int configured = config_set(SR_CONF_LIMIT_SAMPLES, value, sdi, NULL);
    gboolean good = has_application_resources(devc) &&
        dev_acquisition_start(sdi) != SR_OK && dev_close((struct sr_dev_inst *)sdi) != SR_OK &&
        dev_clear(sdi->driver) != SR_OK && configured != SR_OK;
    g_variant_unref(value); return good;
}
static int fake_header(const struct sr_dev_inst *sdi)
{
    if (GetCurrentThreadId() != main_thread) InterlockedIncrement(&wrong_callback_thread);
    headers++;
    receive_events(-1, 0, (void *)sdi);
    reentrant_source_safe = !atomic_read(&sample_reads) && !atomic_read(&commands[0x12]);
    if (stop_from_header) dev_acquisition_stop((struct sr_dev_inst *)sdi);
    return header_status;
}
static int fake_end(const struct sr_dev_inst *sdi)
{
    if (GetCurrentThreadId() != main_thread) InterlockedIncrement(&wrong_callback_thread);
    ends++;
    if (inspect_end) end_guard_ok = lifecycle_gate(sdi);
    return SR_OK;
}
static int fake_send(const struct sr_dev_inst *sdi, const struct sr_datafeed_packet *packet)
{
    const struct sr_datafeed_logic *logic;
    struct dev_context *devc = sdi->priv;

    if (GetCurrentThreadId() != main_thread) InterlockedIncrement(&wrong_callback_thread);
    if (packet->type != SR_DF_LOGIC) return SR_OK;
    if (devc->stopping) InterlockedIncrement(&late_logic);
    if (logic_status != SR_OK) return logic_status;
    logic = packet->payload; delivered += logic->length / logic->unitsize;
    if (stop_from_logic) {
        dev_acquisition_stop((struct sr_dev_inst *)sdi);
        receive_events(-1, 0, (void *)sdi);
        reentrant_source_safe = devc->acquiring && devc->worker_dispatching && !ends;
    }
    return SR_OK;
}
static int logger(void *unused, int level, const char *format, va_list args)
{
    char rendered[1024];
    (void)unused; (void)level;
    if (GetCurrentThreadId() != main_thread) InterlockedIncrement(&wrong_callback_thread);
    vsnprintf(rendered, sizeof(rendered), format, args);
    if (strstr(rendered, "WCH worker diagnostic queue overflow/truncation")) diagnostic_failure_seen = TRUE;
    if (stop_from_terminal_log && !terminal_stop_seen && strstr(rendered, "WCH WORKER terminal:")) {
        terminal_stop_seen = TRUE;
        dev_acquisition_stop(current_sdi);
    }
    return SR_OK;
}
static void fixture_init(struct fixture *f, gboolean legacy)
{
    unsigned int i;

    memset(f, 0, sizeof(*f)); memset((void *)commands, 0, sizeof(commands));
    sdk_reads = sample_reads = sdk_writes = io_active = overlapping_io = 0;
    timeout_wrong = unprepared_io = wrong_callback_thread = late_logic = 0;
    armed = fail_stop = short_stop = worker_setter_fail = fail_create = hold_exit = 0;
    fail_arm = fail_close_worker = worker_close_attempts = 0;
    fail_event = fail_notify = fail_reset = drain_fill_once = 0;
    distinct_reads = FALSE;
    fake_device_present = FALSE;
    stop_error = ERROR_GEN_FAILURE; drain_error = ERROR_SUCCESS;
    worker_thread_handle = NULL; inject_diagnostics = NULL;
    headers = ends = sources = heartbeats = alloc_count = alloc_fail = 0;
    sdk_closes = 0;
    delivered = 0; script_length = 0; memset(scripts, 0, sizeof(scripts));
    stop_from_header = stop_from_logic = inspect_end = FALSE;
    stop_from_terminal_log = terminal_stop_seen = FALSE;
    diagnostic_failure_seen = FALSE;
    end_guard_ok = TRUE; reentrant_source_safe = TRUE;
    source_status = header_status = logic_status = SR_OK;
    prepared_ms = prepared_thread = 0;
    read_entered = CreateEventW(NULL, TRUE, FALSE, NULL);
    read_release = CreateEventW(NULL, TRUE, FALSE, NULL);
    terminal_waiting = CreateEventW(NULL, TRUE, FALSE, NULL);
    exit_release = CreateEventW(NULL, TRUE, FALSE, NULL);
    diagnostic_done = CreateEventW(NULL, TRUE, FALSE, NULL);
    memset(&wch_api, 0, sizeof(wch_api));
    wch_api.read = fake_read; wch_api.write = fake_write;
    wch_api.close = fake_sdk_close;
    wch_api.timeout = fake_timeout;
    wch_api.timeout_ex = legacy ? NULL : fake_timeout_ex;
    f->sdi = g_malloc0(sizeof(*f->sdi)); f->devc = g_malloc0(sizeof(*f->devc));
    f->devc->use_wch = f->devc->wch.opened = f->devc->usb3 = TRUE;
    f->devc->logic_only = TRUE; f->devc->samplerate = SR_MHZ(50);
    f->devc->limits.limit_samples = 64;
    f->driver = fnirsi_dla32_driver_info; f->driver.context = &f->drvc;
    f->drvc.sr_ctx = &f->ctx; f->drvc.instances = g_slist_append(NULL, f->sdi);
    f->sdi->driver = &f->driver; f->sdi->priv = f->devc;
    f->sdi->inst_type = SR_INST_USB; f->sdi->status = SR_ST_ACTIVE;
    f->sdi->conn = sr_usb_dev_inst_new(1, 1, NULL);
    f->sdi->session = &f->session;
    current_sdi = f->sdi;
    for (i = 0; i < 32; i++) sr_channel_new(f->sdi, i, SR_CHANNEL_LOGIC, TRUE, "test");
}
static void close_fake_handles(void)
{
    CloseHandle(read_entered); CloseHandle(read_release);
    CloseHandle(terminal_waiting); CloseHandle(exit_release);
    CloseHandle(diagnostic_done);
}
static gboolean pump_until(struct fixture *f, HANDLE event, gboolean finish)
{
    int64_t deadline = g_get_monotonic_time() + 3000000;

    while (g_get_monotonic_time() < deadline) {
        while (g_main_context_iteration(NULL, FALSE)) { }
        if (f->devc->source_added) receive_events(-1, 0, f->sdi);
        if (event && WaitForSingleObject(event, 0) == WAIT_OBJECT_0) return TRUE;
        if (finish && !has_application_resources(f->devc)) return TRUE;
        g_usleep(1000);
    }
    return FALSE;
}
static void fixture_clear(struct fixture *f)
{
    GSList *l;

    SetEvent(read_release); SetEvent(exit_release);
    fail_stop = short_stop = worker_setter_fail = fail_create = 0;
    if (f->devc->acquiring) {
        dev_acquisition_stop(f->sdi);
        gboolean retired = pump_until(f, NULL, TRUE);
        expect("fixture safely retires outstanding worker", retired);
        if (!retired) {
            fprintf(stderr, "Refusing fixture free while worker ownership remains\n");
            ExitProcess(2);
        }
    }
    if (f->devc->upload_stop_pending) expect("explicit fixture STOP recovery", dev_acquisition_stop(f->sdi) == SR_OK);
    expect("no worker I/O overlap, timeout mutation or frontend callback on worker",
        !atomic_read(&overlapping_io) && !atomic_read(&timeout_wrong) &&
        !atomic_read(&unprepared_io) && !atomic_read(&wrong_callback_thread) && !atomic_read(&late_logic));
    free_prepared_transfers(f->devc);
    if (f->devc->worker) ExitProcess(2);
    for (l = f->sdi->channels; l; l = l->next) {
        struct sr_channel *ch = l->data; g_free(ch->name); g_free(ch);
    }
    g_slist_free(f->sdi->channels); g_slist_free(f->drvc.instances);
    g_free(f->sdi->conn); g_free(f->devc); g_free(f->sdi);
    current_sdi = NULL;
    close_fake_handles();
}
static gboolean heartbeat(gpointer unused)
{ (void)unused; heartbeats++; return G_SOURCE_CONTINUE; }

static void test_responsive_cancel(gboolean legacy)
{
    struct fixture f; guint timer; LONG initial_stops;
    int64_t begin, returned;

    fixture_init(&f, legacy); scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, TRUE}; script_length = 1;
    timer = g_timeout_add(2, heartbeat, NULL);
    expect("held-read capture starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("worker entered held SDK read", pump_until(&f, read_entered, FALSE));
    initial_stops = atomic_read(&commands[0x15]);
    begin = g_get_monotonic_time();
    expect("public stop returns without releasing the SDK read", dev_acquisition_stop(f.sdi) == SR_OK);
    returned = g_get_monotonic_time();
    for (unsigned int i = 0; i < 20; i++) {
        while (g_main_context_iteration(NULL, FALSE)) { }
        receive_events(-1, 0, f.sdi); g_usleep(1000);
    }
    expect("GLib timer ran while read remained held and no STOP raced it",
        heartbeats > 0 && WaitForSingleObject(read_release, 0) == WAIT_TIMEOUT &&
        atomic_read(&commands[0x15]) == initial_stops && f.devc->worker &&
        returned - begin < 100000 && has_application_resources(f.devc));
    inspect_end = TRUE; SetEvent(read_release);
    expect("worker performs ordered STOP/drain and finishes after read release", pump_until(&f, NULL, TRUE));
    expect("canceled positive payload accounted, no LOGIC, single END and END reentry blocked",
        f.devc->worker_unconsumed_bytes == 32 && delivered == 0 && ends == 1 && end_guard_ok &&
        atomic_read(&commands[0x15]) == initial_stops + 1 && !f.devc->upload_stop_pending);
    g_source_remove(timer); fixture_clear(&f);
}
static void test_native_exit_and_credit(void)
{
    struct fixture f; struct dla_worker_terminal terminal;
    fixture_init(&f, FALSE); scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 1;
    hold_exit = TRUE;
    expect("credit test starts", dev_acquisition_start(f.sdi) == SR_OK);
    /* Two occupied buffers must block a third read until a result is released. */
    for (unsigned int i = 0; i < 100 && !atomic_read(&sample_reads); i++) g_usleep(1000);
    g_usleep(10000);
    expect("unconsumed FIFO enforces configured ring bound", atomic_read(&sample_reads) == DLA_WORKER_BUFFER_SLOTS);
    expect("stop wakes worker while result slot is occupied", dev_acquisition_stop(f.sdi) == SR_OK);
    expect("worker published terminal but wrapper OS thread is held", pump_until(&f, terminal_waiting, FALSE));
    receive_events(-1, 0, f.sdi);
    expect("terminal alone cannot finish/free/close context",
        f.devc->worker && f.devc->acquiring && !ends &&
        !dla_worker_exited(f.devc->worker, &terminal) && !dla_worker_free(f.devc->worker) &&
        dev_close(f.sdi) != SR_OK && dev_clear(&f.driver) != SR_OK);
    inspect_end = TRUE; SetEvent(exit_release);
    expect("native exit allows reconciled result/log retirement", pump_until(&f, NULL, TRUE));
    expect("canceled held result retained and END protected", f.devc->worker_unconsumed_bytes == 32 && ends == 1 && end_guard_ok);
    fixture_clear(&f);
}
static void test_fragments_and_reentry(gboolean stop_logic)
{
    struct fixture f;
    fixture_init(&f, FALSE); f.devc->limits.limit_samples = stop_logic ? 1000 : 16;
    scripts[0] = (struct script){16, TRUE, ERROR_SUCCESS, FALSE};
    scripts[1] = (struct script){0, TRUE, ERROR_SUCCESS, FALSE};
    scripts[2] = (struct script){16, TRUE, ERROR_SUCCESS, FALSE};
    scripts[3] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 4;
    stop_from_logic = stop_logic; inspect_end = TRUE;
    expect("fragmented capture starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("fragmented capture finishes", pump_until(&f, NULL, TRUE));
    expect("16+zero+16 carry preserved, no late LOGIC, one END",
        delivered == (stop_logic ? 8 : 16) && ends == 1 && !f.devc->pending &&
        reentrant_source_safe && end_guard_ok && !atomic_read(&late_logic));
    fixture_clear(&f);
}
static void test_stop_failure(gboolean legacy, gboolean short_write)
{
    struct fixture f; uint64_t before;
    fixture_init(&f, legacy); scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 1;
    f.devc->limits.limit_samples = 8;
    if (short_write) short_stop = TRUE; else fail_stop = TRUE;
    inspect_end = TRUE;
    expect("STOP-fault capture starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("STOP-fault capture emits software END without losing hardware ownership", pump_until(&f, NULL, TRUE));
    before = f.devc->limits.limit_samples;
    expect("failed/short STOP persists and blocks restart/close/clear/config",
        ends == 1 && f.devc->upload_stop_pending && !f.devc->worker && end_guard_ok &&
        dev_acquisition_start(f.sdi) != SR_OK && dev_close(f.sdi) != SR_OK &&
        dev_clear(&f.driver) != SR_OK && f.devc->limits.limit_samples == before);
    fail_stop = short_stop = 0;
    expect("explicit inactive STOP recovers exactly once", dev_acquisition_stop(f.sdi) == SR_OK && !f.devc->upload_stop_pending);
    script_length = 1; sample_reads = 0; delivered = 0; ends = 0;
    expect("healthy capture starts after explicit recovery", dev_acquisition_start(f.sdi) == SR_OK);
    expect("healthy capture completes after recovery", pump_until(&f, NULL, TRUE) && delivered == 8 && ends == 1);
    fixture_clear(&f);
}
static void test_create_failure(gboolean stop_also_fails)
{
    struct fixture f; LONG arm_before;
    fixture_init(&f, FALSE); fail_create = TRUE; fail_stop = stop_also_fails;
    inspect_end = TRUE; arm_before = atomic_read(&commands[0x12]);
    expect("CreateThread fault is a failed start after accepted ARM", dev_acquisition_start(f.sdi) != SR_OK &&
        atomic_read(&commands[0x12]) == arm_before + 1 && !atomic_read(&sample_reads));
    expect("failed-start END prevents reentrant restart/clear/close/config and frees only prepared worker",
        ends == 1 && end_guard_ok && !f.devc->worker && !f.devc->acquiring && !sources &&
        f.devc->upload_stop_pending == stop_also_fails);
    if (stop_also_fails) {
        fail_stop = FALSE;
        expect("CreateThread rollback STOP fault remains explicitly recoverable", dev_acquisition_stop(f.sdi) == SR_OK && !f.devc->upload_stop_pending);
    }
    fixture_clear(&f);
}
static void test_header_stop_and_failure(void)
{
    struct fixture f;
    fixture_init(&f, FALSE); stop_from_header = TRUE; inspect_end = TRUE;
    expect("HEADER stop starts retirement without ARM/read", dev_acquisition_start(f.sdi) == SR_OK);
    expect("HEADER stop retires exclusively", pump_until(&f, NULL, TRUE));
    expect("HEADER reentrant source guard, no ARM/read, one END", reentrant_source_safe &&
        !atomic_read(&commands[0x12]) && !atomic_read(&sample_reads) && ends == 1 && end_guard_ok);
    fixture_clear(&f);
    fixture_init(&f, FALSE); header_status = SR_ERR_IO;
    expect("HEADER failure uses main rollback before worker handoff", dev_acquisition_start(f.sdi) != SR_OK &&
        !f.devc->worker && !atomic_read(&commands[0x12]) && !atomic_read(&sample_reads) && !sources && ends == 0);
    fixture_clear(&f);
}
static void test_setter_and_read_error(gboolean legacy, gboolean setter)
{
    struct fixture f;
    fixture_init(&f, legacy);
    scripts[0] = (struct script){0, FALSE, ERROR_GEN_FAILURE, FALSE}; script_length = 1;
    if (setter) worker_setter_fail = 1;
    inspect_end = TRUE;
    expect("worker I/O fault starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("worker I/O fault retires with main-only logs", pump_until(&f, NULL, TRUE));
    expect("setter/read failure returns no stale payload and STOP cleanup preserved",
        delivered == 0 && ends == 1 && !f.devc->upload_stop_pending && end_guard_ok &&
        !atomic_read(&wrong_callback_thread) && atomic_read(&sample_reads) == (setter ? 0 : 1));
    fixture_clear(&f);
}
static void test_prepared_allocations(void)
{
    for (unsigned int nth = 1; nth <= DLA_WORKER_BUFFER_SLOTS + 4; nth++) {
        struct fixture f;
        fixture_init(&f, FALSE); alloc_fail = nth;
        expect("PREPARED allocation failure has no ARM/read/live worker", dev_acquisition_start(f.sdi) != SR_OK &&
            !f.devc->worker && !f.devc->decoded && !atomic_read(&commands[0x12]) && !atomic_read(&sample_reads) && !sources);
        fixture_clear(&f);
    }
}
static void test_terminal_logger_stop(gboolean failed)
{
    struct fixture f;
    fixture_init(&f, FALSE); scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 1;
    f.devc->limits.limit_samples = 8; fail_stop = failed;
    stop_from_terminal_log = TRUE;
    expect("terminal logger reentry case starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("terminal logger stop case finishes", pump_until(&f, NULL, TRUE));
    expect("terminal STOP outcome remains authoritative after logger reentrant stop",
        terminal_stop_seen && ends == 1 && f.devc->upload_stop_pending == failed &&
        atomic_read(&commands[0x15]) == 2 && !atomic_read(&late_logic));
    fixture_clear(&f);
}
static void test_prehandoff_failure(gboolean source_failure, gboolean stop_failure)
{
    struct fixture f;
    fixture_init(&f, FALSE); inspect_end = TRUE;
    if (source_failure) source_status = SR_ERR_IO;
    else { fail_arm = TRUE; fail_stop = stop_failure; }
    expect("source/ARM failure returns before any worker SDK handoff", dev_acquisition_start(f.sdi) != SR_OK &&
        !f.devc->worker && !f.devc->decoded && !atomic_read(&sample_reads) && !sources);
    expect("source/ARM rollback preserves exact END and pending outcome",
        atomic_read(&commands[0x12]) == (source_failure ? 0 : 1) &&
        ends == (source_failure ? 0 : 1) && end_guard_ok &&
        f.devc->upload_stop_pending == stop_failure);
    fixture_clear(&f);
}
static void fill_diagnostic_queue(void)
{
    struct dev_context *devc = current_sdi->priv;
    /* Genuine transport logger calls exercise the production sink. Main does
     * not dispatch until diagnostic_done, so ring exhaustion is deterministic. */
    for (unsigned int i = 0; i < 80; i++)
        dla_wch_log(&devc->wch, SR_LOG_INFO, "Offline bounded diagnostic %u", i);
    SetEvent(diagnostic_done);
}
static void truncate_diagnostic(void)
{
    struct dev_context *devc = current_sdi->priv;
    char text[DLA_WORKER_LOG_LENGTH + 8];
    memset(text, 'X', sizeof(text) - 1); text[sizeof(text) - 1] = 0;
    dla_wch_log(&devc->wch, SR_LOG_INFO, "%s", text);
    SetEvent(diagnostic_done);
}
static void test_diagnostic_failure(gboolean truncated)
{
    struct fixture f;
    fixture_init(&f, FALSE); scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, TRUE}; script_length = 1;
    inject_diagnostics = truncated ? truncate_diagnostic : fill_diagnostic_queue;
    expect("diagnostic pressure case starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("production diagnostic sink completed bounded injection",
        WaitForSingleObject(diagnostic_done, 1000) == WAIT_OBJECT_0);
    SetEvent(read_release); inspect_end = TRUE;
    expect("diagnostic failure still retires STOP and native thread", pump_until(&f, NULL, TRUE));
    expect("overflow/truncation is explicitly failed evidence, not silent success",
        diagnostic_failure_seen && !f.devc->upload_stop_pending && ends == 1 && end_guard_ok &&
        atomic_read(&commands[0x15]) == 2 && !atomic_read(&wrong_callback_thread));
    fixture_clear(&f);
}
static void test_close_handle_retry(void)
{
    struct fixture f;
    fixture_init(&f, FALSE); stop_from_header = TRUE; fail_close_worker = 1; hold_exit = TRUE;
    expect("native handle retirement fault starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("retirement fault worker completed SDK and is held before OS exit", pump_until(&f, terminal_waiting, FALSE));
    SetEvent(exit_release);
    expect("native worker exit becomes signaled", WaitForSingleObject(f.devc->worker->thread, 1000) == WAIT_OBJECT_0);
    /* Drain late queued logs, then the first close attempt must be rejected. */
    receive_events(-1, 0, f.sdi);
    expect("CloseHandle false retains terminal ownership and prevents END/free",
        atomic_read(&worker_close_attempts) == 1 && f.devc->worker && !ends && sources == 1);
    inspect_end = TRUE;
    expect("second native-handle-only retirement succeeds", pump_until(&f, NULL, TRUE));
    expect("handle retry never resends SDK STOP", atomic_read(&worker_close_attempts) == 2 &&
        atomic_read(&commands[0x15]) == 2 && ends == 1 && end_guard_ok);
    fixture_clear(&f);
}
static void test_terminal_setter_failure(gboolean legacy)
{
    struct fixture f;
    LONG before_writes, before_reads;

    fixture_init(&f, legacy); scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, TRUE}; script_length = 1;
    inspect_end = TRUE;
    expect("terminal setter fault starts and reaches held sample read", dev_acquisition_start(f.sdi) == SR_OK &&
        pump_until(&f, read_entered, FALSE));
    before_writes = atomic_read(&sdk_writes); before_reads = atomic_read(&sdk_reads);
    worker_setter_fail = 1;
    expect("stop intent stays nonblocking before terminal setter failure", dev_acquisition_stop(f.sdi) == SR_OK);
    SetEvent(read_release);
    expect("terminal setter failure emits END but retains pending ownership", pump_until(&f, NULL, TRUE) &&
        ends == 1 && end_guard_ok && f.devc->upload_stop_pending && !f.devc->worker);
    expect("failed terminal setter prevents WriteData and post-stop drain",
        atomic_read(&sdk_writes) == before_writes && atomic_read(&sdk_reads) == before_reads &&
        atomic_read(&commands[0x15]) == 1 && delivered == 0 && f.devc->worker_unconsumed_bytes == 32);
    expect("pending outcome blocks fresh start and reopen", dev_acquisition_start(f.sdi) != SR_OK && dev_open(f.sdi) != SR_OK);
    if (legacy) {
        expect("legacy close recovery performs accepted STOP/drain before fake SDK close", dev_close(f.sdi) == SR_OK &&
            !f.devc->upload_stop_pending && !f.devc->wch.opened && sdk_closes == 1 && atomic_read(&commands[0x15]) == 2);
    } else {
        expect("Ex inactiveStop recovery retires pending once without closing device", dev_acquisition_stop(f.sdi) == SR_OK &&
            !f.devc->upload_stop_pending && f.devc->wch.opened && sdk_closes == 0 && atomic_read(&commands[0x15]) == 2);
    }
    fixture_clear(&f);
}
static gboolean wait_queued(struct dla_wch_worker *worker, unsigned int wanted)
{
    int64_t deadline = g_get_monotonic_time() + 1000000;
    while (g_get_monotonic_time() < deadline) {
        gboolean available;
        g_mutex_lock(&worker->mutex);
        available = worker->queue_count >= wanted;
        g_mutex_unlock(&worker->mutex);
        if (available) return TRUE;
        g_usleep(1000);
    }
    return FALSE;
}
static void test_overlap_and_drain_ownership(void)
{
    struct fixture f;
    struct dla_worker_result first, second;
    struct dla_worker_terminal terminal;
    GPollFD poll;
    fixture_init(&f, FALSE); distinct_reads = TRUE;
    f.devc->limits.limit_samples = 1000;
    scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE};
    scripts[1] = (struct script){32, TRUE, ERROR_SUCCESS, TRUE}; script_length = 2;
    expect("overlap test starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("second SDK read starts before first result is consumed",
        WaitForSingleObject(read_entered, 1000) == WAIT_OBJECT_0 &&
        atomic_read(&sample_reads) == 2);
    poll = f.devc->worker_pollfd;
    expect("GLib polls the Win32 completion HANDLE", g_poll(&poll, 1, 0) == 1 &&
        (poll.revents & G_IO_IN));
    dla_worker_acknowledge(f.devc->worker);
    expect("first claimed buffer has original read identity", dla_worker_take_result(f.devc->worker, &first) &&
        first.read_id == 1 && first.count == 32 && first.buffer[0] == 1);
    SetEvent(read_release);
    expect("producer publishes second result while first remains claimed",
        wait_queued(f.devc->worker, 1) && first.buffer[0] == 1 && first.buffer[31] == 1);
    drain_fill_once = 1;
    expect("Stop returns while both result slots remain owned", dev_acquisition_stop(f.sdi) == SR_OK);
    expect("native STOP/drain completes without waiting for frontend release",
        WaitForSingleObject(f.devc->worker->thread, 1000) == WAIT_OBJECT_0);
    expect("dedicated drain storage never overwrites either sample slot",
        first.buffer[0] == 1 && first.buffer[31] == 1 &&
        f.devc->worker->results[1].buffer[0] == 2 &&
        f.devc->worker->terminal.drain_bytes == 32);
    expect("claimed result prevents worker retirement", !dla_worker_exited(f.devc->worker, &terminal) &&
        !dla_worker_free(f.devc->worker));
    worker_preserve_unconsumed(f.devc, &first);
    dla_worker_release_result(f.devc->worker, FALSE);
    expect("second queued identity is preserved in FIFO order", dla_worker_take_result(f.devc->worker, &second) &&
        second.read_id == 2 && second.buffer != first.buffer && second.buffer[0] == 2);
    worker_preserve_unconsumed(f.devc, &second);
    dla_worker_release_result(f.devc->worker, FALSE);
    inspect_end = TRUE;
    expect("both canceled buffers are accounted once and resources retire", pump_until(&f, NULL, TRUE) &&
        f.devc->worker_unconsumed_bytes == 64 && delivered == 0 && ends == 1 &&
        !f.devc->worker_ready && end_guard_ok);
    fixture_clear(&f);
}
static void test_timeout_progress(gboolean partial)
{
    struct fixture f;
    fixture_init(&f, FALSE); inspect_end = TRUE;
    scripts[0] = (struct script){partial ? 16 : 0, FALSE, ERROR_TIMEOUT, FALSE};
    scripts[1] = (struct script){partial ? 16 : 32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 2;
    f.devc->limits.limit_samples = 8;
    expect("recoverable timeout case starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("timeout followed by data completes exactly once", pump_until(&f, NULL, TRUE) &&
        delivered == 8 && ends == 1 && end_guard_ok && !f.devc->upload_stop_pending);
    fixture_clear(&f);
}
static void test_stall_watchdog(gboolean trigger_wait, gboolean after_data)
{
    struct fixture f;
    fixture_init(&f, FALSE); inspect_end = TRUE;
    scripts[0] = (struct script){0, FALSE, ERROR_TIMEOUT, TRUE}; script_length = 1;
    expect("stall case starts with a pending SDK read", dev_acquisition_start(f.sdi) == SR_OK &&
        WaitForSingleObject(read_entered, 1000) == WAIT_OBJECT_0);
    f.devc->has_trigger = trigger_wait;
    f.devc->data_started = after_data;
    f.devc->last_data_us -= f.devc->first_data_timeout_us + 1;
    receive_events(-1, 0, f.sdi);
    if (trigger_wait) {
        expect("waiting for trigger does not falsely report a data stall", !f.devc->stopping && !ends);
        dev_acquisition_stop(f.sdi);
    } else {
        expect("expired first-data watchdog requests nonblocking Stop", f.devc->stopping && !ends);
    }
    SetEvent(read_release);
    expect("watchdog/cancel reconciles pending timeout then retires", pump_until(&f, NULL, TRUE) &&
        delivered == 0 && ends == 1 && end_guard_ok);
    fixture_clear(&f);
}
static void test_disconnect_retirement(DWORD error)
{
    struct fixture f;
    LONG before;
    fixture_init(&f, FALSE); inspect_end = TRUE;
    scripts[0] = (struct script){0, FALSE, error, FALSE}; script_length = 1;
    expect("device-loss case starts", dev_acquisition_start(f.sdi) == SR_OK);
    expect("device loss retires native thread and signals incomplete capture", pump_until(&f, NULL, TRUE) &&
        f.devc->wch_disconnected && f.devc->upload_stop_pending &&
        !f.devc->worker_ready && ends == 1 && delivered == 0 && end_guard_ok);
    before = atomic_read(&sdk_reads) + atomic_read(&sdk_writes);
    expect("disconnected close releases SDK handle without more USB I/O", dev_close(f.sdi) == SR_OK &&
        !f.devc->wch.opened && !f.devc->upload_stop_pending && !f.devc->wch_disconnected &&
        sdk_closes == 1 && before == atomic_read(&sdk_reads) + atomic_read(&sdk_writes));
    /* DLL and all USB functions remain fake. Device identification must be
     * repeated after reconnection, rather than reusing an old open flag. */
    wch_api.module = (HMODULE)(uintptr_t)1;
    wch_api.open = fake_sdk_open; wch_api.usb_id = fake_usb_id;
    wch_api.timeout = fake_initial_timeout;
    expect("absent device cannot reopen stale SDK handle", dev_open(f.sdi) == SR_ERR_IO && !f.devc->wch.opened);
    fake_device_present = TRUE;
    expect("reconnected device reopens only after fresh DL32 identification", dev_open(f.sdi) == SR_OK && f.devc->wch.opened);
    wch_api.timeout = fake_timeout;
    sample_reads = 0; f.devc->limits.limit_samples = 8;
    scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 1;
    expect("fresh capture completes after simulated reconnection", dev_acquisition_start(f.sdi) == SR_OK &&
        pump_until(&f, NULL, TRUE) && delivered == 8 && ends == 2 && !f.devc->upload_stop_pending);
    fixture_clear(&f);
}
static void test_event_failure(unsigned int kind)
{
    struct fixture f;
    fixture_init(&f, FALSE); inspect_end = TRUE;
    if (!kind) {
        fail_event = 1;
        expect("event allocation failure occurs before ARM/read", dev_acquisition_start(f.sdi) != SR_OK &&
            !f.devc->worker_ready && !f.devc->worker && !f.devc->decoded &&
            !atomic_read(&commands[0x12]) && !sources);
    } else {
        scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, FALSE}; script_length = 1;
        if (kind == 1) fail_notify = 1;
        else fail_reset = 1;
        expect("event notification fault starts", dev_acquisition_start(f.sdi) == SR_OK);
        expect("fallback dispatch retires event fault with explicit failed evidence", pump_until(&f, NULL, TRUE) &&
            diagnostic_failure_seen && ends == 1 && !f.devc->worker_ready && end_guard_ok);
    }
    fixture_clear(&f);
}
static void test_disconnect_during_retirement(gboolean drain)
{
    struct fixture f;
    LONG before;
    fixture_init(&f, FALSE); inspect_end = TRUE;
    scripts[0] = (struct script){32, TRUE, ERROR_SUCCESS, TRUE}; script_length = 1;
    expect("retirement device-loss case starts", dev_acquisition_start(f.sdi) == SR_OK &&
        WaitForSingleObject(read_entered, 1000) == WAIT_OBJECT_0);
    if (drain) drain_error = ERROR_NO_SUCH_DEVICE;
    else { fail_stop = TRUE; stop_error = ERROR_DEVICE_NOT_CONNECTED; }
    dev_acquisition_stop(f.sdi); SetEvent(read_release);
    expect("device loss during STOP/drain still retires all host callbacks", pump_until(&f, NULL, TRUE) &&
        ends == 1 && delivered == 0 && f.devc->wch_disconnected && end_guard_ok && !f.devc->worker_ready);
    before = atomic_read(&sdk_reads) + atomic_read(&sdk_writes);
    expect("STOP/drain device loss permits stale-handle close without USB retry", dev_close(f.sdi) == SR_OK &&
        sdk_closes == 1 && !f.devc->upload_stop_pending &&
        before == atomic_read(&sdk_reads) + atomic_read(&sdk_writes));
    fixture_clear(&f);
}
static void test_public_continuous_configuration(void)
{
    struct fixture f;
    fixture_init(&f, FALSE);
    expect("public logic-only API exposes continuous Stream", sr_config_set(f.sdi, NULL,
        SR_CONF_CONTINUOUS, g_variant_new_boolean(TRUE)) == SR_OK &&
        f.devc->continuous && f.devc->stream && !f.devc->limits.limit_samples);
    expect("public continuous disable restores a bounded sample limit", sr_config_set(f.sdi, NULL,
        SR_CONF_CONTINUOUS, g_variant_new_boolean(FALSE)) == SR_OK &&
        !f.devc->continuous && f.devc->limits.limit_samples == DEFAULT_LIMIT_SAMPLES);
    expect("public continuous configuration makes no USB/ARM calls", !atomic_read(&sdk_reads) &&
        !atomic_read(&sdk_writes) && !f.devc->worker);
    fixture_clear(&f);
}
int main(void)
{
    main_thread = GetCurrentThreadId();
    g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    sr_log_loglevel_set(SR_LOG_SPEW); sr_log_callback_set(logger, NULL);
    test_responsive_cancel(FALSE); test_responsive_cancel(TRUE);
    test_native_exit_and_credit(); test_fragments_and_reentry(FALSE); test_fragments_and_reentry(TRUE);
    test_stop_failure(FALSE, FALSE); test_stop_failure(FALSE, TRUE); test_stop_failure(TRUE, FALSE);
    test_create_failure(FALSE); test_create_failure(TRUE); test_header_stop_and_failure();
    test_setter_and_read_error(FALSE, FALSE); test_setter_and_read_error(FALSE, TRUE);
    test_setter_and_read_error(TRUE, FALSE); test_setter_and_read_error(TRUE, TRUE);
    test_prepared_allocations();
    test_terminal_logger_stop(FALSE); test_terminal_logger_stop(TRUE);
    test_prehandoff_failure(TRUE, FALSE); test_prehandoff_failure(FALSE, FALSE);
    test_prehandoff_failure(FALSE, TRUE);
    test_diagnostic_failure(FALSE); test_diagnostic_failure(TRUE);
    test_close_handle_retry();
    test_terminal_setter_failure(FALSE); test_terminal_setter_failure(TRUE);
    test_overlap_and_drain_ownership();
    test_timeout_progress(FALSE); test_timeout_progress(TRUE);
    test_stall_watchdog(FALSE, FALSE); test_stall_watchdog(TRUE, FALSE);
    test_stall_watchdog(FALSE, TRUE);
    test_disconnect_retirement(ERROR_DEVICE_NOT_CONNECTED);
    test_disconnect_retirement(ERROR_NO_SUCH_DEVICE);
    test_event_failure(0); test_event_failure(1); test_event_failure(2);
    test_disconnect_during_retirement(FALSE); test_disconnect_during_retirement(TRUE);
    test_public_continuous_configuration();
    sr_log_callback_set_default();
    printf("Audit: %u checks, %u pending behavioral failures\n", checks, failures);
    return failures ? 1 : 0;
}
