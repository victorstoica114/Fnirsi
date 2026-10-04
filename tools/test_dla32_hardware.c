/* SPDX-License-Identifier: GPL-3.0-only */
/* Real libsigrok session test. Requires confirmed PWM0 -> D0 loopback.
 * Enables PWM0 at 100 kHz/50%, then always attempts to turn it off.
 * Verifies counts and periodic edges; not independent clock calibration. */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <glib.h>
#include <libsigrok/libsigrok.h>

struct result {
    FILE *file;
    uint64_t samples, edges, last_rising, periods, bad_periods;
    uint64_t expected_period, first_packet_samples;
    int64_t first_packet_us, last_packet_us;
    unsigned int signal_channel;
    int skip_signal;
    int last_level, ended, io_error;
};

static void receive(const struct sr_dev_inst *sdi,
    const struct sr_datafeed_packet *packet, void *data)
{
    struct result *result=data;
    const struct sr_datafeed_logic *logic;
    const uint8_t *p;
    uint64_t i, samples;
    int level;
    (void)sdi;
    if (packet->type==SR_DF_END) { result->ended=1; return; }
    if (packet->type!=SR_DF_LOGIC) return;
    logic=packet->payload;
    if (logic->unitsize!=4 || logic->length%4) { result->io_error=1; return; }
    if (result->file && fwrite(logic->data,1,logic->length,result->file)!=logic->length) result->io_error=1;
    p=logic->data;
    samples=logic->length/4;
    result->last_packet_us=g_get_monotonic_time();
    if (!result->first_packet_us) {
        result->first_packet_us=result->last_packet_us;
        result->first_packet_samples=samples;
    }
    if (result->skip_signal) { result->samples+=samples; return; }
    for (i=0;i<samples;i++) {
        level=(p[i*4+result->signal_channel/8]>>(result->signal_channel%8))&1;
        if (result->last_level!=-1 && level!=result->last_level) {
            result->edges++;
            if (level) {
                if (result->periods && result->samples+i-result->last_rising!=result->expected_period) {
                    if (result->bad_periods<16)
                        fprintf(stderr,"Nonconstant period at sample %" G_GUINT64_FORMAT
                            ": %" G_GUINT64_FORMAT " (expected %" G_GUINT64_FORMAT ")\n",
                            result->samples+i,result->samples+i-result->last_rising,result->expected_period);
                    result->bad_periods++;
                }
                result->last_rising=result->samples+i;
                result->periods++;
            }
        }
        result->last_level=level;
    }
    result->samples+=samples;
}

