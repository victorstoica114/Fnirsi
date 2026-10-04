"""Inspect empty reads only. Does not start acquisition or enable outputs."""
import ctypes as C
import json

dll = C.WinDLL(r'C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll', use_last_error=True)
dll.CH375OpenDevice.argtypes = [C.c_ulong]
dll.CH375OpenDevice.restype = C.c_void_p
dll.CH375CloseDevice.argtypes = [C.c_ulong]
dll.CH375SetTimeout.argtypes = [C.c_ulong,C.c_ulong,C.c_ulong]
dll.CH375ReadEndP.argtypes = [C.c_ulong,C.c_ulong,C.c_void_p,C.POINTER(C.c_ulong)]
handle = dll.CH375OpenDevice(0)
if handle in (None,C.c_void_p(-1).value):
    raise SystemExit('Cannot open WCH device 0')
try:
    dll.CH375SetTimeout(0,1000,20)
    result=[]
    for _ in range(2):
        buffer=C.create_string_buffer(1024*1024)
        size=C.c_ulong(len(buffer))
        C.set_last_error(0)
        ok=dll.CH375ReadEndP(0,1,buffer,C.byref(size))
        result.append(dict(ok=bool(ok),bytes=size.value,error=C.get_last_error()))
    print(json.dumps(result,indent=2))
finally:
    dll.CH375CloseDevice(0)
