/* SPDX-License-Identifier: GPL-3.0-only */
/* Real DLA-32 lifecycle regression using an already-running external source.
 * Connect ESP32 GPIO32 (100 kHz, 50%) to D0, with a common ground.
 * This program never configures PWM outputs or accesses the ESP32.
 * Period checking tolerates one sample of independent-clock quantization.
 * A periodic signal cannot prove that entire periods were never discarded.
 */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <glib.h>
#include <libsigrok/libsigrok.h>

#define SOURCE_FREQUENCY UINT64_C(100000)
#define CAPTURE_GUARD_MSEC 30000
#define STOP_GRACE_MSEC 10000

struct capture_result {
    struct sr_session *session;
    FILE *file;
    uint64_t samples, edges, periods, bad_periods, bad_widths;
    uint64_t last_rising, last_edge, period_sum, high_sum, low_sum;
    uint64_t period_hist[3], expected_period;
    int64_t started_us;
    unsigned int headers, ends;
    int last_level, have_rising, have_edge;
    int invalid_packet, cancel_requested, guard_expired, stop_error;
};

static int report_status(const char *operation, int status)
{
    if (status == SR_OK)
        return 0;
    fprintf(stderr, "FAIL %s: %d (%s)\n", operation, status,
        sr_strerror(status));
    return 1;
}

static void receive(const struct sr_dev_inst *sdi,
    const struct sr_datafeed_packet *packet, void *data)
{
    struct capture_result *result = data;
    const struct sr_datafeed_logic *logic;
    const uint8_t *bytes;
    uint64_t i, count, position, width, period;
    int level;

    (void)sdi;
    if (packet->type == SR_DF_HEADER) {
        result->headers++;
        return;
    }
    if (packet->type == SR_DF_END) {
        result->ends++;
        return;
    }
    if (packet->type != SR_DF_LOGIC)
        return;
    logic = packet->payload;
    if (result->headers != 1 || result->ends ||
            logic->unitsize != 4 || logic->length % 4) {
        result->invalid_packet = 1;
        return;
    }
    if (result->file && fwrite(logic->data, 1, logic->length,
            result->file) != logic->length)
        result->invalid_packet = 1;
    bytes = logic->data;
    count = logic->length / 4;
    for (i = 0; i < count; i++) {
        position = result->samples + i;
        level = bytes[i * 4] & 1;
        if (result->last_level >= 0 && level != result->last_level) {
            result->edges++;
            if (result->have_edge) {
                width = position - result->last_edge;
                if (result->last_level)
                    result->high_sum += width;
                else
                    result->low_sum += width;
                if (width + 1 < result->expected_period / 2 ||
                        width > result->expected_period / 2 + 1)
                    result->bad_widths++;
            }
            result->last_edge = position;
            result->have_edge = 1;
            if (level) {
                if (result->have_rising) {
                    period = position - result->last_rising;
                    result->periods++;
                    result->period_sum += period;
                    if (period + 1 < result->expected_period ||
                            period > result->expected_period + 1) {
                        if (result->bad_periods < 8)
                            fprintf(stderr, "Irregular D0 period at sample %"
                                PRIu64 ": %" PRIu64 " (nominal %" PRIu64
                                ", tolerance one sample)\n", position,
                                period, result->expected_period);
                        result->bad_periods++;
                    } else {
                        result->period_hist[period + 1 -
                            result->expected_period]++;
                    }
                }
                result->last_rising = position;
                result->have_rising = 1;
            }
        }
        result->last_level = level;
    }
    result->samples += count;
}

static gboolean cancel_capture(void *data)
{
    struct capture_result *result = data;

    result->cancel_requested = 1;
    result->stop_error |= report_status("sr_session_stop(cancel)",
        sr_session_stop(result->session));
    return G_SOURCE_REMOVE;
}

static gboolean guard_capture(void *data)
{
    struct capture_result *result = data;
    int64_t elapsed = g_get_monotonic_time() - result->started_us;

    if (elapsed >= (int64_t)CAPTURE_GUARD_MSEC * 1000 &&
            !result->guard_expired) {
        result->guard_expired = 1;
        fprintf(stderr, "FAIL acquisition exceeded the 30-second guard; "
            "requesting stop.\n");
        result->stop_error |= report_status("sr_session_stop(guard)",
            sr_session_stop(result->session));
    }
    if (elapsed >= (int64_t)(CAPTURE_GUARD_MSEC + STOP_GRACE_MSEC) * 1000) {
        fprintf(stderr, "FAIL stop did not complete within its 10-second "
            "grace period; terminating the hardware test.\n");
        fflush(NULL);
        exit(1);
    }
    return G_SOURCE_CONTINUE;
}

