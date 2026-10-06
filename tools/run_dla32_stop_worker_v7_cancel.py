"""Isolated V7 cancel/recovery on one open DLA32; sources remain running."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import run_dla32_worker_v7_capture as v7

EXE = v7.ROOT / 'artifacts/test_dla32_stop_worker_v7_cancel.exe'


def verify(directory, mode, digest, sha):
    seq = json.loads((directory/'sequence-result.json').read_text())
    issues = []
    expected = {'harness':'separate_v7_worker_cancel_recovery',
        'expected_loaded_DLL_SHA256':digest,'completed_capture_stages':3,
        'expected_capture_stages':3,'device_close_attempted':True,'device_close_status':0,
        'sr_exit_attempted':True,'sr_exit_status':0,'outer_lifecycle_and_count_pass':True}
    if any(seq.get(k) != value for k,value in expected.items()):
        issues.append('Sequence/close/sr_exit failed')
    stages = []
    for stem, wanted, samples in ((f'cancel001-{mode}',mode,None),
            ('rep001-step1-Buffer','Buffer',5000000),('rep001-step2-Stream','Stream',5000000)):
        meta = json.loads((directory/(stem+'.json')).read_text())
        criteria = {'mode':wanted,'headers':1,'ends':1,'stopped_callbacks':1,
            'logic_after_stop':0,'context_mismatches':0,'run_status':0,'running_after_run':0,
            'stop_status':0,'destroy_status':0,'invalid_packet':0,'guard_expired':0,
            'logic_close_failed':0,'driver_error_log_count':0,'driver_log_write_failures':0,
            'driver_log_close_failed':0,'lifecycle_and_count_pass':True,
            'worker_unconsumed_bytes_are_decoder_input':False}
        if any(meta.get(k) != value for k,value in criteria.items()):
            issues.append(stem+': lifecycle/settings failed')
        count = meta['actual_samples']
        if samples is None:
            if not (count < 10000000 and meta['samples_at_stop'] == count and
                    meta['cancel_timer_fired'] == 1 and meta['stop_call_count'] == 1 and
                    meta['stop_reason'] == 'cancel-timer'):
                issues.append(stem+': cancel timing/count failed')
        elif count != samples or meta['stop_requested']:
            issues.append(stem+': finite recovery count failed')
        files = {}
        for key,size in (('logic_file',count*4),('wire_file',meta['wire_trace_bytes']),
                         ('worker_unconsumed_file',meta['worker_unconsumed_bytes'])):
            name = meta[key]
            if Path(name).name != name:
                raise RuntimeError('Invalid output filename')
            path = directory/name
            if path.stat().st_size != size:
                issues.append(stem+': file accounting failed')
            files[name] = {'bytes':path.stat().st_size,'sha256':sha(path)}
        text = (directory/meta['driver_log_file']).read_text()
        terminal = re.findall(r'WCH WORKER terminal:.*?native thread exited\.',text)
        if len(terminal) != 1 or any(s not in terminal[0] for s in
                ('stop_status 0, pending 0','quiet 1, diagnostics_failed 0',
                 'unconsumed_bytes '+str(meta['worker_unconsumed_bytes']))):
            issues.append(stem+': STOP/drain/native-exit failed')
        if meta['worker_unconsumed_bytes'] > 2097152:
            issues.append(stem+': prefetch exceeded two-buffer bound')
        stages.append({'capture':stem,'actual_samples':count,
                       'preserved_prefetch_bytes':meta['worker_unconsumed_bytes'],'files':files})
    return {'passed':not issues,'issues':issues,'stages':stages,'sequence':seq,
            'physical_integrity_verified':False,'real_disconnect_tested':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block',required=True)
    parser.add_argument('--cancel-mode',choices=('Buffer','Stream'),required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.block):
        raise ValueError('Fresh simple block required')
    directory = v7.BASE/args.block
    if directory.exists() or not directory.resolve().is_relative_to(v7.ROOT):
        raise ValueError('Fresh workspace output required')
    g = v7.v6.guards()
    with g.exclusive_runner():
        protected = g.preflight()
        proof = json.loads(v7.PROOF.read_text())
        if proof['passed'] is not True or proof['hardware_accessed'] is not False:
            raise RuntimeError('Offline acceptance failed')
        protected.update(g.verified_files({Path(p):h for p,h in proof['files_sha256'].items()}))
        protected[str(v7.PROOF)] = g.sha(v7.PROOF)
        env = dict(os.environ)
        env['PATH'] = str(v7.PACKAGE)+os.pathsep+env.get('PATH','')
        env['DLA32_CANCEL_MODE'] = args.cancel_mode
        for key in ('FNIRSI_WCH_DLL','FNIRSI_DLA32_WIRE_TRACE'):
            env.pop(key,None)
        cmd = [str(EXE),str(v7.ROOT),str(directory),proof['core_dll_sha256']]
        record = {'command':cmd,'cancel_mode':args.cancel_mode,'started_utc':datetime.now(timezone.utc).isoformat(),
            'ESP32_accessed':False,'signal_generator_commands_sent':False,'hardware_accessed':True,
            'ETW_preflight':v7.v6.no_active_dla_etw(),'protected_files_sha256':protected}
        v7.BASE.mkdir(parents=True,exist_ok=True)
        g.write_json(v7.BASE/(args.block+'.start.json'),record)
        process,out,err = g.bounded_process(cmd,env,60)
        (v7.BASE/(args.block+'.stdout.log')).write_bytes(out)
        (v7.BASE/(args.block+'.stderr.log')).write_bytes(err)
        record['process'] = process
        record['verification'] = verify(directory,args.cancel_mode,proof['core_dll_sha256'],g.sha)
        record['ETW_postflight'] = v7.v6.no_active_dla_etw()
        record['protected_files_changed'] = [p for p,h in protected.items() if g.sha(Path(p)) != h]
        record['passed'] = process['passed'] and record['verification']['passed'] and not record['protected_files_changed']
        record['ended_utc'] = datetime.now(timezone.utc).isoformat()
        g.write_json(v7.BASE/(args.block+'.result.json'),record)
        print(json.dumps({'passed':record['passed'], 'issues':record['verification']['issues'],
            'stages':[{k:s[k] for k in ('capture','actual_samples','preserved_prefetch_bytes')} for s in record['verification']['stages']]}))
        return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
