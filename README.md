# FNIRSI DLA-32 Plus in PulseView

Current development backup: [V7 r2 validation status](V7_VALIDATION_STATUS.md). Event-driven transport and recovery checks are implemented; continuous 32-channel Stream passes the tested 12-second interval at 25 MS/s. Continuous 50 MS/s still fails packet integrity, and 200 MB/s is not achieved. This branch is an experimental source backup.

This project integrates the FNIRSI DLA-32 Plus logic analyzer into libsigrok and PulseView, using USB 3 transport on Windows through the WCH driver and libusb transport code. The goal is reliable integration validated on hardware.

This repository contains the development work: source code, successive driver variants, build scripts, tests, and ESP32 programs used as signal sources. Captures, downloaded applications, compiled binaries, and evidence packages prepared for the manufacturer are excluded.

## Project status as of October 5, 2026

The initial integration works and has displayed PWM in PulseView. Full validation is still in progress.

- `src/libsigrok` and `src/pulseview` contain the main integration sources.
- `artifacts/dla32-wch-worker-v6-overlay` contains the latest experimental V6 changes. Restore its complete, hash-checked source tree from the retained V5 base with `tools/restore_dla32_worker_v6_source.py`. The overlay avoids another duplicate upstream tree.
- V6 runs SDK0 I/O on a separate worker with explicit buffer ownership and ordered STOP/drain/thread retirement. The main installed driver has not been replaced.
- GUI cancellation in Buffer and Stream, repeated captures in one session, and an exact GUI save/load/binary export round trip have been checked. This does not close every disconnect or timeout path.
- `firmware/esp32-protocol-source` uses ESP-IDF APIs for a hardware PWM reference and UART/SPI/I2C packets with counters and CRC. UART TX, SPI MOSI/CLK/CS and an explicit I2C forced-write/NACK pattern have partial hardware coverage; MISO and successful slave ACK/read/write remain open.
- Stream channel shifts, an incomplete Buffer capture, and command fragments appearing in data returned by the SDK still require investigation. The exact cause has not been established.
- Long Stream integrity and sustained, lossless throughput of 200 MB/s remain unconfirmed.

High-rate, 32-channel Stream captures have failed counter/integrity checks. A scoped host-timer experiment improves throughput but does not resolve those failures. It is retained as a diagnostic tool and has not changed the driver. Buffer and selected smaller Stream captures have passed the limited protocol scope. The remaining work includes sustained Stream integrity/performance, error recovery, all physical inputs and maximum rates, trigger timing, thresholds, PWM outputs and full protocol receiver tests.

## Contents

| Directory | Purpose |
| --- | --- |
| `src/` | Working sources and upstream references, with revisions recorded in `sources.lock.json`. |
| `artifacts/*-source/` | Isolated driver variants retained for comparison and reproduction. |
| `artifacts/*-tests/` | Source files for software test fixtures. |
| `artifacts/source-changes/` | Patches for the initial integration. |
| `tools/` | Build, analysis, and test scripts; some access hardware when executed. |
| `firmware/` | ESP32 code used to generate reference signals. |
| `private/` | Technical journals in a 7z archive with encrypted contents and filenames. |
| `backup/` | SHA-256 records of the original included files, backup preparation checks, and translation checksums. |

See the [build and restore instructions](BUILD_AND_RESTORE.md) and [validation scope](V6_VALIDATION_STATUS.md). This backup does not include ready-to-install binaries. New source checksums are in `backup/v6-protocol-source-update.sha256.json`; earlier snapshot manifests remain unchanged.

## Private journals

`private/driver-work-notes-2026-10-04.7z` contains nine technical journals. The key is stored separately, locally; it is not in Git. Correspondence and documents used to send reports to FNIRSI are excluded from this backup.

The archive was tested and extracted into a separate directory. All nine restored files were compared with the originals using SHA-256. An incorrect key cannot list the encrypted filenames.

## Provenance and licenses

Original revisions are recorded in [sources.lock.json](sources.lock.json). Upstream `COPYING`, `AUTHORS`, and other notices are preserved. Each component retains its own license; this repository does not assign a new license to the entire project. The proprietary WCH library and driver must be obtained separately.

The original backup manifest remains a record of the `backup-driver-2026-10-04` snapshot. Checksums for the later English translations are recorded separately in [backup/english-translation.sha256.json](backup/english-translation.sha256.json).
