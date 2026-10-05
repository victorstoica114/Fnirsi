# Build and restore

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

## Backup limitations

This backup preserves the selected development code and notes. RAW data, `.sr` captures, measurement exports, logs, screenshots, reports prepared for FNIRSI, and binary packages remain exclusively in the original local directory. They cannot be restored from this repository.
