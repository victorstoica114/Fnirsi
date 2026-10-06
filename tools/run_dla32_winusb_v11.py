"""Isolated finite libusb/WinUSB probe with the frozen V7 r2 core."""
import argparse
import ctypes as C
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import run_dla32_worker_v8_benchmark as inherited

ROOT=inherited.ROOT
PACKAGE=ROOT/'artifacts/dla32-winusb-v11'
BASE=ROOT/'captures/winusb-v11-2026-10-06'


def connection():
    class Descriptor(C.Structure):
        _fields_=[('length',C.c_uint8),('kind',C.c_uint8),('usb',C.c_uint16),
            ('devclass',C.c_uint8),('subclass',C.c_uint8),('protocol',C.c_uint8),('maxpacket0',C.c_uint8),
            ('vendor',C.c_uint16),('product',C.c_uint16),('release',C.c_uint16),
            ('manufacturer',C.c_uint8),('product_string',C.c_uint8),('serial',C.c_uint8),('configs',C.c_uint8)]
    dll=C.CDLL(str(PACKAGE/'libusb-1.0.dll'))
    dll.libusb_init.argtypes=[C.POINTER(C.c_void_p)];dll.libusb_init.restype=C.c_int
    dll.libusb_get_device_list.argtypes=[C.c_void_p,C.POINTER(C.POINTER(C.c_void_p))];dll.libusb_get_device_list.restype=C.c_ssize_t
    dll.libusb_get_device_descriptor.argtypes=[C.c_void_p,C.POINTER(Descriptor)];dll.libusb_get_device_descriptor.restype=C.c_int
    for name in ('libusb_get_bus_number','libusb_get_device_address','libusb_get_device_speed'):
        getattr(dll,name).argtypes=[C.c_void_p];getattr(dll,name).restype=C.c_int
    dll.libusb_free_device_list.argtypes=[C.POINTER(C.c_void_p),C.c_int];dll.libusb_exit.argtypes=[C.c_void_p]
    ctx=C.c_void_p();devices=C.POINTER(C.c_void_p)();matches=[]
    if dll.libusb_init(C.byref(ctx)):raise RuntimeError('libusb init failed')
    try:
        count=dll.libusb_get_device_list(ctx,C.byref(devices))
        if count<0:raise RuntimeError('libusb enumeration failed')
        for index in range(count):
            descriptor=Descriptor()
            if dll.libusb_get_device_descriptor(devices[index],C.byref(descriptor)):continue
            if descriptor.vendor==0x1a86 and descriptor.product==0x5537:
                matches.append({'bus':dll.libusb_get_bus_number(devices[index]),
                    'address':dll.libusb_get_device_address(devices[index]),'speed':dll.libusb_get_device_speed(devices[index])})
    finally:
        if devices:dll.libusb_free_device_list(devices,1)
        dll.libusb_exit(ctx)
    if len(matches)!=1 or matches[0]['speed']<4:raise RuntimeError('One SuperSpeed DLA required')
    return matches[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block',required=True)
    parser.add_argument('--save-raw',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.block):parser.error('Simple fresh block required')
    directory=BASE/args.block
    if directory.exists():parser.error('Fresh output required')
    g=inherited.inherited.v6.guards()
    with g.exclusive_runner():
        protected,oldcore=inherited.protected_v7.core_preflight(g)
        proof=PACKAGE/'offline-validation.json'
        core=json.loads(proof.read_text())
        if core['passed'] is not True or core['hardware_accessed'] is not False:raise RuntimeError('V11 offline gate failed')
        protected.update(g.verified_files({Path(p):h for p,h in core['files_sha256'].items()}))
        protected[str(proof)]=g.sha(proof)
        protected[str(Path(__file__))]=g.sha(Path(__file__))
        inherited.inherited.v6.no_active_dla_etw();node=connection()
        directory.mkdir(parents=True)
        config=inherited.inherited.v6.settings('Stream',50000000,100000000,'all','1.6','rising')
        output=directory/'capture.logic.bin' if args.save_raw else Path('NUL')
        command=inherited.inherited.v6.command(SimpleNamespace(PACKAGE=PACKAGE),config,output)
        command[command.index('--driver')+1]=f"fnirsi-dla32:conn={node['bus']}.{node['address']}"
        env=dict(os.environ);env['PATH']=str(PACKAGE)+os.pathsep+env.get('PATH','')
        env['FNIRSI_DLA32_LOGIC_ONLY']='1'
        for key in ('FNIRSI_DLA32_WIRE_TRACE','FNIRSI_WCH_DLL'):env.pop(key,None)
        if args.save_raw:env['FNIRSI_DLA32_WIRE_TRACE']=str(directory/'capture.wire.bin')
        record={'started_utc':datetime.now(timezone.utc).isoformat(),'command':command,'device':node,
            'core_dll_sha256':core['core_dll_sha256'],'settings':config,'protected_files':protected,
            'hardware_accessed':True,'RAW_saved':args.save_raw,'source_outputs_stopped':False,'sample_integrity_verified':False}
        g.write_json(directory/'start.json',record)
        process,out,err=g.bounded_process(command,env,60)
        (directory/'stdout.log').write_bytes(out);(directory/'stderr.log').write_bytes(err)
        text=err.decode('utf-8',errors='replace')
        packets=[]
        for line in text.splitlines():
            m=re.search(r'\[(\d+):(\d+\.\d+)\].*Received SR_DF_LOGIC packet \((\d+) bytes, unitsize = (\d+)\)',line)
            if m:packets.append((int(m[1])*60+float(m[2]),int(m[3]),int(m[4])))
        issues=[]
        if not packets or sum(n for _,n,_ in packets)!=400000000 or any(u!=4 for _,_,u in packets):issues.append('LOGIC count failed')
        if any(text.count('Received SR_DF_'+kind+' packet')!=1 for kind in ('HEADER','END')):issues.append('HEADER/END failed')
        if 'at wch:' in text:issues.append('Wrong transport')
        if re.search(r'Failed|discontinuity|Command .* failed',text):issues.append('Transport error log')
        span=packets[-1][0]-packets[0][0] if len(packets)>1 else 0
        count=sum(n for _,n,_ in packets[1:])
        record.update(process)
        record['measurement']={'issues':issues,'passed':not issues,'LOGIC_bytes':sum(n for _,n,_ in packets),
            'after_first_packet_MB_per_second':count/span/1e6 if span else None,
            'measurement_bytes':count,'measurement_seconds':span,'scope':'Instrumented libusb/WinUSB/CLI after first LOGIC packet; not USB bus utilization'}
        record['protected_files_changed']=[p for p,h in protected.items() if g.sha(Path(p))!=h]
        record['passed']=process['passed'] and not issues and not record['protected_files_changed']
        g.write_json(directory/'result.json',record)
        print(json.dumps({'passed':record['passed'],'measurement':record['measurement'],'device':node}))
        return 0 if record['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
