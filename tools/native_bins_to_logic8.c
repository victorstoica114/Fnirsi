/* Offline conversion: eight native LSB-first bit planes -> physical D0..D7.
 * Original BINs stay untouched. No filtering, resampling or channel rotation.
 * A native export may include one extra stored byte past its declared extent.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include <errno.h>
#include <io.h>
#include <fcntl.h>
#include <sys/stat.h>

#define BLOCK 8192
static uint64_t expanded[256];
static unsigned char planes[8][BLOCK], output[BLOCK * 8];

static void prepare(void)
{
    unsigned value, bit;
    for (value = 0; value < 256; ++value)
        for (bit = 0; bit < 8; ++bit)
            expanded[value] |= (uint64_t)((value >> bit) & 1) << (bit * 8);
}

static void transpose(size_t bytes)
{
    size_t i;
    unsigned ch;
    for (i = 0; i < bytes; ++i) {
        uint64_t value = 0;
        for (ch = 0; ch < 8; ++ch)
            value |= expanded[planes[ch][i]] << ch;
        memcpy(output + i * 8, &value, 8);
    }
}

static int self_test(void)
{
    unsigned ch, bit, pattern;
    size_t i;
    uint64_t little_endian = 1;
    if (*(unsigned char *)&little_endian != 1) return 1;
    for (pattern = 0; pattern < 256; ++pattern) {
        for (ch = 0; ch < 8; ++ch)
            for (i = 0; i < BLOCK; ++i)
                planes[ch][i] = (unsigned char)(pattern ^ (i * 73 + ch * 41));
        transpose(BLOCK);
        for (i = 0; i < BLOCK; ++i)
            for (bit = 0; bit < 8; ++bit) {
                unsigned want = 0;
                for (ch = 0; ch < 8; ++ch)
                    want |= ((planes[ch][i] >> bit) & 1) << ch;
                if (output[i * 8 + bit] != want) return 1;
            }
    }
    puts("PASS: 16777216 independently checked sample bytes; physical channel and LSB ordering exact");
    return 0;
}

int main(int argc, char **argv)
{
    FILE *sources[8] = {0}, *target = NULL;
    uint64_t samples, needed, left, total = 0;
    unsigned ch;
    char *end;
    int result = 1, fd;
    prepare();
    if (argc == 2 && !strcmp(argv[1], "--self-test")) return self_test();
    if (argc != 11) {
        fprintf(stderr, "Usage: native-bins-to-logic8 SAMPLES OUTPUT BIN_D0 ... BIN_D7\n");
        return 2;
    }
    errno = 0; samples = strtoull(argv[1], &end, 10);
    if (errno || !*argv[1] || *end || argv[1][0] == '-' || !samples || samples > UINT64_MAX - 7) return 2;
    needed = (samples + 7) / 8;
    for (ch = 0; ch < 8; ++ch) {
        int64_t length;
        sources[ch] = fopen(argv[3 + ch], "rb");
        if (!sources[ch] || _fseeki64(sources[ch], 0, SEEK_END)) goto done;
        length = _ftelli64(sources[ch]);
        if (length < 0 || (uint64_t)length < needed || (uint64_t)length > needed + 1) {
            fprintf(stderr, "D%u native BIN extent differs from declared sample count\n", ch);
            goto done;
        }
        if (_fseeki64(sources[ch], 0, SEEK_SET)) goto done;
        fprintf(stderr, "D%u stored_bytes=%" PRId64 " examined_bytes=%" PRIu64 "\n", ch, length, needed);
    }
    fd = _open(argv[2], _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY, _S_IREAD | _S_IWRITE);
    if (fd < 0) goto done;
    target = _fdopen(fd, "wb");
    if (!target) { _close(fd); goto done; }
    left = needed;
    while (left) {
        size_t count = left > BLOCK ? BLOCK : (size_t)left;
        size_t write_count;
        for (ch = 0; ch < 8; ++ch)
            if (fread(planes[ch], 1, count, sources[ch]) != count) goto done;
        transpose(count);
        write_count = samples - total < count * 8 ? (size_t)(samples - total) : count * 8;
        if (fwrite(output, 1, write_count, target) != write_count) goto done;
        total += write_count;
        left -= count;
    }
    if (fflush(target) || total != samples) goto done;
    result = 0;
done:
    if (target && fclose(target)) result = 1;
    for (ch = 0; ch < 8; ++ch) if (sources[ch]) fclose(sources[ch]);
    if (result) fprintf(stderr, "Conversion failed; any partial output is retained\n");
    else printf("PASS: %" PRIu64 " physical D0..D7 sample bytes\n", total);
    return result;
}
