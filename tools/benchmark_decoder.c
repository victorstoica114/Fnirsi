/* Pure CPU benchmark; USB throughput and losslessness are separate tests. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include "../src/libsigrok/src/hardware/fnirsi-dla32/protocol.h"

int main(void)
{
    const size_t bytes=1024*1024;
    const unsigned int passes=256;
    struct dla_decoder *decoder=malloc(sizeof(*decoder));
    uint8_t *input=malloc(bytes), *output=malloc(bytes);
    uint32_t random=0x12345678;
    volatile uint32_t checksum=0;
    size_t i;
    unsigned int pass;
    clock_t begin, end;
    double elapsed;
    if (!decoder || !input || !output) return 2;
    for (i=0;i<bytes;i++) { random=random*1664525+1013904223; input[i]=random>>24; }
    if (dla_decoder_init(decoder,UINT32_MAX,1)) return 2;
    begin=clock();
    for (pass=0;pass<passes;pass++) {
        input[0]=(uint8_t)pass;
        dla_decode_frames(decoder,output,input,bytes/32);
        checksum+=output[pass];
    }
    end=clock();
    elapsed=(double)(end-begin)/CLOCKS_PER_SEC;
    if (elapsed<=0) return 2;
#ifdef DLA_DISABLE_SIMD
    printf("Table decoder: ");
#else
    printf("Optimized decoder: ");
#endif
    printf("%.1f MB/s; %.3f s; checksum %u\n",bytes*(double)passes/elapsed/1e6,elapsed,checksum);
    free(decoder); free(input); free(output);
    return 0;
}
