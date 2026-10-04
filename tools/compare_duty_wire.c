/* Read-only comparison on the same all-32-channel raw USB payload.
 * Driver decoder versus an independent frame/sample/channel bit transpose.
 * No transport, device access, or output capture modification.
 */
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/libsigrok/src/hardware/fnirsi-dla32/protocol.h"

#define CHUNK_SIZE (1024U * 1024U)

static void reference_decode(uint8_t *output, const uint8_t *input, size_t frames)
{
    size_t frame;
    unsigned int sample, channel;

    memset(output, 0, frames * 32);
    for (frame = 0; frame < frames; frame++) {
        for (sample = 0; sample < 8; sample++) {
            for (channel = 0; channel < 32; channel++) {
                unsigned int level = (input[frame * 32 + channel] >> sample) & 1U;
                output[frame * 32 + sample * 4 + channel / 8] |=
                    (uint8_t)(level << (channel % 8));
            }
        }
    }
}

static uint32_t reference_le32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
        ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

int main(int argc, char **argv)
{
    struct dla_decoder *decoder = NULL;
    uint8_t *input = NULL, *actual = NULL, *reference = NULL;
    FILE *file = NULL;
    uint64_t wire_bytes = 0, decoded_bytes = 0;
    uint64_t mismatch_words = 0, mismatch_bytes = 0;
    uint64_t first_mismatch_word = 0;
    uint32_t first_actual = 0, first_reference = 0;
    size_t bytes, frames, offset, byte, trailing_bytes = 0;
    int exit_code = 2, io_error = 0;

    if (argc != 2) {
        fprintf(stderr, "Usage: %s ALL32_RAW_WIRE_FILE\n", argv[0]);
        return 2;
    }
    file = fopen(argv[1], "rb");
    if (!file) {
        fprintf(stderr, "Cannot open input: %s\n", strerror(errno));
        return 2;
    }
    decoder = malloc(sizeof(*decoder));
    input = malloc(CHUNK_SIZE);
    actual = malloc(CHUNK_SIZE);
    reference = malloc(CHUNK_SIZE);
    if (!decoder || !input || !actual || !reference) {
        fprintf(stderr, "Allocation failed.\n");
        goto cleanup;
    }
    if (dla_decoder_init(decoder, UINT32_MAX, 0) != 0 ||
        decoder->wire_channels != 32 || decoder->unitsize != 4) {
        fprintf(stderr, "Driver decoder initialization failed.\n");
        goto cleanup;
    }
    while ((bytes = fread(input, 1, CHUNK_SIZE, file)) != 0) {
        wire_bytes += bytes;
        frames = bytes / 32;
        trailing_bytes = bytes % 32;
        dla_decode_frames(decoder, actual, input, frames);
        reference_decode(reference, input, frames);
        for (offset = 0; offset < frames * 32; offset += 4) {
            if (!memcmp(actual + offset, reference + offset, 4))
                continue;
            if (!mismatch_words) {
                first_mismatch_word = (decoded_bytes + offset) / 4;
                first_actual = reference_le32(actual + offset);
                first_reference = reference_le32(reference + offset);
            }
            mismatch_words++;
            for (byte = 0; byte < 4; byte++)
                if (actual[offset + byte] != reference[offset + byte])
                    mismatch_bytes++;
        }
        decoded_bytes += frames * 32;
        /* CHUNK_SIZE is frame-aligned. A short final frame cannot be
         * compared as complete sample words, and is reported as invalid. */
        if (trailing_bytes)
            break;
    }
    io_error = ferror(file) != 0;
    exit_code = (!io_error && !trailing_bytes && wire_bytes && !mismatch_words) ? 0 : 1;
    printf("{\n");
#if defined(__SSE2__) && !defined(DLA_DISABLE_SIMD)
    printf("  \"driver_decoder\": \"SSE2 all-32-lane transpose\",\n");
#else
    printf("  \"driver_decoder\": \"lookup table transpose\",\n");
#endif
    printf("  \"reference_decoder\": \"independent nested frame/sample/channel bits\",\n");
    printf("  \"wire_bytes\": %" PRIu64 ",\n", wire_bytes);
    printf("  \"decoded_bytes\": %" PRIu64 ",\n", decoded_bytes);
    printf("  \"complete_frames\": %" PRIu64 ",\n", decoded_bytes / 32);
    printf("  \"sample_words_compared\": %" PRIu64 ",\n", decoded_bytes / 4);
    printf("  \"trailing_wire_bytes\": %zu,\n", trailing_bytes);
    printf("  \"mismatching_words\": %" PRIu64 ",\n", mismatch_words);
    printf("  \"mismatching_bytes\": %" PRIu64 ",\n", mismatch_bytes);
    if (mismatch_words) {
        printf("  \"first_mismatch_sample\": %" PRIu64 ",\n", first_mismatch_word);
        printf("  \"first_driver_word\": \"%08" PRIx32 "\",\n", first_actual);
        printf("  \"first_reference_word\": \"%08" PRIx32 "\",\n", first_reference);
    } else {
        printf("  \"first_mismatch_sample\": null,\n");
    }
    printf("  \"read_error\": %s,\n", io_error ? "true" : "false");
    printf("  \"same_payload_decoder_match\": %s,\n", exit_code ? "false" : "true");
    printf("  \"signal_integrity_verified\": false\n");
    printf("}\n");
cleanup:
    if (file)
        fclose(file);
    free(reference);
    free(actual);
    free(input);
    free(decoder);
    return exit_code;
}
