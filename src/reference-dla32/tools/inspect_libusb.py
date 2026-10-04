"""AI-generated with assistance from GitHub Copilot.
Model: GitHub Copilot; the underlying model identifier was not exposed.
Human review and hardware validation were performed for this integration.
"""

import os, sys, ctypes

print("Starting script...", flush=True)

libpath = r'C:\GIT\PXView\package\libusb-1.0.dll'
print(f"Loading {libpath}...", flush=True)

try:
    libusb = ctypes.CDLL(libpath)
    print("Loaded DLL successfully.", flush=True)
except Exception as e:
    print(f"Failed to load DLL: {e}", flush=True)
    sys.exit(1)

ctx = ctypes.c_void_p()
ret = libusb.libusb_init(ctypes.byref(ctx))
print(f"libusb_init ret: {ret}", flush=True)

dev_list = ctypes.POINTER(ctypes.c_void_p)()
cnt = libusb.libusb_get_device_list(ctx, ctypes.byref(dev_list))
print(f"libusb_get_device_list count: {cnt}", flush=True)

class DeviceDescriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16),
        ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8),
        ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8),
        ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8),
        ("bNumConfigurations", ctypes.c_uint8)
    ]

for i in range(cnt):
    dev = dev_list[i]
    if not dev:
        break
    desc = DeviceDescriptor()
    libusb.libusb_get_device_descriptor(dev, ctypes.byref(desc))
    print(f"Device {i}: VID=0x{desc.idVendor:04x}, PID=0x{desc.idProduct:04x}", flush=True)
    if desc.idVendor in (0x1a86, 0x0986) and desc.idProduct == 0x5537:
        bus = libusb.libusb_get_bus_number(dev)
        addr = libusb.libusb_get_device_address(dev)
        speed = libusb.libusb_get_device_speed(dev)
        print(f"  -> MATCH FNIRSI! Index {i}, Bus {bus}, Addr {addr}, Speed {speed} (4=Super, 3=High, 2=Full)", flush=True)
        
        dev_hdl = ctypes.c_void_p()
        res = libusb.libusb_open(dev, ctypes.byref(dev_hdl))
        print(f"  libusb_open result: {res} (0=Success)", flush=True)
        if res == 0:
            claim_res = libusb.libusb_claim_interface(dev_hdl, 0)
            print(f"  libusb_claim_interface 0 result: {claim_res} (0=Success)", flush=True)
            libusb.libusb_close(dev_hdl)

libusb.libusb_free_device_list(dev_list, 1)
libusb.libusb_exit(ctx)
print("Done.", flush=True)
