"""Bounded V6 throughput probe, with binary output discarded and RAW trace off.

Measures the complete SDK/decoder/CLI path on this host. It neither measures USB
bus utilization nor validates sample integrity. Source outputs remain running.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import run_dla32_worker_v6_capture as capture

ROOT = capture.ROOT
BASE = ROOT / 'captures/autonomous-2026-10-05'


def evaluate(text, samples):
    issues = []
    packets = re.findall(r'Received SR_DF_LOGIC packet \((\d+) bytes, unitsize = (\d+)\)', text)
    if not packets or any(int(unit) != 4 for _, unit in packets) or sum(int(n) for n, _ in packets) != samples * 4:
        issues.append('LOGIC count or unitsize mismatch')
    if any(text.count('Received SR_DF_' + kind + ' packet') != 1 for kind in ('HEADER', 'END')):
        issues.append('HEADER/END count mismatch')
    terminal = re.findall(r'WCH WORKER terminal: generation (\d+), reads (\d+), stop_status (-?\d+), pending ([01]), stop_start_us (\d+), stop_duration_us (\d+), drain_bytes (\d+), drain_reads (\d+), empty_reads (\d+), quiet ([01]), diagnostics_failed ([01]), unconsumed_bytes (\d+); native thread exited\.', text)
    if len(terminal) != 1 or any(terminal[0][i] != v for i, v in {2:'0', 3:'0', 9:'1', 10:'0', 11:'0'}.items()) or int(terminal[0][8]) < 2:
        issues.append('Worker terminal failed')
    for phase in ('pre-arm', 'stop'):
        if text.count('WCH capture STOP complete: phase ' + phase + '; command accepted.') != 1:
            issues.append('STOP acknowledgement missing: ' + phase)
    if 'WIRE trace enabled:' in text:
        issues.append('RAW tracing unexpectedly active')
    read = re.compile(r'WCH WORKER read: generation (\d+), read_id (\d+), io_start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+), Windows error (\d+), payload_offset (\d+), prefix32 ([0-9a-f]*)\.')
    rows = [tuple(map(int, m.groups()[:-1])) for m in read.finditer(text)]
    if not rows or any(r[4] != 0 or r[5] != 1048576 or r[6] != 1000 or not 0 <= r[7] <= r[5] for r in rows):
        issues.append('Read tuple/status failed')
    if terminal and len(rows) != int(terminal[0][1]):
        issues.append('Read accounting mismatch')
    if rows and [r[1] for r in rows] != list(range(1, len(rows)+1)):
        issues.append('Read ID ordering mismatch')
    count = sum(r[7] for r in rows)
    span = rows[-1][2] + rows[-1][3] - rows[0][2] if rows else 0
    io = sum(r[3] for r in rows)
    return {'passed': not issues, 'issues': issues, 'LOGIC_bytes': samples*4,
            'SDK_returned_bytes': count, 'SDK_reads': len(rows),
            'first_read_to_last_return_seconds': span / 1e6,
            'SDK_call_duration_sum_seconds': io / 1e6,
            'path_MB_per_second': count / span if span else None,
            'SDK_call_only_MB_per_second': count / io if io else None,
            'terminal': terminal, 'sample_integrity_verified': False,
            'scope': 'Instrumented SDK/decoder/CLI throughput; decimal MB/s; not USB bus speed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block', required=True)
    parser.add_argument('--rate', required=True, type=int, choices=(25000000, 50000000))
    parser.add_argument('--samples', type=int, default=100000000)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.block) or args.samples != 100000000:
        parser.error('Fresh simple block and the V6 CLI limit of 100M samples required')
    directory = BASE / args.block
    if directory.exists() or not directory.resolve().is_relative_to(ROOT):
        parser.error('Fresh workspace output required')
    g = capture.guards()
    with g.exclusive_runner():
        protected = g.preflight()
        capture_path = Path(capture.__file__)
        capture_digest = 'e46f73747f5567d9dd42dc7ff526156e7bba925d8a6ecee1e49a0c4b54df5676'
        if g.sha(capture_path) != capture_digest:
            raise RuntimeError('Frozen capture helper differs')
        protected[str(capture_path)] = capture_digest
        protected[str(Path(__file__))] = g.sha(Path(__file__))
        capture.no_active_dla_etw()
        directory.mkdir(parents=True)
        config = capture.settings('Stream', args.rate, 100000000, 'all', '1.6', 'none')
        config.update(requested_samples=args.samples, requested_duration_seconds=args.samples / args.rate)
        command = capture.command(g, config, Path('NUL'))
        env = dict(os.environ)
        env['PATH'] = str(g.PACKAGE) + os.pathsep + env.get('PATH', '')
        for name in ('FNIRSI_DLA32_WIRE_TRACE', 'FNIRSI_WCH_DLL'):
            env.pop(name, None)
        report = {'schema': 1, 'started_utc': datetime.now(timezone.utc).isoformat(),
                  'command': command, 'settings': config, 'protected_files': protected,
                  'source_outputs_stopped': False, 'RAW_saved': False,
                  'sample_integrity_verified': False, 'hardware_accessed': True}
        g.write_json(directory / 'start.json', report)
        result, stdout, stderr = g.bounded_process(command, env, 60)
        (directory / 'stdout.log').write_bytes(stdout)
        (directory / 'stderr.log').write_bytes(stderr)
        report.update(result)
        report['measurement'] = evaluate(stderr.decode('utf-8', errors='replace'), args.samples)
        report['protected_files_changed'] = [p for p, digest in protected.items() if g.sha(Path(p)) != digest]
        try:
            report['ETW_postflight'] = capture.no_active_dla_etw()
        except Exception as exc:
            report['ETW_postflight'] = {'failed': repr(exc)}
        report['passed'] = result['passed'] and report['measurement']['passed'] and not report['protected_files_changed'] and 'failed' not in report['ETW_postflight']
        report['ended_utc'] = datetime.now(timezone.utc).isoformat()
        g.write_json(directory / 'result.json', report)
        print(json.dumps({'passed': report['passed'], 'measurement': report['measurement'], 'record': str(directory/'result.json')}))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
