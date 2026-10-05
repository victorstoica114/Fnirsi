# FNIRSI DLA-32 Plus in PulseView

This project integrates the FNIRSI DLA-32 Plus logic analyzer into libsigrok and PulseView, using USB 3 transport on Windows through the WCH driver and libusb transport code. The goal is reliable integration validated on hardware.

This repository contains the development work: source code, successive driver variants, build scripts, tests, and ESP32 programs used as signal sources. Captures, downloaded applications, compiled binaries, and evidence packages prepared for the manufacturer are excluded.

## Project status as of October 4, 2026

The initial integration works and has displayed PWM in PulseView. Full validation is still in progress.

- `src/libsigrok` and `src/pulseview` contain the main integration sources.
- `artifacts/dla32-wch-stop-v5-source/libsigrok` contains the latest experimental V5 variant included in this snapshot. It tracks outstanding STOP and resource recovery under the SDK0 policy while preserving the SDK1 policy.
- V5 has software checks and partial hardware validation. It has not replaced the DLL used by the main PulseView installation.
- Stream channel shifts, an incomplete Buffer capture, and command fragments appearing in data returned by the SDK still require investigation. The exact cause has not been established.
- Long Stream integrity and sustained, lossless throughput of 200 MB/s remain unconfirmed.

The next steps are to instrument the SDK/kernel/USB boundaries, complete start/stop/recovery tests, and validate all channels, masks, triggers, thresholds, PWM outputs, and protocols.

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

See the [build and restore instructions](BUILD_AND_RESTORE.md). This backup does not include ready-to-install binaries.

## Private journals

`private/driver-work-notes-2026-10-04.7z` contains nine technical journals. The key is stored separately, locally; it is not in Git. Correspondence and documents used to send reports to FNIRSI are excluded from this backup.

The archive was tested and extracted into a separate directory. All nine restored files were compared with the originals using SHA-256. An incorrect key cannot list the encrypted filenames.

## Provenance and licenses

Original revisions are recorded in [sources.lock.json](sources.lock.json). Upstream `COPYING`, `AUTHORS`, and other notices are preserved. Each component retains its own license; this repository does not assign a new license to the entire project. The proprietary WCH library and driver must be obtained separately.

The original backup manifest remains a record of the `backup-driver-2026-10-04` snapshot. Checksums for the later English translations are recorded separately in [backup/english-translation.sha256.json](backup/english-translation.sha256.json).
