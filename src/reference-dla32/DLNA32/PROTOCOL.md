# FNIRSI DLNA32 Protocol Investigation

This document records only evidence produced by the current automated analysis.
Interpretations marked **Hypothesis** require differential captures or device
responses before they should influence a libsigrok driver.

## Current evidence

The supplied [Test.pcapng](Dumps/Test.pcapng) contains:

- One pcapng interface named `\\.\USBPcap2`, link type 249, snaplen 65535.
- 10 enhanced packets: USB enumeration, configuration, two non-empty bulk-OUT
  packets, and two zero-length bulk-OUT completions.
- The configuration descriptor declares a vendor-specific interface and
  multiple SuperSpeed bulk endpoints.
- The two non-empty bulk packets have 2040 extracted application bytes each.
- Their first bytes are respectively:
  - `0a 17 0b 00 10 00 2d 31 01 14 00 00 00 0b 95 5b c9 8b`
  - `0a 17 03 00 10 0b e4 3e c0 2d`

The reproducible extraction is in [analyze_usb_capture.py](analyze_usb_capture.py).
Generated JSON, Markdown, and binary payloads are under `analysis/`.

The two additional captures are:

- `500ms_50MHz_3.3V_Buffer_Normal_PWMoff.pcapng`
- `500ms_50MHz_3.3V_Stream_Loop.pcapng`

Both contain 36 packets and the complete command/stream/stop sequence.

Additional captures now include:

- `Idle.pcapng`
- `8Ch_500ms_50MHz_3.3V_Buffer_Normal_PWMoff.pcapng`
- `16Ch_500ms_50MHz_3.3V_Buffer_Normal_PWMoff.pcapng`
- `32Ch_500ms_50MHz_3.3V_Buffer_Normal_PWMoff.pcapng`
- `PWM0_5MHz_Duty20_Off.pcapng`
- `PWM0_5MHz_Duty20_On.pcapng`
- `PWM0_10MHz_Duty20_On.pcapng`
- `PWM0_10MHz_Duty50_On.pcapng`
- `PWM3_1MHz_Dzty50_On_Off.pcapng` (filename spelling retained from capture)
- `500ms_50MHz_1.2V_Buffer_Normal_PWMoff.pcapng`
- `500ms_50MHz_5.0V_Buffer_Normal_PWMoff.pcapng`
- `1MHz_50_onCH1_500ms_50Mhz_1.2V.pcapng` (label CH1 = protocol CH0)
- `1MHz_50_onCH2_500ms_50Mhz_1.2V.pcapng` (label CH2 = protocol CH1)
- `1MHz_50_onCH4_500ms_50Mhz_1.2V.pcapng` (label CH4 = protocol CH3)
- `1MHz_50_CH4_500ms_50Mhz_1.2V.pcapng` (physically/UI CH3 = protocol CH2)
- `1MHz_50_UI_CH5_Protocol_CH4_500ms_50MHz_1.2V_Buffer_Normal_PWMoff.pcapng`
  (physical/UI CH5, second channel of cable group 2 = protocol CH4)
- `1MHz_50_UI_CH6_Protocol_CH5_500ms_50MHz_1.2V_Buffer_Normal_PWMoff.pcapng`
  (physical/UI CH6, third channel of cable group 2 = protocol CH5)
- `1MHz_50_UI_CH7_Protocol_CH6_500ms_50MHz_1.2V_Buffer_Normal_PWMoff.pcapng`
  (physical/UI CH7, fourth channel of cable group 2 = protocol CH6)
- `1MHz_50_UI_CH8_Protocol_CH7_500ms_50MHz_1.2V_Buffer_Normal_PWMoff.pcapng`
  (physical/UI CH8, first channel of cable group 3 = protocol CH7)
- `Mixed_PWM0-3_UI_CH5-CH8_500ms_50MHz_1.2V_Buffer_Normal.pcapng`
  (four PWM outputs on physical/UI CH5-CH8)
