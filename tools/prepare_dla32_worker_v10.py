"""Reuse the SDK's verified native handle and remove per-read DLL allocation/copy."""
from pathlib import Path
import shutil
from prepare_dla32_worker_v8 import replace_once

ROOT=Path(__file__).resolve().parent.parent


def main():
    source=ROOT/'artifacts/dla32-wch-worker-v10-source/libsigrok'
    tests=ROOT/'artifacts/dla32-wch-worker-v10-tests'
    if source.exists() or tests.exists():raise RuntimeError('Fresh V10 source/tests required')
    shutil.copytree(ROOT/'artifacts/dla32-wch-worker-v9-source/libsigrok',source);tests.mkdir()
    transport=source/'src/hardware/fnirsi-dla32/transport-wch.h'
    text=transport.read_text()
    text=replace_once(text,'#ifdef _WIN32\n#include <windows.h>', '''#ifndef DLA_WCH_NATIVE_READ
#define DLA_WCH_NATIVE_READ 0
#endif
#if DLA_WCH_NATIVE_READ != 0 && DLA_WCH_NATIVE_READ != 1
#error "DLA_WCH_NATIVE_READ must be 0 or 1"
#endif
#if DLA_WCH_NATIVE_READ && (!defined(_WIN32) || !DLA_WCH_WORKER_UPLOAD)
#error "Native sample reads require the opt-in Win32 upload worker"
#endif
#ifdef _WIN32
#include <windows.h>''')
    text=replace_once(text,'    gboolean opened;','''    gboolean opened;
#if DLA_WCH_NATIVE_READ
    HANDLE native_handle; /* Borrowed from SDK; CloseDevice remains the owner. */
#endif''')
    text=replace_once(text,'    wch->opened = TRUE;','''    wch->opened = TRUE;
#if DLA_WCH_NATIVE_READ
    wch->native_handle = handle;
#endif''')
    text=replace_once(text,'    wch->opened = FALSE;\n#if DLA_WCH_UPLOAD_DIAGNOSTIC','''    wch->opened = FALSE;
#if DLA_WCH_NATIVE_READ
    wch->native_handle = NULL;
#endif
#if DLA_WCH_UPLOAD_DIAGNOSTIC''')
    pos=text.rindex('\n#endif\n#endif')
    native=(ROOT/'tools/dla32_native_sample_read.h').read_text()
    text=text[:pos]+'\n'+native+text[pos:]
    transport.write_text(text,newline='\n')
    worker=source/'src/hardware/fnirsi-dla32/worker-wch.h'
    text=worker.read_text()
    text=replace_once(text,'        result.buffer = worker->buffers[slot];','''        result.buffer = worker->buffers[slot];
#if DLA_WCH_NATIVE_READ
        result.buffer += 8; /* Kernel IOCTL wrapper, removed once at this boundary. */
#endif''')
    text=replace_once(text,'''        result.status = dla_wch_read(worker->wch, 1, result.buffer,
            DLA_WCH_READ_SIZE, &result.count, TRANSFER_TIMEOUT_MS);''','''#if DLA_WCH_NATIVE_READ
        result.status = dla_wch_native_sample_read(worker->wch, worker->buffers[slot],
            DLA_WCH_READ_SIZE, &result.count, TRANSFER_TIMEOUT_MS);
#else
        result.status = dla_wch_read(worker->wch, 1, result.buffer,
            DLA_WCH_READ_SIZE, &result.count, TRANSFER_TIMEOUT_MS);
#endif''')
    text=replace_once(text,'        worker->buffers[i] = g_try_malloc(DLA_WCH_READ_SIZE);','''        worker->buffers[i] = g_try_malloc(DLA_WCH_READ_SIZE
#if DLA_WCH_NATIVE_READ
            + 8
#endif
        );''')
    worker.write_text(text,newline='\n')
    fixture=(ROOT/'artifacts/dla32-wch-worker-v9-tests/test_dla32_worker_v9_unit.c').read_text().replace('v9-source','v10-source')
    anchor='#define CreateThread fake_create_thread'
    fixture=replace_once(fixture,anchor,'''static BOOL WINAPI native_ioctl(HANDLE handle,DWORD code,LPVOID input,DWORD input_size,
        LPVOID output,DWORD output_size,LPDWORD returned,LPOVERLAPPED operation)
{
    ULONG length=((ULONG *)input)[1];BOOL ok;
    (void)handle;
    if (code!=0x223cdc || input!=output || input_size!=8 || output_size!=length+8 || operation ||
        ((ULONG *)input)[0]!=0x10000) return FALSE;
    ok=fake_read(0,1,(uint8_t *)output+8,&length);
    ((ULONG *)output)[0]=0;((ULONG *)output)[1]=length;*returned=length+8;
    return ok;
}
#define DeviceIoControl native_ioctl
'''+anchor)
    fixture=fixture.replace('#undef CreateThread','#undef DeviceIoControl\n#undef CreateThread')
    fixture=replace_once(fixture,'    f->devc->logic_only = TRUE;',
        '    f->devc->wch.native_handle = (HANDLE)(uintptr_t)0x777;\n    f->devc->logic_only = TRUE;')
    # The native API failure does not expose a trusted partial output. Preserve
    # the successful short-read progress test and independently test failures.
    fixture=fixture.replace('(struct script){partial ? 16 : 0, FALSE, ERROR_TIMEOUT, FALSE}',
        '(struct script){partial ? 16 : 0, partial ? TRUE : FALSE, partial ? ERROR_SUCCESS : ERROR_TIMEOUT, FALSE}')
    (tests/'test_dla32_worker_v10_unit.c').write_text(fixture,newline='\n')
    upload=(ROOT/'tools/test_dla32_worker_v9_upload.c').read_text().replace('../artifacts/dla32-wch-worker-v9-tests/test_dla32_worker_v9_unit.c','test_dla32_worker_v10_unit.c')
    (tests/'test_dla32_worker_v10_upload.c').write_text(upload,newline='\n')
    msys=Path((ROOT/'tools/toolchain-path.txt').read_text().strip())
    old=msys/'tmp/dla32-wch-worker-v9-build/libsigrok';new=msys/'tmp/dla32-wch-worker-v10-build/libsigrok'
    if new.exists():raise RuntimeError('Fresh native V10 build required')
    shutil.copytree(old,new)
    for path in new.rglob('*'):
        if path.is_file() and (path.name in ('Makefile','config.status','libtool','libsigrok.pc','libsigrokcxx.pc') or path.suffix in ('.Plo','.Po','.la','.lo')):
            path.write_text(path.read_text().replace('dla32-wch-worker-v9-','dla32-wch-worker-v10-'),newline='\n')
    (source/'src/hardware/fnirsi-dla32/api.c').touch()
    print('V10 native read backend prepared; frozen sources/runtimes retained.')


if __name__=='__main__':main()
