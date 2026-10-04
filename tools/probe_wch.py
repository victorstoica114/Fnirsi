"""Read FNIRSI identity through the installed WCH driver; no reset/PWM/capture."""
import argparse
import ctypes as C
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    dll = C.WinDLL(r'C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll', use_last_error=True)
    dll.CH375OpenDevice.argtypes = [C.c_ulong]
    dll.CH375OpenDevice.restype = C.c_void_p
    dll.CH375CloseDevice.argtypes = [C.c_ulong]
    dll.CH375GetUsbID.argtypes = [C.c_ulong]
    dll.CH375GetUsbID.restype = C.c_ulong
    dll.CH375SetTimeout.argtypes = [C.c_ulong, C.c_ulong, C.c_ulong]
    dll.CH375WriteData.argtypes = [C.c_ulong, C.c_void_p, C.POINTER(C.c_ulong)]
    dll.CH375ReadEndP.argtypes = [C.c_ulong, C.c_ulong, C.c_void_p, C.POINTER(C.c_ulong)]
    dll.CH375GetDeviceDescr.argtypes = [C.c_ulong, C.c_void_p, C.POINTER(C.c_ulong)]
    result = []
    for index in range(4):
        handle = dll.CH375OpenDevice(index)
        if handle in (None, C.c_void_p(-1).value):
            continue
        try:
            usb_id = dll.CH375GetUsbID(index)
            if usb_id != 0x55371a86:
                result.append(dict(index=index, usb_id=f'{usb_id:08x}', queried=False))
                continue
            dll.CH375SetTimeout(index, 1000, 500)
            descriptor = C.create_string_buffer(256)
            length = C.c_ulong(256)
            descriptor_ok = dll.CH375GetDeviceDescr(index, descriptor, C.byref(length))
            command = C.create_string_buffer(bytes([0x0a, 0x10]) + bytes(510))
            length = C.c_ulong(512)
            written = dll.CH375WriteData(index, command, C.byref(length))
            reply = C.create_string_buffer(512)
            length = C.c_ulong(512)
            read = dll.CH375ReadEndP(index, 2, reply, C.byref(length)) if written else False
            raw = reply.raw[:length.value] if read else b''
            result.append(dict(index=index, usb_id=f'{usb_id:08x}',
                               device_descriptor=descriptor.raw[:18].hex() if descriptor_ok else None,
                               command_written=bool(written), reply_read=bool(read),
                               reply_length=len(raw), reply_hex=raw.hex(),
                               model_code=raw[13:17].decode('ascii', errors='replace') if len(raw) >= 17 else None))
        finally:
            dll.CH375CloseDevice(index)
    output = json.dumps(result, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
