/* V4 interaction tests execute the real V3 API with the new timeout helper. */
static void v4_timeout_mode(gboolean legacy)
{
    if (legacy) wch_api.timeout_ex = NULL;
}
static void v4_expect(const char *label, gboolean condition)
{
    char text[240];
    snprintf(text, sizeof(text), "UPLOAD V4 %s: %s", wch_api.timeout_ex ? "Ex" : "legacy", label);
    expect(text, condition);
}
static void test_v4_prearm_setter_failure(gboolean legacy)
{
    struct fixture f;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_all = TRUE;
    v4_expect("persistent prearm write setter failure blocks every command/read and retains STOP",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && timeout_calls == 2
        && !sdk_write_calls && !read_calls && !headers && !ends
        && !commands[0x11] && !commands[0x12] && !commands[0x15]
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty
        && !has_application_resources(f.devc));
    v4_expect("prearm pending STOP blocks new capture and context clear",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && f.sdi->priv == f.devc && !sdk_write_calls && !read_calls);
    v4_expect("failed prearm close retry preserves ACTIVE/handle without I/O",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->upload_stop_pending && !close_calls
        && !sdk_write_calls && !read_calls);
    timeout_fail_all = FALSE;
    v4_expect("inactive public STOP retry restores1000 then STOP and two20ms drain reads",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f)
        && commands[0x15] == 1 && read_calls == 2 && !close_calls
        && timeout_last_write_ms == 1000 && timeout_last_read_ms == 20
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_nth = 1;
    v4_expect("one prearm setter failure still returns IO after successful rollback STOP/drain",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && commands[0x15] == 1
        && sdk_write_calls == 1 && read_calls == 2 && resources_clean(&f)
        && !commands[0x11] && !commands[0x12] && !headers && !ends
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
static void test_v4_terminal_setter_failure(gboolean legacy)
{
    struct fixture f;
    unsigned int writes, reads, stops;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("terminal setter failure capture starts", start(&f));
    writes = sdk_write_calls; reads = read_calls; stops = commands[0x15];
    timeout_fail_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    v4_expect("disable succeeds but false STOP setter blocks WriteData/drain and preserves pending across END",
        !sdk_mock_enabled && !f.devc->wch.upload_dirty && f.devc->upload_stop_pending
        && ends == 1 && !has_application_resources(f.devc) && !resources_clean(&f)
        && sdk_write_calls == writes && read_calls == (int)reads && commands[0x15] == stops);
    v4_expect("terminal pending state blocks start/devclear and failed inactive retry sends no I/O",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO && f.devc->upload_stop_pending
        && !f.devc->wch.upload_dirty && sdk_write_calls == writes && read_calls == (int)reads);
    v4_expect("failed close timeout setter preserves ACTIVE handle/context and no reads",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.sdi->priv == f.devc && !close_calls
        && f.devc->upload_stop_pending && sdk_write_calls == writes && read_calls == (int)reads);
    timeout_fail_all = FALSE;
    v4_expect("close retry restores1000 STOP then20ms drain and releases handle exactly once",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && !f.devc->wch.opened
        && close_calls == 1 && sdk_write_calls == writes + 1 && commands[0x15] == stops + 1
        && read_calls == (int)reads + 2 && timeout_last_write_ms == 1000
        && timeout_last_read_ms == 20 && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
static void test_v4_arm_setter_failure(gboolean legacy)
{
    struct fixture f;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_after_clear = TRUE;
    v4_expect("ARM setter failure after enable/Clear sends no ARM and returns IO after disable/STOP cleanup",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && sdk_enable_calls == 1
        && sdk_clear_calls == 1 && sdk_disable_calls == 2 && !commands[0x12]
        && commands[0x11] == 1 && commands[0x15] == 2 && headers == 1 && ends == 1
        && !sdk_mock_enabled && resources_clean(&f) && timeout_last_write_ms == 1000
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
static void test_v4_read_setter_failure(gboolean legacy)
{
    struct fixture f;
    unsigned int before, writes;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy); timeout_fail_nth = 2;
    v4_expect("prearm20ms setter failure calls no ReadEndP and rejects before SETUP/HEADER/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !read_calls && commands[0x15] == 1
        && !commands[0x11] && !commands[0x12] && !headers && !ends
        && resources_clean(&f) && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("sample setter failure fixture starts", start(&f));
    before = sdk_event_count; writes = sdk_write_calls;
    timeout_fail_nth = timeout_calls + 1; feed(&f, 32);
    v4_expect("false sample setter emits no data/read; cleanup restores1000 before disable/STOP/drain",
        !accepted && !delivered && ends == 1 && resources_clean(&f)
        && sdk_events[before] == SDK_DISABLE && sdk_events[before+1] == SDK_STOP
        && sdk_write_calls == writes + 1 && timeout_last_write_ms == 1000
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("real sample read error fixture starts", start(&f));
    read_fail_call = read_calls + 1; feed(&f, 32);
    v4_expect("SDK read failure preserves actual0/no stale LOGIC and disables before successful STOP",
        !accepted && !delivered && ends == 1 && resources_clean(&f)
        && !sdk_mock_enabled && commands[0x15] == 2 && timeout_last_write_ms == 1000
        && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);

    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    v4_expect("poststop drain setter failure fixture starts", start(&f));
    before = read_calls; timeout_fail_nth = timeout_calls + 2;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    v4_expect("false postdrain setter calls no ReadEndP; existing void-drain cleanup remains warning-only",
        read_calls == (int)before && ends == 1 && resources_clean(&f)
        && commands[0x15] == 2 && !timeout_io_without_success);
    /* This resources_clean result is not a full capture PASS: the logged
     * setter/error/quiet failure must be rejected by the log/outer gates. */
    fixture_clear(&f);
}
static void test_v4_timeout_exit_retry(gboolean legacy)
{
    struct fixture f;
    struct sr_context *context = NULL;
    libusb_context *saved;
    int ret;
    fixture_init(&f, TRUE); v4_timeout_mode(legacy);
    ret = sr_init(&context);
    v4_expect("timeout ownership exit test initializes context without hardware scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(context->driver_list); context->driver_list = g_new0(struct sr_dev_driver *, 2);
    context->driver_list[0] = &f.driver; f.drvc->sr_ctx = context; saved = context->libusb_ctx;
    v4_expect("timeout ownership exit fake capture starts", start(&f));
    timeout_fail_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    v4_expect("std_cleanup refuses pending STOP after setter failure without freeing context",
        std_cleanup(&f.driver) != SR_OK && f.driver.context == f.drvc
        && f.sdi->priv == f.devc && f.devc->upload_stop_pending && !close_calls);
    ret = sr_exit(context);
    v4_expect("sr_exit preserves USB/private/pending context on timeout-induced retirement refusal",
        ret != SR_OK && context->libusb_ctx == saved && f.driver.context == f.drvc
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty && !close_calls);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    timeout_fail_all = FALSE;
    v4_expect("explicit close recovers timeout-induced STOP and releases handle once",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && close_calls == 1
        && !timeout_bad_arguments && !timeout_io_without_success);
    ret = sr_exit(context);
    v4_expect("sr_exit retry releases remaining device/context once after explicit close recovery",
        ret == SR_OK && !f.driver.context && !f.session.devs && close_calls == 1);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}
static void test_v4_legacy_export_fallback(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); memset(&wch_api, 0, sizeof(wch_api));
    sdk_missing_export = "CH375SetTimeoutEx";
    expect("UPLOAD V4: missing optional TimeoutEx selects legacy timeout while required upload exports remain",
        dla_wch_load() && !wch_api.timeout_ex && wch_api.timeout
        && wch_api.set_buf_upload_ex && wch_api.clear_buf_upload);
    sdk_missing_export = NULL;
    v4_expect("legacy fallback capture completes with uniform deadlines and unchanged SDK lifecycle", start(&f));
    feed(&f, 200000);
    v4_expect("legacy fallback exact count/END and no unprepared SDK I/O", delivered == 50000
        && ends == 1 && resources_clean(&f) && !timeout_bad_arguments && !timeout_io_without_success);
    fixture_clear(&f);
}
