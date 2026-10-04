# Sursă PWM minimală pe registre, ESP32 clasic

GPIO32 produce autonom un PWM cu duty nominal 50%. Proiectul folosește ESP-IDF
5.5.4 din instalarea locală, nu Arduino, și nu oferă comenzi seriale. Nu pornește
Wi-Fi, Bluetooth, PSRAM sau alte generatoare de semnal.

LEDC high-speed, canalul 0 și timerul 0, folosește APB de 80 MHz. Contorul are
rezoluție de un bit: două stări, dintre care una este HIGH. Registrul duty este
`1 << 4`, fără componentă fracționară. Divizorul este întreg:

| Mediu de compilare | Frecvență nominală | Divizor | Registru Q8 | HIGH / LOW |
|---|---:|---:|---:|---:|
| `pwm-100k` | 100 kHz | 400 | 102400 | 5 µs / 5 µs |
| `pwm-1m` | 1 MHz | 40 | 10240 | 500 ns / 500 ns |

GPIO32 este detașat de multiplexorul RTC, configurat prin IO_MUX și conectat
direct în matrice la `LEDC_HS_SIG_OUT0_IDX` (71). Toate comenzile PWM sunt
scrieri în registrele SoC; nu există ISR care să comute pinul sau să modifice duty.

SDK-ul efectuează pornirea inițială. După configurare, CPU0 maschează
întreruperile normale prin `RSIL 15` și rămâne într-o buclă în IRAM. CPU1 nu
pornește (`CONFIG_FREERTOS_UNICORE=y`). Watchdog-urile de boot, întreruperi și
task sunt dezactivate în configurația acestui firmware de test; gestionarea
dinamică a frecvenței este dezactivată. LEDC continuă independent de CPU.
Resetul, alimentarea și caracteristicile fizice ale cristalului/PLL-ului și
ieșirii pot influența semnalul; simplificarea codului nu dovedește jitter fizic
zero. Întreruperile din etapa de pornire nu sunt excluse.

Compilare, fără upload sau citire flash:

```powershell
$taskPwmProject = Join-Path ([IO.Path]::GetTempPath()) 'dla32-esp32-register-pwm'
# Joncțiunea creată pentru compilare pointează spre acest director din workspace.
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -m platformio run -d $taskPwmProject -e pwm-100k
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -m platformio run -d $taskPwmProject -e pwm-1m
```

ESP-IDF refuză spațiile din calea proiectului, de aceea compilarea folosește
joncțiunea temporară `dla32-esp32-register-pwm` către acest director. Fișierele
nu sunt mutate; binarele rămân în `.pio/build/pwm-100k` și `.pio/build/pwm-1m`.
Componenta internă PlatformIO `__pio_env` este inclusă în lista minimă CMake
pentru a furniza mediul de compilare; nu adaugă o aplicație sau un generator.

Conexiunile de test rămân GPIO32 → D0 și GND comun. Generatorul intern al
analizorului este o sursă separată pe D1. După reset, PWM pornește automat la
frecvența selectată la compilare. Firmware-ul nu salvează și nu citește flash-ul
aplicației precedente.

Registrele au fost confruntate cu headerele instalate `soc/ledc_reg.h`,
`soc/gpio_reg.h`, `soc/io_mux_reg.h`, `soc/rtc_io_reg.h` și implementarea
`hal/ledc_ll.h` pentru ținta ESP32. Orice rezultat al capturii trebuie comparat
și cu osciloscopul înainte de a atribui variațiile sursei sau analizorului.

Ambele medii au fost compilate cu succes la 2026-10-03: utilizare raportată
108077 octeți flash și 8004 octeți RAM. Configurația generată a fost verificată,
iar dezasamblarea confirmă că funcția finală se află în `.iram0.text` și conține
`RSIL 15`, `RSYNC`, `NOP` și saltul înapoi, fără apeluri după mascare.
Configurația timerului din binare este `0x02320001` pentru 100 kHz și
`0x02050001` pentru 1 MHz. Dovezile sunt în `BUILD_VALIDATION.json` și fișierele
`disassembly-*.txt`; acestea verifică compilarea și nu reprezintă o măsurare
fizică a jitter-ului. Evaluarea pe hardware este consemnată separat în jurnal.
