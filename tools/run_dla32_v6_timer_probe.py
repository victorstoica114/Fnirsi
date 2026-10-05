"""Root-owned comparison of default versus scoped 1ms host timers with frozen V6."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import run_dla32_worker_v6_capture as capture

ROOT = capture.ROOT
EXE = ROOT / 'artifacts/test_dla32_v6_timer_probe.exe'
SOURCE = ROOT / 'tools/test_dla32_v6_timer_probe.c'
BUILD = ROOT / 'tools/build_dla32_v6_timer_probe.sh'
FIXTURE = ROOT / 'tools/test_dla32_stop_worker_v6_cancel.c'
VALIDATION = ROOT / 'artifacts/test_dla32_v6_timer_probe.validation.json'
BASE = ROOT / 'captures/autonomous-2026-10-05'


def environment(g):
    env = dict(os.environ)
    env['PATH'] = str(g.PACKAGE) + os.pathsep + env.get('PATH', '')
    for key in ('FNIRSI_WCH_DLL', 'FNIRSI_DLA32_WIRE_TRACE'):
        env.pop(key, None)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline-validation', action='store_true')
    parser.add_argument('--block')
    parser.add_argument('--timer', choices=('default', '1ms'))
    args = parser.parse_args()
    g = capture.guards()
    files = [EXE, SOURCE, BUILD, FIXTURE, Path(__file__)]
    if args.offline_validation:
        if VALIDATION.exists():
            raise RuntimeError('Fresh offline validation required')
        result, out, err = g.bounded_process([str(EXE), '--self-test'], environment(g), 15)
        anchors = {str(p): g.sha(p) for p in files}
        text = out.decode('utf-8', errors='replace')
        passed = result['passed'] and '42 checks, 0 failures' in text and all('GLib 1ms timer ' + s in text for s in ('default', 'requested-1ms', 'restored'))
        report = {'hardware_accessed': False, 'passed': passed, 'process': result,
                  'files_sha256': anchors, 'stdout': text, 'stderr': err.decode('utf-8', errors='replace')}
        g.write_json(VALIDATION, report)
        print(json.dumps(report))
        return 0 if passed else 1
    if not args.block or not args.timer or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.block):
        parser.error('Fresh block and timer mode required')
    directory = BASE / args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):
        raise RuntimeError('Fresh workspace directory required')
    with g.exclusive_runner():
        protected = g.preflight()
        proof = json.loads(VALIDATION.read_text())
        if proof['passed'] is not True or proof['hardware_accessed'] is not False:
            raise RuntimeError('Offline validation gate failed')
        protected.update(g.verified_files({Path(p): h for p,h in proof['files_sha256'].items()}))
        protected[str(VALIDATION)] = g.sha(VALIDATION)
        capture.no_active_dla_etw()
        if shutil.disk_usage(ROOT).free < 1200000000:
            raise RuntimeError('Insufficient space to preserve RAW and LOGIC')
        command = [str(EXE), str(ROOT), str(directory), g.EXPECTED_DLL, args.timer]
        report = {'schema': 1, 'started_utc': datetime.now(timezone.utc).isoformat(),
                  'command': command, 'timer_mode': args.timer,
                  'protected_files_sha256': protected, 'hardware_accessed': True,
                  'driver_changed': False, 'generator_commands_sent': False, 'ESP32_accessed': False}
        g.write_json(BASE / (args.block + '.start.json'), report)
        result, out, err = g.bounded_process(command, environment(g), 60)
        report.update(result)
        (BASE / (args.block + '.stdout.log')).write_bytes(out)
        (BASE / (args.block + '.stderr.log')).write_bytes(err)
        issues = []
        try:
            metadata = json.loads((directory / 'capture.json').read_text())
            report['capture'] = metadata
            if not metadata['lifecycle_count_passed'] or metadata['actual_samples'] != 100000000 or metadata['timer_mode'] != args.timer:
                issues.append('Capture metadata/count failed')
            if args.timer == '1ms' and (metadata['timer_begin_status'] != 0 or metadata['timer_end_status'] != 0):
                issues.append('Timer acquisition/restoration failed')
            log = (directory / 'capture.driver.log').read_text()
            terminal = re.findall(r'WCH WORKER terminal:.*?native thread exited\.', log)
            if len(terminal) != 1 or any(s not in terminal[0] for s in ('stop_status 0, pending 0', 'quiet 1, diagnostics_failed 0', 'unconsumed_bytes 0')):
                issues.append('Worker terminal failed')
            reads = [tuple(map(int, m)) for m in re.findall(r'read_id (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+)', log)]
            if not reads or [r[0] for r in reads] != list(range(1,len(reads)+1)) or any(r[3] != 0 or r[4] != 1048576 or r[5] != 1000 for r in reads):
                issues.append('Read contract/order failed')
            raw_bytes = sum(r[6] for r in reads)
            span = reads[-1][1]+reads[-1][2]-reads[0][1] if reads else 0
            io = sum(r[2] for r in reads)
            report['measurement'] = {'SDK_bytes': raw_bytes, 'SDK_reads': len(reads),
                'first_read_to_last_return_seconds': span/1e6, 'SDK_time_sum_seconds': io/1e6,
                'path_MB_per_second': raw_bytes/span if span else None,
                'SDK_call_only_MB_per_second': raw_bytes/io if io else None,
                'scope': 'Instrumented SDK/decoder/C callback path including RAW+LOGIC writes; not USB bus speed'}
            report['files'] = {p.name: {'bytes':p.stat().st_size,'sha256':g.sha(p)} for p in directory.iterdir() if p.is_file()}
            if report['files']['capture.logic.bin']['bytes'] != 400000000 or report['files']['capture.wire.bin']['bytes'] != raw_bytes:
                issues.append('RAW/LOGIC size accounting mismatch')
            report['ETW_postflight'] = capture.no_active_dla_etw()
        except (OSError, KeyError, ValueError, IndexError, RuntimeError) as exc:
            issues.append(repr(exc))
        report['protected_files_changed'] = [p for p,h in protected.items() if g.sha(Path(p)) != h]
        report['issues'] = issues
        report['passed'] = result['passed'] and not issues and not report['protected_files_changed']
        report['sample_integrity_verified'] = False
        report['ended_utc'] = datetime.now(timezone.utc).isoformat()
        g.write_json(BASE / (args.block + '.result.json'), report)
        print(json.dumps({k:report.get(k) for k in ('passed','issues','measurement','sample_integrity_verified')}))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
