# Test PWM source — Freenove ESP32 WROVER

Target identified on 2026-10-03: ESP32-D0WD-V3, revision v3.1, 4 MB flash, USB-SERIAL CH340 on COM33. The camera and SD card are absent, according to the user.

The initial wiring requires two wires:

| ESP32 / breakout | FNIRSI DLA-32 |
|---|---|
| GND | GND on the input connector |
| GPIO32 / IO32, corresponding S terminal | D0 / channel 0 |

Select the S terminal by tracing **IO32 on the ESP32 board**, rather than assuming that the breakout position number is the GPIO number. Power the ESP32 through USB. Do not connect the 5V/VCC/3V3 rows to D0. Disconnect the previous link from the DLA's PWM0 to this input before connecting the ESP32 output.

The program produces hardware PWM at 100 kHz and 50% duty on GPIO32, but starts with the output LOW/off. Serial commands at 115200 baud:

- `STATUS` + newline: report configuration and output state.
- `ON` + newline: enable PWM.
- `OFF` + newline: stop PWM and hold LOW.

In PulseView: Buffer, 50 MHz, 50k samples, 1.6 V threshold; D0 enabled. At 100 kHz, the nominal period is 10 µs, approximately 500 samples at 50 MHz. The ESP32 and DLA clocks are independent, so the checker must allow actual quantization/drift rather than require every period to be exactly 500 samples as with the internal generator.

Build using the existing PlatformIO Python 3.11 interpreter and local platform:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_esp32_signal_source.ps1
```

The `platformio.ini` file uses the inspected local platform, version 55.03.38, and Arduino 3.3.8. The script checks the Python version and enables `PLATFORMIO_OFFLINE=1` to prevent automatic dependency/Core updates during the build. Do not use the `platformio` command from PATH: on this PC it starts Python 3.13, which is incompatible with the platform's shared Python 3.11 environment. To reproduce the build on another computer, install/pin the same versions and adapt the local paths.

The program was **uploaded to the ESP32** after the connections were confirmed. The user explicitly requested **no backup of the ESP32 flash**, so the previous firmware was not read or saved. The DLA analyzer firmware is not changed.

Result on 2026-10-03: **successful build**, RAM 22,288 B, reported application size 304,459 B. Log: `logs/build-esp32-signal-source-fixed.log`. Image: `.pio/build/freenove-wrover/firmware.bin`, SHA-256 `b433995ce5601dc809f1f8228698aaef80904f3cb322f2308e8586829c7502cd`. PWM was subsequently checked using the real captures described below.

The upload passed hash verification for all four segments: bootloader, partitions, boot_app0 and application. Log: `logs/esp32-upload-pwm-utf8.log`. The first attempt stopped while displaying progress because of the Windows code page; it was repeated in full with Python `-X utf8` and completed successfully.

Manual control without an intentional reset, from the project root:

```powershell
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -X utf8 tools/esp32_pwm_control.py STATUS
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -X utf8 tools/esp32_pwm_control.py ON
```

The `OFF` command stops the output if the user requests it. At the user's request, **the signal is left active after testing**, with no automatic OFF when the serial port closes. A reset or USB reconnection restarts the firmware with PWM off and requires `ON` again.

Verified state: serial reports `enabled=1 ready=1`, and **Buffer 50k, Buffer 500k and Stream 500k pass PWM analysis on D0**, at 50 MHz, a 1.6 V threshold and 32 enabled channels. The 500k tests report approximately **100,002.4 Hz and 49.999% duty**, with periods of 499–500 samples; the DLA clock is not independently calibrated. Captures and reports are in `captures/2026-10-03-ESP32-PWM100kHz-D0-32ch-50MHz-*`, and Buffer 500k was reloaded in PulseView. Long Stream captures and USB 3 throughput are not validated by these short tests.
