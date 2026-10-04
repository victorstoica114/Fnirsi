/* Additional SDK0 checks use the real V5 API and real transport helpers. */
static void sdk0_mode(gboolean legacy) { if (legacy) wch_api.timeout_ex = NULL; }
static void sdk0_check(const char *name, gboolean value)
{
    char label[240]; snprintf(label, sizeof(label), "SDK0 V5 %s: %s", wch_api.timeout_ex ? "Ex" : "legacy", name);
    expect(label, value);
}
static void sdk0_config_guard(struct fixture *f)
{
    uint64_t saved_rate = f->devc->samplerate, saved_limit = f->devc->limits.limit_samples;
    gboolean saved_stream = f->devc->stream; unsigned int saved_threshold = f->devc->threshold;
    struct pwm_setting saved_pwm = f->devc->pwm[0]; unsigned int writes = sdk_write_calls;
    struct sr_channel_group group = { .name = "PWM0" };
    GSList *saved_groups = f->sdi->channel_groups;
    GVariant *rate = g_variant_ref_sink(g_variant_new_uint64(SR_MHZ(100)));
    GVariant *limit = g_variant_ref_sink(g_variant_new_uint64(100));
    GVariant *threshold = g_variant_ref_sink(g_variant_new("(dd)", 2.5, 2.5));
    GVariant *mode = g_variant_ref_sink(g_variant_new_string(saved_stream ? "Buffer" : "Stream"));
    GVariant *frequency = g_variant_ref_sink(g_variant_new_double(2000000));
    GVariant *duty = g_variant_ref_sink(g_variant_new_double(60));
    GVariant *enable = g_variant_ref_sink(g_variant_new_boolean(TRUE));
    f->sdi->channel_groups = g_slist_append(NULL, &group);
    sdk0_check("pending STOP rejects rate/count/threshold/mode configuration before cache mutation",
        sr_config_set(f->sdi, NULL, SR_CONF_SAMPLERATE, rate) != SR_OK &&
        sr_config_set(f->sdi, NULL, SR_CONF_LIMIT_SAMPLES, limit) != SR_OK &&
        sr_config_set(f->sdi, NULL, SR_CONF_VOLTAGE_THRESHOLD, threshold) != SR_OK &&
        sr_config_set(f->sdi, NULL, SR_CONF_DEVICE_MODE, mode) != SR_OK &&
        f->devc->samplerate == saved_rate && f->devc->limits.limit_samples == saved_limit &&
        f->devc->threshold == saved_threshold && f->devc->stream == saved_stream);
    sdk0_check("pending STOP rejects PWM frequency/duty/enable without packets or cached changes",
        sr_config_set(f->sdi, &group, SR_CONF_OUTPUT_FREQUENCY, frequency) != SR_OK &&
        sr_config_set(f->sdi, &group, SR_CONF_DUTY_CYCLE, duty) != SR_OK &&
        sr_config_set(f->sdi, &group, SR_CONF_ENABLED, enable) != SR_OK &&
        !memcmp(&saved_pwm, &f->devc->pwm[0], sizeof(saved_pwm)) && sdk_write_calls == writes);
    g_slist_free(f->sdi->channel_groups); f->sdi->channel_groups = saved_groups;
    g_variant_unref(rate); g_variant_unref(limit); g_variant_unref(threshold); g_variant_unref(mode);
    g_variant_unref(frequency); g_variant_unref(duty); g_variant_unref(enable);
}
static void test_sdk0_v5_prearm_failure(gboolean legacy)
{
    struct fixture f; unsigned int reads;
    fixture_init(&f, TRUE); sdk0_mode(legacy); fail_opcode = 0x15; opcode_failures = 20;
    sdk0_check("persistent initial STOP failure rejects before any SETUP/ARM/HEADER/read",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && commands[0x15] == 2 &&
        !commands[0x11] && !commands[0x12] && !headers && !ends && !read_calls &&
        f.devc->upload_stop_pending && !has_application_resources(f.devc));
    reads = read_calls; sdk0_config_guard(&f);
    sdk0_check("initial pending STOP refuses start/clear/inactive-stop/close without free or drain",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK &&
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO && sr_dev_close(f.sdi) == SR_ERR_IO &&
        f.sdi->status == SR_ST_ACTIVE && f.devc->wch.opened && f.devc->upload_stop_pending &&
        !close_calls && read_calls == (int)reads);
    opcode_failures = 0;
    sdk0_check("initial inactive-stop recovery accepts STOP then two20ms reads and restores ownership",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f) && read_calls == (int)reads + 2 &&
        sdk_last_write_ms == 1000 && sdk_last_read_ms == 20 && !sdk_unprepared_io && !sdk_timeout_bad);
    sdk0_check("positive capture can start after initial pending recovery", start(&f)); feed(&f, 200000);
    sdk0_check("recovered capture has exact data/END and no SDK upload dependency",
        delivered == 50000 && headers == 1 && ends == 1 && resources_clean(&f) && !sdk_unknown_exports);
    fixture_clear(&f);
}
static void test_sdk0_v5_terminal_failure(gboolean legacy, gboolean short_write)
{
    struct fixture f; unsigned int reads, stops;
    fixture_init(&f, TRUE); sdk0_mode(legacy);
    sdk0_check("terminal STOP fixture begins", start(&f)); feed(&f, 32);
    reads = read_calls; stops = commands[0x15];
    if (short_write) sdk_short_stop_count = 20; else { fail_opcode = 0x15; opcode_failures = 20; }
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    sdk0_check(short_write ? "short successful STOP still retains pending beyond END" : "false STOP retains pending beyond END",
        delivered == 8 && ends == 1 && !has_application_resources(f.devc) && f.devc->upload_stop_pending &&
        !resources_clean(&f) && read_calls == (int)reads && commands[0x15] == stops + 1);
    sdk0_config_guard(&f);
    sdk0_check("terminal pending refuses new capture/clear and failed close retains ACTIVE handle",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK &&
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE && f.devc->wch.opened &&
        !close_calls && read_calls == (int)reads);
    opcode_failures = sdk_short_stop_count = 0;
    sdk0_check("public inactive-stop recovery writes exactly one accepted STOP then drains two reads",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && commands[0x15] == stops + 3 &&
        read_calls == (int)reads + 2 && resources_clean(&f) && !close_calls);
    sdk0_check("capture after terminal recovery starts", start(&f)); feed(&f, 200000);
    sdk0_check("capture after terminal recovery has exact fresh accepted sample count", delivered == 50008 && ends == 2 && resources_clean(&f));
    sdk0_check("normal close releases once after pending recovered", sr_dev_close(f.sdi) == SR_OK && close_calls == 1);
    fixture_clear(&f);
}
static void test_sdk0_v5_timeout_failure(gboolean legacy)
{
    struct fixture f; unsigned int writes, reads;
    fixture_init(&f, TRUE); sdk0_mode(legacy); sdk_timeout_fail_all = TRUE;
    sdk0_check("false prearm timeout setter blocks all WriteData/ReadEndP with pending retained",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !sdk_write_calls && !sdk_read_calls &&
        !commands[0x15] && !headers && !ends && f.devc->upload_stop_pending);
    sdk_timeout_fail_all = FALSE;
    sdk0_check("prearm timeout failure close retry restores1000 before STOP and releases once",
        sr_dev_close(f.sdi) == SR_OK && commands[0x15] == 1 && read_calls == 2 && close_calls == 1 &&
        sdk_last_write_ms == 1000 && sdk_last_read_ms == 20 && !sdk_unprepared_io && !sdk_timeout_bad);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk0_mode(legacy); sdk0_check("terminal setter-failure capture starts", start(&f));
    writes = sdk_write_calls; reads = read_calls; sdk_timeout_fail_all = TRUE;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    sdk0_check("failed terminal setter retains pending without stale USB calls despite END",
        sdk_write_calls == writes && read_calls == (int)reads && f.devc->upload_stop_pending && ends == 1);
    sdk0_config_guard(&f); sdk_timeout_fail_all = FALSE;
    sdk0_check("terminal setter close retry accepts STOP/drains/releases exactly once",
        sr_dev_close(f.sdi) == SR_OK && sdk_write_calls == writes + 1 && read_calls == (int)reads + 2 &&
        close_calls == 1 && resources_clean(&f) && !sdk_unprepared_io && !sdk_timeout_bad);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk0_mode(legacy); sdk_timeout_fail_nth = 2;
    sdk0_check("false read-timeout setter leaves actual0 and calls no ReadEndP or decoder",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && sdk_read_calls == 0 && !accepted && !delivered &&
        !headers && !ends && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    fixture_clear(&f);
}
static void test_sdk0_v5_rollback_failure(gboolean legacy, gboolean arm)
{
    struct fixture f; unsigned int reads;
    fixture_init(&f, TRUE); sdk0_mode(legacy);
    if (arm) { fail_opcode = 0x12; opcode_failures = 1; } else header_result = SR_ERR;
    /* Fail rollback STOP only after HEADER was attempted, independently of
     * the HEADER/ARM fault; the fake SDK writer performs that injection. */
    sdk_rollback_stop_fail = TRUE;
    sdk0_check(arm ? "ARM failure plus rollback STOP failure returns IO and retains pending" : "HEADER failure plus rollback STOP failure returns IO and retains pending",
        sr_dev_acquisition_start(f.sdi) != SR_OK && f.devc->upload_stop_pending &&
        !has_application_resources(f.devc) && !resources_clean(&f) && header_attempts == 1 &&
        ends == headers);
    reads = read_calls; sdk0_config_guard(&f);
    sdk0_check("rollback pending blocks retry/clear without transport reads",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK && read_calls == (int)reads);
    opcode_failures = 0; sdk_rollback_stop_fail = FALSE; header_result = SR_OK;
    sdk0_check("rollback explicit STOP recovery accepts one command and drains before a new capture",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f) && read_calls == (int)reads + 2);
    sdk0_check("new capture after rollback recovery starts", start(&f)); feed(&f, 200000);
    sdk0_check("new capture after rollback contains exact fresh data", delivered == 50000 && resources_clean(&f));
    fixture_clear(&f);
}
static void test_sdk0_v5_exit_retry(gboolean legacy)
{
    struct fixture f; struct sr_context *ctx = NULL; libusb_context *saved; int ret;
    fixture_init(&f, TRUE); sdk0_mode(legacy); ret = sr_init(&ctx);
    sdk0_check("pending-exit fixture initializes library context without device scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(ctx->driver_list); ctx->driver_list = g_new0(struct sr_dev_driver *, 2);
    ctx->driver_list[0] = &f.driver; f.drvc->sr_ctx = ctx; saved = ctx->libusb_ctx;
    sdk0_check("pending-exit fake capture starts", start(&f));
    fail_opcode = 0x15; opcode_failures = 20; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    ret = sr_exit(ctx);
    sdk0_check("failed sr_exit retains USB/private/pending ownership after END and no handle free",
        ret != SR_OK && ctx->libusb_ctx == saved && f.driver.context == f.drvc &&
        f.sdi->priv == f.devc && f.devc->upload_stop_pending && !close_calls && !sources && ends == 1);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    opcode_failures = 0;
    sdk0_check("explicit close retries pending STOP and releases handle once before sr_exit retry",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && close_calls == 1);
    ret = sr_exit(ctx);
    sdk0_check("sr_exit retry frees remaining context without duplicate handle close",
        ret == SR_OK && !f.driver.context && !f.session.devs && close_calls == 1);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}
static void test_sdk0_v5_legacy_binding(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); memset(&wch_api, 0, sizeof(wch_api)); sdk_missing_export = "CH375SetTimeoutEx";
    expect("SDK0 V5 real export loader selects legacy fallback without upload dependencies",
        dla_wch_load() && !wch_api.timeout_ex && wch_api.timeout && !sdk_unknown_exports);
    sdk_missing_export = NULL; expect("SDK0 V5 legacy-bound positive capture starts", start(&f)); feed(&f, 200000);
    expect("SDK0 V5 legacy-bound positive capture completes with correct setter/IO ordering",
        delivered == 50000 && ends == 1 && resources_clean(&f) && !sdk_timeout_bad && !sdk_unprepared_io && !sdk_unknown_exports);
    fixture_clear(&f);
}
