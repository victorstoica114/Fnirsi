"""Bounded direct WCH overlapped-read diagnostic; no driver/firmware changes.

SDK commands and native reads have separate handles to the same identified DLA.
All native requests retire before hardware STOP or handle/buffer release.
IOCTL layout is verified against the installed vendor SDK/kernel disassembly.
Win32 contracts: https://learn.microsoft.com/windows/win32/api/ioapiset/nf-ioapiset-deviceiocontrol
"""
import argparse
from contextlib import nullcontext
import ctypes as C
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import struct
import time
import run_dla32_worker_v8_benchmark as inherited

ROOT=inherited.ROOT
BASE=ROOT/'captures/native-pipeline-2026-10-06'
DLL=Path(r'C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll')
READ_SIZE=1048576
TARGET=400000000
RETAINED_PENDING=[]

# GPL driver protocol vectors: Stream, 32 inputs, 50 MS/s, 1.6 V, D0 rising.
PREFIXES={
    'stop':'00000000000000000a1502000be6fc24d7',
    'setup':'00000000000000000a111e00000003030080f0fa0208a00002e80309f401000080f0fa02000000000ba154bc9d',
    'arm':'00000000000000000a12270000ffffffff01000000000000000000000000000000000000000000000000000000000000000b7cfcd0bf',
}


def packet(body):
    crc=0
    for byte in body:
        crc^=byte
        for _ in range(8):crc=(crc>>1)^(0xedb88320 if crc&1 else 0)
    data=bytes(8)+b'\x0a'+body+b'\x0b'+struct.pack('<I',(~crc)&0xffffffff)
    return data+bytes(2048-len(data))


def vector(name):
    prefix=bytes.fromhex(PREFIXES[name]);length=struct.unpack_from('<H',prefix,10)[0]+1
    result=packet(prefix[9:9+length])
    if not result.startswith(prefix):raise RuntimeError('Protocol CRC/vector mismatch')
    return result


def response_count(storage,returned,requested=READ_SIZE):
    status,count=struct.unpack_from('<II',storage)
    if returned<8 or returned>requested+8 or status or count>requested or count+8!=returned:
        raise RuntimeError('Native response size/status/count invalid')
    return count


def self_test():
    checks=0
    for name in PREFIXES:
        assert len(vector(name))==2048;checks+=1
    data=bytearray(READ_SIZE+8);struct.pack_into('<II',data,0,0,32)
    assert response_count(data,40)==32;checks+=1
    for status,count,returned in ((0,0,7),(0,32,41),(0,READ_SIZE+1,READ_SIZE+9),(0xc0000001,32,40)):
        struct.pack_into('<II',data,0,status,count)
        try:response_count(data,returned)
        except RuntimeError:checks+=1
        else:raise AssertionError('Malformed native response accepted')
    assert len(bytes.fromhex(PREFIXES['arm']))==54;checks+=1
    assert struct.unpack_from('<I',bytes.fromhex(PREFIXES['arm']),13)[0]==0xffffffff;checks+=1
    assert struct.unpack_from('<I',bytes.fromhex(PREFIXES['setup']),17)[0]==50000000;checks+=1
    print(json.dumps({'passed':True,'offline_checks':checks,'hardware_accessed':False}))


