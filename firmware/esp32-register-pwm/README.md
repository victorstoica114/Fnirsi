# Minimal PWM source using registers, classic ESP32

GPIO32 autonomously produces PWM with a nominal 50% duty cycle. The project uses
ESP-IDF 5.5.4 from the local installation, rather than Arduino, and provides no
serial commands. It does not start Wi-Fi, Bluetooth, PSRAM or other signal
generators.

High-speed LEDC channel 0 and timer 0 use the 80 MHz APB clock. The counter has
one-bit resolution: two states, one of which is HIGH. The duty register is
`1 << 4`, with no fractional component. The divider is an integer:

| Build environment | Nominal frequency | Divider | Q8 register | HIGH / LOW |
|---|---:|---:|---:|---:|
| `pwm-100k` | 100 kHz | 400 | 102400 | 5 µs / 5 µs |
| `pwm-1m` | 1 MHz | 40 | 10240 | 500 ns / 500 ns |

GPIO32 is disconnected from the RTC multiplexer, configured through IO_MUX and
connected directly through the GPIO matrix to `LEDC_HS_SIG_OUT0_IDX` (71). All
PWM operations write SoC registers; no ISR toggles the pin or changes the duty
cycle.

The SDK handles initial startup. After configuration, CPU0 masks normal
interrupts with `RSIL 15` and remains in an IRAM loop. CPU1 does not start
(`CONFIG_FREERTOS_UNICORE=y`). Boot, interrupt and task watchdogs are disabled
in this test firmware's configuration; dynamic frequency management is also
disabled. LEDC continues independently of the CPU. Reset, power and the
physical characteristics of the crystal/PLL and output can affect the signal;
simplifying the code does not prove zero physical jitter. Interrupts during
startup are not excluded.

Build without uploading or reading flash:

```powershell
$taskPwmProject = Join-Path ([IO.Path]::GetTempPath()) 'dla32-esp32-register-pwm'
# The junction created for the build points to this directory in the workspace.
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -m platformio run -d $taskPwmProject -e pwm-100k
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -m platformio run -d $taskPwmProject -e pwm-1m
```

ESP-IDF rejects spaces in the project path, so the build uses the temporary
`dla32-esp32-register-pwm` junction pointing to this directory. Files are not
moved; binaries remain in `.pio/build/pwm-100k` and `.pio/build/pwm-1m`.
PlatformIO's internal `__pio_env` component is included in the minimal CMake
list to provide the build environment; it does not add an application or
generator.

The test connections remain GPIO32 → D0 with a shared GND. The analyzer's
internal generator is a separate source on D1. After reset, PWM starts
automatically at the frequency selected at build time. The firmware does not
save or read the previous application's flash contents.

The registers were checked against the installed `soc/ledc_reg.h`,
`soc/gpio_reg.h`, `soc/io_mux_reg.h`, `soc/rtc_io_reg.h` headers and the
`hal/ledc_ll.h` implementation for the ESP32 target. Capture results must also
be compared with an oscilloscope before attributing variations to the source
or analyzer.

Both environments built successfully on 2026-10-03: reported usage was
108077 bytes of flash and 8004 bytes of RAM. The generated configuration was
checked, and disassembly confirms that the final function is in `.iram0.text`
and contains `RSIL 15`, `RSYNC`, `NOP` and the backward jump, with no calls after
interrupt masking. The timer configuration in the binaries is `0x02320001` for
100 kHz and `0x02050001` for 1 MHz. Evidence is in `BUILD_VALIDATION.json` and the
`disassembly-*.txt` files; these validate the build and do not constitute a
physical jitter measurement. Hardware evaluation is recorded separately in the
journal.
