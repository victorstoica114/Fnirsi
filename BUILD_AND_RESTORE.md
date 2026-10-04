# Compilare și restaurare

## Restaurarea surselor

```powershell
git clone https://github.com/victorstoica114/Fnirsi.git
Set-Location Fnirsi
```

Toate sursele publicate sunt fișiere obișnuite. Nu există submodule sau obiecte Git LFS de descărcat. Istoricul intern `.git` al checkout-urilor upstream rămâne local; reviziile de origine sunt documentate în `sources.lock.json`.

`backup/source-files.sha256.json` identifică fiecare fișier original inclus, inclusiv arhiva criptată. Copiile de surse au fost verificate față de fișierele locale originale înainte de publicare.

## Restaurarea jurnalelor tehnice

Obține separat cheia locală `DRIVER_NOTES_DECRYPTION_KEY.txt`. Păstreaz-o în afara repository-ului. Cu 7-Zip instalat:

```powershell
& 'C:\Program Files\7-Zip\7z.exe' x 'private\driver-work-notes-2026-10-04.7z' '-o..\Fnirsi-private-notes'
```

Introdu cheia când 7-Zip o solicită. Arhiva conține numai jurnalele tehnice selectate, fără capturi sau corespondență.

## Mediul Windows

Compilarea existentă folosește MSYS2 UCRT64, GCC/G++, Autotools, pkg-config, CMake și Ninja. Componentele sigrok necesită dependențele lor, inclusiv GLib, libusb, libzip și libserialport; PulseView necesită Qt și Boost. Consultă și instrucțiunile din componentele upstream.

Scripturile originale presupun următoarele mapări în mediul MSYS2:

| Cale MSYS2 | Conținut |
| --- | --- |
| `/tmp/dla32-project` | Rădăcina acestui checkout. |
| `/tmp/dla32-src` | Directorul `src` din checkout. |
| `/tmp/dla32-build` | Directorul de compilare al integrării principale. |
| `/opt/dla32` | Prefixul de instalare al integrării principale. |

Creează mapările în mediul de compilare sau adaptează căile înainte de rulare. Wrapper-ele PowerShell citesc `tools/toolchain-path.txt`, un fișier local exclus din Git: acesta trebuie să conțină calea instalării MSYS2. Unele scripturi auxiliare păstrează căile mediului original și trebuie ajustate pentru alt calculator.

Pentru integrarea principală, după pregătirea mediului:

```bash
bash /tmp/dla32-project/tools/build_windows.sh all
```

Pentru varianta experimentală V5:

```bash
bash /tmp/dla32-project/tools/build_dla32_wch_stop_v5.sh 0
bash /tmp/dla32-project/tools/build_dla32_wch_stop_v5.sh 1
```

Aceste scripturi compilează variante separate și rulează verificările software prevăzute. Compilarea V5 este pentru diagnostic și dezactivează binding-ul C++; ea nu produce singură o bibliotecă potrivită pentru PulseView. Integrarea în PulseView trebuie să păstreze opțiunea `--enable-cxx` din compilarea principală.

Sursele fixture-urilor din `artifacts/*-tests` sunt incluse și păstrează unele directive `#include` cu căile absolute ale mediului original. Pentru alt checkout, adaptează aceste căi în copiile de lucru. Generatoarele existente refuză suprascrierea fixture-urilor înghețate; regenerarea trebuie făcută într-un director de lucru separat. Nu rula scripturile `run_*`, `capture_*`, `probe_*`, de instalare sau de upload firmware fără pregătirea hardware-ului corespunzător.

Pe Windows, accesul la DLA-32 prin transportul WCH necesită instalarea separată a driverului FNIRSI/WCH și a bibliotecii `CH375DLL64.dll`. Aceste componente proprietare și instalatoarele oficiale sunt excluse din repository.

## Sursele ESP32

`firmware/esp32-register-pwm` păstrează generatorul simplu bazat pe registre; `firmware/esp32-signal-source` păstrează sursa de test anterioară. Configurațiile PlatformIO originale folosesc o platformă instalată local. Adaptează calea `symlink://` la propriul mediu sau configurează versiunea potrivită a platformei înainte de compilare. Fișierele `.pio`, imaginile de flash și backupurile dispozitivului nu sunt incluse.

## Limitele acestui backup

Backupul păstrează codul și notele de dezvoltare selectate. Datele RAW, capturile `.sr`, exporturile de măsurători, logurile, capturile de ecran, rapoartele destinate FNIRSI și pachetele binare rămân exclusiv în directorul local original. Ele nu pot fi recuperate din acest repository.
