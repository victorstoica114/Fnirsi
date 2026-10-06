# Build and restore

For the current V7 r2 source, run `python tools/restore_dla32_worker_v7_r2_source.py`. This verifies and restores all 490 files from the archived V5 base plus the compact overlay. Then use `tools/build_dla32_wch_worker_v7_r2_fresh.sh` and the V7 r2 interaction/inherited test scripts; see [V7 r2 status](V7_VALIDATION_STATUS.md). Hardware launchers require separate local acceptance evidence and matching DLL hashes.

## Restore the sources

```powershell
git clone https://github.com/victorstoica114/Fnirsi.git
Set-Location Fnirsi
```

All published sources are regular files. There are no submodules or Git LFS objects to download. The internal `.git` history of the upstream checkouts remains local; their original revisions are documented in `sources.lock.json`.

`backup/source-files.sha256.json` identifies each original included file, including the encrypted archive. Source copies were checked against the local originals before the initial publication. That manifest and `backup/verification.json` describe the original `backup-driver-2026-10-04` snapshot. Files translated later have their original and translated checksums recorded separately in `backup/english-translation.sha256.json`; their current contents will differ from the original backup hashes.

## Restore the technical journals

Obtain the local `DRIVER_NOTES_DECRYPTION_KEY.txt` separately. Keep it outside the repository. With 7-Zip installed:

```powershell
& 'C:\Program Files\7-Zip\7z.exe' x 'private\driver-work-notes-2026-10-04.7z' '-o..\Fnirsi-private-notes'
```

Enter the key when prompted by 7-Zip. The archive contains only the selected technical journals, without captures or correspondence.

## Windows build environment

The existing build uses MSYS2 UCRT64, GCC/G++, Autotools, pkg-config, CMake, and Ninja. The sigrok components require their dependencies, including GLib, libusb, libzip, and libserialport; PulseView requires Qt and Boost. Also consult the instructions provided by the upstream components.

The original scripts assume the following paths in the MSYS2 environment:

| MSYS2 path | Contents |
| --- | --- |
| `/tmp/dla32-project` | Root of this checkout. |
| `/tmp/dla32-src` | The checkout's `src` directory. |
| `/tmp/dla32-build` | Build directory for the main integration. |
| `/opt/dla32` | Installation prefix for the main integration. |

Create these mappings in your build environment or adapt the paths before running the scripts. The PowerShell wrappers read `tools/toolchain-path.txt`, a local file excluded from Git. It must contain the path to your MSYS2 installation. Some helper scripts retain paths from the original environment and require adjustment on another computer.

For the main integration, after preparing the environment:

```bash
bash /tmp/dla32-project/tools/build_windows.sh all
```

For the experimental V5 variant:

```bash
bash /tmp/dla32-project/tools/build_dla32_wch_stop_v5.sh 0
bash /tmp/dla32-project/tools/build_dla32_wch_stop_v5.sh 1
```

These scripts build separate variants and run the specified software checks. The V5 build is for diagnostics and disables the C++ binding; it does not by itself produce a library suitable for PulseView. PulseView integration must retain the main build's `--enable-cxx` option.

The fixture sources in `artifacts/*-tests` are included and retain some `#include` directives with absolute paths from the original environment. For another checkout, adapt those paths in working copies. Existing generators refuse to overwrite frozen fixtures; regenerate them in a separate working directory. Prepare the relevant hardware before running any `run_*`, `capture_*`, `probe_*`, installation, or firmware-upload scripts.

On Windows, DLA-32 access through the WCH transport requires the FNIRSI/WCH driver and `CH375DLL64.dll` to be installed separately. These proprietary components and the official installers are excluded from the repository.

## ESP32 sources

`firmware/esp32-register-pwm` contains the simple register-based generator; `firmware/esp32-signal-source` contains the earlier test source. The original PlatformIO configurations use a locally installed platform. Adapt the `symlink://` path to your environment or configure the appropriate platform version before building. `.pio` files, flash images, and device backups are excluded.

`firmware/esp32-protocol-source` is the current ESP-IDF source. It uses the LEDC,
UART, SPI and I2C drivers, emits deterministic counter/CRC packets, and starts
nominal 1 MHz / 50% PWM on GPIO32. Its README documents the current channel map,
console commands and the distinction between an I2C NACK test pattern and actual
slave communication. Build/upload helpers never read or back up previous ESP
flash. Uploading a newly built image resets the board and replaces its program.

## Experimental V6 source restoration

```powershell
python tools/restore_dla32_worker_v6_source.py
```

The helper verifies all 489 V5 base files and the three overlay files before
copying. It then verifies all 490 resulting source files. It refuses an existing
destination and performs no hardware access. The restored source directory is
ignored by Git because the base and overlay already preserve it exactly.

With the original MSYS2 path mappings and dependencies prepared:

```bash
bash /tmp/dla32-project/tools/build_dla32_wch_worker_v6.sh
bash /tmp/dla32-project/tools/test_dla32_wch_worker_v6.sh fresh-unit-test-name
bash /tmp/dla32-project/tools/test_dla32_wch_worker_v6_inherited.sh
```

The V6 fixture includes use relative paths. These scripts compile the isolated
SDK0 worker core and its software tests. The core build disables C++; PulseView
also needs a compatible C++ binding and its dependencies. The retained
`package_dla32_pulseview_v6_cxx.py` documents the ABI checks used to assemble the
tested local package from V6 and the separate V5 C++/PulseView build.

Binary packages and workstation acceptance proofs are excluded from Git.
Hardware runners pin the original tested DLL and supporting-file hashes;
rebuilding does not automatically qualify a different binary. Create and verify
matching package manifests and acceptance evidence before hardware use. The
source-tree checksum is reproducible; byte-identical binaries are not promised.

The timer probe and archive verifier are separate diagnostics. The timer probe
requests/restores a process-scoped resolution and does not alter the frozen
driver. The archive verifier compares metadata, byte count and every saved logic
byte against the full original binary; exporter exit status alone is insufficient.

## Backup limitations

This backup preserves the selected development code and notes. RAW data, `.sr` captures, measurement exports, logs, screenshots, reports prepared for FNIRSI, and binary packages remain exclusively in the original local directory. They cannot be restored from this repository.


## Stream transport profiles V8–V11

Restore the archived V5 base first using the existing instructions, then choose
the required overlay. The destination must be new and inside the repository:

```powershell
python -B tools/restore_dla32_stream_source.py --profile v11 --destination artifacts/dla32-winusb-v11-source/libsigrok
```

The restorer verifies every base file, every overlay byte and the full 490-file
result. V8 is SDK0; V9 is opt-in kernel upload; V10 adds native sample reads;
V11 keeps the V7 r2 WCH lifecycle and corrects libusb command/model endpoints.
These are separate experiments, not a stable release. Source fixtures have
relative includes; workstation build/runners still require their documented
MSYS2 paths, installed SDK and newly generated acceptance evidence. Hardware
runners do not qualify arbitrary rebuilt DLLs automatically.