static GSource *attach_timer(GMainContext *main_context, unsigned int msec,
    GSourceFunc callback, struct capture_result *result)
{
    GSource *source = g_timeout_source_new(msec);

    g_source_set_callback(source, callback, result, NULL);
    if (!g_source_attach(source, main_context)) {
        g_source_unref(source);
        return NULL;
    }
    return source;
}

static int run_capture(struct sr_context *context, struct sr_dev_inst *sdi,
    const char *label, const char *mode, uint64_t samplerate,
    uint64_t sample_limit, unsigned int cancel_msec, const char *raw_path)
{
    struct capture_result result = {0};
    GMainContext *main_context = g_main_context_new();
    GSource *guard = NULL, *cancel = NULL;
    double frequency = 0, duty = 0;
    int failed = 1;

    result.last_level = -1;
    result.expected_period = samplerate / SOURCE_FREQUENCY;
    g_main_context_push_thread_default(main_context);
#define REQUIRE_CAPTURE(expr) do { if (report_status(#expr, (expr))) \
    goto cleanup; } while (0)
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_DATA_SOURCE,
        g_variant_new_string(mode)));
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_SAMPLERATE,
        g_variant_new_uint64(samplerate)));
    REQUIRE_CAPTURE(sr_config_set(sdi, NULL, SR_CONF_LIMIT_SAMPLES,
        g_variant_new_uint64(sample_limit)));
    if (raw_path) {
        result.file = fopen(raw_path, "wb");
        if (!result.file) {
            perror(raw_path);
            goto cleanup;
        }
    }
    REQUIRE_CAPTURE(sr_session_new(context, &result.session));
    REQUIRE_CAPTURE(sr_session_dev_add(result.session, sdi));
    REQUIRE_CAPTURE(sr_session_datafeed_callback_add(result.session,
        receive, &result));
    REQUIRE_CAPTURE(sr_session_start(result.session));
    /* Time the active acquisition after synchronous setup/draining and ARM. */
    result.started_us = g_get_monotonic_time();
    guard = attach_timer(main_context, 100, guard_capture, &result);
    if (!guard) {
        fprintf(stderr, "FAIL could not attach acquisition guard.\n");
        goto cleanup;
    }
    if (cancel_msec) {
        cancel = attach_timer(main_context, cancel_msec, cancel_capture,
            &result);
        if (!cancel) {
            fprintf(stderr, "FAIL could not attach cancellation timer.\n");
            goto cleanup;
        }
    }
    REQUIRE_CAPTURE(sr_session_run(result.session));
    if (result.period_sum)
        frequency = (double)samplerate * result.periods / result.period_sum;
    if (result.high_sum + result.low_sum)
        duty = 100.0 * result.high_sum /
            (result.high_sum + result.low_sum);
    printf("%s: mode=%s rate=%" PRIu64 " samples=%" PRIu64
        "/%" PRIu64 " HEADER=%u END=%u active=%.3fs cancel=%d guard=%d\n",
        label, mode, samplerate, result.samples, sample_limit,
        result.headers, result.ends,
        (g_get_monotonic_time() - result.started_us) / 1000000.0,
        result.cancel_requested, result.guard_expired);
    if (!cancel_msec)
        printf("  D0 periods=%" PRIu64 " histogram[%" PRIu64
            ",%" PRIu64 ",%" PRIu64 "]=[%" PRIu64 ",%" PRIu64
            ",%" PRIu64 "] irregular_periods=%" PRIu64
            " irregular_widths=%" PRIu64 " frequency=%.3fHz duty=%.4f%%\n",
            result.periods, result.expected_period - 1,
            result.expected_period, result.expected_period + 1,
            result.period_hist[0], result.period_hist[1],
            result.period_hist[2], result.bad_periods, result.bad_widths,
            frequency, duty);
    failed = result.headers != 1 || result.ends != 1 ||
        result.invalid_packet || result.guard_expired || result.stop_error ||
        sr_session_is_running(result.session) != FALSE;
    if (cancel_msec) {
        failed |= !result.cancel_requested || result.samples >= sample_limit;
    } else {
        failed |= result.samples != sample_limit || result.periods < 8 ||
            result.bad_periods || result.bad_widths ||
            frequency < SOURCE_FREQUENCY * 0.995 ||
            frequency > SOURCE_FREQUENCY * 1.005 ||
            duty < 49.5 || duty > 50.5;
    }
cleanup:
    if (result.session && sr_session_is_running(result.session) == TRUE) {
        failed |= report_status("sr_session_stop(cleanup)",
            sr_session_stop(result.session));
        failed |= report_status("sr_session_run(cleanup)",
            sr_session_run(result.session));
    }
    if (cancel) {
        g_source_destroy(cancel);
        g_source_unref(cancel);
    }
    if (guard) {
        g_source_destroy(guard);
        g_source_unref(guard);
    }
    if (result.file && fclose(result.file)) {
        perror("fclose(raw capture)");
        failed = 1;
    }
    if (result.session)
        failed |= report_status("sr_session_destroy",
            sr_session_destroy(result.session));
    g_main_context_pop_thread_default(main_context);
    g_main_context_unref(main_context);
    printf("%s %s\n", failed ? "FAIL" : "PASS", label);
    fflush(stdout);
    return failed;
