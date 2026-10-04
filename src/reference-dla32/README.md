# PXView FNIRSI DLA-32 Integration Overlay

A clean, non-fork integration overlay that adds native **FNIRSI DLA-32** (32-channel USB 3.0 Logic Analyzer) support to [PXView](https://github.com/PXLogic/PXView) and its vendored `libsigrok`.

---

## 1. Repository Structure & Logical Separation

This repository contains only the minimal patches and overlay glue needed to build FNIRSI DLA-32 support against upstream checkouts:

- **Core Integration Patches**:
  - `fnirsi-protocol.patch`: Adds the `fnirsi-dla32` driver to `libsigrok` (dual-mode Windows WCH + Linux `libusb` transport, real-time bit-matrix de-interleaving, dynamic channel packing, voltage thresholds, and acquisition lifecycle).
  - `pxview-build.patch`: Build system patches for PXView packaging and Linux dependency resolution.
- **Automation & Apply Scripts**:
  - `apply.sh`: Validates pinned commit SHAs of PXView and `libsigrok` and cleanly applies both patches.
- **Reproduction & Investigation Tools**:
  - `tools/`: Optional USB inspection and capture-analysis helpers used during protocol reverse engineering. These are supporting tools, not part of the PXView/libsigrok overlay.
- **Documentation & Reverse Engineering Evidence**:
  - `DLNA32/PROTOCOL.md`: Detailed hardware protocol description, packet byte maps, DAC/comparator gain controls, and wire formats.

---

## 2. Driver Capabilities & Hardware Architecture

### Dual-Mode Transport
- **Windows (Native WCH Vendor Driver)**:
  Interfaces with the official `CH375_A64.sys` driver via `CH375DLL64.dll`. Control packets are dispatched via `CH375WriteData`, and high-speed logic samples are streamed from bulk pipe 1 (`CH375ReadEndP`). **No Zadig WinUSB driver replacement required.**
- **Linux / POSIX (`libusb`)**:
  Standard asynchronous bulk transfers using endpoint `0x02` (OUT) for commands and `0x81` (IN) for logic streaming.

### Real-Time De-interleaving Engine
The DLA-32 FPGA transmits sample data in bit-sliced wire subframes of `unitsize * 8` bytes. Each byte contains 8 consecutive temporal samples ($t_0 \dots t_7$) for a single enabled channel. The driver dynamically unpacks and transposes these bit matrices into standard sample-interleaved `LA_SPLIT_DATA` packets in real time.

### Dynamic Channel Scaling
Channel data width scales automatically based on the enabled channels in PXView:
- **8 Channels**: `unitsize = 1` byte per sample (8-byte FPGA wire frame).
- **16 Channels**: `unitsize = 2` bytes per sample (16-byte FPGA wire frame).
- **32 Channels**: `unitsize = 4` bytes per sample (32-byte FPGA wire frame).

### Voltage Levels & Threshold Controls
The protocol exposes five named logic standards with synchronized comparator gain / hysteresis range settings:
- `1.2V Logic` (DAC Vth = 0.60V, Range = `0x07`)
- `1.8V Logic` (DAC Vth = 0.90V, Range = `0x02`)
- `2.5V Logic` (DAC Vth = 1.25V, Range = `0x02`)
- `3.3V Logic` (DAC Vth = 1.60V, Range = `0x02`)
- `5.0V Logic` (DAC Vth = 2.50V, Range = `0x01`)

The verified driver profiles currently cover `1.2V`, `3.3V`, and `5.0V`.
The `1.8V`, `2.5V`, and arbitrary continuous VTH combinations remain exposed
for future capture validation but are rejected before transmission today.

### Verified Setup Profiles
The driver sends only setup packets validated against captured hardware traffic.
The 50 MHz default profiles remain available, along with captured 1/2/5/10 ms
profiles at 50 MHz and 1 ms profiles at 200 MHz and 1000 MHz for 3.3 V CMOS.
Buffer/Normal mode is verified; Stream/Loop and other unverified combinations
are rejected before transmission rather than sent with guessed derived fields.
The updated patch was built and packaged successfully with MSYS2 UCRT64 and
tested against live hardware. A combination such as `1000 MHz` with `200 us`
is intentionally rejected because no matching setup profile has been
validated.

---

## 3. Upstream Repositories & Pinned Revisions

| Component | Upstream Repository | Pinned Revision |
| :--- | :--- | :--- |
| **PXView** | `https://github.com/PXLogic/PXView.git` | `59263ff70602e40dc2f3e659b0b434aab30dd500` |
| **Vendored libsigrok** | Submodule in `PXView/libsigrok` | `34362ac9120dbe124e74b1a8c0efd6611b9ecff1` |
| **DLA32 Protocol Reference** | `https://github.com/TechBirdCompany/sigrok-fnirsi-dla32.git` | `cd4252ffe0bde8e1b49e83a4dfffcddf96c549ff` |

---

## 4. Applying the Overlay

Clone PXView and apply the overlay using `apply.sh`:

```sh
# Clone upstream PXView
git clone https://github.com/PXLogic/PXView.git
cd PXView
git submodule update --init --recursive

# Apply overlay patches
cd ../PXView-DLA32-overlay
./apply.sh /path/to/PXView
```

---

## 5. Build & Packaging Instructions

### Linux Build
```sh
cd /path/to/PXView
./build_linux.sh --no-deps
```

### Windows Build (MSYS2 UCRT64)

1. Open **MSYS2 UCRT64** terminal and build PXView:
```sh
cd /path/to/PXView
mkdir -p build && cd build
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="../install.dir"
ninja install
```

2. Package the release binary and bundle the WCH runtime library:
```sh
cd ..
bash window/package.sh

# Copy WCH DLL to package directory
cp "/c/Program Files/DLA_Logic/CH375DLL64.dll" package/
```

3. Run `package/PXView.exe`. Connect the FNIRSI DLA-32 via USB 3.0 and click **Scan for Devices** / select **FNIRSI DLA-32**.

---

## 6. AI Attribution

The overlay source, integration scripts, and investigation tools were generated
with assistance from **GitHub Copilot**. The underlying model identifier is not
exposed by this workspace session, so no more specific model name can be stated
reliably. Each executable source file carries the same attribution locally.

The code was human-reviewed and validated against live FNIRSI DLA-32 hardware,
including the patched PXView build script when applied.

---

## 7. License

The patches and driver implementation are licensed under the **GNU General Public License v3.0 (GPL-3.0)**, consistent with libsigrok and PXView upstream licensing.
