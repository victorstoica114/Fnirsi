/* SPDX-License-Identifier: GPL-3.0-only */
/* Diagnostic host timer experiment; the V6 DLL and its frozen tests are unchanged.
 * Reuse the reviewed callback/path/DLL validation helpers, without device resets,
 * generator commands, sample filtering, or channel remapping. */
#define main frozen_cancel_harness_main
#include "test_dla32_stop_worker_v6_cancel.c"
#undef main
#include <mmsystem.h>

static unsigned int timer_ticks;
static int64_t timer_first, timer_last;
static gboolean timer_tick(void *data)
{
    (void)data;
    if (!timer_ticks) timer_first = g_get_monotonic_time();
    timer_last = g_get_monotonic_time();
    timer_ticks++;
    return timer_ticks < 50;
}

static void measure_glib_timer(const char *label)
{
    GSource *source = g_timeout_source_new(1);
    timer_ticks = 0;
    g_source_set_callback(source, timer_tick, NULL, NULL);
    g_source_attach(source, g_main_context_default());
    while (timer_ticks < 50) g_main_context_iteration(NULL, TRUE);
    printf("GLib 1ms timer %s: ticks=%u span_us=%" PRId64 " average_interval_us=%.3f\n",
        label, timer_ticks, timer_last - timer_first,
        (double)(timer_last - timer_first) / 49.0);
    g_source_destroy(source); g_source_unref(source);
}