- `Start_Stop_3_times_500ms_50MHz_1.2V_Stream_Upload_Roll_PWM0_1MHZ_50.pcapng`
- `Samplerate_200MHz.pcapng`
- `Samplerate_1000MHz.pcapng`
- `Capturetime_1s.pcapng`
- `Capturetime_4s.pcapng`

## Facts versus hypotheses

### Facts

- The device is USB vendor-specific rather than a standard class device.
- The observed non-empty transfers go to candidate endpoint `0x02`.
- The observed USBPcap transfer-type field candidate is `3`, consistent with
  bulk transfer classification in this capture.
- The application bytes begin at byte 35 of the captured USBPcap record for
  these bulk packets.
- `0a 17 0b` occurs once in the capture. `0a 11 1e`, `0a 12 27`, and
  `0a 15 02` do not occur in the binary capture.
- In both new captures, `0a 11 1e` is followed by `0a 12 27`, large bulk-IN
  transfers on candidate endpoint `0x81`, and later `0a 15 02`.
- The new captures contain three 65500-byte candidate bulk-IN payloads in the
  Stream/Loop capture and two in the Buffer/Normal capture. Their first 32
  application bytes are zero in the extracted data.
- The `0a 11 1e` payloads differ at application-payload offsets 5, 6, and
  33-36: offsets 5/6 change from `01 00` to `00 01`, and offsets 33-36 also
  change. Buffer/Normal has `01 00`; Stream/Loop has `00 01`.
- The `0a 12 27` payloads and `0a 15 02` payloads have the same observed
  prefixes in both captures.
- `Idle.pcapng` contains only enumeration and configuration: six packets and
  no vendor bulk transfers.
- The 8/16/32-channel captures have identical `0a 11 1e` setup payloads.
- Their `0a 12 27` payloads change as follows in the first bytes:
  - 8 channels: `0a 12 27 00 00 ff 01 00`
  - 16 channels: `0a 12 27 00 00 ff ff 00`
  - 32 channels: `0a 12 27 00 00 ff ff ff ff`
- The channel-count comparisons also change four trailing bytes at offsets
  42-45, consistent with a derived field or checksum hypothesis.
- The PWM OFF capture sends `0a 17 03 00 10 0b e4 3e c0 2d`; the PWM ON
  capture sends `0a 17 0b 00 10 40 4b 4c 00 14 00 00 00 0b 08 d7 ee c0`.
  Both captures otherwise contain only enumeration and one command transfer.
- The 5 MHz/20% ON and 10 MHz/20% ON captures share an identical first
  `0a 17 0b` payload. The 10 MHz capture then contains two additional
  `0a 17 0b` transfers.
- In the first command of the 10 MHz/20% ON capture, the apparent PWM block is
  `40 4b 4c 00 14`; in the 10 MHz/50% ON capture it is `80 96 98 00 32`.
  The `14` to `32` change is consistent with 20% to 50% duty, but this is not
  proven because other bytes change too.
- Comparing the first 5 MHz/20% and first 10 MHz/20% commands gives no byte
  differences. The later 10 MHz commands differ at offsets 6-7 and 20-23
  relative to the 5 MHz command, so their transaction role is unknown.
- `PWM3_1MHz_Dzty50_On_Off.pcapng` contains one ON command followed by one OFF
  command: `0a 17 0b` then `0a 17 03`.
- Both PWM3 commands contain `00 13` at application offsets 3-4, whereas the
  current PWM0 commands contain `00 10` at those offsets.
- The PWM3 ON command retains `0x32` at application offset 9, matching the
  documented 50% duty captures. The PWM3 OFF command retains the same command
  family form as PWM0 OFF but changes its parameter bytes.
- The 1.2 V, 3.3 V, and 5.0 V threshold captures have the same command
  sequence, payload lengths, and channel-mask command. Only `0a 11 1e` has
  threshold-dependent changes.
- In `0a 11 1e`, application offsets 14 and 16 are:
  - 1.2 V: offset 14=`3c`, offset 16=`07`
  - 3.3 V: offset 14=`a0`, offset 16=`02`
  - 5.0 V: offset 14=`fa`, offset 16=`01`
