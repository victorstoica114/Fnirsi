# V6 validation scope

The integration remains experimental. The tested V6 runtime has not replaced
the main installed PulseView driver. This source backup preserves development
and diagnostics; measurements, private journals, emails and manufacturer
evidence packages remain local.

| Area | Current evidence | Open work |
| --- | --- | --- |
| Worker lifecycle | Offline interactions exercise real worker/API code with fake SDK calls. Selected hardware captures and GUI cancellation/recovery retire the worker normally. | All physical disconnect and timeout cases; faster retirement of an in-flight read. |
| Buffer | PWM display and selected UART/SPI/I2C transmit patterns pass. | Every input, sparse physical wiring, complete supported-rate matrix, calibrated thresholds and trigger timing. |
| Stream | Selected captures with fewer channels or a reduced data rate pass the limited transmit scope. High-rate 32-channel captures show counter/order and occasional CRC anomalies. | Sustained lossless throughput, repeated physical channel placement and cause localization. 200 MB/s is unconfirmed. |
| UART | 8N1 transmit packets with expected payload, CRC and consecutive counters have passed at selected rates. | Broader configurations and independent protocol reference captures. |
| SPI | Mode 0 MOSI/CLK/CS transmit packets have passed at selected rates. | Functional MISO/loopback and the remaining modes. |
| I2C | Explicit forced-write packets with real NACK bits have passed at selected rates. | Successful slave ACK, write/read and repeated-start coverage. |
| GUI files | A live session saved from the GUI was loaded again and exported as binary without changing its bytes. | Further sizes/formats; the cause of an earlier isolated incomplete CLI export remains unresolved. |
| Maximum rates | Selected configurations have count and conversion checks. | Arbitrary-pattern validation at 1 GS/s; observed false edges are unresolved. |
| Signal generators | ESP32 reference and partial internal PWM checks are available. | Physical validation of PWM1–PWM3 and thresholds. |

RAW-to-LOGIC agreement establishes that the unpacker preserved the received
payload under the configured mask. It does not establish the physical origin,
correct timeline or absence of sample loss. Packet CRC verifies packet contents;
counter checks also detect missing, repeated or reordered complete packets.
Neither proves every idle sample. No automatic channel rotation, block
reordering or waveform filtering is applied.

The host timer experiment is diagnostic. A faster timer improved the tested
path's throughput while integrity failures remained. It is not a production
fix and does not establish a firmware cause.

The ESP32 source remains running after captures stop. It uses ESP-IDF libraries,
not bare-metal peripheral configuration, and does not expose a source-OFF
command. Full receiver tests require the connections described in its README.
