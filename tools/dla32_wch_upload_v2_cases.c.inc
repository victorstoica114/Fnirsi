static gboolean sdk_sequence(const int *expected, unsigned int count)
{ return sdk_event_count == count && !memcmp(sdk_events, expected, count * sizeof(*expected)); }

static void test_upload_normal_and_repeat(void)
{
    struct fixture f;
    const int start_events[] = {SDK_STOP, SDK_DISABLE, SDK_READ,
        SDK_SETUP, SDK_ENABLE, SDK_CLEAR, SDK_ARM};
    const int complete_events[] = {SDK_STOP, SDK_DISABLE, SDK_READ,
        SDK_SETUP, SDK_ENABLE, SDK_CLEAR, SDK_ARM, SDK_READ,
        SDK_STOP, SDK_DISABLE, SDK_READ, SDK_READ};
    fixture_init(&f, TRUE); f.devc->stream = TRUE; f.devc->limits.limit_samples = 8;
    expect("UPLOAD V2: starts with active dirty SDK ownership", start(&f)
        && f.devc->wch.upload_dirty && f.devc->wch.upload_enabled && sdk_mock_enabled);
    expect("UPLOAD V2: exact STOP-disable-drain-SETUP-enable-clear-ARM order",
        sdk_sequence(start_events, ARRAY_SIZE(start_events)));
    expect("UPLOAD V2: SDK argument widths and pipe1/1MiB tuple", !sdk_bad_arguments);
    feed(&f, 32);
    expect("UPLOAD V2: normal completion delivers8 and retires SDK/application resources",
        accepted == 8 && ends == 1 && resources_clean(&f)
        && !f.devc->wch.upload_enabled && !sdk_mock_enabled);
    expect("UPLOAD V2: exact read-STOP-disable-poststopdrain without disabled clear",
        sdk_sequence(complete_events, ARRAY_SIZE(complete_events)));
    expect("UPLOAD V2: repeated capture starts cleanly", start(&f) && sdk_enable_calls == 2
        && f.devc->wch.upload_dirty && f.devc->wch.upload_enabled);
    feed(&f, 32);
    expect("UPLOAD V2: repeated capture stops without queue ownership leak", ends == 2
        && resources_clean(&f) && sdk_disable_calls == 4 && sdk_clear_calls == 2
        && !sdk_bad_arguments && !sdk_mock_enabled);
    fixture_clear(&f);
}

static void test_upload_start_failures(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_enable_nth = 1; sdk_fail_enable_side_effect = TRUE;
    expect("UPLOAD V2: enable false with possible side effect rejects and successful disable retires",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x12] && commands[0x15] == 2 && ends == 1
        && sdk_enable_calls == 1 && !sdk_mock_enabled && !sdk_clear_calls);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_clear_nth = 1;
    expect("UPLOAD V2: enabled clear false sends rollbackSTOP and disable before returningIO",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x11] == 1 && !commands[0x12] && commands[0x15] == 2
        && headers == 1 && ends == 1 && sdk_enable_calls == 1 && sdk_clear_calls == 1);
    fixture_clear(&f);
    fixture_init(&f, TRUE); f.devc->limits.limit_samples = 8;
    expect("UPLOAD V2: second-clear failure fixture starts first capture", start(&f));
    feed(&f, 32); sdk_fail_clear_nth = 2;
    expect("UPLOAD V2: repeated enabled clear false returnsIO with no second ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x12] == 1 && commands[0x15] == 4 && ends == 2
        && sdk_enable_calls == 2 && sdk_clear_calls == 2 && !sdk_mock_enabled);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_disable_nth = 1;
    expect("UPLOAD V2: initial disable false rejects startup even after retry succeeds",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && !commands[0x11] && !commands[0x12] && commands[0x15] == 2
        && sdk_disable_calls == 2 && !sdk_clear_calls);
    fixture_clear(&f);
    fixture_init(&f, TRUE); fail_opcode = 0x12; opcode_failures = 1;
    expect("UPLOAD V2: failedARM retires enabled SDKqueue before startup returns",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && resources_clean(&f)
        && commands[0x12] == 1 && commands[0x15] == 2 && ends == 1
        && sdk_enable_calls == 1 && sdk_disable_calls == 2 && sdk_clear_calls == 1
        && !sdk_mock_enabled && !f.devc->wch.upload_enabled);
    fixture_clear(&f);
}

