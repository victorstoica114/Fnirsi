"""Measure empty direct ReadEndP timing; no ARM, PWM, ESP or device reset."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import time
from probe_wch_upload_states import DLL, DLL_SHA


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('Report exists')
    if hashlib.sha256(DLL.read_bytes()).hexdigest() != DLL_SHA:
        raise ValueError('SDK changed')
    dll = C.WinDLL(str(DLL), use_last_error=True)
    U32, BOOL = C.c_uint32, C.c_int32
    for name, types, result in (
        ('CH375OpenDevice', [U32], C.c_void_p),
        ('CH375CloseDevice', [U32], None),
        ('CH375GetUsbID', [U32], U32),
        ('CH375SetTimeoutEx', [U32, U32, U32, U32, U32], BOOL),
        ('CH375SetBufUploadEx', [U32, U32, U32, U32], BOOL),
        ('CH375WriteData', [U32, C.c_void_p, C.POINTER(U32)], BOOL),
        ('CH375ReadEndP', [U32, U32, C.c_void_p, C.POINTER(U32)], BOOL),
    ):
        function = getattr(dll, name)
        function.argtypes, function.restype = types, result
    report = {'SDK_sha256': DLL_SHA, 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'pipe': 1, 'requested_bytes': 1048576, 'ARM_sent': False,
              'generator_commands_sent': False, 'ESP_access': False, 'calls': []}
    handle = dll.CH375OpenDevice(0)
    if handle in (None, C.c_void_p(-1).value):
        raise RuntimeError('Device unavailable')

    def call(name, values, phase):
        C.set_last_error(0)
        start = time.perf_counter_ns()
        result = getattr(dll, name)(*values)
        error = C.get_last_error()
        entry = {'phase': phase, 'function': name, 'BOOL': int(result),
                 'windows_error': error, 'duration_ms': (time.perf_counter_ns()-start)/1000000}
        report['calls'].append(entry)
        return bool(result), entry

    try:
        if dll.CH375GetUsbID(0) != 0x55371A86:
            raise RuntimeError('Unexpected USB ID')
        ok, _ = call('CH375SetTimeoutEx', (0,1000,1000,1000,1000), 'identify-timeouts')
        if not ok:
            raise RuntimeError('Timeout configuration rejected')
        command = bytes((0x0A,0x10))+bytes(510)
        data, count = C.create_string_buffer(command), U32(512)
        if not dll.CH375WriteData(0,data,C.byref(count)) or count.value != 512:
            raise RuntimeError('Identification write failed')
        data, count = C.create_string_buffer(512), U32(512)
        if not dll.CH375ReadEndP(0,2,data,C.byref(count)):
            raise RuntimeError('Identification read failed')
        raw = data.raw[:count.value]
        if not 19 <= count.value <= 512 or raw[:3] != b'\x0a\x02\x0d' or raw[13:17] != b'DL32' or raw[18] != 11:
            raise RuntimeError('DL32 not confirmed')
        ok, _ = call('CH375SetBufUploadEx', (0,0,1,1048576), 'disable-for-direct-read')
        if not ok:
            raise RuntimeError('Direct-reader retirement failed')
        for label, fields in [('legacy-driver-tuple',(1000,20,1000,20)),
                              ('first-field-20ms',(20,1000,1000,1000)),
                              ('first-field-50ms',(50,1000,1000,1000)),
                              ('first-field-100ms',(100,1000,1000,1000)),
                              ('first-field-1000ms',(1000,1000,1000,1000))]:
            for repetition in (1,2):
                phase=f'{label}-r{repetition}'
                ok, item = call('CH375SetTimeoutEx', (0,*fields), phase)
                item['timeout_fields_ms'] = list(fields)
                if not ok:
                    raise RuntimeError('Timeout call rejected')
                data, count = C.create_string_buffer(1048576), U32(1048576)
                ok, item = call('CH375ReadEndP',(0,1,data,C.byref(count)),phase)
                item.update(returned_bytes=int(count.value), timeout_fields_ms=list(fields))
                if not ok or count.value > 1048576:
                    raise RuntimeError('Read failed or returned invalid count')
                if count.value:
                    payload_path=args.report.with_name(args.report.stem+'-'+phase+'.raw.bin')
                    with payload_path.open('xb') as output:
                        output.write(data.raw[:count.value])
                    item['unexpected_payload']={'path':str(payload_path),'sha256':hashlib.sha256(data.raw[:count.value]).hexdigest()}
                    raise RuntimeError('Nonempty read; retained payload and refused remaining timing comparisons')
    except Exception as error:
        report['probe_error']=str(error)
    finally:
        ok, _ = call('CH375SetTimeoutEx',(0,1000,20,1000,20),'restore-frozen-driver-tuple')
        report['original_timeout_tuple_restored']=ok
        dll.CH375CloseDevice(0)
        with args.report.open('x',encoding='utf-8') as output:
            json.dump(report,output,indent=2)
            output.write('\n')
    print(json.dumps({'report':str(args.report),'error':report.get('probe_error'),
                      'reads':[entry for entry in report['calls'] if entry['function']=='CH375ReadEndP']},indent=2))
    if report.get('probe_error') or not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
