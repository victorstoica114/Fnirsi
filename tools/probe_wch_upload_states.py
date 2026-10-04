"""Bounded installed-SDK state probe; no ARM, capture, PWM, ESP or reset commands.

Records actual BOOL/error results without assigning meaning to raw query fields.
The pipe is retired by successful disable before closing; failed disable is retried.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import time

DLL = Path(r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll")
DLL_SHA = "db19ce17fe9b731cc82b9c40f2c7da23b55f19661c46ce1cfba9952b8a8621f7"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("Refusing to overwrite probe evidence")
    actual = hashlib.sha256(DLL.read_bytes()).hexdigest()
    if actual != DLL_SHA:
        raise ValueError("Installed SDK differs from reviewed ABI")
    dll = C.WinDLL(str(DLL), use_last_error=True)
    U32, BOOL = C.c_uint32, C.c_int32
    dll.CH375OpenDevice.argtypes = [U32]
    dll.CH375OpenDevice.restype = C.c_void_p
    dll.CH375CloseDevice.argtypes = [U32]
    dll.CH375CloseDevice.restype = None
    dll.CH375GetUsbID.argtypes = [U32]
    dll.CH375GetUsbID.restype = U32
    dll.CH375SetTimeout.argtypes = [U32, U32, U32]
    dll.CH375SetTimeout.restype = BOOL
    dll.CH375WriteData.argtypes = [U32, C.c_void_p, C.POINTER(U32)]
    dll.CH375WriteData.restype = BOOL
    dll.CH375ReadEndP.argtypes = [U32, U32, C.c_void_p, C.POINTER(U32)]
    dll.CH375ReadEndP.restype = BOOL
    dll.CH375SetBufUploadEx.argtypes = [U32, U32, U32, U32]
    dll.CH375SetBufUploadEx.restype = BOOL
    dll.CH375ClearBufUpload.argtypes = [U32, U32]
    dll.CH375ClearBufUpload.restype = BOOL
    dll.CH375QueryBufUploadEx.argtypes = [U32, U32, C.POINTER(U32), C.POINTER(U32)]
    dll.CH375QueryBufUploadEx.restype = BOOL
    report = {"SDK_path": str(DLL), "SDK_sha256": actual,
              "probe_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "index": 0, "pipe": 1, "length": 1048576,
              "capture_commands": [], "generator_commands": [], "ESP_access": False,
              "query_units_known": False, "calls": []}
    handle = dll.CH375OpenDevice(0)
    if handle in (None, C.c_void_p(-1).value):
        raise RuntimeError("Device unavailable")
    verified = False
    disable_ok = False

    def call(phase, name, values):
        C.set_last_error(0)
        start = time.perf_counter_ns()
        result = getattr(dll, name)(*values)
        error = C.get_last_error()
        elapsed = (time.perf_counter_ns() - start) / 1000
        item = {"phase": phase, "function": name, "BOOL": int(result),
                "windows_error": error, "duration_us": elapsed}
        report["calls"].append(item)
        return bool(result), item

    def query(phase):
        a, b = U32(0xDEADBEEF), U32(0xDEADBEEF)
        ok, item = call(phase, "CH375QueryBufUploadEx", (0, 1, C.byref(a), C.byref(b)))
        item.update(output_a=int(a.value), output_b=int(b.value), outputs_valid=ok)

    try:
        if dll.CH375GetUsbID(0) != 0x55371A86:
            raise RuntimeError("Unexpected USB identity")
        if not dll.CH375SetTimeout(0, 1000, 500):
            raise RuntimeError("Cannot set identification timeout")
        data = bytes((0x0A, 0x10)) + bytes(510)
        buf, length = C.create_string_buffer(data), U32(len(data))
        if not dll.CH375WriteData(0, buf, C.byref(length)) or length.value != len(data):
            raise RuntimeError("Identification write failed")
        response, count = C.create_string_buffer(512), U32(512)
        if not dll.CH375ReadEndP(0, 2, response, C.byref(count)):
            raise RuntimeError("Identification read failed")
        raw = response.raw[:count.value]
        if not 19 <= count.value <= 512 or raw[:3] != b"\x0a\x02\x0d" or raw[13:17] != b"DL32" or raw[18] != 11:
            raise RuntimeError("DL32 identification not confirmed")
        report["identification_bytes_hex"] = raw.hex()
        verified = True
        for round_number in (1, 2):
            prefix = f"round{round_number}"
            disable_ok, _ = call(prefix+"-initial-disable", "CH375SetBufUploadEx", (0, 0, 1, 1048576))
            if not disable_ok:
                raise RuntimeError("Initial disable failed; no enable attempted")
            query(prefix+"-disabled-query")
            call(prefix+"-disabled-clear", "CH375ClearBufUpload", (0, 1))
            enabled, _ = call(prefix+"-enable", "CH375SetBufUploadEx", (0, 1, 1, 1048576))
            disable_ok = False  # A failed enable is also retired before proceeding.
            if not enabled:
                raise RuntimeError("Enable rejected; no clear or read attempted")
            query(prefix+"-enabled-query")
            call(prefix+"-enabled-clear", "CH375ClearBufUpload", (0, 1))
            disable_ok, _ = call(prefix+"-disable", "CH375SetBufUploadEx", (0, 0, 1, 1048576))
            if not disable_ok:
                raise RuntimeError("Disable failed; remaining sequence refused")
            query(prefix+"-retired-query")
            call(prefix+"-retired-clear", "CH375ClearBufUpload", (0, 1))
    except Exception as error:
        report["probe_error"] = str(error)
    finally:
        if verified and not disable_ok:
            for attempt in (1, 2, 3):
                disable_ok, _ = call(f"finally-disable-retry{attempt}", "CH375SetBufUploadEx", (0, 0, 1, 1048576))
                if disable_ok:
                    break
        report["last_disable_success"] = disable_ok
        dll.CH375CloseDevice(0)
        report["handle_closed"] = True
        with args.report.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    print(json.dumps(report, indent=2))
    if report.get("probe_error") or not disable_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
