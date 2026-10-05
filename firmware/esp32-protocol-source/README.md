# ESP32 UART, SPI and I2C test source

Classic ESP32, ESP-IDF 5.5.4 APIs. No bare-metal peripheral programming.
GPIO32 starts hardware LEDC at nominal 1 MHz, 50% duty, independently of the
protocol tasks. UART1 TX uses GPIO25 (115200, 8N1). SPI3 uses CLK18, MOSI23,
MISO19 and active-low CS27 (mode 0, nominal 1 MHz). Connect MOSI23 to MISO19
for loopback; MISO is an input. I2C0 master uses SDA21/SCL22 and I2C1 slave
uses SDA26/SCL33, 7-bit address 0x42, nominal 100 kHz. Join 21 to 26 and 22
to 33; pull each shared line up to ESP32 3V3 using one 2.2 kohm resistor.

The default mode transmits a 16-byte packet on UART, SPI MOSI and I2C write.
I2C ACK checks are disabled in this explicit forced-write test pattern, so
the master emits data even when no slave answers. The analyzer observes the
real NACK bits; this does not establish successful I2C device communication.
Full slave ACK/write/read testing is available with I2C_MODE=full when the
receiver wires are present. Magic A5 5A;
protocol byte U/S/I/R; version 1; little-endian 32-bit round counter;
six deterministic xorshift32 payload bytes; big-endian CRC-16/CCITT-FALSE
over the first 14 bytes. I2C writes I; full mode also reads a different R response.
Counters continue between packets; reset starts a new run at zero.
SPI loopback and, in full mode, both I2C directions are checked in the source.
SPI receive mismatch does not invalidate an independently verified MOSI
capture when the loopback wire is absent. Emitted-write counters in the
default mode indicate API completion only, not slave ACK or data reception.
These checks do not calibrate either clock.

UART0 on the existing CH340 USB console accepts STATUS, UART=115200 or
UART=1000000, SPI=1000000/5000000/10000000, I2C=100000/400000, and GAP=1..100
(milliseconds after each round). I2C_MODE=write_nack/full selects the test
contract; I2C_SLAVE=normal/swapped selects SDA26/SCL33 or SDA33/SCL26.
MISO_PULL=up/none changes only GPIO19's weak input bias, for identification.
Changes occur between complete rounds.
The default gap is 5 ms. Sources remain active after host capture stops
and after resets. No OFF command is implemented.

TOPOLOGY reads GPIO19/26/33 against GPIO21/22/23 for four seconds with weak
receiver pull-ups, then restores their original pulls. It enables input
monitoring without changing output routing, and temporarily loads the CPU.
Its before/after source-level checks reject unstable samples only in this
GPIO diagnostic; analyzer samples are never filtered. This is a signal
correlation probe, not a continuity meter. Configuration commands are
rejected while the probe is active; STATUS is still available.

The current analyzer map is in existing-wiring-map.json:

| Source | ESP32 GPIO | Analyzer channel |
| --- | --- | --- |
| Reference PWM | 32 | D0 |
| UART TX | 25 | D1 |
| I2C SDA | 21 | D2 |
| I2C SCL | 22 | D3 |
| SPI CLK | 18 | D4 |
| SPI MOSI | 23 | D5 |
| SPI MISO input | 19 | D6 |
| SPI CS | 27 | D7 |

The mapping was checked with isolated analyzer channel masks and an input
bias test for GPIO19. It does not modify the driver's bit order. Current
receiver checks do not detect functional SPI loopback or I2C slave signals.
Use the explicit tx_write_nack validation scope for this fixture; SPI MISO
and I2C ACK/read/write remain outside that scope.

From the workspace, build using its cached PlatformIO Python:

```powershell
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -B tools/build_esp32_protocol_source.py --block fresh-build-name
```

Add --upload to write the newly built images to the known ESP32 on COM33.
Each block name must be new. The helper validates the existing no-spaces
junction and writes build/upload logs and a source/binary hash manifest.
It never reads or backs up the previous firmware.

Compile in a no-spaces junction to this source directory. Uploading resets
the ESP32 and writes this newly built project; it does not read or back up
the previous firmware. The original register-only PWM project is retained.
Software build success, source self-checks, analyzer capture integrity and
physical waveform quality are separate validation results.