- The UI labels these as external logic standards. In particular, selecting
  `3.3 V CMOS` calculates an internal comparator DAC threshold of `1.6 V`,
  represented by offset 14=`a0`; `a0` is not a direct 3.3 V DAC value.
- The same threshold captures also change application offsets 33-36. These
  four bytes are treated as a derived field or checksum candidate, not as
  independent threshold values.
- A live 1 ms, 50 MHz threshold matrix reproduced those values while keeping
  the samplerate and sample limit constant. The observed derived bytes at
  offsets 33-36 were `9f 73 5d 5b` (1.2 V), `1a 6d 35 bd` (3.3 V), and
  `1f 30 56 12` (5.0 V).
- A controlled 50 MHz, 3.3 V CMOS, Buffer/Normal, CH0-only sweep confirmed
  the sample-limit encoding at multiple capture times: 1 ms = 50,000
  (`50 c3 00 00`), 2 ms = 100,000 (`a0 86 01 00`), 5 ms = 250,000
  (`90 d0 03 00`), and 10 ms = 500,000 (`20 a1 07 00`). The derived bytes
  changed for each duration, so they must be generated or selected together
  with the capture limit.
- A live Buffer-to-Stream test kept the samplerate, threshold, and 1 ms sample
  limit unchanged. The setup mode bytes changed from `01 00` to `00 00` at
  application offsets 5-6. Earlier Stream/Loop captures showed `00 01`, so
  this session does not yet establish whether the UI selection was Stream
  Loop, another stream mode, or a separate mode flag.
- The captures labeled CH1, CH2, and CH4, corresponding to protocol channels
  CH0, CH1, and CH3, have identical `0a 11 1e`, `0a 12 27`,
  and `0a 15 02` command payloads. Line selection is therefore not visible in
  those control commands.
- Each line capture contains seven large endpoint-`0x81` chunks of 65500
  extracted bytes. The first chunk shows an 8-byte periodic structure:
  - Protocol CH0 (file label CH1): modulo-8 lane 0 varies; the other
    lanes are predominantly `ff`.
  - Protocol CH1 (file label CH2): modulo-8 lane 1 varies; the other lanes
    are predominantly `ff`.
  - Protocol CH3 (file label CH4): modulo-8 lane 3 varies; the other lanes
    are predominantly `ff`.
- The varying lanes contain repeated `00`/`ff`-like values and intermediate
  transition values such as `f8`, `1f`, `e0`, and `7f`. This is consistent with
  a packed or bit-sliced digital stream, but does not by itself prove whether
  one 8-byte unit represents eight time samples or eight channel lanes.
- The repeated start/stop capture contains 110 packets and three occurrences
  each of `0a 11 1e`, `0a 12 27`, and `0a 15 02`.
- The three setup commands are byte-identical to each other. The three channel
  mask commands are byte-identical to each other, and the three stop commands
  are byte-identical to each other.
- The three acquisition cycles contain seven, seven, and six large endpoint-
  `0x81` chunks respectively. The final cycle is shorter in the capture, so
  this does not establish a device-side fixed chunk count.
- Relative to the single corrected-CH0 capture, the repeated-cycle setup command changes
  only offsets 5-6 and derived offsets 33-36; the channel-mask and stop commands
  are identical.
- The 200 MHz and 1000 MHz captures each contain one setup command, one channel
  mask command, and one stop command. The mask and stop payloads are identical
  to the 50 MHz baseline.
- The `0a 11 1e` setup command changes 13 bytes relative to the 50 MHz capture,
  concentrated at offsets 9-13, 24-27, and 33-36. The values at offsets 14 and
  16 remain the 1.2 V threshold values (`3c` and `07`).
- The 200 MHz capture contains four 65500-byte endpoint-`0x81` chunks. The
  1000 MHz capture contains sixteen such chunks.
- The 200 MHz stream uses four dominant byte values: `00`, `0f`, `f0`, and
  `ff`. The 1000 MHz stream uses `00`, `01`, `fc`, and `ff`. This is a clear
  data-stream change, but its relationship to clock source, sample rate, and
  input pattern is not yet isolated.
