# Stream transport investigation — October 6, 2026

Continuous 32-channel Stream at 50 MS/s is **not yet validated as lossless**.
The uncompressed requirement is 200 MB/s. The installed PulseView package has
not been replaced by these experiments.

| Isolated path | Measured decimal MB/s | Result |
| --- | ---: | --- |
| SDK0, 1 MiB reads, two buffers | 140.1 | Below target |
| SDK0, 2 MiB reads, two buffers | 146.6 | Below target |
| SDK0, 4 MiB reads, two buffers | 106.9 | Below target |
| SDK0, 2 MiB reads, 16 buffers | 146.3 | Long capture fails counter/CRC checks |
| Kernel upload, 1 MiB transfers, event worker | 156.3 | Finite throughput only |
| Kernel upload, reused native IOCTL buffers | 155.4 | Finite throughput only |
| Direct native requests, four outstanding | 161.0 | Finite throughput only |
| Direct native requests, eight outstanding, RAW saved | 160.4 | Finite protocol checks pass; sustained 200 MB/s unproven |

These are host-path measurements, not USB bus utilization. Short finite
captures can pass while device/kernel buffers hide a throughput deficit.

V8 generalizes the bounded FIFO and cleans up every allocated buffer, including
partial allocation failures. V9 adds an explicit opt-in upload worker, routes
worker diagnostics through the main thread, and retires the SDK queue before
hardware STOP. V10 reads the verified kernel request into persistent storage.
None of these paths currently reaches the required throughput.

Two 12-second SDK0 captures retain all original 32-channel LOGIC bytes in
lossless Zstandard storage. Decompression verifies the full original SHA-256;
protocol decoders use an explicit D0–D7 projection. Both captures fail packet
continuity. With 16 buffers, total gaps between reads are only 4.8 ms across
12 seconds, so large frontend/disk stalls are not needed to reproduce loss.
Compression integrity does not establish physical acquisition integrity.

The eight-request finite native capture contains 101,974,016 samples. An
independent conversion check matches every encoded bit across all 32 channels;
UART TX, SPI MOSI and forced I2C write/NACK each contain 248 valid consecutive
packets with no CRC failures or decoder warnings. This covers the present
fixture and the finite interval, not a sustained lossless Stream verdict or
complete SPI/I2C receiver validation.

The device advertises the Microsoft WinUSB compatible ID. A temporary test
with the existing signed Microsoft driver returned PIPE on OUT endpoint
`0x02`. That failure does not establish that the endpoint is wrong: earlier
ETW header correlations map successful WCH command writes to OUT `0x02`.
V11 experiments with OUT `0x01` and fresh DL32 identification through OUT1/IN2.
Software verification passes 206 WCH lifecycle checks, 13 endpoint/model
checks and 92 general checks, but those tests do not establish which endpoint
the hardware accepts. V11 remains an unvalidated experiment; its endpoint
change must not be promoted as a verified correction. The WCH binding is restored.

No firmware fault is established by these results. The next comparison is a
long native FNIRSI Stream capture under the same 32-channel/50 MS/s conditions,
with packet CRC/counter validation. Native sustained 200 MB/s has not been
measured. The software USB restart and exact-port reenumeration completed,
but fresh DL32 identification still fails; native acquisition has not started.
Device communication must recover before native or WinUSB hardware tests.
Kernel upload retirement can discard an unknown final kernel tail; this
limitation is reported explicitly.

The offline native BIN converter preserves physical D0–D7 ordering and LSB-first
time order. It passed 16,777,216 independent sample-byte checks, file-boundary
and overwrite checks, and an exact five-million-sample comparison with an old
native CSV. These verify export conversion, not throughput or current hardware.

Source profiles V8–V11 are preserved as small overlays over the archived V5
base. Restore and verify all 490 files with `tools/restore_dla32_stream_source.py`.
Captures, private journals, vendor correspondence, installed driver packages
and compiled binaries are excluded from this public backup.
