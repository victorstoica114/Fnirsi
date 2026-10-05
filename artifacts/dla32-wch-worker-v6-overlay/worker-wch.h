/* SPDX-License-Identifier: GPL-3.0-only */
/* Isolated SDK0 experiment. No session, frontend, trace-file or global logger
 * callback is invoked here. The main thread owns decoding and datafeed. */
#ifndef LIBSIGROK_FNIRSI_DLA32_WORKER_WCH_H
#define LIBSIGROK_FNIRSI_DLA32_WORKER_WCH_H
#if defined(_WIN32) && DLA_WCH_WORKER

#define DLA_WORKER_LOG_SLOTS 64
#define DLA_WORKER_LOG_LENGTH 512

struct dla_worker_log {
    int level;
    int64_t original_us;
    char message[DLA_WORKER_LOG_LENGTH];
};

struct dla_worker_result {
    uint64_t read_id;
    int status, count;
    DWORD windows_error;
    int64_t start_us, end_us;
    uint8_t *buffer;
};

struct dla_worker_terminal {
    gboolean stop_pending, diagnostics_failed, drain_quiet;
    int stop_status, drain_status;
    uint64_t read_count, drain_bytes;
    unsigned int drain_reads, drain_empty;
    int64_t stop_start_us, stop_end_us, drain_end_us;
};

struct dla_wch_worker {
    struct dla_wch *wch;
    HANDLE thread;
    GMutex mutex;
    GCond condition;
    uint64_t generation;
    gboolean stop_requested, read_credit, result_pending, result_claimed;
    gboolean terminal_ready, diagnostics_failed;
    unsigned int next_buffer;
    uint8_t *buffers[2];
    struct dla_worker_result result;
    struct dla_worker_terminal terminal;
    struct dla_worker_log logs[DLA_WORKER_LOG_SLOTS];
    unsigned int log_start, log_count;
};

static void dla_worker_sink(void *context, int level, const char *message)
{
    struct dla_wch_worker *worker = context;
    struct dla_worker_log *entry;

    g_mutex_lock(&worker->mutex);
    if (worker->log_count == DLA_WORKER_LOG_SLOTS ||
            strlen(message) >= DLA_WORKER_LOG_LENGTH) {
        worker->diagnostics_failed = TRUE;
        worker->stop_requested = TRUE;
        g_cond_signal(&worker->condition);
    } else {
        entry = &worker->logs[(worker->log_start + worker->log_count) % DLA_WORKER_LOG_SLOTS];
        entry->level = level;
        entry->original_us = g_get_monotonic_time();
        g_strlcpy(entry->message, message, sizeof(entry->message));
        worker->log_count++;
    }
    g_mutex_unlock(&worker->mutex);
}

static void dla_worker_logf(struct dla_wch_worker *worker, int level,
        const char *format, ...) G_GNUC_PRINTF(3, 4);

static void dla_worker_logf(struct dla_wch_worker *worker, int level,
        const char *format, ...)
{
    char message[DLA_WORKER_LOG_LENGTH];
    va_list args;
    int written;

    va_start(args, format);
    written = vsnprintf(message, sizeof(message), format, args);
    va_end(args);
    if (written < 0 || (size_t)written >= sizeof(message)) {
        g_mutex_lock(&worker->mutex);
        worker->diagnostics_failed = TRUE;
        worker->stop_requested = TRUE;
        g_mutex_unlock(&worker->mutex);
        return;
    }
    dla_worker_sink(worker, level, message);
}

