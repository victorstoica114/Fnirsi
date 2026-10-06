/* SPDX-License-Identifier: GPL-3.0-only */
/* Separate V7 worker cancellation/recovery probe; the frozen A/B harness is untouched.
 * One long Buffer capture is cancelled through sr_session_stop from a GLib timer,
 * followed by finite Buffer and Stream captures in the same open device instance.
 * DLL SHA and resolved workspace containment are checked before sr_init/scan/open.
 * Root alone executes hardware. There are no PWM, ESP, reset, or remapping calls.
 * Derived from frozen V5 cancel SHAac5e0995c9a111a1df97c172ac67563543e784e20f9e8fd4f9427796286fc216.
 * Frozen V5 source/EXE are unchanged. The V7 worker-unconsumed sidecar is retained.
 * Derived from frozen A/B source SHA4cfc4b3f17c223e94d0eb4772f4c16b53a4921b3c41ff45991da33ee55e8635d.
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

#define RECOVERY_SAMPLES UINT64_C(5000000)
#define RECOVERY_RATE UINT64_C(50000000)
#define CANCEL_SAMPLES UINT64_C(10000000)
#define CANCEL_RATE UINT64_C(1000000)
#define CANCEL_DELAY_MS 100
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
    int64_t active_started_us, start_begin_us, start_end_us, run_begin_us, run_end_us;
    int64_t timer_attached_us, timer_fired_us, stop_begin_us, stop_end_us;
    int64_t header_us, end_us, stopped_us, destroy_begin_us, destroy_end_us;
    uint64_t configured_limit;
    const char *stop_reason;
    unsigned int stop_call_count, cancel_timer_fired;
    int cancel_test;
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

static void request_capture_stop(struct capture_result *result, const char *reason)
{
    if (result->stop_requested) return;
    result->stop_requested = 1;
    result->samples_at_stop = result->samples;
    result->stop_reason = reason;
    result->stop_call_count++;
    result->stop_begin_us = g_get_monotonic_time();
    result->stop_status = result->stop_fn(result->session);
    result->stop_end_us = g_get_monotonic_time();
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
    if (packet->type == SR_DF_HEADER) {
        if (!result->headers) result->header_us = g_get_monotonic_time();
        result->headers++; return;
    }
    if (packet->type == SR_DF_END) {
        if (!result->ends) result->end_us = g_get_monotonic_time();
        result->ends++; return;
    }
    if (packet->type != SR_DF_LOGIC) return;
    logic = packet->payload;
    if (!logic || result->headers != 1 || result->ends || logic->unitsize != 4 ||
        logic->length % 4 || !logic->length || !logic->data) {
        result->invalid_packet = 1;
        request_capture_stop(result, "invalid-packet");
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
    if (result->invalid_packet) request_capture_stop(result, "invalid-packet");
}

static void session_stopped(void *data)
{
    struct capture_result *result = data;
    result->stopped_callbacks++;
    result->stopped_us = g_get_monotonic_time();
    if (!g_main_context_is_owner(result->main_context)) result->context_mismatches++;
}

static gboolean guard_capture(void *data)
{
    struct capture_result *result = data;
    int64_t elapsed = g_get_monotonic_time() - result->active_started_us;
    if (elapsed >= GUARD_USEC && !result->guard_expired) {
        result->guard_expired = 1;
        fprintf(stderr, "FAIL acquisition guard expired; orderly STOP requested.\n");
        request_capture_stop(result, "guard");
    }
    if (elapsed >= GUARD_USEC + STOP_GRACE_USEC) {
        fprintf(stderr, "FAIL STOP grace expired; refusing reuse of the device instance.\n");
        fflush(NULL);
        exit(124);
    }
    return G_SOURCE_CONTINUE;
}

static gboolean cancel_capture(void *data)
{
    struct capture_result *result = data;
    if (!result->stop_requested) {
        result->cancel_timer_fired++;
        result->timer_fired_us = g_get_monotonic_time();
        if (!g_main_context_is_owner(result->main_context)) result->context_mismatches++;
        request_capture_stop(result, "cancel-timer");
    }
    return G_SOURCE_REMOVE;
}

static int lifecycle_counts_pass(const struct capture_result *r, int run_status,
    int running_after_run)
{
    if (run_status != SR_OK || running_after_run != FALSE ||
        r->headers != 1 || r->ends != 1 || r->stopped_callbacks != 1 ||
        r->invalid_packet || r->guard_expired || r->context_mismatches ||
        r->stop_status != SR_OK || r->logic_after_stop ||
        r->written_bytes != r->samples * 4)
        return 0;
    if (r->cancel_test)
        return r->stop_requested && r->stop_call_count == 1 &&
            r->cancel_timer_fired == 1 && r->stop_reason &&
            !strcmp(r->stop_reason, "cancel-timer") &&
            r->samples == r->samples_at_stop && r->samples < r->configured_limit;
    return !r->stop_requested && !r->stop_call_count &&
        !r->cancel_timer_fired && r->samples == r->configured_limit;
}

static int run_capture(struct sr_context *context, struct sr_dev_inst *sdi,
    const char *directory, unsigned int step, const char *mode, int cancel_test)
{
    struct capture_result result = {0};
    struct log_result log = {0};
    struct sr_trigger *trigger = NULL;
    struct sr_trigger_stage *stage;
    struct sr_channel *d0 = sr_dev_inst_channels_get(sdi)->data;
    GSource *guard = NULL, *cancel_timer = NULL;
    FILE *report = NULL;
    GStatBuf wire_stat, unconsumed_stat;
    char *stem = cancel_test ? g_strdup_printf("cancel001-%s", mode) :
        g_strdup_printf("rep001-step%u-%s", step, mode);
    const uint64_t capture_rate = cancel_test && strcmp(mode, "Stream") ? CANCEL_RATE : RECOVERY_RATE;
    const uint64_t capture_limit = cancel_test ? CANCEL_SAMPLES : RECOVERY_SAMPLES;
    char *logic_name = g_strconcat(stem, ".logic.bin", NULL);
    char *wire_name = g_strconcat(stem, ".wire.bin", NULL);
    char *report_name = g_strconcat(stem, ".json", NULL);
    char *log_name = g_strconcat(stem, ".driver.log", NULL);
    char *logic_path = g_build_filename(directory, logic_name, NULL);
    char *wire_path = g_build_filename(directory, wire_name, NULL);
    char *unconsumed_name = g_strconcat(wire_name, ".worker-unconsumed.bin", NULL);
    char *unconsumed_path = g_build_filename(directory, unconsumed_name, NULL);
    char *report_path = g_build_filename(directory, report_name, NULL);
    char *log_path = g_build_filename(directory, log_name, NULL);
    uint64_t wire_bytes = 0, unconsumed_bytes = 0;
    int failed = 1, run_status = SR_ERR, destroy_status = SR_OK;
    int running_after_run = -1, logic_close_failed = 0, log_close_failed = 0;
    result.main_context = g_main_context_default();
    result.stop_fn = sr_session_stop;
    result.stop_status = SR_OK;
    result.cancel_test = cancel_test;
    result.configured_limit = capture_limit;
    result.stop_reason = "none";
    fprintf(stderr, "BEGIN_CAPTURE %s same_device_instance=%p\n", stem, (void *)sdi);
#define REQUIRE_CAPTURE(expr) do { if (status_failed(#expr, (expr))) goto cleanup; } while (0)
    result.logic = exclusive_file(logic_path);
    log.file = exclusive_file(log_path);
    if (!result.logic || !log.file) goto cleanup;
    REQUIRE_CAPTURE(sr_log_callback_set(save_log, &log));
    if (!g_setenv("FNIRSI_DLA32_WIRE_TRACE", wire_path, TRUE)) goto cleanup;
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_DATA_SOURCE, g_variant_new_string(mode)));
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_SAMPLERATE, g_variant_new_uint64(capture_rate)));
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_LIMIT_SAMPLES,
        g_variant_new_uint64(capture_limit)));
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
    result.start_begin_us = g_get_monotonic_time();
    {
        int start_status = sr_session_start(result.session);
        result.start_end_us = g_get_monotonic_time();
        if (status_failed("sr_session_start", start_status)) goto cleanup;
    }
    result.active_started_us = g_get_monotonic_time();
    if (cancel_test) {
        cancel_timer = g_timeout_source_new(CANCEL_DELAY_MS);
        g_source_set_priority(cancel_timer, G_PRIORITY_HIGH);
        g_source_set_callback(cancel_timer, cancel_capture, &result, NULL);
        result.timer_attached_us = g_get_monotonic_time();
        if (!g_source_attach(cancel_timer, result.main_context)) goto cleanup;
    }
    guard = g_timeout_source_new(100);
    g_source_set_callback(guard, guard_capture, &result, NULL);
    if (!g_source_attach(guard, result.main_context)) goto cleanup;
    result.run_begin_us = g_get_monotonic_time();
    run_status = sr_session_run(result.session);
    result.run_end_us = g_get_monotonic_time();
    running_after_run = sr_session_is_running(result.session);
    failed = !lifecycle_counts_pass(&result, run_status, running_after_run);
    status_failed("sr_session_run", run_status);
cleanup:
    if (result.session && sr_session_is_running(result.session) == TRUE) {
        request_capture_stop(&result, "cleanup");
        if (sr_session_is_running(result.session) == TRUE)
            failed |= status_failed("sr_session_run(cleanup)", sr_session_run(result.session));
    }
    if (guard) { g_source_destroy(guard); g_source_unref(guard); }
    if (cancel_timer) { g_source_destroy(cancel_timer); g_source_unref(cancel_timer); }
    if (result.logic) {
        logic_close_failed = fclose(result.logic) != 0;
        failed |= logic_close_failed;
    }
    if (result.session) {
        result.destroy_begin_us = g_get_monotonic_time();
        destroy_status = sr_session_destroy(result.session);
        result.destroy_end_us = g_get_monotonic_time();
        failed |= status_failed("sr_session_destroy", destroy_status);
    }
    sr_trigger_free(trigger);
    if (!g_stat(wire_path, &wire_stat) && wire_stat.st_size >= 0)
        wire_bytes = (uint64_t)wire_stat.st_size;
    else failed = 1;
    failed |= wire_bytes < result.samples * 4;
    /* Successful reads withheld after STOP must survive in a separate original
     * byte stream. They are not decoder input and are never appended to LOGIC. */
    if (!g_stat(unconsumed_path, &unconsumed_stat) && unconsumed_stat.st_size >= 0)
        unconsumed_bytes = (uint64_t)unconsumed_stat.st_size;
    else failed = 1;
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
            "  \"worker_unconsumed_file\": \"%s\",\n"
            "  \"worker_unconsumed_bytes\": %" PRIu64 ",\n"
            "  \"worker_unconsumed_tail_mod32\": %u,\n"
            "  \"worker_unconsumed_bytes_are_decoder_input\": false,\n"
            "  \"lane_diagnostics_affect_capture_loop\": false,\n"
            "  \"frame_origin_validated\": false,\n  \"trigger_behavior_validated\": false,\n"
            "  \"signal_integrity_verified\": false,\n"
            "  \"capture_kind\": \"%s\",\n  \"cancel_timer_nominal_ms\": %u,\n"
            "  \"cancel_timer_fired\": %u,\n  \"stop_call_count\": %u,\n"
            "  \"stop_reason\": \"%s\",\n  \"running_after_stop_request\": %d,\n"
            "  \"session_start_begin_us\": %" PRId64 ",\n  \"session_start_end_us\": %" PRId64 ",\n"
            "  \"session_run_begin_us\": %" PRId64 ",\n  \"session_run_end_us\": %" PRId64 ",\n"
            "  \"timer_attached_us\": %" PRId64 ",\n  \"timer_fired_us\": %" PRId64 ",\n"
            "  \"actual_timer_delay_us\": %" PRId64 ",\n"
            "  \"stop_begin_us\": %" PRId64 ",\n  \"stop_end_us\": %" PRId64 ",\n"
            "  \"stop_call_duration_us\": %" PRId64 ",\n"
            "  \"HEADER_us\": %" PRId64 ",\n  \"END_us\": %" PRId64 ",\n"
            "  \"stopped_callback_us\": %" PRId64 ",\n"
            "  \"stop_to_stopped_callback_us\": %" PRId64 ",\n"
            "  \"session_destroy_duration_us\": %" PRId64 ",\n"
            "  \"timer_deadline_is_not_a_hardware_timing_guarantee\": true\n}\n",
            stem, 1U, step, mode, capture_rate, capture_limit, result.samples,
            result.first_packet_samples, result.stop_requested ? "true" : "false",
            result.samples_at_stop, result.stop_status, result.headers, result.ends,
            result.logic_packets, result.logic_after_stop, result.stopped_callbacks,
            result.context_mismatches, run_status, running_after_run, destroy_status,
            result.invalid_packet, result.guard_expired, logic_close_failed, log.errors,
            log.writes_failed, log_close_failed, result.written_bytes, wire_bytes,
            (unsigned int)(wire_bytes % 32), result.observed_bits,
            result.unexpected_fixture_samples, logic_name, wire_name, log_name,
            failed ? "false" : "true", unconsumed_name, unconsumed_bytes,
            (unsigned int)(unconsumed_bytes % 32),
            cancel_test ? "programmatic-cancel" : "finite-recovery",
            cancel_test ? CANCEL_DELAY_MS : 0, result.cancel_timer_fired,
            result.stop_call_count, result.stop_reason, result.running_after_stop_request,
            result.start_begin_us, result.start_end_us, result.run_begin_us, result.run_end_us,
            result.timer_attached_us, result.timer_fired_us,
            result.timer_fired_us ? result.timer_fired_us - result.timer_attached_us : 0,
            result.stop_begin_us, result.stop_end_us, result.stop_end_us - result.stop_begin_us,
            result.header_us, result.end_us, result.stopped_us,
            result.stop_requested && result.stopped_us ? result.stopped_us - result.stop_begin_us : 0,
            result.destroy_end_us - result.destroy_begin_us);
        failed |= ferror(report) != 0;
        failed |= fclose(report) != 0;
    } else failed = 1;
    printf("%s lifecycle/count: %s samples=%" PRIu64 " HEADER=%u END=%u stopped=%u "
        "observed_bits=%08x unexpected_fixture_samples=%" PRIu64 " driver_errors=%u\n",
        failed ? "FAIL" : "PASS", stem, result.samples, result.headers, result.ends,
        result.stopped_callbacks, result.observed_bits, result.unexpected_fixture_samples, log.errors);
    g_free(stem); g_free(logic_name); g_free(wire_name); g_free(report_name); g_free(log_name);
    g_free(logic_path); g_free(wire_path); g_free(report_path); g_free(log_path);
    g_free(unconsumed_name); g_free(unconsumed_path);
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

