"""Prepare an isolated V7 experiment from the frozen V6 source. No hardware I/O."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'artifacts/dla32-wch-worker-v7-source/libsigrok'
BASE = ROOT / 'artifacts/dla32-wch-worker-v6-source/libsigrok'


def replace(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Unexpected source anchor: ' + old[:100])
    return text.replace(old, new, 1)


def main():
    path = SOURCE / 'src/hardware/fnirsi-dla32/worker-wch.h'
    text = (BASE / path.relative_to(SOURCE)).read_text()
    text = replace(text, '#define DLA_WORKER_LOG_SLOTS 64',
                   '#define DLA_WORKER_BUFFER_SLOTS 2\n#define DLA_WORKER_LOG_SLOTS 64')
    text = replace(text, '    HANDLE thread;', '    HANDLE thread;\n    HANDLE ready; /* Borrowed; main removes its poll source before closing. */')
    text = replace(text,
        '    gboolean stop_requested, read_credit, result_pending, result_claimed;\n'
        '    gboolean terminal_ready, diagnostics_failed;\n'
        '    unsigned int next_buffer;\n'
        '    uint8_t *buffers[2];\n'
        '    struct dla_worker_result result;',
        '    gboolean stop_requested, terminal_ready, diagnostics_failed;\n'
        '    gboolean busy[DLA_WORKER_BUFFER_SLOTS];\n'
        '    unsigned int queue_head, queue_count, claimed_slot;\n'
        '    unsigned int queue[DLA_WORKER_BUFFER_SLOTS];\n'
        '    uint8_t *buffers[DLA_WORKER_BUFFER_SLOTS], *drain_buffer;\n'
        '    struct dla_worker_result results[DLA_WORKER_BUFFER_SLOTS];')
    text = replace(text, 'static void dla_worker_sink(void *context, int level, const char *message)',
        '''/* Called with the mutex held. A failed notification stops further I/O;
 * the session watchdog still polls and reconciles the terminal state. */
static void dla_worker_notify_locked(struct dla_wch_worker *worker)
{
    if (!SetEvent(worker->ready)) {
        worker->diagnostics_failed = worker->stop_requested = TRUE;
        g_cond_signal(&worker->condition);
    }
}

static void dla_worker_acknowledge(struct dla_wch_worker *worker)
{
    g_mutex_lock(&worker->mutex);
    if (!ResetEvent(worker->ready)) {
        worker->diagnostics_failed = worker->stop_requested = TRUE;
        g_cond_signal(&worker->condition);
    }
    g_mutex_unlock(&worker->mutex);
}

static void dla_worker_sink(void *context, int level, const char *message)''')
    text = replace(text, '        worker->log_count++;\n    }\n    g_mutex_unlock(&worker->mutex);',
        '        worker->log_count++;\n    }\n    dla_worker_notify_locked(worker);\n    g_mutex_unlock(&worker->mutex);')
    text = replace(text, '        worker->stop_requested = TRUE;\n        g_mutex_unlock(&worker->mutex);\n        return;',
        '        worker->stop_requested = TRUE;\n        g_cond_signal(&worker->condition);\n        dla_worker_notify_locked(worker);\n        g_mutex_unlock(&worker->mutex);\n        return;')
    text = replace(text, '    unsigned int i;\n    char prefix[109];', '    unsigned int i, slot;\n    char prefix[109];')
    start = text.index('        while (!worker->stop_requested && !worker->read_credit)')
    end = text.index('\n        result.start_us', start)
    text = text[:start] + '''        for (;;) {
            for (slot = 0; slot < DLA_WORKER_BUFFER_SLOTS; slot++)
                if (!worker->busy[slot])
                    break;
            if (worker->stop_requested || slot < DLA_WORKER_BUFFER_SLOTS)
                break;
            g_cond_wait(&worker->condition, &worker->mutex);
        }
        if (worker->stop_requested) {
            g_mutex_unlock(&worker->mutex);
            break;
        }
        worker->busy[slot] = TRUE;
        memset(&result, 0, sizeof(result));
        result.buffer = worker->buffers[slot];
        result.read_id = ++terminal.read_count;
        g_mutex_unlock(&worker->mutex);
''' + text[end:]
    text = replace(text,
        '        /* A second read cannot start until main releases this exact result. */\n'
        '        worker->result = result;\n'
        '        worker->result_pending = TRUE;\n'
        '        worker->result_claimed = FALSE;',
        '''        /* FIFO publication keeps the claimed buffer immutable while the
         * next SDK read uses the other slot. Full queues apply backpressure. */
        worker->results[slot] = result;
        worker->queue[(worker->queue_head + worker->queue_count) %
            DLA_WORKER_BUFFER_SLOTS] = slot;
        worker->queue_count++;''')
    text = replace(text,
        '        if (result.status != LIBUSB_SUCCESS && result.status != LIBUSB_ERROR_TIMEOUT)\n'
        '            worker->stop_requested = TRUE;\n'
        '        g_mutex_unlock(&worker->mutex);',
        '        if (result.status != LIBUSB_SUCCESS && result.status != LIBUSB_ERROR_TIMEOUT)\n'
        '            worker->stop_requested = TRUE;\n'
        '        dla_worker_notify_locked(worker);\n'
        '        g_mutex_unlock(&worker->mutex);')
    text = replace(text,
        '        /* The other slot cannot alias a pending/claimed sample result. */\n'
        '        drain_buffer = worker->buffers[worker->next_buffer];',
        '        /* Drain storage never aliases either queued or claimed result. */\n'
        '        drain_buffer = worker->drain_buffer;')
    text = replace(text, '    worker->terminal_ready = TRUE;\n    g_mutex_unlock(&worker->mutex);',
        '    worker->terminal_ready = TRUE;\n    dla_worker_notify_locked(worker);\n    g_mutex_unlock(&worker->mutex);')
    text = replace(text, '        uint64_t generation)\n{', '        uint64_t generation, HANDLE ready)\n{')
    text = replace(text, '    worker->generation = generation;\n    for (i = 0; i < 2; i++) {',
        '    worker->generation = generation;\n    worker->ready = ready;\n'
        '    worker->claimed_slot = DLA_WORKER_BUFFER_SLOTS;\n'
        '    for (i = 0; i < DLA_WORKER_BUFFER_SLOTS; i++) {')
    text = replace(text, '    return worker;\n}',
        '''    worker->drain_buffer = g_try_malloc(TRANSFER_SIZE);
    if (!worker->drain_buffer) {
        for (i = 0; i < DLA_WORKER_BUFFER_SLOTS; i++)
            g_free(worker->buffers[i]);
        g_cond_clear(&worker->condition);
        g_mutex_clear(&worker->mutex);
        g_free(worker);
        return NULL;
    }
    return worker;
}''')
    text = replace(text, '    worker->read_credit = !stop;\n', '')
    text = replace(text, '    worker->stop_requested = TRUE;\n    g_cond_signal(&worker->condition);\n    g_mutex_unlock(&worker->mutex);',
        '    worker->stop_requested = TRUE;\n    g_cond_signal(&worker->condition);\n'
        '    dla_worker_notify_locked(worker);\n    g_mutex_unlock(&worker->mutex);')
    text = replace(text,
        '    available = worker->result_pending && !worker->result_claimed;\n'
        '    if (available) {\n'
        '        *result = worker->result;\n'
        '        worker->result_claimed = TRUE;\n'
        '    }',
        '''    available = worker->queue_count &&
        worker->claimed_slot == DLA_WORKER_BUFFER_SLOTS;
    if (available) {
        worker->claimed_slot = worker->queue[worker->queue_head];
        worker->queue_head = (worker->queue_head + 1) % DLA_WORKER_BUFFER_SLOTS;
        worker->queue_count--;
        *result = worker->results[worker->claimed_slot];
    }''')
    text = replace(text,
        '    worker->result_pending = worker->result_claimed = FALSE;\n'
        '    if (allow_read && !worker->stop_requested && !worker->terminal_ready)\n'
        '        worker->read_credit = TRUE;',
        '''    if (worker->claimed_slot < DLA_WORKER_BUFFER_SLOTS) {
        worker->busy[worker->claimed_slot] = FALSE;
        worker->claimed_slot = DLA_WORKER_BUFFER_SLOTS;
    }
    if (!allow_read)
        worker->stop_requested = TRUE;
    if (worker->queue_count || worker->log_count)
        dla_worker_notify_locked(worker);''')
    text = text.replace('worker->terminal_ready && !worker->result_pending && !worker->log_count',
        'worker->terminal_ready && !worker->queue_count &&\n'
        '        worker->claimed_slot == DLA_WORKER_BUFFER_SLOTS && !worker->log_count')
    text = replace(text, '    g_free(worker->buffers[1]);\n',
                   '    g_free(worker->buffers[1]);\n    g_free(worker->drain_buffer);\n')
    text = replace(text, 'gboolean stop_pending, diagnostics_failed, drain_quiet;',
                   'gboolean stop_pending, diagnostics_failed, drain_quiet, device_disconnected;')
    text = replace(text, '            worker->stop_requested = TRUE;\n        dla_worker_notify_locked(worker);',
        '            worker->stop_requested = TRUE;\n'
        '        if (result.status == LIBUSB_ERROR_NO_DEVICE)\n'
        '            terminal.device_disconnected = TRUE;\n'
        '        dla_worker_notify_locked(worker);')
    text = replace(text, '    terminal.stop_pending = TRUE;\n', '''    terminal.stop_pending = TRUE;
    if (terminal.device_disconnected) {
        /* Explicit OS device-loss errors permit host-handle retirement, never
         * a claim that the hardware accepted STOP or that data was complete. */
        terminal.stop_status = SR_ERR_IO;
        terminal.drain_status = LIBUSB_ERROR_NO_DEVICE;
        terminal.stop_start_us = terminal.stop_end_us = g_get_monotonic_time();
        dla_worker_logf(worker, SR_LOG_ERR,
            "WCH device disconnected; capture incomplete, hardware STOP unacknowledged.");
        goto publish_terminal;
    }
''')
    text = replace(text, '    terminal.drain_end_us = g_get_monotonic_time();',
                   'publish_terminal:\n    terminal.drain_end_us = g_get_monotonic_time();')
    text = replace(text, '    terminal.stop_pending = terminal.stop_status != SR_OK;',
        '    terminal.stop_pending = terminal.stop_status != SR_OK;\n'
        '    if (terminal.stop_pending && (worker->wch->last_operation_error ==\n'
        '            ERROR_DEVICE_NOT_CONNECTED || worker->wch->last_operation_error == ERROR_NO_SUCH_DEVICE))\n'
        '        terminal.device_disconnected = TRUE;')
    text = replace(text, '                dla_worker_logf(worker, SR_LOG_WARN, "Post-stop drain failed:',
        '                if (status == LIBUSB_ERROR_NO_DEVICE)\n'
        '                    terminal.device_disconnected = TRUE;\n'
        '                dla_worker_logf(worker, SR_LOG_WARN, "Post-stop drain failed:')
    path.write_text(text, newline='\n')

    path = SOURCE / 'src/hardware/fnirsi-dla32/api.c'
    text = (BASE / path.relative_to(SOURCE)).read_text()
    text = replace(text, 'struct dla_wch_worker *worker;\n',
        'struct dla_wch_worker *worker;\n\tHANDLE worker_ready;\n\tGPollFD worker_pollfd;\n'
        '\tgboolean wch_disconnected;\n')
    # Event lifetime is independent of worker retirement: remove the GSource
    # before closing its HANDLE; it cannot point at freed worker memory.
    text = text.replace('|| devc->worker || devc->worker_dispatching\n',
                   '|| devc->worker || devc->worker_ready || devc->worker_dispatching\n')
    text = replace(text, '\t\tdevc->worker = NULL;\n\t}\n#endif\n\n\twire_trace_close(devc);',
        '''\t\tdevc->worker = NULL;
\t}
\tif (devc->worker_ready && !devc->source_added) {
\t\tif (!CloseHandle(devc->worker_ready))
\t\t\tsr_err("Cannot close WCH completion event; handle retained.");
\t\telse
\t\t\tdevc->worker_ready = NULL;
\t}
#endif

\twire_trace_close(devc);''')
    text = replace(text, '\t\tif (devc->use_wch)\n\t\t\tsr_session_source_remove(sdi->session, -1);\n\t\telse',
        '''\t\tif (devc->use_wch) {
#if DLA_WCH_WORKER
\t\t\tif (devc->worker_ready)
\t\t\t\tsr_session_source_remove_pollfd(sdi->session, &devc->worker_pollfd);
\t\t\telse
#endif
\t\t\t\tsr_session_source_remove(sdi->session, -1);
\t\t} else''')
    text = replace(text, '\tdevc->worker_dispatching = TRUE;\n\tworker_emit_logs(worker);',
        '\tdevc->worker_dispatching = TRUE;\n\tdla_worker_acknowledge(worker);\n\tworker_emit_logs(worker);')
    text = replace(text, '\t\tdevc->worker = dla_worker_prepare(&devc->wch, ++devc->worker_generation);',
        '''\t\tdevc->worker_ready = CreateEventW(NULL, TRUE, FALSE, NULL);
\t\tif (!devc->worker_ready) {
\t\t\tfree_prepared_transfers(devc);
\t\t\treturn SR_ERR_IO;
\t\t}
\t\tdevc->worker_pollfd.fd = (gintptr)devc->worker_ready;
\t\tdevc->worker_pollfd.events = G_IO_IN;
\t\tdevc->worker_pollfd.revents = 0;
\t\tdevc->worker = dla_worker_prepare(&devc->wch, ++devc->worker_generation,
\t\t\tdevc->worker_ready);''')
    text = replace(text,
        '\t\tret = sr_session_source_add(sdi->session, -1, 0, WCH_POLL_MS,\n'
        '\t\t\treceive_events, (void *)sdi);',
        '''#if DLA_WCH_WORKER
\t\t/* Completion event dispatches immediately; 100ms is only the
\t\t * no-data/native-exit watchdog, without global timer changes. */
\t\tret = sr_session_source_add_pollfd(sdi->session, &devc->worker_pollfd,
\t\t\t100, receive_events, (void *)sdi);
#else
\t\tret = sr_session_source_add(sdi->session, -1, 0, WCH_POLL_MS,
\t\t\treceive_events, (void *)sdi);
#endif''')
    position = text.index('static int dev_close(struct sr_dev_inst *sdi)')
    text = text[:position] + text[position:].replace('\tif (devc->use_wch) {\n', '''\tif (devc->use_wch) {
#if DLA_WCH_WORKER
\t\tif (devc->wch_disconnected) {
\t\t\t/* The OS explicitly reported device loss. The worker has exited;
\t\t\t * release the stale host handle without issuing further USB I/O. */
\t\t\tdla_wch_close(&devc->wch);
\t\t\tdevc->upload_stop_pending = FALSE;
\t\t\tdevc->wch_disconnected = FALSE;
\t\t\treturn SR_OK;
\t\t}
#endif
''', 1)
    text = replace(text, '\t\tdevc->upload_stop_pending = terminal.stop_pending;',
        '\t\tdevc->upload_stop_pending = terminal.stop_pending;\n'
        '\t\tdevc->wch_disconnected = terminal.device_disconnected;')
    path.write_text(text, newline='\n')
    path = SOURCE / 'src/hardware/fnirsi-dla32/transport-wch.h'
    text = (BASE / path.relative_to(SOURCE)).read_text()
    text = replace(text, '        pipe, error, *actual);\n    return LIBUSB_ERROR_IO;',
        '        pipe, error, *actual);\n'
        '    if (error == ERROR_DEVICE_NOT_CONNECTED || error == ERROR_NO_SUCH_DEVICE)\n'
        '        return LIBUSB_ERROR_NO_DEVICE;\n    return LIBUSB_ERROR_IO;')
    path.write_text(text, newline='\n')
    print('Isolated V7 FIFO/event transport prepared. Frozen V6 unchanged.')


if __name__ == '__main__':
    main()
