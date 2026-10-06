"""Freeze WinUSB endpoint/model correction and the checked continuous harness."""
from datetime import datetime,timezone
import json
import re
from pathlib import Path
from accept_dla32_worker_v8 import ROOT,sha


def main():
    package=ROOT/'artifacts/dla32-winusb-v11';proof=package/'offline-validation.json'
    if proof.exists():raise RuntimeError('Fresh V11 acceptance required')
    files=[ROOT/f'logs/dla32-winusb-v11-{kind}.log' for kind in ('build','general-checks','unit')]
    if not re.search(r'CC\s+src/hardware/fnirsi-dla32/api.lo',files[0].read_text()) or '100%: Checks: 92, Failures: 0, Errors: 0' not in files[1].read_text():raise RuntimeError('Build/general gates failed')
    text=files[2].read_text()
    if 'Audit: 206 checks, 0 pending behavioral failures' not in text or 'WinUSB endpoint audit: 13 checks, 0 failures.' not in text or re.search(r'^FAIL:',text,re.M):raise RuntimeError('WCH/WinUSB behavior gates failed')
    harness=ROOT/'logs/dla32-winusb-continuous-self-test-r2.log'
    if '42 checks, 0 failures' not in harness.read_text() or 'Lossless LOGIC sink: PASS' not in harness.read_text():raise RuntimeError('Continuous offline gates failed')
    files.append(harness)
    files += [p for p in (ROOT/'artifacts/dla32-winusb-v11-source/libsigrok').rglob('*') if p.is_file()]
    files += [p for p in (ROOT/'artifacts/dla32-winusb-v11-tests').iterdir() if p.is_file()]
    files += [ROOT/'tools'/name for name in ('prepare_dla32_winusb_v11.py','build_dla32_winusb_v11.sh',
        'test_dla32_winusb_v11.c','accept_dla32_winusb_v11.py','test_dla32_winusb_continuous_r2.c',
        'build_dla32_winusb_continuous_r2.sh','dla32_lossless_logic_sink.h')]
    files += [ROOT/'artifacts/test_dla32_winusb_continuous_r2.exe']
    files += [p for p in package.iterdir() if p.is_file()]
    record={'passed':True,'hardware_accessed':False,'created_utc':datetime.now(timezone.utc).isoformat(),
        'general_checks':92,'WCH_lifecycle_checks':206,'WinUSB_endpoint_checks':13,'continuous_harness_checks':42,
        'libusb_command_endpoint':1,'libusb_identification_endpoint':130,'fresh_DL32_identification_required':True,
        'core_dll_sha256':sha(package/'libsigrok-4.dll'),
        'files_sha256':{str(p.resolve()):sha(p) for p in files},'main_runtime_promoted':False,
        'physical_integrity_verified':False,'lossless_stream_verified':False}
    with proof.open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps({k:record[k] for k in ('passed','general_checks','WCH_lifecycle_checks','WinUSB_endpoint_checks','core_dll_sha256')}))


if __name__=='__main__':main()