static void test_upload_dirty_ownership(gboolean stop_failure)
{
    struct fixture f;
    unsigned int command_count, count;
    fixture_init(&f, TRUE);
    if (stop_failure) {
        expect("UPLOAD V2: persistent disable failure capture starts", start(&f));
        sdk_fail_disable_all = TRUE; count = read_calls;
        sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    } else {
        sdk_fail_clear_all = TRUE; sdk_fail_disable_nth = 2;
        expect("UPLOAD V2: failed enabled clear with failed rollback disable returnsIO",
            sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x12]);
        sdk_fail_disable_all = TRUE; count = read_calls;
    }
    expect("UPLOAD V2: disable false preserves SDKdirty after application END/resources retire",
        f.devc->wch.upload_dirty && !has_application_resources(f.devc)
        && !f.devc->decoded && !f.devc->wch_buffer && !sources && ends == 1
        && !resources_clean(&f));
    expect("UPLOAD V2: uncertain SDKqueue prevents poststop read", read_calls == (int)count);
    expect("UPLOAD V2: failed disable preserves previously successful enabled state",
        f.devc->wch.upload_enabled && sdk_mock_enabled);
    expect("UPLOAD V2: inactive stop reports retained SDKqueue failure",
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO);
    command_count = commands[0x15];
    expect("UPLOAD V2: newcapture refuses SDKdirty before new USBcommands",
        sr_dev_acquisition_start(f.sdi) != SR_OK && commands[0x15] == command_count);
    expect("UPLOAD V2: clear refuses to free dirty context", sr_dev_clear(&f.driver) != SR_OK
        && f.driver.context == f.drvc && f.sdi->priv == f.devc && !close_calls);
    expect("UPLOAD V2: close failure retains active handle/context and SDKdirty",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->wch.upload_dirty && !close_calls);
    sdk_fail_disable_all = sdk_fail_clear_all = FALSE; sdk_fail_disable_nth = 0;
    expect("UPLOAD V2: explicit close retry disables SDKqueue then closes exactly once",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty
        && !f.devc->wch.upload_enabled && !f.devc->wch.opened && close_calls == 1);
    expect("UPLOAD V2: retired context becomes clearable", resources_clean(&f));
    fixture_clear(&f);
}

