static void test_upload_close_only_false(void)
{
    struct fixture f;
    unsigned int kind;
    for (kind = 0; kind < 2; kind++) {
        fixture_init(&f, TRUE);
        if (kind) sdk_fail_clear_all = TRUE; else sdk_fail_disable_all = TRUE;
        expect("UPLOAD: close-only SDKfalse retains handle/context/dirty without capturecommands",
            sr_dev_close(f.sdi) == SR_ERR_IO && f.devc->wch.opened && f.devc->wch.upload_dirty
            && f.sdi->status == SR_ST_ACTIVE && !close_calls && !commands[0x15]
            && !commands[0x11] && !commands[0x12]);
        expect("UPLOAD: close-only SDKdirty refuses context clear", sr_dev_clear(&f.driver) != SR_OK
            && f.driver.context == f.drvc && f.sdi->priv == f.devc);
        sdk_fail_disable_all = sdk_fail_clear_all = FALSE;
        expect("UPLOAD: close-only retry disables/clears before exactly one close",
            sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1);
        fixture_clear(&f);
    }
}

static void test_upload_public_exit_dirty(void)
{
    struct fixture f;
    struct sr_context *context = NULL;
    struct sr_dev_driver before = { .name = "audit-before", .cleanup = audit_stub_cleanup };
    struct sr_dev_driver after = { .name = "audit-after", .cleanup = audit_stub_cleanup };
    libusb_context *saved_usb_context;
    int ret;

    fixture_init(&f, TRUE);
    before_cleanup_count = after_cleanup_count = 0;
    ret = sr_init(&context);
    expect("UPLOAD: dirty exit initializes real library context without hardware scan", ret == SR_OK);
    if (ret != SR_OK) { fixture_clear(&f); return; }
    g_free(context->driver_list);
    context->driver_list = g_new0(struct sr_dev_driver *, 4);
    before.context = g_malloc0(1); after.context = g_malloc0(1);
    context->driver_list[0] = &before;
    context->driver_list[1] = &f.driver;
    context->driver_list[2] = &after;
    f.drvc->sr_ctx = context;
    saved_usb_context = context->libusb_ctx;
    expect("UPLOAD: dirty exit starts fake SDK capture", start(&f));
    sdk_fail_clear_all = TRUE;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD: dirty exit retains only SDK ownership after application END", ends == 1
        && !has_application_resources(f.devc) && f.devc->wch.upload_dirty);
    expect("UPLOAD: std_cleanup refuses SDKdirty and preserves private context",
        std_cleanup(&f.driver) != SR_OK && f.driver.context == f.drvc
        && f.drvc->instances && f.drvc->instances->data == f.sdi && !close_calls);
    ret = sr_exit(context);
    expect("UPLOAD: sr_exit reports SDKdirty cleanup failure", ret != SR_OK);
    if (ret == SR_OK) {
        f.driver.context = NULL; f.sdi = NULL; f.devc = NULL;
        return;
    }
    expect("UPLOAD: sr_exit preserves library/USB/handle/device/SDKdirty without callbacks",
        context->libusb_ctx == saved_usb_context && saved_usb_context
        && f.driver.context == f.drvc && f.sdi->priv == f.devc
        && f.devc->wch.opened && f.devc->wch.upload_dirty && !sources && !close_calls);
    expect("UPLOAD: sr_exit defers later drivers after SDKdirty refusal",
        before_cleanup_count == 1 && !before.context && !after_cleanup_count && after.context);
    expect("UPLOAD: public close failure remains retryable after sr_exit refusal",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE && !close_calls);
    sdk_fail_clear_all = FALSE;
    expect("UPLOAD: public close retry retires SDKqueue before final exit",
        sr_dev_close(f.sdi) == SR_OK && !f.devc->wch.upload_dirty && close_calls == 1);
    ret = sr_exit(context);
    expect("UPLOAD: sr_exit retry frees remaining contexts once after SDK retirement",
        ret == SR_OK && !f.driver.context && before_cleanup_count == 1
        && after_cleanup_count == 1 && !after.context && !f.session.devs
        && close_calls == 1 && !sources && !queued_count() && !unsafe_frees);
    if (ret == SR_OK) { f.sdi = NULL; f.devc = NULL; }
    fixture_clear(&f);
}
