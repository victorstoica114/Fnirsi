# V7 r2 development status

This is an experimental Windows SDK0 driver update. It has not replaced the
main PulseView runtime and is not a stable release or a 200 MB/s claim.

The worker now signals a Win32 event instead of waiting for a 1 ms polling
timer. A bounded FIFO overlaps reading the next 1 MiB block with processing
the previous one. Claimed and queued blocks remain immutable. A third buffer
is dedicated to Stop draining. Prefetched bytes after Stop are accounted for
separately and are never delivered as LOGIC samples.

Explicit OS device-loss errors permit stale host-handle retirement after the
native thread has exited. A reopened device must pass fresh identification.
Other failed STOP commands retain their existing recovery requirements.

R2 exposes continuous Stream through the public API only for the experimental
worker profile. SDK1 remains incompatible with the worker; default/explicit
worker0 builds retain their prior capability restrictions. No channel shifts,
sample filtering, firmware edits or timer-resolution changes were introduced.

## Verification completed on 2026-10-06

- 206 worker/API interaction checks passed, including blocked reads, empty and
  partial timeouts, watchdogs, trigger waiting, reentrant callbacks, Stop/drain
  ownership, simulated device loss and reopen, and notification failures.
- 92 general libsigrok checks passed. Inherited SDK0 checks: 551 each for
  default and explicit worker0; SDK1/worker0: 598; timeout checks: 47. Invalid
  worker/SDK/platform combinations are rejected during preprocessing.
- Finite 100M-sample acquisitions passed original RAW-to-LOGIC conversion and
  CRC/counter checks in Buffer32/50 MS/s, Stream32/25 MS/s and Stream32/50 MS/s.
- Continuous Stream32/25 MS/s ran for 12 seconds: 299,892,736 saved samples,
  approximately 100 MB/s, and 1,461 consecutive valid packets for each of
  UART TX, SPI MOSI and forced I2C write with real NACK bits.
- Continuous Stream32/50 MS/s still failed packet integrity in a 12-second
  probe: shared counter gaps, one UART CRC error and an incomplete I2C
  transaction. Original RAW-to-LOGIC conversion remained exact.
- With RAW tracing off and LOGIC output discarded to NUL, the finite
  SDK/decoder/CLI path reached 145.823 MB/s on this host. This is not a USB bus
  measurement and carries no sample-integrity verdict.
- Hardware cancellation and subsequent Buffer/Stream acquisitions passed on
  one open instance. A separate PulseView candidate captured and saved both
  modes at 5M samples/32 channels/50 MS/s, passed packet checks on the saved
  archives, and closed normally with native exit code 0.

Five large saved acquisitions total 937,272,064 samples and 29,992,706,048 exact
encoded-bit comparisons against their original RAW inputs. This validates
conversion for those inputs; it does not certify every physical sample.

Protocol validation covers UART TX at 115200 baud, SPI MOSI/CLK/CS at 1 MHz,
and I2C forced-write patterns at 100 kHz with actual NACKs. SPI MISO loopback,
I2C slave ACK/read, every physical input, thresholds and calibrated timing
remain outside this completed scope. Physical unplug/replug was not induced
in this round; device-loss coverage is simulated.

## Rebuilding source

The compact overlay restores the exact 490-file tree from the archived V5
base. Its source identity is
`d03598b4ff51506e5fc9bc125794f8fb26324571f67f3add90c6049931e4bf5d`.

```powershell
python tools/restore_dla32_worker_v7_r2_source.py
```

With the existing MSYS2/UCRT64 project mapping and dependencies described in
`BUILD_AND_RESTORE.md`, build from the restored source:

```bash
bash tools/build_dla32_wch_worker_v7_r2_fresh.sh
bash tools/test_dla32_wch_worker_v7_r2.sh
bash tools/test_dla32_wch_worker_v7_r2_inherited.sh
```

Hardware launchers deliberately require local acceptance files and matching
runtime hashes. Rebuilding source is not an automatic hardware authorization
or acceptance of a different DLL. Binary reproducibility is not promised.

The public backup contains development code and this technical status. Raw
captures, private journals, vendor evidence and correspondence stay local.
The existing encrypted notes archive and its key are unchanged.

The remaining transport work is to identify and remove the SDK0 throughput
limit and protect continuous acquisition from prolonged consumer stalls.
Any SDK queue or alternative transport must independently pass ordering,
Stop/recovery and original-payload integrity checks before promotion.
