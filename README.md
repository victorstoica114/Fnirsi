# FNIRSI DLA-32 Plus în PulseView

Proiect de integrare a analizorului FNIRSI DLA-32 Plus în libsigrok și PulseView, cu transport USB 3 pe Windows prin driverul WCH și codul de transport libusb. Obiectivul este o integrare fiabilă, verificată pe hardware.

Acest repository păstrează munca de dezvoltare: sursele, variantele succesive ale driverului, scripturile de compilare, testele și programele ESP32 folosite ca surse de semnal. Capturile, aplicațiile descărcate, executabilele compilate și pachetele de probe pentru producător nu sunt incluse.

## Starea proiectului la 4 octombrie 2026

Integrarea inițială funcționează și a afișat PWM în PulseView. Validarea completă rămâne în lucru.

- `src/libsigrok` și `src/pulseview` păstrează sursele integrării principale.
- `artifacts/dla32-wch-stop-v5-source/libsigrok` păstrează ultima variantă experimentală V5. Aceasta corectează păstrarea obligației de STOP și recuperarea resurselor pentru politica SDK0, cu păstrarea politicii SDK1.
- V5 are verificări software și probe hardware parțiale. Nu a înlocuit DLL-ul folosit de instalarea principală PulseView.
- Rămân de investigat deplasările canalelor în Stream, o captură Buffer incompletă și apariția unor fragmente de comandă în datele returnate de SDK. Cauza exactă nu este stabilită.
- Integritatea unui Stream lung și ținta de 200 MB/s fără pierderi nu sunt confirmate.

Următorii pași sunt instrumentarea graniței SDK/kernel/USB, închiderea testelor de pornire/oprire/recuperare și validarea tuturor canalelor, măștilor, triggerelor, pragurilor, ieșirilor PWM și protocoalelor.

## Conținut

| Director | Rol |
| --- | --- |
| `src/` | Sursele de lucru și referințele upstream, cu reviziile din `sources.lock.json`. |
| `artifacts/*-source/` | Variantele izolate ale driverului, păstrate pentru comparație și reproducere. |
| `artifacts/*-tests/` | Sursele fixture-urilor pentru testele software. |
| `artifacts/source-changes/` | Patch-urile integrării inițiale. |
| `tools/` | Scripturi de compilare, analiză și testare; includ scripturi care accesează hardware-ul dacă sunt executate. |
| `firmware/` | Codul ESP32 folosit pentru semnale de referință. |
| `private/` | Jurnalele tehnice într-o arhivă 7z criptată, cu numele fișierelor criptate. |
| `backup/` | Lista SHA-256 a fișierelor originale incluse și verificarea pregătirii backupului. |

Vezi [instrucțiunile de compilare și restaurare](BUILD_AND_RESTORE.md). Nu există executabile gata de instalat în acest backup.

## Jurnalele private

`private/driver-work-notes-2026-10-04.7z` conține nouă jurnale tehnice. Cheia este păstrată separat, local; nu este în Git. Corespondența și documentele de trimitere către FNIRSI sunt excluse din acest backup.

Arhiva a fost testată, extrasă într-un director separat, iar toate cele nouă fișiere restaurate au fost comparate prin SHA-256 cu originalele. O cheie greșită nu permite listarea numelor fișierelor.

## Proveniență și licențe

Reviziile de origine sunt în [sources.lock.json](sources.lock.json). Fișierele `COPYING`, `AUTHORS` și celelalte notificări din componentele upstream sunt păstrate. Fiecare componentă își păstrează licența; acest repository nu atribuie o licență nouă întregului proiect. Bibliotecile și driverul proprietar WCH trebuie obținute separat.
