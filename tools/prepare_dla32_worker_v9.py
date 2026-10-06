"""Isolate opt-in WCH kernel upload with exclusive event-driven worker ownership."""
from pathlib import Path
import shutil
from prepare_dla32_worker_v8 import replace_once

ROOT=Path(__file__).resolve().parent.parent


def main():
    source=ROOT/'artifacts/dla32-wch-worker-v9-source/libsigrok'
    tests=ROOT/'artifacts/dla32-wch-worker-v9-tests'
    if source.exists() or tests.exists():raise RuntimeError('Fresh V9 paths required')
    shutil.copytree(ROOT/'artifacts/dla32-wch-worker-v8-source/libsigrok',source)
    tests.mkdir()
    transport=source/'src/hardware/fnirsi-dla32/transport-wch.h'
    text=transport.read_text()
    text=replace_once(text,'#if DLA_WCH_WORKER && (!defined(_WIN32) || DLA_WCH_UPLOAD_DIAGNOSTIC)',
        '''#ifndef DLA_WCH_WORKER_UPLOAD
#define DLA_WCH_WORKER_UPLOAD 0
#endif
#if DLA_WCH_WORKER_UPLOAD != 0 && DLA_WCH_WORKER_UPLOAD != 1
#error "DLA_WCH_WORKER_UPLOAD must be 0 or 1"
#endif
#if DLA_WCH_WORKER_UPLOAD && (!defined(_WIN32) || !DLA_WCH_WORKER || !DLA_WCH_UPLOAD_DIAGNOSTIC)
#error "Worker upload requires Win32 worker and SDK upload enabled"
#endif
#if DLA_WCH_WORKER && (!defined(_WIN32) || (DLA_WCH_UPLOAD_DIAGNOSTIC && !DLA_WCH_WORKER_UPLOAD))''')
    text=replace_once(text,'static void dla_wch_log(struct dla_wch *wch,','static void dla_wch_log(const struct dla_wch *wch,')
    text=replace_once(text,'#define DLA_WCH_UPLOAD_LENGTH (1024UL * 1024UL)',
        '''#ifndef DLA_WCH_UPLOAD_LENGTH
#define DLA_WCH_UPLOAD_LENGTH (1024UL * 1024UL)
#endif
#if DLA_WCH_UPLOAD_LENGTH < 1048576 || DLA_WCH_UPLOAD_LENGTH > 4194304 || \\
        (DLA_WCH_UPLOAD_LENGTH & (DLA_WCH_UPLOAD_LENGTH - 1))
#error "DLA_WCH_UPLOAD_LENGTH must be a power of two from 1 to 4 MiB"
#endif''')
    start=text.index('#define DLA_WCH_UPLOAD_PIPE 1');end=text.index('static int dla_wch_open')
    block=text[start:end]
    block=block.replace('sr_info(', 'dla_wch_log(wch, SR_LOG_INFO, ').replace('sr_err(', 'dla_wch_log(wch, SR_LOG_ERR, ')
    # Preserve native failure identity before a diagnostic callback can change it.
    block=block.replace('error = GetLastError();','''error = GetLastError();
#if DLA_WCH_WORKER
    wch->last_operation_error = error;
#endif''')
    text=text[:start]+block+text[end:]
    text=replace_once(text,'    wch->opened = FALSE;\n}\n\n/* The installed SDK/kernel',
        '''    wch->opened = FALSE;
#if DLA_WCH_UPLOAD_DIAGNOSTIC
    /* Host CloseDevice retires the stale kernel handle after known OS loss. */
    wch->upload_dirty = wch->upload_enabled = FALSE;
#endif
}

/* The installed SDK/kernel''')
    transport.write_text(text,newline='\n')
    worker=source/'src/hardware/fnirsi-dla32/worker-wch.h'
    text=worker.read_text()
    text=replace_once(text,'    dla_stop(packet);\n', '''#if DLA_WCH_WORKER_UPLOAD
    /* The same thread owns SDK queue retirement and subsequent hardware STOP.
     * Disable may discard a completed kernel tail; its byte count is unknown.
     * Main-owned sample results remain intact and are reconciled separately. */
    terminal.stop_start_us = g_get_monotonic_time();
    terminal.stop_status = dla_wch_upload_retire(worker->wch, "worker-stop");
    terminal.stop_end_us = g_get_monotonic_time();
    if (terminal.stop_status != SR_OK) {
        terminal.drain_status = LIBUSB_ERROR_IO;
        terminal.device_disconnected = worker->wch->last_operation_error == ERROR_DEVICE_NOT_CONNECTED ||
            worker->wch->last_operation_error == ERROR_NO_SUCH_DEVICE;
        dla_worker_logf(worker, SR_LOG_ERR, "WCH worker SDK retirement failed; STOP deferred and ownership retained.");
        goto publish_terminal;
    }
    dla_worker_logf(worker, SR_LOG_INFO, "WCH worker SDK queue retired before STOP; discarded kernel tail byte count unknown.");
#endif
    dla_stop(packet);
''')
    worker.write_text(text,newline='\n')
    fixture=(ROOT/'artifacts/dla32-wch-worker-v8-tests/test_dla32_wch_worker_v8_unit_deep_r2.c').read_text()
    fixture=fixture.replace('v8-source','v9-source')
    fixture=replace_once(fixture,'static void fixture_init(struct fixture *f, gboolean legacy)', '''static BOOL WINAPI upload_set(ULONG index,ULONG enable,ULONG pipe,ULONG length)
{
    (void)index; (void)pipe; (void)length;
    enter_io(); leave_io(); SetLastError(ERROR_SUCCESS);
    (void)enable;
    return TRUE;
}
static BOOL WINAPI upload_clear(ULONG index,ULONG pipe)
{ (void)index; (void)pipe; enter_io(); leave_io(); SetLastError(ERROR_SUCCESS); return TRUE; }
static void fixture_init(struct fixture *f, gboolean legacy)''')
    fixture=replace_once(fixture,'    wch_api.read = fake_read; wch_api.write = fake_write;',
        '''    wch_api.read = fake_read; wch_api.write = fake_write;
    wch_api.set_buf_upload_ex = upload_set; wch_api.clear_buf_upload = upload_clear;''')
    (tests/'test_dla32_worker_v9_unit.c').write_text(fixture,newline='\n')
    msys=Path((ROOT/'tools/toolchain-path.txt').read_text().strip())
    old=msys/'tmp/dla32-wch-worker-v8-build/libsigrok'
    new=msys/'tmp/dla32-wch-worker-v9-build/libsigrok'
    if new.exists():raise RuntimeError('Fresh native V9 build required')
    shutil.copytree(old,new)
    for path in new.rglob('*'):
        if path.is_file() and (path.name in ('Makefile','config.status','libtool','libsigrok.pc','libsigrokcxx.pc') or path.suffix in ('.Plo','.Po','.la','.lo')):
            path.write_text(path.read_text().replace('dla32-wch-worker-v8-','dla32-wch-worker-v9-'),newline='\n')
    (source/'src/hardware/fnirsi-dla32/api.c').touch()
    print('V9 isolated opt-in upload worker prepared; previous source/runtime files unchanged.')


if __name__=='__main__':main()
