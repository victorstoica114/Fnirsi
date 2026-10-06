"""Create a lossless-compressed copy of the previously tested 12 s harness."""
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    target=ROOT/'tools/test_dla32_worker_v8_continuous.c'
    if target.exists():
        raise RuntimeError('Fresh harness required')
    text=(ROOT/'tools/test_dla32_worker_v7_continuous.c').read_text()
    text=text.replace('#define main frozen_cancel_harness_main', '''#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <glib.h>
#include "dla32_lossless_logic_sink.h"
#define fwrite logic_sink_write
#define fclose logic_sink_close
#define main frozen_cancel_harness_main''')
    text=text.replace('return self_test();','return self_test() || logic_sink_self_test();')
    text=text.replace('"capture.logic.bin"','"capture.logic.zst"')
    text=text.replace('if (!r.logic || !log.file) goto cleanup;',
                      'if (!r.logic || !log.file || logic_sink_init(r.logic)) goto cleanup;')
    text=text.replace('g_setenv("FNIRSI_DLA32_WIRE_TRACE", wire_path, TRUE);',
                      'g_unsetenv("FNIRSI_DLA32_WIRE_TRACE"); /* No RAW disk writes in this probe. */')
    text=text.replace('V7-continuous-12s','V8-continuous-12s-zstd').replace('V7 continuous 12s','V8 continuous 12s zstd')
    text=text.replace('failed |= log.errors != 0 || log.writes_failed != 0;',
                      'failed |= log.errors != 0 || log.writes_failed != 0 || compression_failed || compression_input_bytes != r.samples*4;')
    anchor='        fprintf(report, "{'
    replacement='''        fprintf(report, "{\\\"LOGIC_encoding\\\":\\\"zstd\\\",\\\"RAW_saved\\\":false,"
            "\\\"LOGIC_uncompressed_sha256\\\":\\\"%s\\\",\\\"LOGIC_uncompressed_bytes\\\":%" PRIu64 ","
            "\\\"LOGIC_saved_bytes\\\":%" PRIu64 ",",compression_sha256,compression_input_bytes,compression_saved_bytes);
        fprintf(report, "'''
    if text.count(anchor)!=1:
        raise RuntimeError('Expected metadata anchor')
    text=text.replace(anchor,replacement)
    target.write_text(text,newline='\n')
    print('V8 lossless-compressed all-channel harness prepared; frozen harness unchanged.')


if __name__=='__main__':
    main()
