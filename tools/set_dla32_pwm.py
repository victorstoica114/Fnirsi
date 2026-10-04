"""Enable a DLA-32 PWM output and leave it running for external measurement.

Uses the installed WCH runtime and the same framing as the driver. Does not
start/stop acquisition, touch ESP32, or disable the output when closing USB.
Successful USB writing confirms the command, not the measured waveform.
"""
import argparse
import ctypes as C
import struct

from capture_wch import packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", type=int, choices=range(4), default=0)
    parser.add_argument("--frequency", type=int, required=True)
    parser.add_argument("--duty", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.frequency <= 20000000 or not 1 <= args.duty <= 99:
        parser.error("Frequency must be 1..20000000 Hz and duty 1..99 percent.")
    dll = C.WinDLL(r"C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll", use_last_error=True)
    dll.CH375OpenDevice.argtypes = [C.c_ulong]
    dll.CH375OpenDevice.restype = C.c_void_p
    dll.CH375CloseDevice.argtypes = [C.c_ulong]
    dll.CH375CloseDevice.restype = None
    dll.CH375GetUsbID.argtypes = [C.c_ulong]
    dll.CH375GetUsbID.restype = C.c_ulong
    dll.CH375SetTimeout.argtypes = [C.c_ulong, C.c_ulong, C.c_ulong]
    dll.CH375SetTimeout.restype = C.c_int
    dll.CH375WriteData.argtypes = [C.c_ulong, C.c_void_p, C.POINTER(C.c_ulong)]
    dll.CH375WriteData.restype = C.c_int
    dll.CH375ReadEndP.argtypes = [C.c_ulong, C.c_ulong, C.c_void_p, C.POINTER(C.c_ulong)]
    dll.CH375ReadEndP.restype = C.c_int
    index = 0
    handle = dll.CH375OpenDevice(index)
    if handle in (None, C.c_void_p(-1).value):
        raise RuntimeError("WCH device 0 unavailable; finish any active acquisition first")

    def write(data):
        buffer = C.create_string_buffer(data)
        length = C.c_ulong(len(data))
        C.set_last_error(0)
        if not dll.CH375WriteData(index, buffer, C.byref(length)) or length.value != len(data):
            raise RuntimeError(f"WCH write failed: error={C.get_last_error()}, bytes={length.value}/{len(data)}")
        return length.value

    try:
        if dll.CH375GetUsbID(index) != 0x55371a86:
            raise RuntimeError("Unexpected USB identity")
        if not dll.CH375SetTimeout(index, 1000, 500):
            raise RuntimeError("Cannot configure WCH timeout")
        write(bytes([0x0a, 0x10]) + bytes(510))
        reply = C.create_string_buffer(512)
        length = C.c_ulong(512)
        if not dll.CH375ReadEndP(index, 2, reply, C.byref(length)):
            raise RuntimeError("Device identification read failed")
        raw = reply.raw[:length.value]
        if not 19 <= length.value <= 512 or raw[:3] != b"\x0a\x02\x0d" or raw[13:17] != b"DL32" or raw[18] != 0x0b:
            raise RuntimeError("Device did not confirm DL32")
        body = bytes([0x17, 0x0b, 0, 0x10 + args.channel]) + struct.pack("<II", args.frequency, args.duty)
        count = write(packet(body))
        print(f"DL32 PWM{args.channel}: requested {args.frequency} Hz, duty {args.duty}%, enabled; {count} command bytes written. Output left ON.")
    finally:
        dll.CH375CloseDevice(index)


if __name__ == "__main__":
    main()