static DWORD WINAPI dla_worker_thread(LPVOID context)
{
    struct dla_wch_worker *worker = context;
    struct dla_worker_result result;
    struct dla_worker_terminal terminal = {0};
    uint8_t packet[DLA_COMMAND_SIZE], *drain_buffer;
    unsigned int i;
    char prefix[109];
    int count, length, status;
    int64_t deadline, started, ended;

    /* CreateThread is the exclusive I/O handoff. Before it, only main owns
     * WCH; after it, main performs no WCH operation until native exit. */
    for (;;) {
        g_mutex_lock(&worker->mutex);
        while (!worker->stop_requested && !worker->read_credit)
            g_cond_wait(&worker->condition, &worker->mutex);
        if (worker->stop_requested) {
            g_mutex_unlock(&worker->mutex);
            break;
        }
        worker->read_credit = FALSE;
        memset(&result, 0, sizeof(result));
        result.buffer = worker->buffers[worker->next_buffer];
        worker->next_buffer ^= 1;
        result.read_id = ++terminal.read_count;
        g_mutex_unlock(&worker->mutex);

        result.start_us = g_get_monotonic_time();
        result.status = dla_wch_read(worker->wch, 1, result.buffer,
            TRANSFER_SIZE, &result.count, TRANSFER_TIMEOUT_MS);
        result.windows_error = worker->wch->last_operation_error;
        result.end_us = g_get_monotonic_time();

        g_mutex_lock(&worker->mutex);
        /* A second read cannot start until main releases this exact result. */
        worker->result = result;
        worker->result_pending = TRUE;
        worker->result_claimed = FALSE;
        if (result.status != LIBUSB_SUCCESS && result.status != LIBUSB_ERROR_TIMEOUT)
            worker->stop_requested = TRUE;
        g_mutex_unlock(&worker->mutex);
    }

    terminal.stop_pending = TRUE;
    dla_stop(packet);
    for (i = 0; i < 54; i++)
        g_snprintf(prefix + 2 * i, 3, "%02x", packet[i]);
    terminal.stop_start_us = g_get_monotonic_time();
    dla_worker_logf(worker, SR_LOG_DBG, "WCH WORKER command: generation %" PRIu64
        ", opcode 15, io_start_us %" PRId64 ", prefix54 %s.",
        worker->generation, terminal.stop_start_us, prefix);
    terminal.stop_status = dla_wch_write(worker->wch, packet, DLA_COMMAND_SIZE);
    terminal.stop_end_us = g_get_monotonic_time();
    terminal.stop_pending = terminal.stop_status != SR_OK;
    if (terminal.stop_pending) {
        dla_worker_logf(worker, SR_LOG_ERR, "WCH capture STOP failed: phase stop; pending ownership retained.");
    } else {
        dla_worker_logf(worker, SR_LOG_INFO, "WCH capture STOP complete: phase stop; command accepted.");
        /* The other slot cannot alias a pending/claimed sample result. */
        drain_buffer = worker->buffers[worker->next_buffer];
        deadline = g_get_monotonic_time() + STOP_DRAIN_USEC;
        terminal.drain_status = LIBUSB_SUCCESS;
        while (terminal.drain_empty < STOP_EMPTY_READS &&
                terminal.drain_bytes < STOP_DRAIN_BYTES &&
                g_get_monotonic_time() < deadline) {
            count = 0;
            length = MIN((uint64_t)TRANSFER_SIZE, STOP_DRAIN_BYTES - terminal.drain_bytes);
            started = g_get_monotonic_time();
            status = dla_wch_read(worker->wch, 1, drain_buffer,
                length, &count, 20);
            ended = g_get_monotonic_time();
            terminal.drain_reads++;
            terminal.drain_status = status;
            dla_worker_logf(worker, SR_LOG_DBG, "WCH WORKER drain read: generation %" PRIu64
                ", io_start_us %" PRId64 ", duration_us %" PRId64
                ", status %d, requested %u, timeout_ms 20, count %d.",
                worker->generation, started, ended - started, status,
                (unsigned int)length, count);
            if (status != LIBUSB_SUCCESS && status != LIBUSB_ERROR_TIMEOUT) {
                dla_worker_logf(worker, SR_LOG_WARN, "Post-stop drain failed: %s.", libusb_error_name(status));
                break;
            }
            if (!count) {
                terminal.drain_empty++;
            } else {
                terminal.drain_empty = 0;
                terminal.drain_bytes += count;
            }
        }
        terminal.drain_quiet = terminal.drain_empty >= STOP_EMPTY_READS;
        if (!terminal.drain_quiet)
            dla_worker_logf(worker, SR_LOG_WARN, "Post-stop endpoint did not become quiet within the drain bound.");
    }
    terminal.drain_end_us = g_get_monotonic_time();
    g_mutex_lock(&worker->mutex);
    terminal.diagnostics_failed = worker->diagnostics_failed;
    worker->terminal = terminal;
    worker->terminal_ready = TRUE;
    g_mutex_unlock(&worker->mutex);
    /* Main must additionally observe the native HANDLE signaled. Publication
     * does not permit destroying a still-running thread or any of its state. */
    return 0;
}