int main(int argc, char **argv)
{
    struct sr_context *context = NULL;
    struct sr_dev_driver **drivers, *driver = NULL;
    struct sr_dev_inst *sdi = NULL;
    GSList *devices = NULL, *channels;
    struct capture_result r = {0};
    struct log_result log = {0};
    GSource *guard_source = NULL;
    FILE *report = NULL;
    char *logic_path = NULL, *wire_path = NULL, *log_path = NULL, *record_path = NULL;
    const uint64_t count = UINT64_C(100000000), rate = UINT64_C(50000000);
    unsigned int i;
    int failed = 1, opened = 0, timer_held = 0, close_status = SR_ERR,
        exit_status = SR_ERR, destroy_status = SR_ERR, run_status = SR_ERR;
    UINT timer_begin_status = UINT_MAX, timer_end_status = UINT_MAX;
    const char *timer_mode;
    if (argc == 2 && !strcmp(argv[1], "--self-test")) {
        int status = self_test();
        measure_glib_timer("default");
        if (timeBeginPeriod(1) != TIMERR_NOERROR) return 1;
        measure_glib_timer("requested-1ms");
        if (timeEndPeriod(1) != TIMERR_NOERROR) return 1;
        measure_glib_timer("restored");
        return status;
    }
    if (argc != 5 || (strcmp(argv[4], "default") && strcmp(argv[4], "1ms")) ||
        !valid_sha256(argv[3]) || verify_diagnostic_dll(argv[3]) ||
        !fresh_workspace_output(argv[1], argv[2])) {
        fprintf(stderr, "Usage: timer_probe WORKSPACE NEW_DIRECTORY DLL_SHA256 default|1ms\n");
        return 2;
    }
    timer_mode = argv[4];
    if (g_mkdir(argv[2], 0700)) return 2;
    logic_path = g_build_filename(argv[2], "capture.logic.bin", NULL);
    wire_path = g_build_filename(argv[2], "capture.wire.bin", NULL);
    log_path = g_build_filename(argv[2], "capture.driver.log", NULL);
    record_path = g_build_filename(argv[2], "capture.json", NULL);
    r.logic = exclusive_file(logic_path);
    log.file = exclusive_file(log_path);
    if (!r.logic || !log.file) goto cleanup;
    r.main_context = g_main_context_default();
    r.stop_fn = sr_session_stop;
    r.stop_reason = "none";
    r.configured_limit = count;
    sr_log_loglevel_set(SR_LOG_DBG);
    sr_log_callback_set(save_log, &log);
    g_setenv("FNIRSI_DLA32_WIRE_TRACE", wire_path, TRUE);
    if (!strcmp(timer_mode, "1ms")) {
        timer_begin_status = timeBeginPeriod(1);
        if (timer_begin_status != TIMERR_NOERROR) goto cleanup;
        timer_held = 1;
    }
#define REQUIRE_PROBE(expr) do { if (status_failed(#expr, (expr))) goto cleanup; } while (0)
    REQUIRE_PROBE(sr_init(&context));
    drivers = sr_driver_list(context);
    for (i = 0; drivers[i]; i++)
        if (!strcmp(drivers[i]->name, "fnirsi-dla32")) driver = drivers[i];
    if (!driver) goto cleanup;
    REQUIRE_PROBE(sr_driver_init(context, driver));
    devices = sr_driver_scan(driver, NULL);
    if (g_slist_length(devices) != 1) goto cleanup;
    sdi = devices->data;
    channels = sr_dev_inst_channels_get(sdi);
    if (g_slist_length(channels) != 32) goto cleanup;
    REQUIRE_PROBE(sr_dev_open(sdi)); opened = 1;
    for (; channels; channels = channels->next)
        REQUIRE_PROBE(sr_dev_channel_enable(channels->data, TRUE));
    REQUIRE_PROBE(sr_config_set(sdi, NULL, SR_CONF_DATA_SOURCE, g_variant_new_string("Stream")));
    REQUIRE_PROBE(sr_config_set(sdi, NULL, SR_CONF_SAMPLERATE, g_variant_new_uint64(rate)));
    REQUIRE_PROBE(sr_config_set(sdi, NULL, SR_CONF_LIMIT_SAMPLES, g_variant_new_uint64(count)));
    REQUIRE_PROBE(sr_config_set(sdi, NULL, SR_CONF_VOLTAGE_THRESHOLD, g_variant_new("(dd)", 1.6, 1.6)));
    REQUIRE_PROBE(sr_session_new(context, &r.session));
    REQUIRE_PROBE(sr_session_dev_add(r.session, sdi));
    REQUIRE_PROBE(sr_session_datafeed_callback_add(r.session, receive, &r));
    REQUIRE_PROBE(sr_session_stopped_callback_set(r.session, session_stopped, &r));
    r.start_begin_us = g_get_monotonic_time();
    REQUIRE_PROBE(sr_session_start(r.session));
    r.start_end_us = g_get_monotonic_time();
    r.active_started_us = r.start_end_us;
    guard_source = g_timeout_source_new(100);
    g_source_set_callback(guard_source, guard_capture, &r, NULL);
    if (!g_source_attach(guard_source, r.main_context)) goto cleanup;
    r.run_begin_us = g_get_monotonic_time();
    run_status = sr_session_run(r.session);
    r.run_end_us = g_get_monotonic_time();
    failed = !lifecycle_counts_pass(&r, run_status, sr_session_is_running(r.session));
cleanup:
    if (r.session && sr_session_is_running(r.session)) {
        request_capture_stop(&r, "cleanup");
        if (sr_session_is_running(r.session)) failed |= sr_session_run(r.session) != SR_OK;
    }
    if (guard_source) { g_source_destroy(guard_source); g_source_unref(guard_source); }
    if (r.session) { destroy_status = sr_session_destroy(r.session); failed |= destroy_status != SR_OK; }
    if (opened) { close_status = sr_dev_close(sdi); failed |= close_status != SR_OK; }
    g_slist_free(devices);
    if (context) { exit_status = sr_exit(context); failed |= exit_status != SR_OK; }
    if (timer_held) { timer_end_status = timeEndPeriod(1); failed |= timer_end_status != TIMERR_NOERROR; }
    g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    sr_log_callback_set_default();
    if (r.logic) failed |= fclose(r.logic) != 0;
    if (log.file) failed |= fclose(log.file) != 0;
    failed |= log.errors != 0 || log.writes_failed != 0;
    report = exclusive_file(record_path);
    if (report) {
        fprintf(report, "{\"harness\":\"isolated-V6-host-timer-probe\","
            "\"timer_mode\":\"%s\",\"timer_begin_status\":%u,\"timer_end_status\":%u,"
            "\"mode\":\"Stream\",\"samplerate_hz\":%" PRIu64 ",\"requested_samples\":%" PRIu64 ","
            "\"actual_samples\":%" PRIu64 ",\"headers\":%u,\"ends\":%u,\"stopped_callbacks\":%u,"
            "\"run_status\":%d,\"destroy_status\":%d,\"close_status\":%d,\"exit_status\":%d,"
            "\"context_mismatches\":%u,\"invalid_packet\":%d,\"guard_expired\":%d,"
            "\"run_duration_us\":%" PRId64 ",\"lifecycle_count_passed\":%s,"
            "\"physical_integrity_verified\":false,\"driver_changed\":false}\n",
            timer_mode, timer_begin_status, timer_end_status, rate, count, r.samples,
            r.headers, r.ends, r.stopped_callbacks, run_status, destroy_status, close_status,
            exit_status, r.context_mismatches, r.invalid_packet, r.guard_expired,
            r.run_end_us-r.run_begin_us, failed ? "false" : "true");
        failed |= ferror(report) != 0;
        failed |= fclose(report) != 0;
    } else failed = 1;
    printf("%s V6 host timer %s; samples=%" PRIu64 " HEADER=%u END=%u stopped=%u.\n",
        failed ? "FAIL" : "PASS", timer_mode, r.samples, r.headers, r.ends, r.stopped_callbacks);
    g_free(logic_path); g_free(wire_path); g_free(log_path); g_free(record_path);
    return failed ? 1 : 0;
#undef REQUIRE_PROBE
}
