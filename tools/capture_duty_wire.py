"""Finite, unchanged DLA-32 USB payload capture for duty-cycle investigation.

Uses the installed native WCH transport and all 32 physical inputs. Buffer
mode and no trigger are the defaults; Stream and one trigger are optional.
It never changes PWM, FPGA reset, firmware, or the ESP32.
Close applications that own the analyzer before running this helper. This
records wire bytes only; it does not infer signal integrity or a fault cause.
"""

import argparse
import ctypes as C
import hashlib
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from capture_wch import packet


RATES = (
    1_000_000, 2_000_000, 4_000_000, 5_000_000, 10_000_000,
    20_000_000, 25_000_000, 40_000_000, 50_000_000, 100_000_000,
    125_000_000, 200_000_000, 250_000_000,
)
DURATIONS_MS = (
    1, 2, 5, 10, 20, 50, 100, 200, 500,
    1000, 2000, 5000, 10000, 20000, 50000,
)
THRESHOLDS = {"0.6": (60, 7), "1.6": (160, 2), "2.5": (250, 1)}
MODES = ("Buffer", "Stream")
# Factory channel condition codes, also used by protocol.h/api.c.
TRIGGER_MATCHES = {"rising": 1, "high": 2, "falling": 3, "low": 4, "edge": 5}
MAX_SAMPLES = (4 * 1024 * 1024 * 1024) // 32
READ_SIZE = 1024 * 1024
TIMEOUT_ERRORS = {121, 258, 1460}  # SEM_TIMEOUT, WAIT_TIMEOUT, ERROR_TIMEOUT.
STOP = packet(bytes.fromhex("15 02 00"))


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def trigger_conditions(channel=None, match="rising"):
    """Physical-channel conditions; zero means no trigger."""
    if match not in TRIGGER_MATCHES:
        raise ValueError(f"Unknown trigger match: {match}")
    conditions = bytearray(32)
    if channel is not None:
        if not isinstance(channel, int) or not 0 <= channel < 32:
            raise ValueError("Trigger channel must be in 0..31")
        conditions[channel] = TRIGGER_MATCHES[match]
    return bytes(conditions)


def setup_command(rate, samples, threshold, mode="Buffer", trigger_channel=None,
                  trigger_position_raw=None):
    """Encode the factory 31-byte SETUP body independently of libsigrok."""
    if mode not in MODES:
        raise ValueError(f"Unknown capture mode: {mode}")
    trigger_conditions(trigger_channel)
    if trigger_position_raw is None:
        trigger_position_raw = 500 if trigger_channel is not None else 0
    if (not isinstance(trigger_position_raw, int) or
            not 0 <= trigger_position_raw <= 1000):
        raise ValueError("Raw trigger position must be in 0..1000")
    if trigger_channel is None and trigger_position_raw != 0:
        raise ValueError("Nonzero trigger position requires a trigger channel")
    milliseconds = (samples * 1000 + rate - 1) // rate
    duration_index = next(
        (i for i, limit in enumerate(DURATIONS_MS) if milliseconds <= limit),
        len(DURATIONS_MS) - 1,
    )
    threshold_cv, threshold_range = THRESHOLDS[threshold]
    body = bytearray(31)
    body[:3] = bytes.fromhex("11 1e 00")
    body[3:6] = bytes((0, int(mode == "Buffer"), 0))  # RLE off, finite mode.
    struct.pack_into("<H", body, 6, 3)
    struct.pack_into("<I", body, 8, rate)
    body[12] = RATES.index(rate)
    struct.pack_into("<H", body, 13, threshold_cv)
    body[15] = threshold_range
    struct.pack_into("<H", body, 16, min(milliseconds, 65535))
    body[18] = duration_index
    # Default matches the driver. Explicit values are diagnostic controls;
    # this helper does not interpret the field as a sample or frame address.
    struct.pack_into("<I", body, 19, trigger_position_raw)
    struct.pack_into("<Q", body, 23, samples)
    return packet(body)


def channel_command(trigger_channel=None, trigger_match="rising"):
    body = bytearray(40)
    body[:3] = bytes.fromhex("12 27 00")
    struct.pack_into("<I", body, 4, 0xffffffff)
    body[8:40] = trigger_conditions(trigger_channel, trigger_match)
    return packet(body)


