/* SPDX-License-Identifier: GPL-3.0-only */
/* Repeated captures in one already-open DLA32 instance. No PWM/ESP/reset calls.
 * A/B is selected by the loaded DLL, checked against a runtime SHA256 before
 * sr_init, device discovery or opening. Root alone executes hardware captures.
 */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <glib.h>
#include <glib/gstdio.h>
#include <libsigrok/libsigrok.h>
#include <windows.h>
#include <io.h>

#define CAPTURE_SAMPLES UINT64_C(5000000)
#define SAMPLERATE UINT64_C(50000000)
#define GUARD_USEC (30 * G_TIME_SPAN_SECOND)
#define STOP_GRACE_USEC (10 * G_TIME_SPAN_SECOND)

struct log_result { FILE *file; unsigned int errors, writes_failed; };
struct capture_result {
    struct sr_session *session;
    GMainContext *main_context;
    FILE *logic;
    uint64_t samples, written_bytes, first_packet_samples, samples_at_stop;
    uint64_t unexpected_fixture_samples;
    uint32_t observed_bits;
    unsigned int headers, ends, logic_packets, stopped_callbacks;
    unsigned int logic_after_stop, context_mismatches, ends_during_stop;
    int64_t active_started_us;
    int stop_requested, stop_status, running_after_stop_request;
    int invalid_packet, guard_expired;
    int (*stop_fn)(struct sr_session *);
};

static int status_failed(const char *operation, int status)
{
    if (status == SR_OK) return 0;
    fprintf(stderr, "FAIL %s: %d (%s)\n", operation, status, sr_strerror(status));
    return 1;
}

static FILE *exclusive_file(const char *path)
{
    FILE *file;
    int fd = g_open(path, O_WRONLY | O_CREAT | O_EXCL | O_BINARY, 0600);
    if (fd < 0) {
        fprintf(stderr, "FAIL exclusive output '%s': %s\n", path, g_strerror(errno));
        return NULL;
    }
    file = fdopen(fd, "wb");
    if (!file) { close(fd); return NULL; }
    if (setvbuf(file, NULL, _IONBF, 0)) { fclose(file); return NULL; }
    return file;
}

static int save_log(void *data, int level, const char *format, va_list args)
{
    struct log_result *result = data;
    char *message = g_strdup_vprintf(format, args);
    int status;
    if (!message) { result->writes_failed++; return SR_ERR_MALLOC; }
    status = fprintf(result->file, "time_us=%" PRId64 " level=%d %s\n",
        g_get_monotonic_time(), level, message);
    if (level == SR_LOG_ERR) result->errors++;
    if (status < 0 || ferror(result->file)) result->writes_failed++;
    g_free(message);
    return result->writes_failed ? SR_ERR_IO : SR_OK;
}

static void request_capture_stop(struct capture_result *result)
{
    if (result->stop_requested) return;
    result->stop_requested = 1;
    result->samples_at_stop = result->samples;
    result->stop_status = result->stop_fn(result->session);
    result->ends_during_stop = result->ends;
    result->running_after_stop_request = result->session ?
        sr_session_is_running(result->session) == TRUE : -1;
    status_failed("sr_session_stop(callback)", result->stop_status);
}

static void receive(const struct sr_dev_inst *sdi,
    const struct sr_datafeed_packet *packet, void *data)
{
    struct capture_result *result = data;
    const struct sr_datafeed_logic *logic;
    const uint8_t *bytes;
    uint64_t count, i;
    uint32_t word;
    size_t written;
    (void)sdi;
    if (packet->type == SR_DF_HEADER) { result->headers++; return; }
    if (packet->type == SR_DF_END) { result->ends++; return; }
    if (packet->type != SR_DF_LOGIC) return;
    logic = packet->payload;
    if (!logic || result->headers != 1 || result->ends || logic->unitsize != 4 ||
        logic->length % 4 || !logic->length || !logic->data) {
        result->invalid_packet = 1;
        request_capture_stop(result);
        return;
    }
    result->logic_packets++;
    if (result->stop_requested) result->logic_after_stop++;
    if (result->main_context && !g_main_context_is_owner(result->main_context))
        result->context_mismatches++;
    count = logic->length / 4;
    if (result->logic_packets == 1) result->first_packet_samples = count;
    written = fwrite(logic->data, 1, (size_t)logic->length, result->logic);
    result->written_bytes += written;
    if (written != logic->length || ferror(result->logic)) result->invalid_packet = 1;
    bytes = logic->data;
    for (i = 0; i < count; i++) {
        word = (uint32_t)bytes[i * 4] | ((uint32_t)bytes[i * 4 + 1] << 8) |
            ((uint32_t)bytes[i * 4 + 2] << 16) | ((uint32_t)bytes[i * 4 + 3] << 24);
        result->observed_bits |= word;
        if (word & ~UINT32_C(3)) result->unexpected_fixture_samples++;
    }
    result->samples += count;
    if (result->invalid_packet) request_capture_stop(result);
}

