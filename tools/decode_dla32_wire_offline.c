/* SPDX-License-Identifier: GPL-3.0-only */
/* Offline all-32-channel decoder. Exclusive output, no SDK/USB or filtering. */
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <io.h>
#include <sys/stat.h>
#include "protocol.h"

int wmain(int argc,wchar_t **argv)
{
    struct dla_decoder decoder;FILE *input,*output;int descriptor;
    uint8_t *wire,*logic,tail[32];size_t count,pending=0,produced;
    uint64_t read_bytes=0,written_bytes=0;int failed=0;
    if (argc!=3)return 2;
    input=_wfopen(argv[1],L"rb");if (!input)return 2;
    descriptor=_wopen(argv[2],_O_WRONLY|_O_CREAT|_O_EXCL|_O_BINARY,_S_IREAD|_S_IWRITE);
    if (descriptor<0) {fclose(input);return 2;}
    output=_fdopen(descriptor,"wb");if (!output) {_close(descriptor);fclose(input);return 2;}
    wire=malloc(1048576);logic=malloc(1048576+32);
    if (!wire || !logic || dla_decoder_init(&decoder,UINT32_MAX,1))failed=1;
    while (!failed && (count=fread(wire,1,1048576,input))) {
        read_bytes+=count;
        if (dla_decode_feed(&decoder,tail,&pending,logic,1048576+32,wire,count,&produced) ||
            fwrite(logic,1,produced,output)!=produced)failed=1;
        else written_bytes+=produced;
    }
    failed|=ferror(input) || ferror(output) || pending || read_bytes!=written_bytes;
    failed|=fclose(input)!=0;failed|=fclose(output)!=0;free(wire);free(logic);
    printf("%s offline decoder: input=%llu output=%llu tail=%zu.\n",failed?"FAIL":"PASS",
        (unsigned long long)read_bytes,(unsigned long long)written_bytes,pending);
    return failed?1:0;
}