class NativeWch:
    def __init__(self, dll_path):
        self.dll_path = dll_path
        self.dll = None
        self.opened = False
        self.buffer = C.create_string_buffer(READ_SIZE)

    def open(self):
        self.dll = C.WinDLL(str(self.dll_path), use_last_error=True)
        dll = self.dll
        ulong = C.c_uint32
        dll.CH375OpenDevice.argtypes = [ulong]
        dll.CH375OpenDevice.restype = C.c_void_p
        dll.CH375CloseDevice.argtypes = [ulong]
        dll.CH375CloseDevice.restype = None
        dll.CH375GetUsbID.argtypes = [ulong]
        dll.CH375GetUsbID.restype = ulong
        dll.CH375SetTimeout.argtypes = [ulong, ulong, ulong]
        dll.CH375SetTimeout.restype = C.c_int
        if hasattr(dll, "CH375SetTimeoutEx"):
            dll.CH375SetTimeoutEx.argtypes = [ulong, ulong, ulong, ulong, ulong]
            dll.CH375SetTimeoutEx.restype = C.c_int
        dll.CH375ReadEndP.argtypes = [ulong, ulong, C.c_void_p, C.POINTER(ulong)]
        dll.CH375ReadEndP.restype = C.c_int
        dll.CH375WriteData.argtypes = [ulong, C.c_void_p, C.POINTER(ulong)]
        dll.CH375WriteData.restype = C.c_int
        handle = dll.CH375OpenDevice(0)
        if handle in (None, C.c_void_p(-1).value):
            raise RuntimeError("WCH device 0 unavailable")
        self.opened = True
        usb_id = dll.CH375GetUsbID(0)
        if usb_id != 0x55371a86:
            raise RuntimeError(f"Unexpected USB ID 0x{usb_id:08x}")
        return usb_id

    def close(self):
        if self.opened:
            self.dll.CH375CloseDevice(0)
            self.opened = False

    def write(self, data):
        if not self.opened:
            raise RuntimeError("Device closed")
        size = C.c_uint32(len(data))
        native = C.create_string_buffer(data)
        C.set_last_error(0)
        ok = self.dll.CH375WriteData(0, native, C.byref(size))
        error = C.get_last_error()
        if not ok or size.value != len(data):
            raise RuntimeError(
                f"WCH write failed ({size.value}/{len(data)} bytes, error {error})"
            )

    def read(self, pipe=1, length=READ_SIZE, timeout_ms=100):
        if not self.opened or not 0 < length <= READ_SIZE:
            raise RuntimeError("Invalid read or device closed")
        dll = self.dll
        C.set_last_error(0)
        if hasattr(dll, "CH375SetTimeoutEx"):
            configured = dll.CH375SetTimeoutEx(0, 1000, timeout_ms, 1000, timeout_ms)
        else:
            configured = dll.CH375SetTimeout(0, 1000, timeout_ms)
        if not configured:
            raise RuntimeError(f"WCH timeout setup failed ({C.get_last_error()})")
        size = C.c_uint32(length)
        C.set_last_error(0)
        started_utc = utc_timestamp()
        started_ns = time.perf_counter_ns()
        started = time.perf_counter()
        ok = bool(dll.CH375ReadEndP(0, pipe, self.buffer, C.byref(size)))
        error = C.get_last_error()
        elapsed = time.perf_counter() - started
        ended_ns = time.perf_counter_ns()
        ended_utc = utc_timestamp()
        reported = size.value
        if reported > length:
            raise RuntimeError(f"WCH reported an overflowing read ({reported}/{length})")
        # A failed read may leave the requested length unchanged. Such bytes
        # are stale buffer contents, not received payload. Partial timeout
        # bytes are accepted only when the DLL shortened the length.
        actual = reported if ok or reported < length else 0
        data = C.string_at(self.buffer, actual)
        info = dict(
            ok=ok, timeout=not ok and error in TIMEOUT_ERRORS,
            bytes=actual, native_reported_bytes=reported, requested_bytes=length,
            error=error, elapsed_seconds=elapsed,
            pipe=pipe, timeout_ms=timeout_ms, bytes_mod32=actual % 32,
            native_reported_bytes_mod32=reported % 32,
            requested_bytes_mod32=length % 32,
            started_utc=started_utc, ended_utc=ended_utc,
            started_perf_counter_ns=started_ns, ended_perf_counter_ns=ended_ns,
            first32_hex=data[:32].hex(),
        )
        return data, info

    def confirm_model(self):
        self.write(bytes((0x0a, 0x10)) + bytes(510))
        data, info = self.read(pipe=2, length=512, timeout_ms=500)
        if not (info["ok"] and len(data) >= 19 and
                data[:3] == bytes((0x0a, 0x02, 0x0d)) and
                data[13:17] == b"DL32" and data[18] == 0x0b):
            raise RuntimeError(f"Device did not confirm DL32: {info}")
        return info


def require_read(info):
    if not info["ok"] and not info["timeout"]:
        raise RuntimeError(f"WCH sample read failed: {info}")