static void session_stopped(void *data)
{
    struct capture_result *result = data;
    result->stopped_callbacks++;
    if (!g_main_context_is_owner(result->main_context)) result->context_mismatches++;
}

static gboolean guard_capture(void *data)
{
    struct capture_result *result = data;
    int64_t elapsed = g_get_monotonic_time() - result->active_started_us;
    if (elapsed >= GUARD_USEC && !result->guard_expired) {
        result->guard_expired = 1;
        fprintf(stderr, "FAIL acquisition guard expired; orderly STOP requested.\n");
        request_capture_stop(result);
    }
    if (elapsed >= GUARD_USEC + STOP_GRACE_USEC) {
        fprintf(stderr, "FAIL STOP grace expired; refusing reuse of the device instance.\n");
        fflush(NULL);
        exit(124);
    }
    return G_SOURCE_CONTINUE;
}

static int run_capture(struct sr_context *context, struct sr_dev_inst *sdi,
    const char *directory, unsigned int repetition, unsigned int step, const char *mode)
{
    struct capture_result result = {0};
    struct log_result log = {0};
    struct sr_trigger *trigger = NULL;
    struct sr_trigger_stage *stage;
    struct sr_channel *d0 = sr_dev_inst_channels_get(sdi)->data;
    GSource *guard = NULL;
    FILE *report = NULL;
    GStatBuf wire_stat;
    char *stem = g_strdup_printf("rep%03u-step%u-%s", repetition, step, mode);
    char *logic_name = g_strconcat(stem, ".logic.bin", NULL);
    char *wire_name = g_strconcat(stem, ".wire.bin", NULL);
    char *report_name = g_strconcat(stem, ".json", NULL);
    char *log_name = g_strconcat(stem, ".driver.log", NULL);
    char *logic_path = g_build_filename(directory, logic_name, NULL);
    char *wire_path = g_build_filename(directory, wire_name, NULL);
    char *report_path = g_build_filename(directory, report_name, NULL);
    char *log_path = g_build_filename(directory, log_name, NULL);
    uint64_t wire_bytes = 0;
    int failed = 1, run_status = SR_ERR, destroy_status = SR_OK;
    int running_after_run = -1, logic_close_failed = 0, log_close_failed = 0;
    result.main_context = g_main_context_default();
    result.stop_fn = sr_session_stop;
    result.stop_status = SR_OK;
    fprintf(stderr, "BEGIN_CAPTURE %s same_device_instance=%p\n", stem, (void *)sdi);
#define REQUIRE_CAPTURE(expr) do { if (status_failed(#expr, (expr))) goto cleanup; } while (0)
    result.logic = exclusive_file(logic_path);
    log.file = exclusive_file(log_path);
    if (!result.logic || !log.file) goto cleanup;
    REQUIRE_CAPTURE(sr_log_callback_set(save_log, &log));
    if (!g_setenv("FNIRSI_DLA32_WIRE_TRACE", wire_path, TRUE)) goto cleanup;
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_DATA_SOURCE, g_variant_new_string(mode)));
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_SAMPLERATE, g_variant_new_uint64(SAMPLERATE)));
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_LIMIT_SAMPLES,
        g_variant_new_uint64(CAPTURE_SAMPLES)));
    REQUIRE_CAPTURE(sr_session_new(context, &result.session));
    REQUIRE_CAPTURE(sr_session_dev_add(result.session, sdi));
    trigger = sr_trigger_new("D0 rising hardware request; driver raw position 500");
    if (!trigger) goto cleanup;
    stage = sr_trigger_stage_add(trigger);
    if (!stage) goto cleanup;
    REQUIRE_CAPTURE(sr_trigger_match_add(stage, d0, SR_TRIGGER_RISING, 0));
    REQUIRE_CAPTURE(sr_session_trigger_set(result.session, trigger));
    REQUIRE_CAPTURE(sr_session_datafeed_callback_add(result.session, receive, &result));
    REQUIRE_CAPTURE(sr_session_stopped_callback_set(result.session, session_stopped, &result));
    REQUIRE_CAPTURE(sr_session_start(result.session));
    result.active_started_us = g_get_monotonic_time();
    guard = g_timeout_source_new(100);
    g_source_set_callback(guard, guard_capture, &result, NULL);
    if (!g_source_attach(guard, result.main_context)) goto cleanup;
    run_status = sr_session_run(result.session);
    running_after_run = sr_session_is_running(result.session);
    failed = status_failed("sr_session_run", run_status) || running_after_run != FALSE ||
        result.headers != 1 || result.ends != 1 || result.stopped_callbacks != 1 ||
        result.invalid_packet || result.guard_expired || result.context_mismatches ||
        result.stop_status != SR_OK || result.logic_after_stop || result.stop_requested ||
        result.written_bytes != result.samples * 4 || result.samples != CAPTURE_SAMPLES;