def execute(directory,depth,save_raw):
    kernel=C.WinDLL('kernel32',use_last_error=True)
    class Overlapped(C.Structure):
        _fields_=[('internal',C.c_size_t),('internal_high',C.c_size_t),
                  ('offset',C.c_ulong),('offset_high',C.c_ulong),('event',C.c_void_p)]
    if C.sizeof(C.c_ulong)!=4 or C.sizeof(Overlapped)!=32 or Overlapped.event.offset!=24:
        raise RuntimeError('Win64 ABI check failed')
    kernel.CreateFileW.argtypes=[C.c_wchar_p,C.c_ulong,C.c_ulong,C.c_void_p,C.c_ulong,C.c_ulong,C.c_void_p]
    kernel.CreateFileW.restype=C.c_void_p
    kernel.CreateEventW.argtypes=[C.c_void_p,C.c_int,C.c_int,C.c_wchar_p];kernel.CreateEventW.restype=C.c_void_p
    kernel.CloseHandle.argtypes=[C.c_void_p];kernel.CloseHandle.restype=C.c_int
    kernel.ResetEvent.argtypes=[C.c_void_p];kernel.ResetEvent.restype=C.c_int
    kernel.WaitForSingleObject.argtypes=[C.c_void_p,C.c_ulong];kernel.WaitForSingleObject.restype=C.c_ulong
    kernel.DeviceIoControl.argtypes=[C.c_void_p,C.c_ulong,C.c_void_p,C.c_ulong,C.c_void_p,C.c_ulong,
                                   C.POINTER(C.c_ulong),C.POINTER(Overlapped)]
    kernel.DeviceIoControl.restype=C.c_int
    kernel.GetOverlappedResult.argtypes=[C.c_void_p,C.POINTER(Overlapped),C.POINTER(C.c_ulong),C.c_int]
    kernel.GetOverlappedResult.restype=C.c_int
    kernel.CancelIoEx.argtypes=[C.c_void_p,C.c_void_p];kernel.CancelIoEx.restype=C.c_int
    sdk=C.WinDLL(str(DLL),use_last_error=True)
    sdk.CH375OpenDevice.argtypes=[C.c_ulong];sdk.CH375OpenDevice.restype=C.c_void_p
    sdk.CH375CloseDevice.argtypes=[C.c_ulong];sdk.CH375CloseDevice.restype=None
    sdk.CH375GetUsbID.argtypes=[C.c_ulong];sdk.CH375GetUsbID.restype=C.c_ulong
    sdk.CH375GetDeviceName.argtypes=[C.c_ulong];sdk.CH375GetDeviceName.restype=C.c_char_p
    sdk.CH375SetTimeoutEx.argtypes=[C.c_ulong]*5;sdk.CH375SetTimeoutEx.restype=C.c_int
    sdk.CH375SetBufUploadEx.argtypes=[C.c_ulong]*4;sdk.CH375SetBufUploadEx.restype=C.c_int
    sdk.CH375WriteData.argtypes=[C.c_ulong,C.c_void_p,C.POINTER(C.c_ulong)];sdk.CH375WriteData.restype=C.c_int
    sdk.CH375ReadEndP.argtypes=[C.c_ulong,C.c_ulong,C.c_void_p,C.POINTER(C.c_ulong)];sdk.CH375ReadEndP.restype=C.c_int
    opened=False;native=None;armed=False;slots=[];queue=[];rows=[];events=[];file=None
    metadata={'depth':depth,'read_size_bytes':READ_SIZE,'target_bytes':TARGET,'RAW_saved':save_raw,
        'samplerate_hz':50000000,'channels':32,'mode':'Stream','trigger':'D0 rising',
        'source_outputs_stopped':False,'driver_binding_changed':False,'firmware_changed':False,
        'sample_integrity_verified':False,'hardware_STOP_accepted':False,'drain_quiet':False,
        'native_requests_retired':False,'native_handle_closed':False,'SDK_handle_closed':False}
    started=time.perf_counter_ns();target_end=None;total=0;error=None;cleanup_errors=[]
    def timeout(ms):
        if not sdk.CH375SetTimeoutEx(0,ms,ms,ms,ms):raise RuntimeError('SDK timeout configuration failed')
    def write(data,name):
        timeout(1000);storage=C.create_string_buffer(data);size=C.c_ulong(len(data));C.set_last_error(0)
        ok=sdk.CH375WriteData(0,storage,C.byref(size));events.append({'operation':name,'ok':bool(ok),'bytes':size.value,'error':C.get_last_error()})
        if not ok or size.value!=len(data):raise RuntimeError('SDK command failed: '+name)
    def read_sdk(pipe,ms):
        timeout(ms);storage=C.create_string_buffer(READ_SIZE);size=C.c_ulong(READ_SIZE)
        if not sdk.CH375ReadEndP(0,pipe,storage,C.byref(size)):raise RuntimeError('SDK identification/drain failed')
        if size.value>READ_SIZE:raise RuntimeError('SDK read overflow')
        return storage.raw[:size.value]
    def submit(slot):
        if slot['pending']:raise RuntimeError('Pending buffer reuse rejected')
        if not kernel.ResetEvent(slot['op'].event):raise RuntimeError('ResetEvent failed')
        slot['op'].internal=slot['op'].internal_high=0
        slot['op'].offset=slot['op'].offset_high=0
        struct.pack_into('<II',slot['buffer'],0,0x10000,READ_SIZE)
        returned=C.c_ulong();C.set_last_error(0);slot['start_ns']=time.perf_counter_ns()
        ok=kernel.DeviceIoControl(native,0x223cdc,slot['buffer'],8,slot['buffer'],READ_SIZE+8,C.byref(returned),C.byref(slot['op']))
        last=C.get_last_error()
        if not ok and last!=997:raise RuntimeError('Native submission failed: '+str(last))
        slot['pending']=True;slot['id']=len(rows)+len(queue)+1;queue.append(slot)
    def collect(slot,cancelling=False):
        if kernel.WaitForSingleObject(slot['op'].event,5000)!=0:raise RuntimeError('Native completion did not retire')
        returned=C.c_ulong();C.set_last_error(0)
        ok=kernel.GetOverlappedResult(native,C.byref(slot['op']),C.byref(returned),False);last=C.get_last_error()
        slot['pending']=False
        if not ok:
            rows.append({'id':slot['id'],'error':last,'bytes':0,'cancelled':last==995,
                         'start_ns':slot['start_ns'],'end_ns':time.perf_counter_ns()})
            if not cancelling or last!=995:raise RuntimeError('Native completion failed: '+str(last))
            return 0
        count=response_count(slot['buffer'],returned.value)
        end=time.perf_counter_ns()
        rows.append({'id':slot['id'],'error':0,'bytes':count,'start_ns':slot['start_ns'],'end_ns':end})
        if file and count:file.write(memoryview(slot['buffer']).cast('B')[8:8+count])
        return count
    try:
        handle=sdk.CH375OpenDevice(0)
        if not handle or handle==C.c_void_p(-1).value:raise RuntimeError('SDK open failed')
        opened=True
        if sdk.CH375GetUsbID(0)!=0x55371a86:raise RuntimeError('Unexpected USB identity')
        write(bytes([0x0a,0x10])+bytes(510),'identify')
        reply=read_sdk(2,500)
        if len(reply)<19 or reply[13:17]!=b'DL32':raise RuntimeError('Fresh DL32 identification failed')
        name=sdk.CH375GetDeviceName(0)
        if not name or b'vid_1a86&pid_5537' not in name.lower():raise RuntimeError('DLA device path mismatch')
        native=kernel.CreateFileW(name.decode('ascii'),0xc0000000,3,None,3,0x40000080,None)
        if not native or native==C.c_void_p(-1).value:raise RuntimeError('Overlapped native open failed: '+str(C.get_last_error()))
        if not sdk.CH375SetBufUploadEx(0,0,1,READ_SIZE):raise RuntimeError('Pre-arm SDK disable failed')
        write(vector('stop'),'pre-arm STOP')
        for _ in range(64):
            if not read_sdk(1,20):break
        else:raise RuntimeError('Pre-arm endpoint not quiet')
        if save_raw:file=(directory/'capture.wire.bin').open('xb')
        for _ in range(depth):
            event=kernel.CreateEventW(None,True,False,None)
            if not event:raise RuntimeError('Native event allocation failed')
            slots.append({'buffer':C.create_string_buffer(READ_SIZE+8),'op':Overlapped(event=event),'pending':False})
        write(vector('setup'),'setup');time.sleep(0.1);write(vector('arm'),'ARM');armed=True
        timeout(1000);started=time.perf_counter_ns()
        for slot in slots:submit(slot)
        while total<TARGET:
            if time.perf_counter_ns()-started>15_000_000_000:raise RuntimeError('Acquisition deadline exceeded')
            slot=queue.pop(0);total+=collect(slot)
            if total<TARGET:submit(slot)
        target_end=time.perf_counter_ns()
        # Finishing already-issued requests preserves their successful tails.
        while queue:total+=collect(queue.pop(0))
        metadata['native_requests_retired']=True
    except Exception as exc:error=repr(exc)
    finally:
        pending=[slot for slot in slots if slot['pending']]
        if pending and native:
            C.set_last_error(0);ok=kernel.CancelIoEx(native,None);last=C.get_last_error()
            events.append({'operation':'cancel pending native reads','ok':bool(ok),'error':last})
            for slot in pending:
                try:total+=collect(slot,True)
                except Exception as exc:cleanup_errors.append(repr(exc))
            metadata['native_requests_retired']=not any(slot['pending'] for slot in slots)
        if not pending:metadata['native_requests_retired']=True
        if opened and metadata['native_requests_retired']:
            try:
                write(vector('stop'),'final STOP');metadata['hardware_STOP_accepted']=True
                empty=drained=0;deadline=time.perf_counter()+3
                while empty<2 and drained<64*READ_SIZE and time.perf_counter()<deadline:
                    data=read_sdk(1,20);drained+=len(data);empty=empty+1 if not data else 0
                metadata.update(drain_quiet=empty>=2,drain_bytes=drained)
            except Exception as exc:cleanup_errors.append(repr(exc))
        if file:
            try:file.close()
            except Exception as exc:cleanup_errors.append(repr(exc))
        if metadata['native_requests_retired']:
            for slot in slots:
                if not kernel.CloseHandle(slot['op'].event):cleanup_errors.append('Native event close failed')
            if native and native!=C.c_void_p(-1).value:
                metadata['native_handle_closed']=bool(kernel.CloseHandle(native))
            if opened:sdk.CH375CloseDevice(0);metadata['SDK_handle_closed']=True
        else:
            # A failed retirement must not release memory while the kernel owns it.
            metadata['buffers_retained_until_process_exit']=True
            RETAINED_PENDING.append((slots,kernel,sdk))
    span=(target_end-started)/1e9 if target_end else None
    measurement=sum(r['bytes'] for r in rows if target_end and r['end_ns']<=target_end)
    metadata.update(returned_bytes=total,measurement_bytes=measurement,measurement_seconds=span,
        path_MB_per_second=measurement/span/1e6 if span else None,native_reads=rows,events=events,
        error=error,cleanup_errors=cleanup_errors,
        passed=not error and not cleanup_errors and all(metadata[k] for k in (
            'hardware_STOP_accepted','drain_quiet','native_requests_retired','native_handle_closed','SDK_handle_closed')))
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--depth',type=int,choices=(1,4,8))
    parser.add_argument('--block')
    parser.add_argument('--save-raw',action='store_true')
    args=parser.parse_args()
    if args.self_test:self_test();return 0
    if args.depth is None or not args.block or not __import__('re').fullmatch('[A-Za-z0-9_-]{1,80}',args.block):
        parser.error('Simple fresh block and depth required')
    directory=BASE/args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):parser.error('Fresh workspace output required')
    g=inherited.inherited.v6.guards()
    if os.environ.get('DLA_NATIVE_PIPELINE_CHILD_SHA')!=g.sha(Path(__file__)):
        parser.error('Use the watchdog runner; unbounded hardware execution is disabled')
    with nullcontext():
        protected,core=inherited.protected_v7.core_preflight(g)
        protected[str(Path(__file__))]=g.sha(Path(__file__))
        inherited.inherited.v6.no_active_dla_etw()
        directory.mkdir(parents=True)
        g.write_json(directory/'start.json',{'hardware_accessed':True,'protected_files':protected,
            'started_utc':datetime.now(timezone.utc).isoformat(),'source_outputs_stopped':False})
        result=execute(directory,args.depth,args.save_raw)
        result['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        result['passed']=result['passed'] and not result['protected_files_changed']
        g.write_json(directory/'result.json',result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('native_reads','events','protected_files_changed')}))
        return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