static void test_upload_absent_exports(void)
{
    struct fixture f;
    const char *exports[] = {"CH375SetBufUploadEx", "CH375ClearBufUpload"};
    unsigned int i;
    for (i = 0; i < ARRAY_SIZE(exports); i++) {
        struct dla_wch missing = {0};
        memset(&wch_api, 0, sizeof(wch_api)); sdk_free_calls = 0;
        sdk_open_calls = sdk_write_calls = sdk_event_count = 0;
        sdk_missing_export = exports[i];
        expect("UPLOAD V2: real loader rejects missing required diagnostic export",
            !dla_wch_load() && !wch_api.module && sdk_free_calls == 1);
        expect("UPLOAD V2: absent export rejects WCHopen before identification/capturecommands",
            dla_wch_open(&missing) == SR_ERR_NA && !sdk_open_calls
            && !sdk_write_calls && !sdk_event_count && !missing.opened);
        sdk_missing_export = NULL;
    }
    fixture_init(&f, TRUE); wch_api.set_buf_upload_ex = NULL;
    expect("UPLOAD V2: absent Set export rejects capture before STOP/SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    wch_api.set_buf_upload_ex = sdk_set_upload; fixture_clear(&f);
    fixture_init(&f, TRUE); wch_api.clear_buf_upload = NULL;
    expect("UPLOAD V2: absent Clear export rejects capture before STOP/SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_NA && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && resources_clean(&f));
    wch_api.clear_buf_upload = sdk_clear_upload; fixture_clear(&f);
}

static void test_upload_close_only_false(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_disable_all = TRUE;
    expect("UPLOAD V2: close-only disablefalse retains handle/context/dirty without capturecommands",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.devc->wch.opened && f.devc->wch.upload_dirty
        && f.sdi->status == SR_ST_ACTIVE && !close_calls && !commands[0x15]
        && !commands[0x11] && !commands[0x12]);
    expect("UPLOAD V2: close-only SDKdirty refuses context clear", sr_dev_clear(&f.driver) != SR_OK
        && f.driver.context == f.drvc && f.sdi->priv == f.devc);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V2: close-only retry disables before exactly one close withoutclear",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1 && !sdk_clear_calls);
    fixture_clear(&f);
    fixture_init(&f, TRUE);
    expect("UPLOAD V2: observed kernel rejects Clear when disabled with1450",
        !sdk_clear_upload(0, 1) && GetLastError() == ERROR_NO_SYSTEM_RESOURCES);
    expect("UPLOAD V2: disabled clear1450 is not a required SDKretirement call",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1 && sdk_clear_calls == 1);
    fixture_clear(&f);
}

static void test_upload_public_exit_dirty(void)
{
    struct fixture f;
    struct sr_context *context = NULL;
    struct sr_dev_driver before = { .name = "audit-before", .cleanup = audit_stub_cleanup };
    struct sr_dev_driver after = { .name = "audit-after", .cleanup = audit_stub_cleanup };
    libusb_context *saved_usb_context;
    int ret;
    fixture_init(&f, TRUE); before_cleanup_count = after_cleanup_count = 0;
    ret = sr_init(&context);
    expect("UPLOAD V2: dirty exit initializes real library context without hardware scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(context->driver_list); context->driver_list = g_new0(struct sr_dev_driver *, 4);
    before.context = g_malloc0(1); after.context = g_malloc0(1);
    context->driver_list[0] = &before; context->driver_list[1] = &f.driver; context->driver_list[2] = &after;
    f.drvc->sr_ctx = context; saved_usb_context = context->libusb_ctx;
    expect("UPLOAD V2: dirty exit starts fake SDK capture", start(&f));
    sdk_fail_disable_all = TRUE; sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V2: dirty exit retains only SDK ownership after application END", ends == 1
        && !has_application_resources(f.devc) && f.devc->wch.upload_dirty);
    expect("UPLOAD V2: std_cleanup refuses SDKdirty and preserves private context",
        std_cleanup(&f.driver) != SR_OK && f.driver.context == f.drvc
        && f.drvc->instances && f.drvc->instances->data == f.sdi && !close_calls);
    ret = sr_exit(context);
    expect("UPLOAD V2: sr_exit reports SDKdirty cleanup failure", ret != SR_OK);
    if (ret == SR_OK) { f.driver.context = NULL; f.sdi = NULL; f.devc = NULL; return; }
    expect("UPLOAD V2: sr_exit preserves library/USB/handle/device/SDKdirty without callbacks",
        context->libusb_ctx == saved_usb_context && saved_usb_context
        && f.driver.context == f.drvc && f.sdi->priv == f.devc
        && f.devc->wch.opened && f.devc->wch.upload_dirty && !sources && !close_calls);
    expect("UPLOAD V2: sr_exit defers later drivers after SDKdirty refusal",
        before_cleanup_count == 1 && !before.context && !after_cleanup_count && after.context);
    expect("UPLOAD V2: public close failure remains retryable after sr_exit refusal",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE && !close_calls);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V2: public close retry retires SDKqueue before final exit",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1);
    ret = sr_exit(context);
    expect("UPLOAD V2: sr_exit retry frees remaining contexts once after SDK retirement",
        ret == SR_OK && !f.driver.context && before_cleanup_count == 1
        && after_cleanup_count == 1 && !after.context && !f.session.devs
        && close_calls == 1 && !sources && !queued_count() && !unsafe_frees);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}

static void test_upload_enable_false_unknown_side_effect(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); sdk_fail_enable_nth = 1; sdk_fail_enable_side_effect = TRUE;
    sdk_fail_disable_nth = 2;
    expect("UPLOAD V2: enablefalse plus failed retirement remains startupIO beforeARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x12] && ends == 1);
    expect("UPLOAD V2: unknown enablefalse side effect retains dirty despite enabledflagFALSE",
        f.devc->wch.upload_dirty && !f.devc->wch.upload_enabled && sdk_mock_enabled
        && !has_application_resources(f.devc) && !f.devc->decoded && !f.devc->wch_buffer);
    expect("UPLOAD V2: unknown enablefalse SDKdirty refuses newcapture/contextclear",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK);
    sdk_fail_disable_nth = 0;
    expect("UPLOAD V2: unknown enablefalse retirement retry disables actualmockqueue beforeclose",
        sr_dev_close(f.sdi) == SR_OK && !sdk_mock_enabled && !f.devc->wch.upload_dirty && close_calls == 1);
    fixture_clear(&f);
}