cleanup:
    if (result.session && sr_session_is_running(result.session) == TRUE) {
        request_capture_stop(&result);
        failed |= status_failed("sr_session_run(cleanup)", sr_session_run(result.session));
    }
    if (guard) { g_source_destroy(guard); g_source_unref(guard); }
    if (result.logic) {
        logic_close_failed = fclose(result.logic) != 0;
        failed |= logic_close_failed;
    }
    if (result.session) {
        destroy_status = sr_session_destroy(result.session);
        failed |= status_failed("sr_session_destroy", destroy_status);
    }
    sr_trigger_free(trigger);
    if (!g_stat(wire_path, &wire_stat) && wire_stat.st_size >= 0)
        wire_bytes = (uint64_t)wire_stat.st_size;
    else failed = 1;
    failed |= wire_bytes < result.samples * 4;
    sr_log_callback_set_default();
    if (log.file) { log_close_failed = fclose(log.file) != 0; }
    failed |= log.errors || log.writes_failed || log_close_failed;
    g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    report = exclusive_file(report_path);
    if (report) {
        fprintf(report,
            "{\n  \"capture\": \"%s\",\n  \"repetition\": %u,\n  \"step\": %u,\n"
            "  \"context\": \"single-default\",\n  \"mode\": \"%s\",\n"
            "  \"samplerate_hz\": %" PRIu64 ",\n  \"threshold_volts\": 1.6,\n"
            "  \"trigger\": \"D0 rising hardware request\",\n  \"raw_trigger_position\": 500,\n"
            "  \"unitsize\": 4,\n  \"physical_channel_mask\": \"ffffffff\",\n"
            "  \"configured_sample_limit\": %" PRIu64 ",\n  \"actual_samples\": %" PRIu64 ",\n"
            "  \"first_packet_samples\": %" PRIu64 ",\n  \"stop_requested\": %s,\n"
            "  \"samples_at_stop\": %" PRIu64 ",\n  \"stop_status\": %d,\n"
            "  \"headers\": %u,\n  \"ends\": %u,\n  \"logic_packets\": %u,\n"
            "  \"logic_after_stop\": %u,\n  \"stopped_callbacks\": %u,\n"
            "  \"context_mismatches\": %u,\n  \"run_status\": %d,\n"
            "  \"running_after_run\": %d,\n  \"destroy_status\": %d,\n"
            "  \"invalid_packet\": %d,\n  \"guard_expired\": %d,\n"
            "  \"logic_close_failed\": %d,\n  \"driver_error_log_count\": %u,\n"
            "  \"driver_log_write_failures\": %u,\n  \"driver_log_close_failed\": %d,\n"
            "  \"written_logic_bytes\": %" PRIu64 ",\n  \"wire_trace_bytes\": %" PRIu64 ",\n"
            "  \"wire_trace_tail_mod32\": %u,\n  \"observed_sample_bits_or\": \"%08x\",\n"
            "  \"samples_with_bits_outside_known_fixture_D0_D1\": %" PRIu64 ",\n"
            "  \"logic_file\": \"%s\",\n  \"wire_file\": \"%s\",\n"
            "  \"driver_log_file\": \"%s\",\n  \"lifecycle_and_count_pass\": %s,\n"
            "  \"lane_diagnostics_affect_capture_loop\": false,\n"
            "  \"frame_origin_validated\": false,\n  \"trigger_behavior_validated\": false,\n"
            "  \"signal_integrity_verified\": false\n}\n",
            stem, repetition, step, mode, SAMPLERATE, CAPTURE_SAMPLES, result.samples,
            result.first_packet_samples, result.stop_requested ? "true" : "false",
            result.samples_at_stop, result.stop_status, result.headers, result.ends,
            result.logic_packets, result.logic_after_stop, result.stopped_callbacks,
            result.context_mismatches, run_status, running_after_run, destroy_status,
            result.invalid_packet, result.guard_expired, logic_close_failed, log.errors,
            log.writes_failed, log_close_failed, result.written_bytes, wire_bytes,
            (unsigned int)(wire_bytes % 32), result.observed_bits,
            result.unexpected_fixture_samples, logic_name, wire_name, log_name,
            failed ? "false" : "true");
        failed |= ferror(report) != 0;
        failed |= fclose(report) != 0;
    } else failed = 1;
    printf("%s lifecycle/count: %s samples=%" PRIu64 " HEADER=%u END=%u stopped=%u "
        "observed_bits=%08x unexpected_fixture_samples=%" PRIu64 " driver_errors=%u\n",
        failed ? "FAIL" : "PASS", stem, result.samples, result.headers, result.ends,
        result.stopped_callbacks, result.observed_bits, result.unexpected_fixture_samples, log.errors);
    g_free(stem); g_free(logic_name); g_free(wire_name); g_free(report_name); g_free(log_name);
    g_free(logic_path); g_free(wire_path); g_free(report_path); g_free(log_path);
    return failed;
