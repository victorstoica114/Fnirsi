"""Prepare isolated SDK0 transfer-size/ring experiments from frozen V7 r2."""
from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parent.parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Expected exactly one source anchor: ' + old[:70])
    return text.replace(old, new)


def main():
    source = ROOT/'artifacts/dla32-wch-worker-v8-source/libsigrok'
    tests = ROOT/'artifacts/dla32-wch-worker-v8-tests'
    if source.exists() or tests.exists():
        raise RuntimeError('Fresh isolated V8 paths required')
    shutil.copytree(ROOT/'artifacts/dla32-wch-worker-v7-r2-source/libsigrok', source)
    tests.mkdir()
    api = source/'src/hardware/fnirsi-dla32/api.c'
    text = api.read_text()
    text = replace_once(text, '#define TRANSFER_TIMEOUT_MS 1000', '''/* Sample reads are varied independently of the 1 MiB command/drain policy. */
#ifndef DLA_WCH_READ_SIZE
#define DLA_WCH_READ_SIZE (1024 * 1024)
#endif
#if DLA_WCH_READ_SIZE < 1048576 || DLA_WCH_READ_SIZE > 4194304 || \\
        (DLA_WCH_READ_SIZE & (DLA_WCH_READ_SIZE - 1))
#error "DLA_WCH_READ_SIZE must be a power of two from 1 to 4 MiB"
#endif
#define TRANSFER_TIMEOUT_MS 1000''')
    text = replace_once(text, 'frames = TRANSFER_SIZE / devc->decoder.wire_channels + 1;', '''frames = TRANSFER_SIZE / devc->decoder.wire_channels + 1;
#if defined(_WIN32) && DLA_WCH_WORKER
    if (devc->use_wch)
        frames = DLA_WCH_READ_SIZE / devc->decoder.wire_channels + 1;
#endif''')
    text = replace_once(text, 'result.count > 0 && result.count <= TRANSFER_SIZE ?',
                        'result.count > 0 && result.count <= DLA_WCH_READ_SIZE ?')
    text = replace_once(text, '(unsigned int)TRANSFER_SIZE, TRANSFER_TIMEOUT_MS, result.count,',
                        '(unsigned int)DLA_WCH_READ_SIZE, TRANSFER_TIMEOUT_MS, result.count,')
    api.write_text(text, newline='\n')
    worker = source/'src/hardware/fnirsi-dla32/worker-wch.h'
    text = worker.read_text()
    text = replace_once(text, '#define DLA_WORKER_BUFFER_SLOTS 2', '''#ifndef DLA_WORKER_BUFFER_SLOTS
#define DLA_WORKER_BUFFER_SLOTS 2
#endif
#if DLA_WORKER_BUFFER_SLOTS < 2 || DLA_WORKER_BUFFER_SLOTS > 32
#error "DLA_WORKER_BUFFER_SLOTS must be from 2 to 32"
#endif
#if DLA_WORKER_BUFFER_SLOTS * DLA_WCH_READ_SIZE > 134217728
#error "WCH sample ring must not exceed 128 MiB"
#endif''')
    text = replace_once(text, 'TRANSFER_SIZE, &result.count, TRANSFER_TIMEOUT_MS);',
                        'DLA_WCH_READ_SIZE, &result.count, TRANSFER_TIMEOUT_MS);')
    text = replace_once(text, 'worker->buffers[i] = g_try_malloc(TRANSFER_SIZE);',
                        'worker->buffers[i] = g_try_malloc(DLA_WCH_READ_SIZE);')
    text = replace_once(text, '            g_free(worker->buffers[0]);',
                        '            while (i)\n                g_free(worker->buffers[--i]);')
    text = replace_once(text, '    gboolean reconciled;\n', '    gboolean reconciled;\n    unsigned int i;\n')
    text = replace_once(text, '    g_free(worker->buffers[0]);\n    g_free(worker->buffers[1]);',
                        '    for (i = 0; i < DLA_WORKER_BUFFER_SLOTS; i++)\n        g_free(worker->buffers[i]);')
    text = text.replace('the other slot', 'another free slot').replace('either queued or claimed result', 'any queued or claimed result')
    worker.write_text(text, newline='\n')
    for path in (ROOT/'artifacts/dla32-wch-worker-v7-r2-tests').glob('*.c'):
        text = path.read_text().replace('v7-r2-source', 'v8-source')
        if path.name.endswith('_unit.c'):
            text = text.replace('atomic_read(&sample_reads) == 2);\n    expect("stop wakes',
                                'atomic_read(&sample_reads) == DLA_WORKER_BUFFER_SLOTS);\n    expect("stop wakes')
            text = text.replace('unconsumed FIFO enforces two-buffer bound', 'unconsumed FIFO enforces configured ring bound')
            text = text.replace('nth <= 6', 'nth <= DLA_WORKER_BUFFER_SLOTS + 4')
        (tests/path.name.replace('v7_r2', 'v8')).write_text(text, newline='\n')
    # Only generated native build paths change. The frozen build is retained.
    msys = Path((ROOT/'tools/toolchain-path.txt').read_text().strip())
    old = msys/'tmp/dla32-wch-worker-v7-r2-build/libsigrok'
    new = msys/'tmp/dla32-wch-worker-v8-build/libsigrok'
    if new.exists():
        raise RuntimeError('Fresh native V8 build required')
    shutil.copytree(old, new)
    for path in new.rglob('*'):
        if path.is_file() and (path.name in ('Makefile','config.status','libtool','libsigrok.pc','libsigrokcxx.pc') or path.suffix in ('.Plo','.Po','.la','.lo')):
            text = path.read_text().replace('dla32-wch-worker-v7-r2-', 'dla32-wch-worker-v8-')
            path.write_text(text, newline='\n')
    api.touch()  # Copied libtool metadata must never hide a required API rebuild.
    print(json.dumps({'prepared':True,'frozen_V7_modified':False}))


if __name__ == '__main__':
    main()
