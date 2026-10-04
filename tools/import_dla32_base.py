"""One-time import of PR #306, preserving attribution; refuses overwrite."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent / 'src' / 'libsigrok'
original = root / 'src' / 'hardware' / 'fnirsi-dla16'
target = original.with_name('fnirsi-dla32')
if target.exists():
    raise SystemExit('DLA-32 already imported; refusing to overwrite local changes')
target.mkdir()
protocol = (original / 'protocol.h').read_text()
protocol = protocol.replace('DLA-16', 'DLA-32').replace('FNIRSI_DLA16', 'FNIRSI_DLA32')
protocol = protocol.replace('#define DLA_CHANNELS 16', '#define DLA_CHANNELS 32')
protocol = protocol.replace('channel >= 2', 'channel >= 4').replace('PWM0/PWM1', 'PWM0/PWM1/PWM2/PWM3')
protocol = protocol.replace('uint16_t mask', 'uint32_t mask')
protocol = protocol.replace('0x00, 0xff, 0xff };', '0x00, 0xff, 0xff, 0xff, 0xff };')
# Remove the fixed 16-channel decoder; retain the sparse lookup decoder below.
start = protocol.index('/* A wire frame contains')
end = protocol.index('/* The factory rounds', start)
protocol = protocol[:start] + protocol[end:]
protocol = protocol.replace('uint64_t expand[DLA_CHANNELS][256][2]', 'uint64_t expand[DLA_CHANNELS][256][4]')
protocol = protocol.replace('highest < 8 ? 1 : 2', 'highest < 8 ? 1 : highest < 16 ? 2 : 4')
start = protocol.index('static inline void dla_decode_frames')
protocol = protocol[:start] + '''static inline void dla_decode_frames(const struct dla_decoder *decoder,
    uint8_t *out, const uint8_t *in, size_t frames)
{
    size_t f;
    unsigned int lane, word;
    uint64_t words[4];

    for (f = 0; f < frames; f++) {
        memset(words, 0, sizeof(words));
        for (lane = 0; lane < decoder->channels; lane++)
            for (word = 0; word < decoder->unitsize; word++)
                words[word] |= decoder->expand[lane][in[lane]][word];
        for (word = 0; word < decoder->unitsize; word++)
            dla_put64(out + word * 8, words[word]);
        in += decoder->wire_channels;
        out += decoder->unitsize * 8;
    }
}
#endif
'''
(target / 'protocol.h').write_text(protocol, newline='\n')
api = (original / 'api.c').read_text()
api = api.replace('DLA-16', 'DLA-32').replace('dla16', 'dla32').replace('DLA16', 'DLA32')
api = api.replace('uint16_t mask', 'uint32_t mask').replace('uint16_t channel_mask', 'uint32_t channel_mask')
api = api.replace('struct pwm_setting pwm[2]', 'struct pwm_setting pwm[4]')
api = api.replace('index < 2', 'index < 4').replace('UINT16_MAX &&', 'UINT32_MAX &&')
api = api.replace('return channels <= 8 ? SR_GHZ(1) : SR_MHZ(500);',
                  'return channels <= 8 ? SR_GHZ(1) : channels <= 16 ? SR_MHZ(500) : SR_MHZ(250);')
api = api.replace('return SR_MHZ(125);', 'return channels <= 16 ? SR_MHZ(125) : SR_MHZ(50);')
# PulseView selects logic analyzers; PWM groups remain available through libsigrok.
api = api.replace('return !g_strcmp0(g_getenv("FNIRSI_DLA32_LOGIC_ONLY"), "1");', 'return TRUE;')
(target / 'api.c').write_text(api, newline='\n')

makefile = root / 'Makefile.am'
text = makefile.read_text().replace('if HW_FTDI_LA\n', '''if HW_FNIRSI_DLA32
src_libdrivers_la_SOURCES += \\
    src/hardware/fnirsi-dla32/protocol.h \\
    src/hardware/fnirsi-dla32/transport-wch.h \\
    src/hardware/fnirsi-dla32/api.c
endif
if HW_FTDI_LA
''', 1)
makefile.write_text(text, newline='\n')
configure = root / 'configure.ac'
text = configure.read_text().replace('SR_DRIVER([FTDI LA]',
    'SR_DRIVER([FNIRSI DLA-32], [fnirsi-dla32], [libusb])\nSR_DRIVER([FTDI LA]', 1)
configure.write_text(text, newline='\n')
print('Imported DLA-32 base from PR #306; WCH transport and model identification to follow.')
