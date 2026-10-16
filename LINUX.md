# FNIRSI DLA-32 Plus on Linux

The `fnirsi-dla32` libsigrok driver in `src/libsigrok` talks to the device
directly over libusb on Linux. No vendor driver or DLL is needed.

Tested on Ubuntu 22.04 (x86-64) over USB 3 (SuperSpeed) and USB 2 with
sigrok-cli and PulseView.

## Build

Install the usual sigrok build dependencies (Ubuntu package names):

```sh
sudo apt install build-essential autoconf automake libtool pkg-config cmake \
    libglib2.0-dev libglibmm-2.4-dev libusb-1.0-0-dev libzip-dev \
    libserialport-dev libftdi1-dev libhidapi-dev nettle-dev python3-dev \
    qtbase5-dev qttools5-dev qttools5-dev-tools libqt5svg5-dev \
    libboost-dev libboost-filesystem-dev libboost-serialization-dev \
    libboost-system-dev libboost-test-dev doxygen swig
```

Then build libsigrok, libsigrokdecode, sigrok-cli and PulseView into a local
prefix:

```sh
tools/build_linux.sh [PREFIX] [BUILD_DIR]   # defaults: ../prefix ../build
```

## Device access

Install the udev rule from `src/libsigrok/contrib` (or a single-line rule for
`1a86:5537`) and replug the device:

```sh
sudo cp src/libsigrok/contrib/60-libsigrok.rules src/libsigrok/contrib/61-libsigrok-uaccess.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Usage

The WCH VID:PID `1a86:5537` is shared with other devices, so the driver does
not auto-detect it. Pass the USB bus and address:

```sh
lsusb -d 1a86:5537          # e.g. "Bus 004 Device 011" -> conn=4.11
export LD_LIBRARY_PATH=$PREFIX/lib

sigrok-cli -d fnirsi-dla32:conn=4.11 --show
sigrok-cli -d fnirsi-dla32:conn=4.11 -c samplerate=50MHz --samples 1M -o capture.sr
sigrok-cli -d fnirsi-dla32:conn=4.11 -c samplerate=50MHz:data_source=Stream --time 10s -o long.sr
sigrok-cli -d fnirsi-dla32:conn=4.11 -c samplerate=200MHz --samples 1M -t D0=r -o trig.sr
pulseview -d fnirsi-dla32:conn=4.11
```

The address changes on every replug.

### Modes and rates

- Buffer: 1 MHz to 250 MHz on 32 channels; 500 MHz and 1 GHz with 4 channels.
- Stream (USB 3 only): up to 50 MHz on 32 channels (200 MB/s sustained),
  higher rates with fewer channels.
- Thresholds: 0.6 V, 1.6 V (3.3 V logic) and 2.5 V.
- Triggers: one channel, rising/falling/either edge or high/low level, Buffer
  mode only (untested in Stream). The trigger sits at the middle of the capture.

### PWM outputs

The four PWM generator outputs are hidden by default. Set `SR_DLA32_PWM=1` to
expose them as channel groups `PWM0`..`PWM3`:

```sh
SR_DLA32_PWM=1 sigrok-cli -d fnirsi-dla32:conn=4.11 -g PWM0 \
    -c enabled=true:output_frequency=10000:output_duty_cycle=50 --set
```

## Known limitations

- Each acquisition waits 0.8 s after the previous STOP, and 2 s for the first
  acquisition of a session or after switching Buffer/Stream. The device
  misbehaves if commands arrive sooner.
- The first 4 KiB of every Buffer upload is discarded because it can contain
  data from the previous capture. The requested sample count is still
  delivered.
- Level-trigger markers are placed at the expected position and may be off by
  a few hundred samples; edge-trigger markers are exact.
- Continuous sampling keeps every sample in the frontend. At 50 MHz on 32
  channels that is 200 MB/s, which PulseView cannot hold for long; use fewer
  channels or a lower rate for continuous runs.
- Linux limits usbfs buffers to 16 MiB per process by default; the driver
  uses three 4 MiB transfers to stay within it.
