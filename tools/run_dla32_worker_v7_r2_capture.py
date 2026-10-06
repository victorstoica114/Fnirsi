"""Isolated V7 finite acquisition with frozen V6 protection and offline gates.

Preserves RAW, LOGIC and every prefetched block discarded after Stop separately.
No signal generator commands, ESP32 access, device resets or timer changes.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import run_dla32_worker_v6_capture as v6

ROOT = v6.ROOT
BASE = ROOT / 'captures/wch-worker-v7-r2-sdk0-2026-10-06'
PACKAGE = ROOT / 'artifacts/dla32-wch-worker-v7-r2-sdk0'
PROOF = PACKAGE / 'offline-validation.json'


def verify(config, paths, text, sha):
    # V7 permits bounded prefetch after a finite limit. The original V6 gate
    # remains unchanged; adapt its expected sidecar accounting for this probe.
    match = re.findall(r'unconsumed_bytes (\d+); native thread exited\.', text)
    count = int(match[0]) if len(match) == 1 else -1
    side = paths['unconsumed']
    issues = []
    if count < 0 or count > 2 * 1048576 or not side.exists() or side.stat().st_size != count:
        issues.append('Prefetch sidecar size/terminal accounting/bound failed')
    closed = re.findall(r'WCH WORKER unconsumed trace closed: (\d+) bytes, failed ([01]);',text)
    if closed != [(str(count),'0')]:
        issues.append('Prefetch trace close failed or byte count differs')
    # Run the inherited gate with only its V6 zero-prefetch expectation replaced.
    normalized = re.sub(r'unconsumed_bytes \d+; native thread exited\.',
                        'unconsumed_bytes 0; native thread exited.', text)
    normalized = re.sub(r'WCH WORKER unconsumed trace closed: \d+ bytes, failed ([01]);',
                        r'WCH WORKER unconsumed trace closed: 0 bytes, failed \1;', normalized)
    checks = v6.verify(config, paths, normalized.encode(), sha)
    checks['issues'] = [s for s in checks['issues'] if s != 'Unexpected unconsumed data in finite capture']
    reads = [tuple(map(int, m)) for m in re.findall(
        r'read_id (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+)', text)]
    if not reads or [r[0] for r in reads] != list(range(1, len(reads)+1)) or any(
        r[3] not in (0, -7) or r[4] != 1048576 or r[5] != 1000 or not 0 <= r[6] <= r[4] for r in reads):
        issues.append('SDK read identities/status/count contract failed')
    raw = paths['wire'].stat().st_size if paths['wire'].exists() else -1
    total = sum(r[6] for r in reads)
    if total != raw + count:
        issues.append('SDK returned bytes differ from decoder input plus preserved prefetch')
    if checks.get('terminal') and int(checks['terminal'][0][1]) != len(reads):
        issues.append('Terminal/read count mismatch')
    spans = [reads[i][1] - reads[i-1][1] - reads[i-1][2] for i in range(1,len(reads))]
    if any(gap < 0 for gap in spans):
        issues.append('SDK calls overlap or recorded ordering is invalid')
    io = sum(r[2] for r in reads)
    span = reads[-1][1] + reads[-1][2] - reads[0][1] if reads else 0
    checks['issues'].extend(issues)
    checks['passed'] = not checks['issues']
    checks['prefetched_unconsumed_bytes'] = count
    checks['prefetch_is_decoder_input'] = False
    checks['measurement'] = {'SDK_returned_bytes':total, 'SDK_reads':len(reads),
        'path_MB_per_second':total/span if span else None,
        'SDK_call_only_MB_per_second':total/io if io else None,
        'maximum_between_read_gap_us':max(spans) if spans else None,
        'scope':'Finite instrumented SDK/decoder/CLI path, decimal MB/s; not USB bus speed or a sample integrity verdict'}
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block', required=True)
    parser.add_argument('--mode', required=True, choices=('Buffer','Stream'))
    parser.add_argument('--rate', required=True, type=int)
    parser.add_argument('--samples', required=True, type=int)
    parser.add_argument('--channels', default='all')
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    config = v6.settings(args.mode,args.rate,args.samples,args.channels,'1.6','rising')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.block):
        raise ValueError('Fresh simple block required')
    directory = BASE / args.block
    paths = {'directory':directory, 'wire':directory/'capture.wire.bin',
             'logic':directory/'capture.logic.bin',
             'unconsumed':directory/'capture.wire.bin.worker-unconsumed.bin'}
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):
        raise ValueError('Fresh workspace output required')
    g = v6.guards()
    with g.exclusive_runner():
        protected = g.preflight()
        proof = json.loads(PROOF.read_text())
        if proof['passed'] is not True or proof['hardware_accessed'] is not False:
            raise RuntimeError('V7 offline acceptance failed')
        protected.update(g.verified_files({Path(p):h for p,h in proof['files_sha256'].items()}))
        protected[str(PROOF)] = g.sha(PROOF)
        protected[str(Path(__file__))] = g.sha(Path(__file__))
        if g.sha(PACKAGE/'libsigrok-4.dll') != proof['core_dll_sha256']:
            raise RuntimeError('V7 runtime differs from tested build')
        cmd = [str(PACKAGE/'sigrok-cli.exe'),'--driver','fnirsi-dla32','--config',
            f"data_source={args.mode}:samplerate={args.rate}:voltage_threshold=1.6-1.6",
            '--channels',','.join(config['channels']),'--samples',str(args.samples),
            '--triggers','D0=r','--output-format','binary','--output-file',str(paths['logic']),
            '--loglevel','4']
        etw = v6.no_active_dla_etw()
        if args.preflight_only:
            print(json.dumps({'preflight_passed':True,'hardware_accessed':False,'command':cmd}))
            return 0
        if shutil.disk_usage(ROOT).free < args.samples*config['unitsize'] + ((args.samples+7)//8)*config['wire_frame_bytes']+268435456:
            raise RuntimeError('Insufficient space to retain original evidence')
        directory.mkdir(parents=True)
        report = {'settings':config,'command':cmd,'started_utc':datetime.now(timezone.utc).isoformat(),
            'core_dll_sha256':proof['core_dll_sha256'],'hardware_accessed':True,
            'ESP32_accessed':False,'signal_generator_commands_sent':False,
            'timer_resolution_changed':False,'protected_files_sha256':protected,'ETW_preflight':etw}
        g.write_json(directory/'start.json',report)
        env = dict(os.environ)
        env['PATH'] = str(PACKAGE)+os.pathsep+env.get('PATH','')
        env.pop('FNIRSI_WCH_DLL',None)
        env['FNIRSI_DLA32_WIRE_TRACE'] = str(paths['wire'])
        process,out,err = g.bounded_process(cmd,env,60)
        report['process'] = process
        (directory/'stdout.log').write_bytes(out)
        (directory/'stderr.log').write_bytes(err)
        report['verification'] = verify(config,paths,err.decode('utf-8',errors='replace'),g.sha)
        report['ETW_postflight'] = v6.no_active_dla_etw()
        report['protected_files_changed'] = [p for p,h in protected.items() if g.sha(Path(p)) != h]
        report['passed'] = process['passed'] and report['verification']['passed'] and not report['protected_files_changed']
        report['physical_integrity_verified'] = False
        report['ended_utc'] = datetime.now(timezone.utc).isoformat()
        g.write_json(directory/'result.json',report)
        print(json.dumps({'passed':report['passed'],'issues':report['verification']['issues'],
                          'measurement':report['verification']['measurement'],'record':str(directory/'result.json')}))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
