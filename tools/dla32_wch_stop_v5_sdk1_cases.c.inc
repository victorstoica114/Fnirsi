static void test_v5_sdk1_configuration_ownership(gboolean dirty_only)
{
    struct fixture f;
    struct sr_channel_group *cg;
    struct pwm_setting saved;
    uint64_t samplerate;
    unsigned int writes;
    fixture_init(&f, TRUE);
    cg = sr_channel_group_new(f.sdi, "PWM0", NULL);
    f.devc->pwm[0].frequency = 1000000; f.devc->pwm[0].duty = 50;
    f.devc->pwm[0].enabled = TRUE;
    saved = f.devc->pwm[0]; samplerate = f.devc->samplerate;
    if (dirty_only) {
        sdk_fail_disable_all = TRUE;
        expect("STOP V5 SDK1: failed close-only retirement retains dirty without pendingSTOP",
            sr_dev_close(f.sdi) == SR_ERR_IO && f.devc->wch.upload_dirty
            && !f.devc->upload_stop_pending && f.sdi->status == SR_ST_ACTIVE);
    } else {
        expect("STOP V5 SDK1: pending configuration fixture starts", start(&f));
        fail_opcode = 0x15; opcode_failures = 1;
        sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
        expect("STOP V5 SDK1: failed STOP ends app but retains only pending hardware ownership",
            !has_application_resources(f.devc) && f.devc->upload_stop_pending
            && !f.devc->wch.upload_dirty && ends == 1);
    }
    writes = sdk_write_calls;
    expect("STOP V5 SDK1: pending/dirty rejects samplerate mutation before cached configuration changes",
        config_set(SR_CONF_SAMPLERATE, g_variant_new_uint64(SR_MHZ(100)), f.sdi, NULL) == SR_ERR
        && f.devc->samplerate == samplerate && sdk_write_calls == writes);
    expect("STOP V5 SDK1: pending/dirty rejects PWM frequency and preserves cached setting without packet",
        config_set(SR_CONF_OUTPUT_FREQUENCY, g_variant_new_double(2000000), f.sdi, cg) == SR_ERR
        && !memcmp(&saved, &f.devc->pwm[0], sizeof(saved)) && sdk_write_calls == writes);
    expect("STOP V5 SDK1: pending/dirty rejects PWM duty/disable writes without touching active generator setting",
        config_set(SR_CONF_DUTY_CYCLE, g_variant_new_double(25), f.sdi, cg) == SR_ERR
        && config_set(SR_CONF_ENABLED, g_variant_new_boolean(FALSE), f.sdi, cg) == SR_ERR
        && !memcmp(&saved, &f.devc->pwm[0], sizeof(saved)) && sdk_write_calls == writes);
    sdk_fail_disable_all = FALSE; opcode_failures = 0;
    expect("STOP V5 SDK1: only explicit STOP/close recovery retires ownership without PWM packets",
        sr_dev_acquisition_stop(f.sdi) == SR_OK && resources_clean(&f)
        && f.devc->pwm[0].enabled && sdk_write_calls == writes + 1
        && !memcmp(&saved, &f.devc->pwm[0], sizeof(saved)));
    expect("STOP V5 SDK1: configuration mutation is allowed again after explicit retirement",
        config_set(SR_CONF_SAMPLERATE, g_variant_new_uint64(SR_MHZ(100)), f.sdi, NULL) == SR_OK
        && f.devc->samplerate == SR_MHZ(100));
    fixture_clear(&f);
}