- A live USBPcap session confirmed a clean `50 MHz -> 200 MHz -> 50 MHz`
  round trip. The first and third setup commands were byte-identical; only the
  middle setup command differed.
- In the setup command, application offsets 9-12 encode the samplerate as a
  little-endian value in Hz: `80 f0 fa 02` = 50,000,000 and `00 c2 eb 0b` =
  200,000,000.
- The same experiment changed offset 13 from `08` to `0b`, offsets 24-27 from
  `20 a1 07 00` to `80 84 1e 00` (500,000 to 2,000,000), and the trailing
  derived bytes at offsets 33-36. The role of offset 13 and the exact meaning
  of the scaled field remain to be isolated.
- A live 1 ms capture at 50 MHz retained the samplerate bytes `80 f0 fa 02`
  and set offsets 24-27 to `50 c3 00 00`, which is 50,000 samples in
  little-endian form. This confirms that the field is the requested sample
  limit for the short acquisition.
- A second live 1 ms matrix confirmed the scaling at all three tested rates:
  - 50 MHz: samplerate `80 f0 fa 02`, limit `50 c3 00 00` = 50,000.
  - 200 MHz: samplerate `00 c2 eb 0b`, limit `40 0d 03 00` = 200,000.
  - 1000 MHz: samplerate `00 ca 9a 3b`, limit `40 42 0f 00` = 1,000,000.
  The limit therefore scales as $f_s \times 1\,\mathrm{ms}$ for these captures.

### Hypotheses

- `0a 17` may be a command family marker and the third byte may select a
  subcommand. This is not proven by the current two samples.
- Application-payload offsets 5 and 6 may select Buffer/Normal versus
  Stream/Loop behavior, while offsets 33-36 may be a checksum or derived
  value. These are correlations, not field definitions.
- The `0a 12 27` byte mask may represent enabled channels, with one bit or
  byte per channel group. The observed values fit 8/16/32-channel settings,
  but the exact bit ordering and whether `ff` means enabled are unproven. The
  physical CH3 capture producing `0x08` means the mask mapping needs direct
  single-channel calibration rather than filename-based interpretation.
- `0a 17 03` and `0a 17 0b` may be PWM-off and PWM-on subcommands or modes.
  The PWM pair changes multiple fields, so individual frequency/duty fields
  cannot yet be isolated.
- The `00 13` versus `00 10` difference may identify PWM3 versus PWM0, or may
  encode a device-specific output/channel plus mode. More PWM channel captures
  are required before assigning a direct channel meaning.
- Application offsets 14 and 16 of `0a 11 1e` are strong threshold-field
  candidates because they are the only non-derived setup bytes changing across
  the controlled 1.2 V, 3.3 V, and 5.0 V captures. Their units, endianness, and
  voltage conversion are unknown.
- The `0a 17 0b` command may be emitted more than once for some PWM settings;
  the 10 MHz/20% capture has three such transfers while the other current ON
  captures have one. These may be retries, channel/output setup, or separate
  PWM stages.
- The endpoint-`0x81` transfers are likely acquisition data or device status,
  but an all-zero prefix alone does not establish sample packing or framing.
- The CH0/CH1/CH2/CH3 results suggest an 8-byte interleaving or bit-sliced
  data layout in which line selection affects protocol channels 0, 1, 2, and 3.
  The mapping from those channels to lanes and time samples remains unproven.
- The focused `1MHz_50_CH4...` capture sends channel-mask prefix
  `0a 12 27 00 00 08`, while the earlier CH1/CH2/CH4 line captures sent
  `0a 12 27 00 00 0f`. Since the physical/UI source was CH3 (protocol CH2),
  the `0x08` field is not yet safely interpretable as a direct zero-based
  channel bit.
- The focused protocol-CH2 capture contains 16 large endpoint-`0x81` chunks.
  Its first chunk uses dominant values `00`, `02`, `bf`, `f4`, and `ff`; this
  differs from the earlier multi-channel line capture and is consistent with
  the changed channel mask, but sample decoding is still incomplete.