#undef REQUIRE_CAPTURE
}

static int valid_sha256(const char *sha)
{
    size_t i;
    if (strlen(sha) != 64) return 0;
    for (i = 0; i < 64; i++) if (!g_ascii_isxdigit(sha[i])) return 0;
    return 1;
}

static int verify_diagnostic_dll(const char *expected)
{
    wchar_t wide_path[32768];
    HMODULE module = GetModuleHandleW(L"libsigrok-4.dll");
    char *path, *contents = NULL, *sha;
    gsize length;
    int failed;
    if (!module || !GetModuleFileNameW(module, wide_path, G_N_ELEMENTS(wide_path))) {
        fprintf(stderr, "FAIL cannot identify the loaded libsigrok DLL.\n"); return 1;
    }
    path = g_utf16_to_utf8((const gunichar2 *)wide_path, -1, NULL, NULL, NULL);
    if (!path || !g_file_get_contents(path, &contents, &length, NULL)) {
        g_free(path); return 1;
    }
    sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, (const guchar *)contents, length);
    failed = g_ascii_strcasecmp(sha, expected) != 0;
    fprintf(stderr, "Loaded libsigrok: %s SHA256=%s expected_match=%d\n", path, sha, !failed);
    g_free(path); g_free(contents); g_free(sha);
    return failed;
}

static unsigned int self_stop_calls;
static int self_stop(struct sr_session *session)
{ (void)session; self_stop_calls++; return SR_OK; }
static int self_test(void)
{
    struct capture_result result = {0};
    uint8_t bytes[8] = {1, 0, 0, 0, 2, 0, 16, 0};
    struct sr_datafeed_logic logic = {sizeof(bytes), 4, bytes};
    struct sr_datafeed_packet packet = {SR_DF_HEADER, NULL};
    int failed;
    result.logic = tmpfile(); result.stop_fn = self_stop;
    if (!result.logic) return 1;
    receive(NULL, &packet, &result);
    packet.type = SR_DF_LOGIC; packet.payload = &logic;
    receive(NULL, &packet, &result);
    failed = result.samples != 2 || result.first_packet_samples != 2 ||
        self_stop_calls || result.stop_requested || result.observed_bits != UINT32_C(0x00100003) ||
        result.unexpected_fixture_samples != 1 || result.written_bytes != 8 || result.invalid_packet;
    /* A shifted active lane must remain diagnostic and must not stop acquisition. */
    logic.unitsize = 2;
    receive(NULL, &packet, &result);
    failed |= !result.invalid_packet || self_stop_calls != 1 || !result.stop_requested;
    request_capture_stop(&result);
    failed |= self_stop_calls != 1;
    packet.type = SR_DF_END; packet.payload = NULL;
    receive(NULL, &packet, &result);
    failed |= result.ends != 1;
    failed |= !valid_sha256("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef") ||
        valid_sha256("wrong");
    fclose(result.logic);
    printf("%s self-test: shifted lane diagnostic, invalid packet STOP once, counts, END, SHA syntax; no hardware.\n",
        failed ? "FAIL" : "PASS");
    return failed;
}

