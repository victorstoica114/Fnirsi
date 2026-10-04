# Sursă PWM de test — Freenove ESP32 WROVER

Țintă identificată pe 2026-10-03: ESP32-D0WD-V3, revizia v3.1, flash 4 MB, USB-SERIAL CH340 pe COM33. Camera și cardul SD lipsesc, conform utilizatorului.

Prima schemă necesită două fire:

| ESP32 / breakout | FNIRSI DLA-32 |
|---|---|
| GND | GND de la conectorul de intrări |
| GPIO32 / IO32, terminalul S corespunzător | D0 / canalul 0 |

Terminalul S se alege urmărind pinul **IO32 de pe placa ESP32**, nu presupunând că numărul poziției breakout este numărul GPIO. Alimentăm ESP32 prin USB. Nu conectăm rândurile 5V/VCC/3V3 la D0. Desprindem legătura anterioară de la PWM0 al DLA către această intrare înainte de a lega ieșirea ESP32.

Programul produce PWM hardware la 100 kHz și 50% pe GPIO32, însă pornește cu ieșirea LOW/oprită. Pe serial, la 115200 baud:

- `STATUS` + newline: configurație și starea ieșirii.
- `ON` + newline: activează PWM.
- `OFF` + newline: oprește PWM și menține LOW.

În PulseView: Buffer, 50 MHz, 50k samples, prag 1,6 V; D0 activ. La 100 kHz, perioada nominală este 10 µs, aproximativ 500 de eșantioane la 50 MHz. Ceasurile ESP32 și DLA sunt independente, deci verificatorul trebuie să admită cuantizarea/driftul real, nu să impună fiecare perioadă exact 500 ca la generatorul intern.

Compilare, cu interpretorul Python 3.11 al PlatformIO existent și platforma locală:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_esp32_signal_source.ps1
```

Fișierul `platformio.ini` folosește platforma locală inspectată, versiunea 55.03.38, și Arduino 3.3.8. Scriptul verifică versiunea Python și activează `PLATFORMIO_OFFLINE=1` pentru a evita actualizarea automată a dependențelor/Core în timpul compilării. Nu folosim comanda `platformio` din PATH: pe acest PC ea pornește Python 3.13, incompatibil cu mediul comun Python 3.11 al platformei. Pentru reproducere pe alt calculator trebuie instalate/fixate aceleași versiuni și adaptate căile locale.

Programul a fost **încărcat în ESP32** după confirmarea conexiunilor. Utilizatorul a cerut explicit **fără copie a flash-ului ESP32**, deci nu am citit/salvat firmware-ul anterior. Nu schimbăm firmware-ul analizorului DLA.

Rezultat 2026-10-03: **compilare reușită**, RAM 22.288 B, dimensiune raportată a aplicației 304.459 B. Log: `logs/build-esp32-signal-source-fixed.log`. Imagine: `.pio/build/freenove-wrover/firmware.bin`, SHA-256 `b433995ce5601dc809f1f8228698aaef80904f3cb322f2308e8586829c7502cd`. PWM este ulterior verificat prin capturile reale descrise mai jos.

Încărcarea a trecut verificarea hash-ului pentru toate cele patru segmente: bootloader, partiții, boot_app0 și aplicație. Log `logs/esp32-upload-pwm-utf8.log`. Prima încercare s-a oprit la afișarea progresului din cauza codepage-ului Windows; reluată integral cu Python `-X utf8` și finalizată corect.

Control manual fără reset intenționat, din rădăcina proiectului:

```powershell
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -X utf8 tools/esp32_pwm_control.py STATUS
& 'C:\Users\User\.platformio\penv\Scripts\python.exe' -X utf8 tools/esp32_pwm_control.py ON
```

Comanda `OFF` oprește ieșirea dacă utilizatorul o solicită. La cererea lui, **lăsăm semnalul activ după probe**, fără OFF automat la închiderea portului serial. Un reset sau o reconectare USB repornește firmware-ul cu PWM oprit și necesită din nou `ON`.

Stare verificată: serialul confirmă `enabled=1 ready=1`, iar **Buffer 50k, Buffer 500k și Stream 500k trec analiza PWM pe D0**, la 50 MHz, prag 1,6 V și 32 canale active. Probele de 500k raportează aproximativ **100.002,4 Hz și 49,999% duty**, cu perioade de 499–500 eșantioane; ceasul DLA nu este calibrat independent. Capturile și rapoartele sunt în `captures/2026-10-03-ESP32-PWM100kHz-D0-32ch-50MHz-*`, iar Buffer 500k este reîncărcat în PulseView. Stream lung și debitul USB 3 nu sunt validate de aceste probe scurte.
