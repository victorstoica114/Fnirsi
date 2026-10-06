/* SPDX-License-Identifier: GPL-3.0-only */
/* Bounded continuous-mode probe. No timer-resolution, firmware, generator or
 * device-reset operations. Original RAW/LOGIC/prefetch files are separate. */
#define main frozen_cancel_harness_main
#include "test_dla32_stop_worker_v7_cancel_r2.c"
#undef main

int main(int argc, char **argv)
{
    struct sr_context *context = NULL;
    struct sr_dev_driver **drivers, *driver = NULL;
    struct sr_dev_inst *sdi = NULL;
    GSList *devices = NULL, *channels;
    struct capture_result r = {0};
    struct log_result log = {0};
    struct sr_trigger *trigger = NULL;
    struct sr_trigger_stage *stage;
    GSource *guard = NULL, *cancel = NULL;
    FILE *report = NULL;
    char *logic_path = NULL, *wire_path = NULL, *log_path = NULL, *record_path = NULL;
    uint64_t rate;
    unsigned int i;
    int failed = 1, opened = 0, close_status = SR_ERR, exit_status = SR_ERR,
        destroy_status = SR_ERR, run_status = SR_ERR;
    if (argc == 2 && !strcmp(argv[1], "--self-test")) return self_test();
    if (argc != 5 || (strcmp(argv[4], "25000000") && strcmp(argv[4], "50000000")) ||
        !valid_sha256(argv[3]) || verify_diagnostic_dll(argv[3]) ||
        !fresh_workspace_output(argv[1], argv[2])) {
        fprintf(stderr, "Usage: continuous WORKSPACE NEW_DIRECTORY DLL_SHA256 25000000|50000000\n");
        return 2;
    }
    rate = !strcmp(argv[4], "25000000") ? UINT64_C(25000000) : UINT64_C(50000000);
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
    r.cancel_test = TRUE;
    r.configured_limit = UINT64_MAX; /* Only the host timer stops this probe. */
    sr_log_loglevel_set(SR_LOG_DBG);
    sr_log_callback_set(save_log, &log);
    g_setenv("FNIRSI_DLA32_WIRE_TRACE", wire_path, TRUE);
#define REQUIRE_LONG(expr) do { if (status_failed(#expr, (expr))) goto cleanup; } while (0)
    REQUIRE_LONG(sr_init(&context));
    drivers = sr_driver_list(context);
    for (i = 0; drivers[i]; i++)
        if (!strcmp(drivers[i]->name, "fnirsi-dla32")) driver = drivers[i];
    if (!driver) goto cleanup;
    REQUIRE_LONG(sr_driver_init(context, driver));
    devices = sr_driver_scan(driver, NULL);
    if (g_slist_length(devices) != 1) goto cleanup;
    sdi = devices->data;
    channels = sr_dev_inst_channels_get(sdi);
    if (g_slist_length(channels) != 32) goto cleanup;
    REQUIRE_LONG(sr_dev_open(sdi)); opened = 1;
    for (; channels; channels = channels->next)
        REQUIRE_LONG(sr_dev_channel_enable(channels->data, TRUE));
    REQUIRE_LONG(sr_config_set(sdi, NULL, SR_CONF_DATA_SOURCE, g_variant_new_string("Stream")));
    REQUIRE_LONG(sr_config_set(sdi, NULL, SR_CONF_SAMPLERATE, g_variant_new_uint64(rate)));
    REQUIRE_LONG(sr_config_set(sdi, NULL, SR_CONF_CONTINUOUS, g_variant_new_boolean(TRUE)));
    REQUIRE_LONG(sr_config_set(sdi, NULL, SR_CONF_VOLTAGE_THRESHOLD, g_variant_new("(dd)", 1.6, 1.6)));
    REQUIRE_LONG(sr_session_new(context, &r.session));
    REQUIRE_LONG(sr_session_dev_add(r.session, sdi));
    REQUIRE_LONG(sr_session_datafeed_callback_add(r.session, receive, &r));
    REQUIRE_LONG(sr_session_stopped_callback_set(r.session, session_stopped, &r));
    trigger = sr_trigger_new("D0 rising; raw position 500");
    if (!trigger) goto cleanup;
    stage = sr_trigger_stage_add(trigger);
    if (!stage) goto cleanup;
    REQUIRE_LONG(sr_trigger_match_add(stage, sr_dev_inst_channels_get(sdi)->data, SR_TRIGGER_RISING, 0));
    REQUIRE_LONG(sr_session_trigger_set(r.session, trigger));
    r.start_begin_us = g_get_monotonic_time();
    REQUIRE_LONG(sr_session_start(r.session));
    r.start_end_us = g_get_monotonic_time();
    r.active_started_us = r.start_end_us;
    guard = g_timeout_source_new(100);
    g_source_set_callback(guard, guard_capture, &r, NULL);
    cancel = g_timeout_source_new(12000);
    g_source_set_callback(cancel, cancel_capture, &r, NULL);
    if (!g_source_attach(guard, r.main_context) || !g_source_attach(cancel, r.main_context)) goto cleanup;
    r.run_begin_us = g_get_monotonic_time();
    run_status = sr_session_run(r.session);
    r.run_end_us = g_get_monotonic_time();
    failed = !lifecycle_counts_pass(&r, run_status, sr_session_is_running(r.session));
cleanup:
    if (r.session && sr_session_is_running(r.session)) {
        request_capture_stop(&r, "cleanup");
        if (sr_session_is_running(r.session)) failed |= sr_session_run(r.session) != SR_OK;
    }
    if (cancel) { g_source_destroy(cancel); g_source_unref(cancel); }
    if (guard) { g_source_destroy(guard); g_source_unref(guard); }
    if (r.session) { destroy_status = sr_session_destroy(r.session); failed |= destroy_status != SR_OK; }
    if (trigger) sr_trigger_free(trigger);
    if (opened) { close_status = sr_dev_close(sdi); failed |= close_status != SR_OK; }
    g_slist_free(devices);
    if (context) { exit_status = sr_exit(context); failed |= exit_status != SR_OK; }
    g_unsetenv("FNIRSI_DLA32_WIRE_TRACE");
    sr_log_callback_set_default();
    if (r.logic) failed |= fclose(r.logic) != 0;
    if (log.file) failed |= fclose(log.file) != 0;
    failed |= log.errors != 0 || log.writes_failed != 0;
    report = exclusive_file(record_path);
    if (report) {
        fprintf(report, "{\"harness\":\"V7-continuous-12s\",\"continuous\":true,"
            "\"mode\":\"Stream\",\"samplerate_hz\":%" PRIu64 ",\"host_timer_ms\":12000,"
            "\"actual_samples\":%" PRIu64 ",\"headers\":%u,\"ends\":%u,\"stopped_callbacks\":%u,"
            "\"run_status\":%d,\"destroy_status\":%d,\"close_status\":%d,\"exit_status\":%d,"
            "\"logic_after_stop\":%u,\"context_mismatches\":%u,\"invalid_packet\":%d,\"guard_expired\":%d,"
            "\"run_duration_us\":%" PRId64 ",\"stop_call_duration_us\":%" PRId64 ","
            "\"stop_to_END_us\":%" PRId64 ",\"stop_reason\":\"%s\",\"lifecycle_passed\":%s,"
            "\"physical_integrity_verified\":false,\"timer_resolution_changed\":false}\n",
            rate, r.samples, r.headers, r.ends, r.stopped_callbacks, run_status, destroy_status,
            close_status, exit_status, r.logic_after_stop, r.context_mismatches, r.invalid_packet,
            r.guard_expired, r.run_end_us-r.run_begin_us, r.stop_end_us-r.stop_begin_us,
            r.end_us-r.stop_begin_us, r.stop_reason, failed ? "false" : "true");
        failed |= ferror(report) != 0;
        failed |= fclose(report) != 0;
    } else failed = 1;
    printf("%s V7 continuous 12s @%" PRIu64 "; samples=%" PRIu64 " HEADER=%u END=%u stopped=%u.\n",
        failed ? "FAIL" : "PASS", rate, r.samples, r.headers, r.ends, r.stopped_callbacks);
    g_free(logic_path); g_free(wire_path); g_free(log_path); g_free(record_path);
    return failed ? 1 : 0;
#undef REQUIRE_LONG
}
