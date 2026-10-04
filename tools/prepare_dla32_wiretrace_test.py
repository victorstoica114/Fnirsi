"""Build a hardware-free fixture around the exact isolated diagnostic driver."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
directory = root / "artifacts/dla32-wiretrace-tests"
directory.mkdir(parents=True, exist_ok=True)
driver = root / "artifacts/la1010-reference-source/libsigrok/src/hardware/fnirsi-dla32/api.c"
source = (root / "tools/audit_dla32_driver.c").read_text(encoding="utf-8")
source = source.replace('#include "../src/libsigrok/src/hardware/fnirsi-dla32/api.c"',
                        f'#include "{driver.as_posix()}"')
extra = r'''
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
'''
source = source.replace("int main(void)\n{", extra + "\nint main(void)\n{")
source = source.replace("    test_guards_and_capabilities();", "    test_wire_trace();\n    test_guards_and_capabilities();")
(directory / "test_dla32_wiretrace.c").write_text(source, encoding="utf-8")
print(directory / "test_dla32_wiretrace.c")