#undef REQUIRE_CAPTURE
}

int main(int argc, char **argv)
{
    struct sr_context *context = NULL;
    struct sr_dev_driver **drivers, *driver = NULL;
    struct sr_dev_inst *sdi = NULL;
    GSList *devices = NULL, *channels;
    const char *long_raw_path = NULL;
    char label[64];
    int opened = 0, failed = 1, i;

    if (argc != 1 && (argc != 3 || strcmp(argv[1], "--long-buffer"))) {
        fprintf(stderr, "Usage: test_dla32_reliability "
            "[--long-buffer OUTPUT.bin]\n"
            "Requires external 100 kHz/50%% PWM on D0 and a common ground.\n"
            "Runs nine lifecycle captures; optional tenth is a 10-second "
            "1 MHz Buffer capture. No PWM output settings are changed.\n");
        return 2;
    }
    if (argc == 3)
        long_raw_path = argv[2];
    sr_log_loglevel_set(SR_LOG_INFO);
#define REQUIRE_MAIN(expr) do { if (report_status(#expr, (expr))) \
    goto cleanup; } while (0)
    REQUIRE_MAIN(sr_init(&context));
    drivers = sr_driver_list(context);
    for (i = 0; drivers[i]; i++)
        if (!strcmp(drivers[i]->name, "fnirsi-dla32"))
            driver = drivers[i];
    if (!driver) {
        fprintf(stderr, "FAIL FNIRSI DLA-32 driver absent.\n");
        goto cleanup;
    }
    REQUIRE_MAIN(sr_driver_init(context, driver));
    devices = sr_driver_scan(driver, NULL);
    if (g_slist_length(devices) != 1) {
        fprintf(stderr, "FAIL expected exactly one confirmed DLA-32.\n");
        goto cleanup;
    }
    sdi = devices->data;
    if (g_slist_length(sr_dev_inst_channels_get(sdi)) != 32) {
        fprintf(stderr, "FAIL expected 32 logic channels.\n");
        goto cleanup;
    }
    printf("Device: %s %s; %s\n", sr_dev_inst_vendor_get(sdi),
        sr_dev_inst_model_get(sdi), sr_dev_inst_connid_get(sdi));
    REQUIRE_MAIN(sr_dev_open(sdi));
    opened = 1;
    for (channels = sr_dev_inst_channels_get(sdi); channels;
            channels = channels->next)
        REQUIRE_MAIN(sr_dev_channel_enable(channels->data, TRUE));
    REQUIRE_MAIN(sr_config_set(sdi, NULL, SR_CONF_VOLTAGE_THRESHOLD,
        g_variant_new("(dd)", 1.6, 1.6)));
    puts("External PWM remains active; all 32 inputs enabled, threshold 1.6 V.");
    for (i = 1; i <= 3; i++) {
        snprintf(label, sizeof(label), "Buffer repeated capture %d", i);
        if (run_capture(context, sdi, label, "Buffer", 50000000,
                50000, 0, NULL))
            goto cleanup;
    }
    for (i = 1; i <= 3; i++) {
        snprintf(label, sizeof(label), "Stream repeated capture %d", i);
        if (run_capture(context, sdi, label, "Stream", 50000000,
                500000, 0, NULL))
            goto cleanup;
    }
    if (run_capture(context, sdi, "Cancel armed 10-second Buffer", "Buffer",
            1000000, 10000000, 100, NULL))
        goto cleanup;
    if (run_capture(context, sdi, "Buffer recovery after cancellation",
            "Buffer", 50000000, 50000, 0, NULL))
        goto cleanup;
    if (run_capture(context, sdi, "Stream recovery after cancellation",
            "Stream", 50000000, 500000, 0, NULL))
        goto cleanup;
    if (long_raw_path && run_capture(context, sdi,
            "10-second Buffer watchdog regression", "Buffer", 1000000,
            10000000, 0, long_raw_path))
        goto cleanup;
    failed = 0;
cleanup:
    if (opened)
        failed |= report_status("sr_dev_close", sr_dev_close(sdi));
    g_slist_free(devices);
    if (context)
        failed |= report_status("sr_exit", sr_exit(context));
    if (!failed)
        puts("PASS repeated captures, cancellation, recovery, session cleanup "
            "and device/context cleanup. External PWM unchanged.\n"
            "PWM continuity checks do not establish absolute clock accuracy "
            "or lossless long Stream transfer.");
    return failed ? 1 : 0;
#undef REQUIRE_MAIN
}