- The focused physical/UI CH5 capture has channel-mask prefix
  `0a 12 27 00 00 08` and contains 16 large endpoint-`0x81` chunks. Its
  first chunk uses dominant values `00`, `0f`, `80`, and `ff`.
- In this CH5/protocol-CH4 capture, all modulo-8 byte lanes have similar
  statistics. This weakens the earlier hypothesis that one active line always
  appears in exactly one modulo-8 byte lane.
- The physical/UI CH6 capture is command-identical to CH5 for `0a 11 1e` and
  `0a 15 02`, while its channel-mask prefix changes from `...08` to `...20`.
  Four trailing mask-command bytes also change. This confirms the mask command
  carries capture-selection state, but its direct channel encoding is still
  unresolved.
- The focused physical/UI CH6 capture contains 16 large endpoint-`0x81` chunks
  with dominant values `00`, `0f`, `80`, and `ff`.
- The physical/UI CH7 capture is command-identical to CH6 for `0a 11 1e` and
  `0a 15 02`, while its channel-mask prefix changes from `...20` to `...80`.
  Four trailing mask-command bytes also change. This continues the observed
  per-channel mask progression without proving the field encoding.
- The physical/UI CH8 capture is command-identical to CH7 for `0a 11 1e` and
  `0a 15 02`. Its channel-mask prefix changes from `...80 00` to `...00 01`,
  and four trailing mask bytes change. Individual captures now cover protocol
  channels CH0 through CH7.
- The mixed PWM capture on UI CH5-CH8 uses channel-mask prefix
  `0a 12 27 00 00 0f`, contains 121 large endpoint-`0x81` chunks, and shows
  repeated mixed byte patterns from the four outputs. This is the first capture
  suitable for separating channel position from time/sample interleaving.
- The bytes following the apparent command marker may contain a fixed header,
  length, parameters, and/or checksum. The current capture has no controlled
  parameter pair to distinguish these layouts.
- The 2040-byte application payload may be a padded command buffer because
  the USB transfer-length candidate is 2048. Its zero-filled tail must not be
  treated as protocol payload until confirmed.
- The setup fields around offsets 8-13 and 24-27 likely encode samplerate,
  clock configuration, or derived timing values. The 200 MHz and 1000 MHz
  captures are insufficient to separate those fields because both samplerate
  and clock conditions changed together.
- The 1-second and 4-second captures contain 31 and 121 large endpoint-`0x81`
  chunks respectively, each with 65500 extracted bytes. This is approximately
  four times the stream volume, with one additional chunk in the 4-second run.
- Their `0a 12 27` channel-mask commands and `0a 15 02` stop commands are
  identical. The `0a 11 1e` setup changes at offsets 17-19 and 25-27, plus
  derived offsets 33-36.
- The fields changed by capture duration may encode requested sample count,
  memory depth, or a timing-derived limit. Their units cannot be assigned from
  only 1-second and 4-second captures.

## Command catalogue status

| Candidate | Current status | Evidence needed |
| --- | --- | --- |
| `0a 17 03` | Observed in PWM OFF capture | Capture PWM frequency/duty changes with PWM kept off |
| `0a 17 0b` | Observed in Test and PWM ON captures | Capture PWM frequency/duty changes with PWM kept on |
| `0a 11 1e` | Acquisition setup/start candidate | Compare mode and samplerate experiments |
| `0a 12 27` | Acquisition/channel mask candidate | Capture channel counts between 1 and 32 |
| `0a 15 02` | Observed after streaming | Capture stop while varying acquisition state |

No command payload size, checksum, response contract, acquisition format, or
sample packing rule is established yet.

## Re-running the analysis

```powershell
python .\analyze_usb_capture.py .\Dumps\Test.pcapng --out .\analysis
python .\compare_usb_captures.py .\Dumps\500ms_50MHz_3.3V_Buffer_Normal_PWMoff.pcapng .\Dumps\500ms_50MHz_3.3V_Stream_Loop.pcapng
```

