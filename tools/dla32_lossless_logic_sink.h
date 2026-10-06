/* SPDX-License-Identifier: GPL-3.0-only */
/* Test-harness sink: preserve every LOGIC byte in one lossless Zstandard frame.
 * This is not part of the driver and makes no physical-integrity claim. */
#include <zstd.h>

static FILE *compressed_logic;
static ZSTD_CCtx *logic_compressor;
static GChecksum *logic_checksum;
static uint8_t *compression_output;
static size_t compression_output_size;
static uint64_t compression_input_bytes, compression_saved_bytes;
static int compression_failed;
static char compression_sha256[65];

static int logic_sink_init(FILE *file)
{
    if (!file || compressed_logic) return -1;
    logic_compressor = ZSTD_createCCtx();
    logic_checksum = g_checksum_new(G_CHECKSUM_SHA256);
    compression_output_size = ZSTD_CStreamOutSize();
    compression_output = g_try_malloc(compression_output_size);
    if (!logic_compressor || !logic_checksum || !compression_output ||
        ZSTD_isError(ZSTD_CCtx_setParameter(logic_compressor,ZSTD_c_compressionLevel,1))) {
        ZSTD_freeCCtx(logic_compressor); g_checksum_free(logic_checksum);
        g_free(compression_output); logic_compressor=NULL; logic_checksum=NULL;
        compression_output=NULL; return -1;
    }
    compressed_logic=file; compression_failed=0;
    compression_input_bytes=compression_saved_bytes=0;
    compression_sha256[0]='\0';
    return 0;
}

static int logic_sink_feed(const void *data, size_t size, ZSTD_EndDirective end)
{
    ZSTD_inBuffer input={data,size,0};
    size_t remaining;
    if (compression_failed) return -1;
    do {
        ZSTD_outBuffer output={compression_output,compression_output_size,0};
        remaining=ZSTD_compressStream2(logic_compressor,&output,&input,end);
        if (ZSTD_isError(remaining) ||
            fwrite(output.dst,1,output.pos,compressed_logic)!=output.pos) {
            compression_failed=1; return -1;
        }
        compression_saved_bytes+=output.pos;
    } while (input.pos<input.size || (end==ZSTD_e_end && remaining));
    return 0;
}

static size_t logic_sink_write(const void *data, size_t size, size_t count, FILE *file)
{
    size_t bytes;
    if (file!=compressed_logic) return fwrite(data,size,count,file);
    if (size && count>SIZE_MAX/size) { compression_failed=1; return 0; }
    bytes=size*count;
    if (logic_sink_feed(data,bytes,ZSTD_e_continue)) return 0;
    g_checksum_update(logic_checksum,data,bytes);
    compression_input_bytes+=bytes;
    return count;
}

static int logic_sink_close(FILE *file)
{
    int failed;
    if (file!=compressed_logic) return fclose(file);
    failed=logic_sink_feed(NULL,0,ZSTD_e_end);
    g_strlcpy(compression_sha256,g_checksum_get_string(logic_checksum),sizeof(compression_sha256));
    failed|=fclose(file)!=0;
    ZSTD_freeCCtx(logic_compressor); g_checksum_free(logic_checksum);
    g_free(compression_output);
    compressed_logic=NULL; logic_compressor=NULL; logic_checksum=NULL; compression_output=NULL;
    return failed ? EOF : 0;
}

static int logic_sink_self_test(void)
{
    uint8_t source[65536], restored[sizeof(source)];
    FILE *file=tmpfile();
    size_t size, result;
    uint8_t *encoded;
    int failed=0;
    for (size_t i=0;i<sizeof(source);i++) source[i]=(uint8_t)((i*73)^(i>>4));
    if (!file || logic_sink_init(file)) return 1;
    failed|=logic_sink_write(source,1,123,file)!=123;
    failed|=logic_sink_write(source+123,1,sizeof(source)-123,file)!=sizeof(source)-123;
    failed|=logic_sink_feed(NULL,0,ZSTD_e_end)!=0;
    failed|=compression_input_bytes!=sizeof(source) || compression_failed;
    fflush(file); size=(size_t)ftell(file); rewind(file);
    encoded=g_malloc(size);
    failed|=fread(encoded,1,size,file)!=size;
    result=ZSTD_decompress(restored,sizeof(restored),encoded,size);
    failed|=ZSTD_isError(result) || result!=sizeof(source) || memcmp(source,restored,sizeof(source));
    g_free(encoded);
    /* The frame was already finalized for readback. Retire storage directly. */
    fclose(file); ZSTD_freeCCtx(logic_compressor); g_checksum_free(logic_checksum);
    g_free(compression_output); compressed_logic=NULL; logic_compressor=NULL;
    logic_checksum=NULL; compression_output=NULL;
    printf("Lossless LOGIC sink: %s byte-exact split-write round trip.\n",failed?"FAIL":"PASS");
    return failed;
}
