"""Finite raw transport check with all 32 inputs; never changes PWM.

Buffer baseline defaults match independently captured 50 MHz / 1 ms setup.
Use only with the official app and PulseView closed. This is not a signal test.
"""
import argparse
import ctypes as C
import json
import struct
import time
from pathlib import Path


def packet(body):
    crc = 0
    for byte in body:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    frame = bytes(8) + b'\x0a' + body + b'\x0b' + struct.pack('<I', (~crc) & 0xffffffff)
    return frame + bytes(2048-len(frame))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=50000)
    parser.add_argument('--mode', choices=['buffer','stream'], default='buffer')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--read-size', type=int, default=1024*1024)
    parser.add_argument('--initialize', action='store_true', help='Release FPGA reset, as in the reference driver')
    parser.add_argument('--pwm-test', action='store_true', help='Confirmed local PWM0 to D0 loopback: 100 kHz, 50%%')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100000000 or not 0 < args.seconds <= 30:
        parser.error('Use 1..100 million samples and 0..30 seconds')
    if not 1024 <= args.read_size <= 4*1024*1024 or args.read_size % 1024:
        parser.error('Read size must be 1024..4194304 bytes, a multiple of 1024')
    dll = C.WinDLL(r'C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll', use_last_error=True)
    dll.CH375OpenDevice.argtypes=[C.c_ulong]; dll.CH375OpenDevice.restype=C.c_void_p
    dll.CH375CloseDevice.argtypes=[C.c_ulong]
    dll.CH375GetUsbID.argtypes=[C.c_ulong]; dll.CH375GetUsbID.restype=C.c_ulong
    dll.CH375SetTimeout.argtypes=[C.c_ulong,C.c_ulong,C.c_ulong]
    dll.CH375SetTimeoutEx.argtypes=[C.c_ulong,C.c_ulong,C.c_ulong,C.c_ulong,C.c_ulong]
    dll.CH375AbortEndPRead.argtypes=[C.c_ulong,C.c_ulong]
    dll.CH375ReadEndP.argtypes=[C.c_ulong,C.c_ulong,C.c_void_p,C.POINTER(C.c_ulong)]
    dll.CH375WriteData.argtypes=[C.c_ulong,C.c_void_p,C.POINTER(C.c_ulong)]
    handle=dll.CH375OpenDevice(0)
    if handle in (None,C.c_void_p(-1).value): raise RuntimeError('WCH device 0 unavailable')
    buffer=C.create_string_buffer(max(1024*1024,args.read_size))
    records=[]
    def write(data):
        size=C.c_ulong(len(data)); native=C.create_string_buffer(data)
        if not dll.CH375WriteData(0,native,C.byref(size)) or size.value!=len(data):
            raise RuntimeError(f'Write failed: {C.get_last_error()}, {size.value} bytes')
    def read(pipe=1,length=None):
        size=C.c_ulong(length or len(buffer)); C.set_last_error(0)
        begin=time.perf_counter()
        ok=dll.CH375ReadEndP(0,pipe,buffer,C.byref(size))
        return dict(ok=bool(ok),bytes=size.value,error=C.get_last_error(),elapsed=time.perf_counter()-begin)
    stop=packet(bytes.fromhex('15 02 00'))
    total=0
    pwm_enabled=False
    try:
        if dll.CH375GetUsbID(0)!=0x55371a86: raise RuntimeError('Unexpected USB ID')
        dll.CH375SetTimeout(0,1000,100)
        write(bytes([0x0a,0x10])+bytes(510))
        reply=read(2,length=512)
        if not reply['ok'] or reply['bytes']<19 or buffer.raw[13:17]!=b'DL32':
            raise RuntimeError('Device did not confirm DL32')
        if args.initialize:
            write(bytes([0x0a,0x87,0x01])+bytes(509))
            time.sleep(0.2)
        dll.CH375SetTimeoutEx(0,1000,1000,1000,1000)
        dll.CH375AbortEndPRead(0,1)
        write(stop)
        for _ in range(64):
            info=read()
            if not info['ok']: raise RuntimeError(f'Initial drain failed: {info}')
            if info['bytes']==0: break
        else: raise RuntimeError('Device did not stop')
        if args.pwm_test:
            write(packet(bytes.fromhex('17 0b 00 10')+struct.pack('<II',100000,50)))
            pwm_enabled=True
        # Factory baseline body (50 MHz, Vth 1.6 V, Buffer, trigger position 10).
        body=bytearray.fromhex('11 1e 00 00 01 00 03 00 80 f0 fa 02 08 a0 00 02 01 00 00 0a 00 00 00 50 c3 00 00 00 00 00 00')
        assert packet(body)[41:45]==bytes.fromhex('1a 6d 35 bd')
        milliseconds=(args.samples+49999)//50000
        durations=[1,2,5,10,20,50,100,200,500,1000,2000]
        body[4]=1 if args.mode=='buffer' else 0
        struct.pack_into('<H',body,16,milliseconds)
        body[18]=next((i for i,n in enumerate(durations) if milliseconds<=n),len(durations)-1)
        struct.pack_into('<Q',body,23,args.samples)
        setup=packet(body)
        channels=packet(bytes.fromhex('12 27 00 00 ff ff ff ff')+bytes(32))
        write(setup); time.sleep(0.1)
        begin=time.perf_counter(); write(channels)
        target=args.samples*4
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('wb') as file:
            while total<target and time.perf_counter()-begin<args.seconds:
                info=read(length=args.read_size); records.append(info)
                if not info['ok']: raise RuntimeError(f'Sample read failed: {info}')
                if info['bytes']:
                    count=min(info['bytes'],target-total)
                    file.write(C.string_at(buffer,count)); total+=count
        elapsed=time.perf_counter()-begin
        result=dict(mode=args.mode,samplerate=50000000,channels=32,requested_samples=args.samples,
                    received_bytes=total,expected_bytes=target,complete=total==target,
                    elapsed_seconds=elapsed,raw_bytes_per_second=total/elapsed,
                    signal_integrity_verified=False,reads=records,
                    setup_hex=setup[:45].hex(),channel_command_hex=channels[:54].hex())
        args.output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('reads','setup_hex','channel_command_hex')},indent=2))
        if total!=target: raise RuntimeError('Incomplete acquisition')
    finally:
        try:
            try:
                write(stop)
            finally:
                if pwm_enabled:
                    write(packet(bytes.fromhex('17 03 00 10')))
        finally: dll.CH375CloseDevice(0)


if __name__=='__main__': main()