The tool does not require Wireshark or third-party Python packages. It writes:

- `analysis/capture.json`: packet metadata, candidate fields, hashes, and
  prefixes.
- `analysis/report.md`: statistical inventory and payload clusters.
- `analysis/bulk_payloads/frame-*.bin`: extracted non-empty or zero-length
  candidate bulk payloads, preserving packet numbering.

The comparator prints per-command length differences and byte offsets. It is
intended for future one-parameter capture pairs.

## Recommended captures

Use one capture per experiment, resetting the device between runs:

1. Idle enumeration only.
2. Same acquisition settings, with one samplerate changed.
  The 200 MHz and 1000 MHz captures now exist, but need matching captures with
  the clock source held constant and a known input pattern.
3. Same samplerate, with channel counts 1, 8, 16, and 32. The 8/16/32
  captures now exist; a 1-channel capture remains useful for confirming the
  mask encoding.
4. PWM off/on and multiple frequency/duty pairs. Current captures include
  5 MHz/20%, 10 MHz/20%, and 10 MHz/50%; a same-frequency OFF capture and
  more frequencies are still needed.
5. Trigger disabled/enabled with identical threshold and source. Threshold
  voltage captures at 1.2 V, 3.3 V, and 5.0 V now exist; intermediate values
  would help determine the encoding at offsets 14 and 16.
6. Acquisition start, steady streaming, stop, and restart.
7. A short capture using a deliberately recognizable digital pattern on each
   channel to infer bit order and channel packing.

For every pair, retain the complete pcapng and record the exact UI setting.
The analyzer can then be extended with aligned byte-diff and field-candidate
reports without changing the raw extraction step.

## Final handoff status

The investigation produced a working Windows PXView/libsigrok driver for the
FNIRSI DLA-32 using the factory WCH driver and `CH375DLL64.dll`. A live test
with the rebuilt package successfully captured data and stopped cleanly.

### Confirmed driver behavior

- Windows transport uses the WCH vendor API; no Zadig WinUSB replacement is
  required.
- Linux transport uses `libusb` endpoint `0x02` for commands and `0x81` for
  acquisition data.
- Setup, channel-mask, stream, de-interleaving, sample limiting, stop, and
  session-end handling are implemented.
- Validated UI samplerates are 50 MHz, 200 MHz, and 1000 MHz.
- At 50 MHz, the tested capture limits are 1, 2, 5, and 10 ms. The driver also
  retains the validated 50 MHz fallback setup for other local limits.
- The 200 MHz and 1000 MHz profiles are validated at 1 ms with 3.3 V CMOS.
- Validated named thresholds are 1.2 V, 3.3 V CMOS, and 5.0 V. For 3.3 V CMOS,
  the external logic level maps to an internal comparator threshold of 1.6 V.
- Buffer/Normal mode is validated. Stream/Loop mode remains unresolved.

### Known limitations

- The four derived setup bytes at offsets 33-36 have not been reduced to a
  general checksum formula. They depend on samplerate, capture limit,
  threshold, and operation mode.
- Consequently, unsupported combinations are rejected before transmission.
  For example, 1000 MHz with 200 us has no validated profile, so PXView sends
  no acquisition setup command; the observed USB trace contains enumeration
  only.
- 1.8 V, 2.5 V, arbitrary continuous VTH, and Stream/Loop profiles need more
  captures before they can be enabled safely.
- A 1 MHz input measured at 50 MHz can show approximately 48-52% on individual
  cycles because the analyzer sample period is 20 ns. This is sampling
  quantization, not a driver glitch. Multi-cycle measurements average toward
  the real duty cycle; higher samplerates reduce the quantization step.

### Build and test result

The updated patch was applied to the pinned PXView/libsigrok checkout, built
with MSYS2 UCRT64 Ninja, packaged with `CH375DLL64.dll`, and launched. The
first test exposed an over-strict profile check; that fallback was corrected,
the package was rebuilt, and a subsequent live capture succeeded. The final
1000 MHz/200 us test was correctly rejected as an unsupported profile.