int main(int argc, char **argv)
{
    struct sr_context *context = NULL;
    struct sr_dev_driver **drivers, *driver = NULL;
    struct sr_dev_inst *sdi = NULL;
    GSList *devices = NULL, *channels;
    char *original_trace = g_strdup(g_getenv("FNIRSI_DLA32_WIRE_TRACE"));
    char *end = NULL;
    unsigned long repetitions = 0, repetition;
    int failed = 1, opened = 0, i;
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 2 && !strcmp(argv[1], "--self-test")) {
        g_free(original_trace); return self_test();
    }
    if (argc == 5) { errno = 0; repetitions = strtoul(argv[3], &end, 10); }
    if (argc != 5 || !g_path_is_absolute(argv[1]) || !valid_sha256(argv[2]) ||
        errno || !end || *end || !repetitions || repetitions > 1000 || argv[3][0] == '-' ||
        (strcmp(argv[4], "stream-only") && strcmp(argv[4], "buffer-stream"))) {
        fprintf(stderr, "Usage: test_dla32_drain_ab NEW_ABSOLUTE_OUTPUT_DIR EXPECTED_DLL_SHA256 "
            "REPETITIONS_1_TO_1000 stream-only|buffer-stream\nOr --self-test (no hardware).\n");
        g_free(original_trace); return 2;
    }
    /* SHA mismatch exits before sr_init/scan/open, and before creating outputs. */
    if (verify_diagnostic_dll(argv[2]) || g_mkdir(argv[1], 0700)) {
        fprintf(stderr, "FAIL matching diagnostic DLL and NEW output directory required.\n");
        g_free(original_trace); return 2;
    }
    sr_log_loglevel_set(SR_LOG_DBG);
#define REQUIRE_MAIN(expr) do { if (status_failed(#expr, (expr))) goto cleanup; } while (0)
    REQUIRE_MAIN(sr_init(&context));
    drivers = sr_driver_list(context);
    for (i = 0; drivers[i]; i++) if (!strcmp(drivers[i]->name, "fnirsi-dla32")) driver = drivers[i];
    if (!driver) goto cleanup;
    REQUIRE_MAIN(sr_driver_init(context, driver));
    devices = sr_driver_scan(driver, NULL);
    if (g_slist_length(devices) != 1) goto cleanup;
    sdi = devices->data;
    channels = sr_dev_inst_channels_get(sdi);
    if (g_slist_length(channels) != 32 || ((struct sr_channel *)channels->data)->index != 0)
        goto cleanup;
    REQUIRE_MAIN(sr_dev_open(sdi)); opened = 1;
    for (; channels; channels = channels->next)
        REQUIRE_MAIN(sr_dev_channel_enable(channels->data, TRUE));
    REQUIRE_MAIN(sr_config_set(sdi, NULL, SR_CONF_VOLTAGE_THRESHOLD, g_variant_new("(dd)", 1.6, 1.6)));
    printf("Same open DLA32 instance %p; full32,50MS/s,5M samples,threshold1.6V,D0 rising,"
        "rawpos500,single default context; sequence=%s repetitions=%lu.\n", (void *)sdi, argv[4], repetitions);
    for (repetition = 1; repetition <= repetitions; repetition++) {
        if (!strcmp(argv[4], "buffer-stream") &&
            run_capture(context, sdi, argv[1], (unsigned int)repetition, 1, "Buffer")) goto cleanup;
        if (run_capture(context, sdi, argv[1], (unsigned int)repetition,
            !strcmp(argv[4], "buffer-stream") ? 2 : 1, "Stream")) goto cleanup;
    }
    failed = 0;
cleanup:
    if (opened) failed |= status_failed("sr_dev_close", sr_dev_close(sdi));
    g_slist_free(devices);
    if (context) failed |= status_failed("sr_exit", sr_exit(context));
    if (original_trace) g_setenv("FNIRSI_DLA32_WIRE_TRACE", original_trace, TRUE);
    else g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    g_free(original_trace);
    printf("%s repeated lifecycle/count sequence. RAW lane origin, physical trigger placement "
        "and lossless signal integrity require separate offline evaluation.\n", failed ? "FAIL" : "PASS");
    return failed ? 1 : 0;
#undef REQUIRE_MAIN
}
