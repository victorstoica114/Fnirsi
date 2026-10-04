"""AI-generated with assistance from GitHub Copilot.
Model: GitHub Copilot; the underlying model identifier was not exposed.
Human review and hardware validation were performed for this integration.
"""

import os, sys
import usb.core, usb.backend.libusb1

os.environ['PATH'] += r';C:\Program Files\DLA_Logic;C:\GIT\PXView\package'

backend = usb.backend.libusb1.get_backend(
    find_library=lambda x: r'C:\GIT\PXView\package\libusb-1.0.dll'
)

devs = list(usb.core.find(find_all=True, idVendor=0x1a86, idProduct=0x5537, backend=backend))
print(f"Found {len(devs)} matching USB device(s):")

for idx, dev in enumerate(devs):
    print(f"\n--- Device {idx+1} ---")
    print(f"Bus: {dev.bus}, Address: {dev.address}")
    print(f"Speed code: {dev._speed if hasattr(dev, '_speed') else 'unknown'}")
    try:
        print(f"Manufacturer: {dev.manufacturer}")
        print(f"Product: {dev.product}")
        print(f"Serial: {dev.serial_number}")
    except Exception as e:
        print(f"Descriptor string read error: {e}")
    
    cfg = dev.active_config()
    if cfg is None:
        try:
            dev.set_configuration()
            cfg = dev.active_config()
        except Exception as e:
            print(f"Set config error: {e}")
            
    if cfg:
        for intf in cfg:
            print(f" Interface {intf.bInterfaceNumber}, Alt {intf.bAlternateSetting}: class={intf.bInterfaceClass}")
            for ep in intf:
                print(f"   Endpoint 0x{ep.bEndpointAddress:02x}: type={ep.bmAttributes}, max_packet={ep.wMaxPacketSize}")
