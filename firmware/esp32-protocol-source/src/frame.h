#ifndef DLA32_PROTOCOL_FRAME_H
#define DLA32_PROTOCOL_FRAME_H
#include <stddef.h>
#include <stdint.h>

enum { FRAME_SIZE = 16 };

/* CRC-16/CCITT-FALSE: poly 0x1021, init 0xffff, no reflection, no xorout. */
static inline uint16_t frame_crc(const uint8_t *p, size_t count)
{
    uint16_t crc = 0xffff;
    while (count--) {
        crc ^= (uint16_t)*p++ << 8;
        for (unsigned bit = 0; bit < 8; bit++)
            crc = (uint16_t)((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
    }
    return crc;
}

static inline void frame_make(uint8_t p[FRAME_SIZE], uint8_t protocol,
                              uint32_t sequence)
{
    p[0] = 0xa5;
    p[1] = 0x5a;
    p[2] = protocol;  /* U=UART, S=SPI, I=I2C write, R=I2C read. */
    p[3] = 1;
    for (unsigned i = 0; i < 4; i++)
        p[4+i] = (uint8_t)(sequence >> (8*i));
    uint32_t state = sequence ^ ((uint32_t)protocol << 24) ^ 0x6d2b79f5;
    for (unsigned i = 8; i < 14; i++) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        p[i] = (uint8_t)state;
    }
    uint16_t crc = frame_crc(p, 14);
    p[14] = (uint8_t)(crc >> 8);
    p[15] = (uint8_t)crc;
}
#endif
