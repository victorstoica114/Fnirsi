static void test_upload_v3_observed_active_stop(void)
{
    struct fixture f;
    uint8_t packet[DLA_COMMAND_SIZE];
    fixture_init(&f, TRUE); f.devc->limits.limit_samples = 8;
    sdk_reject_stop_when_enabled = TRUE;
    expect("UPLOAD V3: observed active-upload STOP fault case starts", start(&f));
    dla_stop(packet);
    expect("UPLOAD V3: mock explicitly reproduces observed active-upload STOP false/Windows31",
        dla_wch_write(&f.devc->wch, packet, sizeof(packet)) == SR_ERR_IO
        && GetLastError() == ERROR_GEN_FAILURE && sdk_active_stop_attempts == 1);
    feed(&f, 32);
    expect("UPLOAD V3: disable-before-STOP completion avoids injected active-upload command failure",
        resources_clean(&f) && accepted == 8 && ends == 1
        && sdk_active_stop_attempts == 1 && !sdk_mock_enabled);
    expect("UPLOAD V3: same injected case permits a repeated capture", start(&f));
    feed(&f, 32);
    expect("UPLOAD V3: repeated capture never attempts STOP with SDK enabled",
        resources_clean(&f) && ends == 2 && sdk_active_stop_attempts == 1);
    fixture_clear(&f);
}

static void test_upload_v3_disable_before_stop_failure(void)
{
    struct fixture f;
    unsigned int stops, reads;
    fixture_init(&f, TRUE);
    expect("UPLOAD V3: deferred STOP fixture starts", start(&f));
    stops = commands[0x15]; reads = read_calls; sdk_fail_disable_all = TRUE;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V3: disable false suppresses hardware STOP and poststop reads",
        commands[0x15] == stops && read_calls == (int)reads && !sdk_active_stop_attempts);
    expect("UPLOAD V3: deferred STOP survives END separately from SDKdirty/application resources",
        f.devc->upload_stop_pending && f.devc->wch.upload_dirty && sdk_mock_enabled
        && ends == 1 && !has_application_resources(f.devc) && !resources_clean(&f));
    expect("UPLOAD V3: new capture and devclear refuse pending ownership",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && commands[0x15] == stops && f.sdi->priv == f.devc);
    expect("UPLOAD V3: explicit inactive STOP retry cannot write while disable still fails",
        sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO && commands[0x15] == stops
        && f.devc->upload_stop_pending && f.devc->wch.upload_dirty);
    expect("UPLOAD V3: close retry cannot write or free while disable still fails",
        sr_dev_close(f.sdi) == SR_ERR_IO && commands[0x15] == stops && !close_calls
        && f.sdi->status == SR_ST_ACTIVE && f.devc->wch.opened);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V3: inactive STOP retry retires disable then hardwareSTOP then drain",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && commands[0x15] == stops + 1
        && read_calls == (int)reads + 2 && resources_clean(&f)
        && !sdk_mock_enabled && !sdk_active_stop_attempts && !close_calls);
    fixture_clear(&f);
}

static void test_upload_v3_pending_stop_close_retry(void)
{
    struct fixture f;
    unsigned int stops, reads;
    fixture_init(&f, TRUE);
    expect("UPLOAD V3: failed hardware STOP close-retry fixture starts", start(&f));
    stops = commands[0x15]; reads = read_calls;
    fail_opcode = 0x15; opcode_failures = 3;
    sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    expect("UPLOAD V3: hardware STOP false retains pending with clean SDK and completed appEND",
        f.devc->upload_stop_pending && !f.devc->wch.upload_dirty && !sdk_mock_enabled
        && !has_application_resources(f.devc) && ends == 1 && commands[0x15] == stops + 1
        && read_calls == (int)reads && !resources_clean(&f));
    expect("UPLOAD V3: pending hardware STOP blocks new start/contextclear/inactive retry reports IO",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK
        && sr_dev_acquisition_stop(f.sdi) == SR_ERR_IO
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty);
    expect("UPLOAD V3: failed close STOP retry preserves active handle/context without reads",
        sr_dev_close(f.sdi) == SR_ERR_IO && f.sdi->status == SR_ST_ACTIVE
        && f.devc->wch.opened && f.devc->upload_stop_pending && !close_calls
        && read_calls == (int)reads && commands[0x15] == stops + 3);
    expect("UPLOAD V3: successful close STOP retry drains before exactly one handle release",
        sr_dev_close(f.sdi) == SR_OK && resources_clean(&f) && !f.devc->wch.opened
        && close_calls == 1 && read_calls == (int)reads + 2 && commands[0x15] == stops + 4);
    fixture_clear(&f);
}

static void test_upload_v3_prearm_stop_failures(void)
{
    struct fixture f;
    fixture_init(&f, TRUE); fail_opcode = 0x15; opcode_failures = 2;
    expect("UPLOAD V3: persistent initial hardware STOP failure rejects before SETUP/ARM",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x11] && !commands[0x12]
        && commands[0x15] == 2 && !headers && !ends && !read_calls
        && f.devc->upload_stop_pending && !f.devc->wch.upload_dirty
        && !f.devc->decoded && !f.devc->wch_buffer && !has_application_resources(f.devc));
    expect("UPLOAD V3: initial pending STOP blocks capture/clear until explicit retry",
        sr_dev_acquisition_start(f.sdi) != SR_OK && sr_dev_clear(&f.driver) != SR_OK);
    expect("UPLOAD V3: initial pending STOP inactive retry succeeds and drains",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f)
        && commands[0x15] == 3 && read_calls == 2);
    fixture_clear(&f);
    fixture_init(&f, TRUE); sdk_fail_disable_all = TRUE;
    expect("UPLOAD V3: persistent initial disable failure attempts zero hardware commands or reads",
        sr_dev_acquisition_start(f.sdi) == SR_ERR_IO && !commands[0x15]
        && !commands[0x11] && !commands[0x12] && !read_calls && sdk_disable_calls == 2
        && f.devc->upload_stop_pending && f.devc->wch.upload_dirty);
    sdk_fail_disable_all = FALSE;
    expect("UPLOAD V3: initial disable failure close retry sends deferredSTOP then drains before close",
        sr_dev_close(f.sdi) == SR_OK && commands[0x15] == 1 && read_calls == 2
        && close_calls == 1 && resources_clean(&f));
    fixture_clear(&f);
}
