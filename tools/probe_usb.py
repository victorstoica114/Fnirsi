"""Read-only USB enumeration: negotiated speed, using the installed libusb DLL.

Does not open interfaces, replace drivers, start acquisition or enable PWM.
"""
import argparse
import ctypes as C
import json
import os
from pathlib import Path


class Descriptor(C.Structure):
    _fields_ = [(name, typ) for name, typ in (
        ('bLength', C.c_uint8), ('bDescriptorType', C.c_uint8),
        ('bcdUSB', C.c_uint16), ('bDeviceClass', C.c_uint8),
        ('bDeviceSubClass', C.c_uint8), ('bDeviceProtocol', C.c_uint8),
        ('bMaxPacketSize0', C.c_uint8), ('idVendor', C.c_uint16),
        ('idProduct', C.c_uint16), ('bcdDevice', C.c_uint16),
        ('iManufacturer', C.c_uint8), ('iProduct', C.c_uint8),
        ('iSerialNumber', C.c_uint8), ('bNumConfigurations', C.c_uint8))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dll', default=r'C:\Program Files\FNIRSI\DLA Logic\libusb-1.0.dll')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    directory = os.add_dll_directory(str(Path(args.dll).parent))
    dll = C.CDLL(args.dll)
    dll.libusb_init.argtypes = [C.POINTER(C.c_void_p)]
    dll.libusb_get_device_list.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_void_p))]
    dll.libusb_get_device_list.restype = C.c_ssize_t
    dll.libusb_get_device_descriptor.argtypes = [C.c_void_p, C.POINTER(Descriptor)]
    for name in ('libusb_get_bus_number', 'libusb_get_device_address', 'libusb_get_device_speed'):
        getattr(dll, name).argtypes = [C.c_void_p]
    dll.libusb_free_device_list.argtypes = [C.POINTER(C.c_void_p), C.c_int]
    dll.libusb_exit.argtypes = [C.c_void_p]
    ctx, devices = C.c_void_p(), C.POINTER(C.c_void_p)()
    if dll.libusb_init(C.byref(ctx)):
        raise RuntimeError('libusb_init failed')
    count = dll.libusb_get_device_list(ctx, C.byref(devices))
    if count < 0:
        dll.libusb_exit(ctx)
        raise RuntimeError(f'libusb_get_device_list failed: {count}')
    result = []
    try:
        for index in range(count):
            device, desc = devices[index], Descriptor()
            if dll.libusb_get_device_descriptor(device, C.byref(desc)):
                continue
            if (desc.idVendor, desc.idProduct) != (0x1a86, 0x5537):
                continue
            speed = dll.libusb_get_device_speed(device)
            result.append(dict(
                vid_pid=f'{desc.idVendor:04x}:{desc.idProduct:04x}',
                bus=dll.libusb_get_bus_number(device),
                address=dll.libusb_get_device_address(device),
                negotiated_speed_code=speed,
                negotiated_speed={0:'unknown', 1:'low', 2:'full', 3:'high (USB 2)',
                                  4:'SuperSpeed (USB 3)', 5:'SuperSpeed Plus'}.get(speed, 'unknown'),
                bcdUSB=f'{desc.bcdUSB:04x}',
                note='VID/PID is shared by WCH devices; model still needs protocol identification'))
    finally:
        dll.libusb_free_device_list(devices, 1)
        dll.libusb_exit(ctx)
        directory.close()
    output = json.dumps(result, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