/* Resolve an EXISTING directory through Windows handles, including junctions.
 * Only the missing final output directory is created, after these checks. */
static char *resolved_directory(const char *path)
{
    gunichar2 *wide = g_utf8_to_utf16(path, -1, NULL, NULL, NULL);
    wchar_t final_path[32768];
    HANDLE handle;
    DWORD length;
    char *resolved = NULL;
    if (!wide || !g_file_test(path, G_FILE_TEST_IS_DIR)) { g_free(wide); return NULL; }
    handle = CreateFileW((const wchar_t *)wide, FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, NULL);
    g_free(wide);
    if (handle == INVALID_HANDLE_VALUE) return NULL;
    length = GetFinalPathNameByHandleW(handle, final_path, G_N_ELEMENTS(final_path),
        FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    CloseHandle(handle);
    if (length && length < G_N_ELEMENTS(final_path))
        resolved = g_utf16_to_utf8((const gunichar2 *)final_path, -1, NULL, NULL, NULL);
    return resolved;
}

static int fresh_workspace_output(const char *workspace, const char *output)
{
    char *root = NULL, *parent = NULL, *directory = NULL, *name = NULL;
    size_t length;
    int allowed = 0;
    if (!g_path_is_absolute(workspace) || !g_path_is_absolute(output) ||
        g_file_test(output, G_FILE_TEST_EXISTS)) return 0;
    directory = g_path_get_dirname(output); name = g_path_get_basename(output);
    if (!name || !*name || !strcmp(name, ".") || !strcmp(name, "..") ||
        strchr(name, ':') || strchr(name, '/') || strchr(name, '\\')) goto done;
    root = resolved_directory(workspace); parent = resolved_directory(directory);
    if (!root || !parent) goto done;
    length = strlen(root);
    while (length && (root[length - 1] == '\\' || root[length - 1] == '/'))
        root[--length] = 0;
    allowed = length && !g_ascii_strncasecmp(root, parent, length) &&
        (!parent[length] || parent[length] == '\\' || parent[length] == '/');
done:
    g_free(root); g_free(parent); g_free(directory); g_free(name);
    return allowed;
}

static unsigned int self_stop_calls;
static int self_stop_status = SR_OK;
static int self_stop(struct sr_session *session)
{ (void)session; self_stop_calls++; return self_stop_status; }

static int self_test(void)
{
    struct capture_result r = {0};
    uint8_t bytes[8] = {1, 0, 0, 0, 2, 0, 16, 0};
    struct sr_datafeed_logic logic = {sizeof(bytes), 4, bytes};
    struct sr_datafeed_packet packet = {SR_DF_HEADER, NULL};
    char *cwd = g_get_current_dir(), *testdir, *fresh, *outside, *existing;
    FILE *f = NULL, *second = NULL;
    GSource *timer;
    int checks = 0, failures = 0;
#define CHECK(condition) do { checks++; if (!(condition)) { \
    failures++; fprintf(stderr, "SELFTEST FAIL: %s\n", #condition); } } while (0)
    r.main_context = g_main_context_default();
    r.logic = tmpfile(); r.stop_fn = self_stop; r.stop_reason = "none";
    r.cancel_test = 1; r.configured_limit = CANCEL_SAMPLES;
    if (!r.logic) { g_free(cwd); return 1; }
    /* Actual GLib timer dispatch on the default context; no sr_init/session/device. */
    r.timer_attached_us = g_get_monotonic_time();
    timer = g_timeout_source_new(CANCEL_DELAY_MS);
    g_source_set_priority(timer, G_PRIORITY_HIGH);
    g_source_set_callback(timer, cancel_capture, &r, NULL);
    CHECK(g_source_attach(timer, r.main_context) != 0);
    while (!r.cancel_timer_fired) g_main_context_iteration(r.main_context, TRUE);
    g_source_destroy(timer); g_source_unref(timer);
    CHECK(r.cancel_timer_fired == 1 && self_stop_calls == 1 && r.stop_call_count == 1);
    CHECK(r.stop_requested && r.samples_at_stop == 0 && r.stop_status == SR_OK);
    CHECK(r.timer_fired_us >= r.timer_attached_us &&
        r.stop_end_us >= r.stop_begin_us && r.context_mismatches == 0);
    request_capture_stop(&r, "cleanup");
    CHECK(self_stop_calls == 1 && !strcmp(r.stop_reason, "cancel-timer"));
    CHECK(g_main_context_acquire(r.main_context));
    receive(NULL, &packet, &r);
    packet.type = SR_DF_END;
    receive(NULL, &packet, &r);
    session_stopped(&r);
    g_main_context_release(r.main_context);
    CHECK(lifecycle_counts_pass(&r, SR_OK, FALSE)); /* zero samples are valid cancel */
    r.samples = r.samples_at_stop = CANCEL_SAMPLES; r.written_bytes = CANCEL_SAMPLES * 4;
    CHECK(!lifecycle_counts_pass(&r, SR_OK, FALSE)); /* full capture is not early cancel */
    r.samples = r.samples_at_stop = r.written_bytes = 0;
    r.stop_reason = "guard";
    CHECK(!lifecycle_counts_pass(&r, SR_OK, FALSE));
    r.stop_reason = "cancel-timer"; r.stop_status = SR_ERR_IO;
    CHECK(!lifecycle_counts_pass(&r, SR_OK, FALSE));
    fclose(r.logic);

    memset(&r, 0, sizeof(r)); self_stop_calls = 0;
    r.logic = tmpfile(); r.stop_fn = self_stop; r.stop_reason = "none";
    r.configured_limit = 2;
    if (!r.logic) { g_free(cwd); return 1; }
    packet.type = SR_DF_HEADER; packet.payload = NULL;
    receive(NULL, &packet, &r);
    packet.type = SR_DF_LOGIC; packet.payload = &logic;
    receive(NULL, &packet, &r);
    CHECK(r.samples == 2 && r.written_bytes == 8 && r.first_packet_samples == 2);
    CHECK(r.observed_bits == UINT32_C(0x00100003) &&
        r.unexpected_fixture_samples == 1 && !r.stop_requested && !self_stop_calls);
    packet.type = SR_DF_END; packet.payload = NULL;
    receive(NULL, &packet, &r); r.stopped_callbacks = 1;
    CHECK(lifecycle_counts_pass(&r, SR_OK, FALSE)); /* lanes remain diagnostic */
    r.configured_limit = 3;
    CHECK(!lifecycle_counts_pass(&r, SR_OK, FALSE));
    r.configured_limit = 2;
    CHECK(!lifecycle_counts_pass(&r, SR_ERR_IO, FALSE));
    CHECK(!lifecycle_counts_pass(&r, SR_OK, TRUE));
    r.headers = 2;
    CHECK(!lifecycle_counts_pass(&r, SR_OK, FALSE));
    r.headers = 1; r.ends = 0; logic.unitsize = 2;
    packet.type = SR_DF_LOGIC; packet.payload = &logic;
    receive(NULL, &packet, &r);
    CHECK(r.invalid_packet && self_stop_calls == 1 && r.stop_call_count == 1);
    request_capture_stop(&r, "cleanup");
    CHECK(self_stop_calls == 1);
    logic.unitsize = 4;
    receive(NULL, &packet, &r);
    CHECK(r.logic_after_stop == 1 && r.samples == 4 && r.written_bytes == 16);
    CHECK(!lifecycle_counts_pass(&r, SR_OK, FALSE));
    fclose(r.logic);
    CHECK(valid_sha256("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"));
    CHECK(!valid_sha256("wrong"));
    CHECK(!valid_sha256("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdeg"));

    testdir = g_build_filename(cwd, "cancel-offline-selftest-XXXXXX", NULL);
    CHECK(g_mkdtemp(testdir) != NULL);
    fresh = g_build_filename(testdir, "fresh", NULL);
    existing = g_build_filename(testdir, "existing", NULL);
    outside = g_build_filename(cwd, "fresh-outside-selftest", NULL);
    CHECK(fresh_workspace_output(testdir, fresh));
    CHECK(!fresh_workspace_output(testdir, testdir));
    CHECK(!fresh_workspace_output(testdir, outside));
    CHECK(!fresh_workspace_output(testdir, "relative"));
    CHECK(!fresh_workspace_output("relative", fresh));
    CHECK(g_mkdir(existing, 0700) == 0);
    CHECK(!fresh_workspace_output(testdir, existing));
    CHECK(g_rmdir(existing) == 0);
    f = exclusive_file(existing);
    CHECK(f != NULL);
    if (f) { CHECK(fwrite("unchanged", 1, 9, f) == 9); CHECK(fclose(f) == 0); }
    second = exclusive_file(existing);
    CHECK(second == NULL);
    if (second) fclose(second);
    CHECK(!fresh_workspace_output(testdir, existing));
    {
        char *contents = NULL; gsize length = 0;
        CHECK(g_file_get_contents(existing, &contents, &length, NULL));
        CHECK(length == 9 && !memcmp(contents, "unchanged", 9));
        g_free(contents);
    }
    CHECK(g_remove(existing) == 0);
    CHECK(g_rmdir(testdir) == 0);
    g_free(cwd); g_free(testdir); g_free(fresh); g_free(existing); g_free(outside);
    printf("%s offline self-test: %d checks, %d failures; timer dispatch, zero-sample "
        "cancel, stop once, finite recovery counts, shifted-lane diagnostics, "
        "invalid/post-stop packets, SHA syntax, resolved fresh path and exclusive files; no hardware.\n",
        failures ? "FAIL" : "PASS", checks, failures);
    return failures ? 1 : 0;
#undef CHECK
}

int main(int argc, char **argv)
{
    struct sr_context *context = NULL;
    struct sr_dev_driver **drivers, *driver = NULL;
    struct sr_dev_inst *sdi = NULL;
    GSList *devices = NULL, *channels;
    char *original_trace = g_strdup(g_getenv("FNIRSI_DLA32_WIRE_TRACE"));
    const char *workspace, *directory, *dll_sha;
    char *report_path;
    FILE *report;
    unsigned int completed = 0;
    int failed = 1, opened = 0, i, validate_only = 0;
    int close_status = SR_OK, exit_status = SR_OK;
    int64_t close_begin_us = 0, close_end_us = 0, exit_begin_us = 0, exit_end_us = 0;
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 2 && !strcmp(argv[1], "--self-test")) {
        g_free(original_trace); return self_test();
    }
    if (argc == 5 && !strcmp(argv[1], "--validate-only")) validate_only = 1;
    if (argc != 4 && !validate_only) goto usage;
    workspace = argv[1 + validate_only];
    directory = argv[2 + validate_only];
    dll_sha = argv[3 + validate_only];
    if (!g_path_is_absolute(workspace) || !g_path_is_absolute(directory) ||
        !valid_sha256(dll_sha)) goto usage;
    /* SHA mismatch or path failure exits before sr_init/scan/open/output creation. */
    if (verify_diagnostic_dll(dll_sha) || !fresh_workspace_output(workspace, directory)) {
        fprintf(stderr, "FAIL matching DLL and resolved NEW workspace output required; no device opened.\n");
        g_free(original_trace); return 2;
    }
    if (validate_only) {
        printf("PASS SHA and NEW workspace-output validation; no output created, no sr_init/scan/open.\n");
        g_free(original_trace); return 0;
    }
    if (g_mkdir(directory, 0700)) {
        fprintf(stderr, "FAIL exclusive NEW output directory: %s\n", g_strerror(errno));
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
    printf("Same open DLA32 instance %p; all32,threshold1.6V,D0 rising,rawpos500,"
        "single default context. Cancel DLA32_CANCEL_MODE/10M -> timer nominal100ms -> "
        "finite Buffer50MS/s/5M -> finite Stream50MS/s/5M. Timer, STOP request, and stopped-callback delays are measured separately.\n",
        (void *)sdi);
    const char *cancel_mode = g_getenv("DLA32_CANCEL_MODE");
    if (!cancel_mode) cancel_mode = "Buffer";
    if (strcmp(cancel_mode, "Buffer") && strcmp(cancel_mode, "Stream")) goto cleanup;
    if (run_capture(context, sdi, directory, 0, cancel_mode, 1)) goto cleanup;
    completed++;
    if (run_capture(context, sdi, directory, 1, "Buffer", 0)) goto cleanup;
    completed++;
    if (run_capture(context, sdi, directory, 2, "Stream", 0)) goto cleanup;
    completed++;
    failed = 0;
cleanup:
    if (opened) {
        close_begin_us = g_get_monotonic_time();
        close_status = sr_dev_close(sdi);
        close_end_us = g_get_monotonic_time();
        failed |= status_failed("sr_dev_close", close_status);
    }
    g_slist_free(devices);
    if (context) {
        exit_begin_us = g_get_monotonic_time();
        exit_status = sr_exit(context);
        exit_end_us = g_get_monotonic_time();
        failed |= status_failed("sr_exit", exit_status);
    }
    if (original_trace) g_setenv("FNIRSI_DLA32_WIRE_TRACE", original_trace, TRUE);
    else g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    g_free(original_trace);
    report_path = g_build_filename(directory, "sequence-result.json", NULL);
    report = exclusive_file(report_path);
    if (report) {
        fprintf(report, "{\n  \"harness\": \"separate_v7_worker_cancel_recovery\",\n"
            "  \"expected_loaded_DLL_SHA256\": \"%s\",\n"
            "  \"completed_capture_stages\": %u,\n  \"expected_capture_stages\": 3,\n"
            "  \"device_close_attempted\": %s,\n  \"device_close_status\": %d,\n"
            "  \"device_close_duration_us\": %" PRId64 ",\n"
            "  \"sr_exit_attempted\": %s,\n  \"sr_exit_status\": %d,\n"
            "  \"sr_exit_duration_us\": %" PRId64 ",\n"
            "  \"outer_lifecycle_and_count_pass\": %s,\n"
            "  \"cancel_nominal_ms\": 100,\n"
            "  \"timer_deadline_is_not_a_hardware_timing_guarantee\": true,\n"
            "  \"lane_diagnostics_affect_capture_loop\": false,\n"
            "  \"physical_integrity_validated\": false\n}\n",
            dll_sha, completed, opened ? "true" : "false", close_status,
            close_end_us - close_begin_us, exit_begin_us ? "true" : "false",
            exit_status, exit_end_us - exit_begin_us, failed ? "false" : "true");
        failed |= ferror(report) != 0;
        failed |= fclose(report) != 0;
    } else failed = 1;
    g_free(report_path);
    printf("%s cancellation/recovery lifecycle sequence; completed=%u/3 "
        "close_status=%d close_us=%" PRId64 " sr_exit_status=%d sr_exit_us=%" PRId64
        ". Physical trigger/origin/signal integrity require separate analysis.\n",
        failed ? "FAIL" : "PASS", completed, close_status, close_end_us - close_begin_us,
        exit_status, exit_end_us - exit_begin_us);
    return failed ? 1 : 0;
usage:
    fprintf(stderr, "Usage: test_dla32_stop_worker_v7_cancel WORKSPACE_ABS_DIR NEW_OUTPUT_ABS_DIR EXPECTED_DLL_SHA256\n"
        "Or --validate-only WORKSPACE_ABS_DIR NEW_OUTPUT_ABS_DIR EXPECTED_DLL_SHA256 (no hardware/output creation)\n"
        "Or --self-test (no hardware).\n");
    g_free(original_trace); return 2;
#undef REQUIRE_MAIN
}