int main(int argc, char **argv)
{
    struct sr_context *ctx=NULL;
    struct sr_dev_driver **drivers, *driver=NULL;
    struct sr_dev_inst *sdi=NULL;
    struct sr_session *session=NULL;
    struct sr_channel_group *pwm=NULL;
    GSList *devices=NULL;
    struct result result={0};
    int i, ret=1, opened=0, pwm_enabled=0;
    uint64_t samplerate=50000000, sample_limit=50000;
    const char *mode="Buffer";
    int64_t started_us=0;
    result.last_level=-1;
    if (argc!=2 && argc!=5 && argc!=6) {
        fprintf(stderr,"Usage: test_dla32_hardware OUTPUT.bin|- [SAMPLERATE SAMPLES Buffer|Stream [SIGNAL_CHANNEL|--no-pwm]]\n"); return 2;
    }
    if (argc>=5) {
        char *end;
        samplerate=g_ascii_strtoull(argv[2],&end,10);
        if (*end || samplerate<1000000 || samplerate%100000) return 2;
        sample_limit=g_ascii_strtoull(argv[3],&end,10);
        if (*end || sample_limit<samplerate/100000*10) return 2;
        mode=argv[4];
        if (strcmp(mode,"Buffer") && strcmp(mode,"Stream")) return 2;
        if (argc==6) {
            if (!strcmp(argv[5],"--no-pwm")) result.skip_signal=1;
            else {
                result.signal_channel=g_ascii_strtoull(argv[5],&end,10);
                if (*end || result.signal_channel>=32) return 2;
            }
        }
    }
    result.expected_period=samplerate/100000;
#define REQUIRE(expr) do { int status=(expr); if (status!=SR_OK) { \
    fprintf(stderr,"FAIL %s: %d (%s)\n",#expr,status,sr_strerror(status)); goto cleanup; } } while (0)
    sr_log_loglevel_set(SR_LOG_INFO);
    REQUIRE(sr_init(&ctx));
    drivers=sr_driver_list(ctx);
    for (i=0;drivers[i];i++) if (!strcmp(drivers[i]->name,"fnirsi-dla32")) driver=drivers[i];
    if (!driver) { fprintf(stderr,"DLA-32 driver absent\n"); goto cleanup; }
    REQUIRE(sr_driver_init(ctx,driver));
    devices=sr_driver_scan(driver,NULL);
    if (g_slist_length(devices)!=1) { fprintf(stderr,"Expected exactly one confirmed DL32\n"); goto cleanup; }
    sdi=devices->data;
    printf("Device: %s %s; %s; channels=%u\n",sr_dev_inst_vendor_get(sdi),
        sr_dev_inst_model_get(sdi),sr_dev_inst_connid_get(sdi),g_slist_length(sr_dev_inst_channels_get(sdi)));
    REQUIRE(sr_dev_open(sdi)); opened=1;
    pwm=g_slist_nth_data(sr_dev_inst_channel_groups_get(sdi),0);
    if (!pwm) { fprintf(stderr,"PWM0 group absent\n"); goto cleanup; }
    REQUIRE(sr_config_set(sdi,NULL,SR_CONF_DATA_SOURCE,g_variant_new_string(mode)));
    REQUIRE(sr_config_set(sdi,NULL,SR_CONF_SAMPLERATE,g_variant_new_uint64(samplerate)));
    REQUIRE(sr_config_set(sdi,NULL,SR_CONF_LIMIT_SAMPLES,g_variant_new_uint64(sample_limit)));
    if (!result.skip_signal) {
        REQUIRE(sr_config_set(sdi,pwm,SR_CONF_OUTPUT_FREQUENCY,g_variant_new_double(100000)));
        REQUIRE(sr_config_set(sdi,pwm,SR_CONF_DUTY_CYCLE,g_variant_new_double(50)));
        REQUIRE(sr_config_set(sdi,pwm,SR_CONF_ENABLED,g_variant_new_boolean(TRUE)));
        pwm_enabled=1;
    }
    if (strcmp(argv[1],"-")) {
        result.file=fopen(argv[1],"wb");
        if (!result.file) { perror("fopen"); goto cleanup; }
    }
    REQUIRE(sr_session_new(ctx,&session));
    REQUIRE(sr_session_dev_add(session,sdi));
    REQUIRE(sr_session_datafeed_callback_add(session,receive,&result));
    started_us=g_get_monotonic_time();
    REQUIRE(sr_session_start(session));
    REQUIRE(sr_session_run(session));
    printf("Signal D%u: samples=%" G_GUINT64_FORMAT ", edges=%" G_GUINT64_FORMAT
        ", rising=%" G_GUINT64_FORMAT ", incorrect periods=%" G_GUINT64_FORMAT ", END=%d\n",
        result.signal_channel,result.samples,result.edges,result.periods,result.bad_periods,result.ended);
    printf("Mode=%s, samplerate=%" G_GUINT64_FORMAT ", session wall time=%.3f s\n",
        mode,samplerate,(g_get_monotonic_time()-started_us)/1000000.0);
    if (result.last_packet_us>result.first_packet_us)
        printf("Delivered after first packet: %.3f MB/s (includes native USB, conversion and validation)\n",
            (result.samples-result.first_packet_samples)*4.0/(result.last_packet_us-result.first_packet_us));
    if (result.samples!=sample_limit || (!result.skip_signal && (result.periods<8 || result.bad_periods)) || !result.ended || result.io_error) {
        fprintf(stderr,"FAIL capture count/waveform/lifecycle\n"); goto cleanup;
    }
    puts(result.skip_signal ? "PASS sample count and lifecycle; PWM unchanged, signal integrity NOT checked." :
        "PASS real DLA-32 libsigrok session: 32 channels, requested sample count, PWM0 loopback periodicity.");
    ret=0;
cleanup:
    if (session && sr_session_is_running(session)) { sr_session_stop(session); sr_session_run(session); }
    if (pwm_enabled && sr_config_set(sdi,pwm,SR_CONF_ENABLED,g_variant_new_boolean(FALSE))!=SR_OK) {
        fprintf(stderr,"PWM0 disable failed; reconnect USB to stop the output.\n"); ret=1;
    }
    if (result.file) fclose(result.file);
    if (session) sr_session_destroy(session);
    if (opened) sr_dev_close(sdi);
    g_slist_free(devices);
    if (ctx) sr_exit(ctx);
    return ret;
}
