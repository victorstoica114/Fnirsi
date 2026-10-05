/* Source packet golden output for independent host CRC/model checks. */
#include <stdio.h>
#include "../firmware/esp32-protocol-source/src/frame.h"
int main(void)
{
    static const uint32_t sequences[] = {0, 1, 255, 256, 65535, 65536,
                                         0xfffffffeu, 0xffffffffu};
    static const char protocols[] = "USIR";
    if (frame_crc((const uint8_t *)"123456789", 9) != 0x29b1)
        return 1;
    for (size_t p = 0; p < sizeof(protocols)-1; p++) {
        for (size_t s = 0; s < sizeof(sequences)/sizeof(sequences[0]); s++) {
            uint8_t bytes[FRAME_SIZE];
            frame_make(bytes, (uint8_t)protocols[p], sequences[s]);
            printf("%c %u ", protocols[p], (unsigned)sequences[s]);
            for (size_t i = 0; i < sizeof(bytes); i++) printf("%02x", bytes[i]);
            putchar('\n');
        }
    }
    return 0;
}