static struct dla_wch_worker *dla_worker_prepare(struct dla_wch *wch,
        uint64_t generation)
{
    struct dla_wch_worker *worker = g_try_malloc0(sizeof(*worker));
    unsigned int i;

    if (!worker)
        return NULL;
    g_mutex_init(&worker->mutex);
    g_cond_init(&worker->condition);
    worker->wch = wch;
    worker->generation = generation;
    for (i = 0; i < 2; i++) {
        worker->buffers[i] = g_try_malloc(TRANSFER_SIZE);
        if (!worker->buffers[i]) {
            g_free(worker->buffers[0]);
            g_cond_clear(&worker->condition);
            g_mutex_clear(&worker->mutex);
            g_free(worker);
            return NULL;
        }
    }
    return worker;
}

static gboolean dla_worker_start(struct dla_wch_worker *worker, gboolean stop)
{
    /* No SDK calls occurred while PREPARED. CreateThread failure leaves main
     * the sole owner, so its existing synchronous rollback remains legal. */
    worker->stop_requested = stop;
    worker->read_credit = !stop;
    worker->wch->diagnostic_sink = dla_worker_sink;
    worker->wch->diagnostic_context = worker;
    worker->thread = CreateThread(NULL, 0, dla_worker_thread, worker, 0, NULL);
    if (!worker->thread) {
        worker->wch->diagnostic_sink = NULL;
        worker->wch->diagnostic_context = NULL;
        return FALSE;
    }
    return TRUE;
}

static void dla_worker_request_stop(struct dla_wch_worker *worker)
{
    g_mutex_lock(&worker->mutex);
    worker->stop_requested = TRUE;
    g_cond_signal(&worker->condition);
    g_mutex_unlock(&worker->mutex);
}

static gboolean dla_worker_take_result(struct dla_wch_worker *worker,
        struct dla_worker_result *result)
{
    gboolean available;

    g_mutex_lock(&worker->mutex);
    available = worker->result_pending && !worker->result_claimed;
    if (available) {
        *result = worker->result;
        worker->result_claimed = TRUE;
    }
    g_mutex_unlock(&worker->mutex);
    return available;
}

static void dla_worker_release_result(struct dla_wch_worker *worker,
        gboolean allow_read)
{
    g_mutex_lock(&worker->mutex);
    worker->result_pending = worker->result_claimed = FALSE;
    if (allow_read && !worker->stop_requested && !worker->terminal_ready)
        worker->read_credit = TRUE;
    g_cond_signal(&worker->condition);
    g_mutex_unlock(&worker->mutex);
}

static gboolean dla_worker_take_log(struct dla_wch_worker *worker,
        struct dla_worker_log *entry)
{
    gboolean available;

    g_mutex_lock(&worker->mutex);
    available = worker->log_count != 0;
    if (available) {
        *entry = worker->logs[worker->log_start];
        worker->log_start = (worker->log_start + 1) % DLA_WORKER_LOG_SLOTS;
        worker->log_count--;
    }
    g_mutex_unlock(&worker->mutex);
    return available;
}

static gboolean dla_worker_exited(struct dla_wch_worker *worker,
        struct dla_worker_terminal *terminal)
{
    gboolean complete;

    g_mutex_lock(&worker->mutex);
    complete = worker->terminal_ready && !worker->result_pending && !worker->log_count;
    if (complete)
        *terminal = worker->terminal;
    g_mutex_unlock(&worker->mutex);
    return complete && worker->thread &&
        WaitForSingleObject(worker->thread, 0) == WAIT_OBJECT_0;
}

static gboolean dla_worker_free(struct dla_wch_worker *worker)
{
    gboolean reconciled;

    if (!worker)
        return TRUE;
    if (worker->thread) {
        if (WaitForSingleObject(worker->thread, 0) != WAIT_OBJECT_0)
            return FALSE;
        g_mutex_lock(&worker->mutex);
        reconciled = worker->terminal_ready && !worker->result_pending && !worker->log_count;
        g_mutex_unlock(&worker->mutex);
        if (!reconciled)
            return FALSE;
        if (!CloseHandle(worker->thread))
            return FALSE;
        worker->wch->diagnostic_sink = NULL;
        worker->wch->diagnostic_context = NULL;
    }
    g_free(worker->buffers[0]);
    g_free(worker->buffers[1]);
    g_cond_clear(&worker->condition);
    g_mutex_clear(&worker->mutex);
    g_free(worker);
    return TRUE;
}
#endif
#endif