def drain(transport):
    """Bound stopped-endpoint reads; require two consecutive empty reads."""
    started = time.perf_counter()
    total = 0
    empty = 0
    records = []
    while len(records) < 64 and total < 64 * 1024 * 1024:
        if time.perf_counter() - started >= 3:
            break
        _, info = transport.read(timeout_ms=20)
        info["drain_offset_bytes"] = total
        info["drain_offset_mod32"] = total % 32
        records.append(info)
        require_read(info)
        total += info["bytes"]
        empty = 0 if info["bytes"] else empty + 1
        if empty >= 2:
            break
    return dict(
        quiet=empty >= 2, bytes=total, bytes_mod32=total % 32,
        elapsed_seconds=time.perf_counter() - started, reads=records,
    )


def run(args):
    output = args.output.resolve()
    sidecar = output.with_suffix(".json")
    if output == sidecar or output.exists() or sidecar.exists():
        raise RuntimeError("Choose an output path with no existing capture or JSON sidecar")
    output.parent.mkdir(parents=True, exist_ok=True)
    dll_path = Path(os.environ.get(
        "FNIRSI_WCH_DLL", r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll"
    ))
    if not dll_path.is_absolute():
        raise RuntimeError("FNIRSI_WCH_DLL must be an absolute DLL path")
    # getattr preserves existing programmatic callers with the old Namespace.
    mode = getattr(args, "mode", "Buffer")
    trigger_channel = getattr(args, "trigger_channel", None)
    trigger_match = getattr(args, "trigger_match", None) or "rising"
    trigger_position_raw = getattr(args, "trigger_position_raw", None)
    if trigger_position_raw is None:
        trigger_position_raw = 500 if trigger_channel is not None else 0
    conditions = trigger_conditions(trigger_channel, trigger_match)
    setup = setup_command(args.rate, args.samples, args.threshold, mode, trigger_channel,
                          trigger_position_raw)
    channels = channel_command(trigger_channel, trigger_match)
    transport = NativeWch(dll_path)
    result = dict(
        output=str(output), transport="native WCH", mode=mode,
        samplerate=args.rate, requested_samples=args.samples,
        threshold_volts=float(args.threshold), physical_channel_mask="0xffffffff",
        channels=32, rle=False, trigger=trigger_channel is not None,
        trigger_channel=trigger_channel,
        trigger_match=trigger_match if trigger_channel is not None else None,
        trigger_condition_code=TRIGGER_MATCHES[trigger_match] if trigger_channel is not None else 0,
        trigger_conditions=list(conditions),
        trigger_position_raw=trigger_position_raw,
        buffer_field=int(mode == "Buffer"), finite_mode_field=0,
        packing="32 physical lane bytes per frame; 8 LSB-first samples per lane",
        expected_bytes=args.samples * 4, received_bytes=0, complete=False,
        setup_hex=setup[:45].hex(), setup_body_hex=setup[9:40].hex(),
        channel_command_hex=channels[:54].hex(), channel_body_hex=channels[9:49].hex(),
        reads=[], discarded_padding_bytes=0, first_data_seconds=None,
        sample_read_seconds=0, acquisition_elapsed_seconds=None,
        waveform_verdict=None, signal_integrity_verified=False,
        started_utc=utc_timestamp(), commands=[],
        dll_path=str(dll_path), helper_path=str(Path(__file__).resolve()),
        helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        setup_to_arm_delay_seconds=0.1, frame_bytes=32,
        note="Wire payload prefix unchanged; only bytes after the requested count are clipped. "
             "No PWM commands, lane rotation, prefix removal, or waveform verdict.",
    )
    digest = hashlib.sha256()
    verified = False
    error = None

    def write_command(name, data):
        record = dict(name=name, bytes=len(data), started_utc=utc_timestamp(),
                      started_perf_counter_ns=time.perf_counter_ns(),
                      prefix54_hex=data[:54].hex(),
                      sha256=hashlib.sha256(data).hexdigest())
        result["commands"].append(record)
        try:
            transport.write(data)
            record["success"] = True
        except Exception as exc:
            record["success"] = False
            record["error"] = str(exc)
            raise
        finally:
            record["ended_perf_counter_ns"] = time.perf_counter_ns()
            record["ended_utc"] = utc_timestamp()

    try:
        with output.open("xb") as file:
            result["usb_id"] = f"0x{transport.open():08x}"
            result["model_probe"] = transport.confirm_model()
            verified = True
            write_command("initial_stop", STOP)
            result["initial_drain"] = drain(transport)
            if not result["initial_drain"]["quiet"]:
                raise RuntimeError("Stopped endpoint did not become quiet before SETUP")
            write_command("setup", setup)
            time.sleep(0.1)
            started = time.perf_counter()
            last_progress = started
            first_data_timeout = 30.0 if trigger_channel is not None else (
                (args.samples / args.rate if mode == "Buffer" else 0) + 3.0
            )
            result["first_data_timeout_seconds"] = first_data_timeout
            write_command("arm_channels", channels)
            while result["received_bytes"] < result["expected_bytes"]:
                now = time.perf_counter()
                if now - started >= 30:
                    raise RuntimeError("Overall acquisition deadline exceeded (30 seconds)")
                if result["first_data_seconds"] is None:
                    if now - started > first_data_timeout:
                        raise RuntimeError("No first payload before capture duration plus 3 seconds")
                elif now - last_progress > 3:
                    raise RuntimeError("No payload progress for 3 seconds")
                data, info = transport.read(timeout_ms=100)
                info["wire_offset_bytes"] = result["received_bytes"]
                info["wire_offset_mod32"] = result["received_bytes"] % 32
                info["written_bytes"] = 0
                info["clipped_after_target_bytes"] = 0
                info["clipped_first32_hex"] = ""
                info["seconds_after_arm"] = time.perf_counter() - started
                result["reads"].append(info)
                result["sample_read_seconds"] += info["elapsed_seconds"]
                require_read(info)
                if data:
                    last_progress = time.perf_counter()
                    if result["first_data_seconds"] is None:
                        result["first_data_seconds"] = last_progress - started
                    remaining = result["expected_bytes"] - result["received_bytes"]
                    valid = data[:remaining]
                    info["written_bytes"] = len(valid)
                    info["clipped_after_target_bytes"] = len(data) - len(valid)
                    info["clipped_first32_hex"] = data[len(valid):len(valid) + 32].hex()
                    file.write(valid)
                    digest.update(valid)
                    result["received_bytes"] += len(valid)
                    result["discarded_padding_bytes"] += len(data) - len(valid)
            result["acquisition_elapsed_seconds"] = time.perf_counter() - started
            result["complete"] = result["received_bytes"] == result["expected_bytes"]
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or type(exc).__name__
        result["error"] = error
    finally:
        if verified:
            try:
                write_command("final_stop", STOP)
                result["final_drain"] = drain(transport)
                if not result["final_drain"]["quiet"]:
                    result["cleanup_warning"] = "Stopped endpoint did not become quiet within bounds"
            except Exception as exc:
                result["cleanup_error"] = str(exc)
                error = error or f"Acquisition cleanup failed: {exc}"
        transport.close()
        result["sha256"] = digest.hexdigest()
        result["ended_utc"] = utc_timestamp()
        result["frames_received"] = result["received_bytes"] // 32
        result["trailing_frame_bytes"] = result["received_bytes"] % 32
        result["success"] = result["complete"] and error is None
        with sidecar.open("x", encoding="utf-8") as file:
            json.dump(result, file, indent=2)
            file.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "output", "samplerate", "requested_samples", "threshold_volts",
            "mode", "trigger_channel", "trigger_match",
            "expected_bytes", "received_bytes", "complete", "success", "sha256",
            "first_data_seconds", "acquisition_elapsed_seconds", "waveform_verdict",
        )
    }, indent=2))
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=int, choices=RATES, default=100_000_000)
    parser.add_argument("--samples", type=int, default=1_000_000)
    parser.add_argument("--threshold", choices=tuple(THRESHOLDS), default="1.6")
    parser.add_argument("--mode", choices=MODES, default="Buffer")
    parser.add_argument("--trigger-channel", type=int, choices=range(32), metavar="0..31")
    parser.add_argument("--trigger-match", choices=tuple(TRIGGER_MATCHES))
    parser.add_argument("--trigger-position-raw", type=int, metavar="0..1000",
                        help="Diagnostic SETUP field override; default is 500 with a trigger, 0 otherwise")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.trigger_match is not None and args.trigger_channel is None:
        parser.error("--trigger-match requires --trigger-channel")
    if args.trigger_match is None:
        args.trigger_match = "rising"
    if args.trigger_position_raw is not None:
        if not 0 <= args.trigger_position_raw <= 1000:
            parser.error("--trigger-position-raw must be in 0..1000")
        if args.trigger_channel is None and args.trigger_position_raw != 0:
            parser.error("Nonzero --trigger-position-raw requires --trigger-channel")
    if not 8 <= args.samples <= MAX_SAMPLES or args.samples % 8:
        parser.error(f"Samples must be a multiple of 8 in 8..{MAX_SAMPLES}")
    if args.samples / args.rate + 3 > 30:
        parser.error("Capture duration plus 3-second allowance must fit the 30-second deadline")
    try:
        return run(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
