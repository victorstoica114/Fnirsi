"""Offline libsigrokdecode UART/SPI/I2C plus independent source packet checks.

Never accesses hardware, filters samples, or changes a driver's channel mapping.
An explicit diagnostic offset changes only the decoder's input selection and
cannot produce a physical-pin validation PASS.
"""
import argparse
import binascii
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'artifacts/pulseview-dla32'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024*1024), b''):
            digest.update(part)
    return digest.hexdigest()


def expected_packet(protocol, sequence):
    protocol = ord(protocol)
    packet = bytearray(b'\xa5\x5a' + bytes([protocol, 1]))
    packet.extend(sequence.to_bytes(4, 'little'))
    state = sequence ^ (protocol << 24) ^ 0x6d2b79f5
    for _ in range(6):
        state ^= (state << 13) & 0xffffffff
        state ^= state >> 17
        state ^= (state << 5) & 0xffffffff
        packet.append(state & 255)
    packet.extend(binascii.crc_hqx(packet, 0xffff).to_bytes(2, 'big'))
    return bytes(packet)


def validate_packet(data, protocol):
    if len(data) != 16:
        return {'valid': False, 'length': len(data), 'hex': data.hex()}
    sequence = int.from_bytes(data[4:8], 'little')
    magic = data[:4] == b'\xa5\x5a' + protocol.encode() + b'\x01'
    crc = binascii.crc_hqx(data[:14], 0xffff) == int.from_bytes(data[14:], 'big')
    expected = data == expected_packet(protocol, sequence)
    return {'sequence': sequence, 'valid': magic and crc and expected,
            'header_valid': magic, 'crc_valid': crc,
            'complete_expected_packet': expected, 'hex': data.hex()}


def packet_stream(data, protocol):
    magic = b'\xa5\x5a' + protocol.encode() + b'\x01'
    start = data.find(magic)
    if start < 0:
        return {'verdict': 'FAIL', 'decoded_bytes': len(data),
                'complete_packets': 0, 'reason': 'No source packet marker',
                'decoded_hex': data.hex()}
    packets, skipped = [], []
    cursor = start
    while cursor + 16 <= len(data):
        item = validate_packet(data[cursor:cursor+16], protocol)
        item['decoded_byte_offset'] = cursor
        packets.append(item)
        cursor += 16
        if cursor + 16 <= len(data) and data[cursor:cursor+4] != magic:
            following = data.find(magic, cursor)
            if following < 0:
                skipped.append({'offset': cursor, 'bytes': len(data)-cursor})
                cursor = len(data)
                break
            skipped.append({'offset': cursor, 'bytes': following-cursor})
            cursor = following
    valid = [p for p in packets if p['valid']]
    counters = [p['sequence'] for p in valid]
    discontinuities = [{'previous': a, 'next': b,
                        'delta_mod_2_32': (b-a) & 0xffffffff}
                       for a, b in zip(counters, counters[1:])
                       if ((b-a) & 0xffffffff) != 1]
    suffix = data[cursor:]
    prefix = data[:start]
    prefix_match = None
    suffix_match = None
    if packets and packets[0]['valid'] and prefix:
        previous = expected_packet(protocol, (packets[0]['sequence']-1) & 0xffffffff)
        prefix_match = len(prefix) < 16 and previous.endswith(prefix)
    if packets and packets[-1]['valid'] and suffix:
        following = expected_packet(protocol, (packets[-1]['sequence']+1) & 0xffffffff)
        suffix_match = len(suffix) < 16 and following.startswith(suffix)
    good = len(packets) >= 2 and len(valid) == len(packets) and not skipped and not discontinuities
    return {'verdict': 'PASS' if good else 'FAIL', 'decoded_bytes': len(data),
            'complete_packets': len(packets), 'valid_packets': len(valid),
            'crc_failures': sum(not p.get('crc_valid', False) for p in packets),
            'counter_discontinuities': discontinuities, 'interior_skipped_bytes': skipped,
            'capture_boundary_prefix_bytes': len(prefix),
            'capture_boundary_suffix_bytes': len(suffix),
            'boundary_prefix_matches_previous_packet': prefix_match,
            'boundary_suffix_matches_next_packet': suffix_match,
            'packets': packets,
            'verdict_scope': 'Complete interior decoded packets; capture boundary fragments listed separately'}


def parse_annotations(text):
    events = []
    for line in text.splitlines():
        match = re.fullmatch(r'(\d+)-(\d+) ([\w-]+): ([\w-]+): (.*)', line)
        if not match:
            raise ValueError('Unexpected decoder annotation: ' + line[:200])
        start, end, decoder, kind, value = match.groups()
        events.append({'start': int(start), 'end': int(end), 'decoder': decoder,
                       'kind': kind, 'text': value})
    return events


def data_bytes(events, kind):
    return bytes(int(e['text'].rsplit(': ', 1)[-1], 16)
                 for e in events if e['kind'] == kind)


def i2c_transactions(events, contract='full'):
    if contract not in ('full', 'write_nack'):
        raise ValueError('Unsupported I2C contract')
    complete, pending = [], None
    for event in events:
        kind = event['kind']
        if kind in ('start', 'repeat-start'):
            if pending:
                pending['ended_by_repeat_start'] = True
                complete.append(pending)
            pending = {'start': event['start'], 'data': [], 'acks': [], 'warnings': []}
        elif pending is not None:
            if kind.startswith('address-') and event['text'].startswith('Address '):
                pending['address'] = int(event['text'].rsplit(': ', 1)[-1], 16)
                pending['direction'] = kind.removeprefix('address-')
            elif kind.startswith('data-'):
                pending['data'].append(int(event['text'].rsplit(': ', 1)[-1], 16))
            elif kind in ('ack', 'nack'):
                pending['acks'].append(kind)
            elif kind == 'warning':
                pending['warnings'].append(event['text'])
            elif kind == 'stop':
                pending['end'] = event['end']
                direction = pending.get('direction')
                protocol = 'I' if direction == 'write' else 'R'
                pending['packet'] = validate_packet(bytes(pending['data']), protocol)
                expected_acks = (['nack'] * 17 if contract == 'write_nack' else
                                 ['ack'] * 17 if direction == 'write' else ['ack'] * 16 + ['nack'])
                pending['acks_expected'] = pending['acks'] == expected_acks
                pending['valid'] = (pending.get('address') == 0x42 and
                                    pending['packet']['valid'] and pending['acks_expected'] and
                                    not pending['warnings'] and
                                    (contract != 'write_nack' or direction == 'write'))
                complete.append(pending)
                pending = None
    return {'contract': contract,
            'verdict': 'PASS' if len(complete) >= (2 if contract == 'write_nack' else 4) and
            all(t.get('valid', False) for t in complete) else 'FAIL',
            'complete_transactions': len(complete),
            'valid_transactions': sum(t.get('valid', False) for t in complete),
            'ack_count': sum(t['acks'].count('ack') for t in complete),
            'nack_count': sum(t['acks'].count('nack') for t in complete),
            'transactions': complete, 'unfinished_capture_boundary_transaction': pending}


def spi_transfer_check(events):
    transfers, boundary = [], []
    for event in events:
        if event['kind'] != 'mosi-transfer':
            continue
        packet = bytes.fromhex(event['text'])
        item = {**validate_packet(packet, 'S'), 'start': event['start'], 'end': event['end']}
        if event['start'] == 0 and not item['valid']:
            boundary.append(item)
        else:
            transfers.append(item)
    return {'verdict': 'PASS' if len(transfers) >= 2 and all(t['valid'] for t in transfers) else 'FAIL',
            'complete_valid_CS_transfers': sum(t['valid'] for t in transfers),
            'transfers': transfers, 'clipped_initial_CS_transfer': boundary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logic', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--samplerate', type=int, default=50000000)
    parser.add_argument('--numchannels', type=int, choices=(8, 16, 32), default=32)
    parser.add_argument('--uart-baud', type=int, default=115200)
    parser.add_argument('--diagnostic-offset', type=int, choices=(-1, 0, 15, 16), default=0)
    parser.add_argument('--pinmap', type=Path, help='Explicit frozen decoder channel map JSON')
    parser.add_argument('--scope', choices=('full', 'tx_write_nack'), default='full')
    args = parser.parse_args()
    if args.out.exists() or not args.out.resolve().is_relative_to(ROOT):
        parser.error('Output must be a fresh directory inside workspace')
    assert binascii.crc_hqx(b'123456789', 0xffff) == 0x29b1
    before = (args.logic.stat().st_size, args.logic.stat().st_mtime_ns)
    unit = args.numchannels // 8
    if before[0] % unit:
        parser.error('Input does not contain complete sample words')
    args.out.mkdir(parents=True)
    env = os.environ.copy()
    env['PYTHONHOME'] = str(PACKAGE)
    env['SIGROKDECODE_DIR'] = str(PACKAGE / 'decoders')
    env['PATH'] = str(PACKAGE) + os.pathsep + env.get('PATH', '')
    offset = args.diagnostic_offset
    pinmap = {name: bit+offset for name, bit in
              {'uart_tx': 2, 'i2c_sda': 3, 'i2c_scl': 4, 'spi_clk': 5,
               'spi_mosi': 6, 'spi_miso': 7, 'spi_cs': 8}.items()}
    mapping_evidence = None
    if args.pinmap:
        if offset:
            parser.error('Explicit pinmap and diagnostic offset cannot be combined')
        mapping_evidence = json.loads(args.pinmap.read_text(encoding='utf-8'))
        candidate = mapping_evidence['decoder_channels']
        if set(candidate) != set(pinmap) or any(type(v) is not int or v < 0 for v in candidate.values()):
            parser.error('Explicit pinmap must contain all named nonnegative channel indices')
        pinmap = candidate
    if max(pinmap.values()) >= args.numchannels:
        parser.error('Requested diagnostic channel is outside the saved word')
    base = [str(PACKAGE/'sigrok-cli.exe'), '--dont-scan', '-i', str(args.logic.resolve()),
            '-I', f'binary:numchannels={args.numchannels}:samplerate={args.samplerate}']
    spi_spec = (f"spi:clk={pinmap['spi_clk']}:mosi={pinmap['spi_mosi']}:cs={pinmap['spi_cs']}:cpol=0:cpha=0" +
                (f":miso={pinmap['spi_miso']}" if args.scope == 'full' else ''))
    specifications = {
        'uart': (f"uart:tx={pinmap['uart_tx']}:baudrate={args.uart_baud}",
                 'uart=tx-data:tx-warning:tx-break:tx-parity-err'),
        'spi': (spi_spec,
                'spi=mosi-data:miso-data:warning:mosi-transfer:miso-transfer'),
        'i2c': (f"i2c:sda={pinmap['i2c_sda']}:scl={pinmap['i2c_scl']}",
                'i2c=start:repeat-start:stop:ack:nack:address-read:address-write:data-read:data-write:warning'),
    }
    all_events, commands = {}, []
    for name, (specification, annotations) in specifications.items():
        command = base + ['-P', specification, '-A', annotations,
                          '--protocol-decoder-samplenum', '--protocol-decoder-ann-class']
        result = subprocess.run(command, env=env, capture_output=True, timeout=60)
        (args.out/(name+'.annotations.txt')).write_bytes(result.stdout)
        (args.out/(name+'.stderr.txt')).write_bytes(result.stderr)
        commands.append({'command': command, 'exit_code': result.returncode})
        if result.returncode:
            raise RuntimeError(name + ' decoder failed: ' + result.stderr.decode(errors='replace'))
        all_events[name] = parse_annotations(result.stdout.decode('utf-8'))
    streams = {'uart_tx': ('uart', 'tx-data', 'U'),
               'spi_mosi': ('spi', 'mosi-data', 'S'),
               'spi_miso': ('spi', 'miso-data', 'S'),
               'i2c_write': ('i2c', 'data-write', 'I'),
               'i2c_read': ('i2c', 'data-read', 'R')}
    if args.scope == 'tx_write_nack':
        del streams['spi_miso']
        del streams['i2c_read']
    checks = {}
    for name, (decoder, kind, protocol) in streams.items():
        decoded = data_bytes(all_events[decoder], kind)
        (args.out/(name+'.decoded.bin')).write_bytes(decoded)
        checks[name] = packet_stream(decoded, protocol)
    warnings = [e for events in all_events.values() for e in events
                if 'warning' in e['kind'] or 'break' in e['kind'] or 'err' in e['kind']]
    transactions = i2c_transactions(all_events['i2c'],
                                    'write_nack' if args.scope == 'tx_write_nack' else 'full')
    spi_transfers = spi_transfer_check(all_events['spi'])
    payload_pass = all(c['verdict'] == 'PASS' for c in checks.values())
    scope_pass = payload_pass and transactions['verdict'] == 'PASS' and spi_transfers['verdict'] == 'PASS' and not warnings
    verdict = ('DIAGNOSTIC_ONLY' if offset else
               ('PASS_LIMITED_SCOPE' if args.scope == 'tx_write_nack' else 'PASS')
               if scope_pass else 'FAIL')
    report = {'verdict': verdict, 'decoded_payloads_pass': payload_pass,
              'validation_scope': args.scope, 'scope_passed': scope_pass,
              'complete_SPI_I2C_receiver_validation': args.scope == 'full' and scope_pass and not offset,
              'explicit_pinmap': {'path': str(args.pinmap), 'sha256': sha(args.pinmap),
                                 'evidence': mapping_evidence} if args.pinmap else None,
              'diagnostic_channel_offset': offset,
              'physical_pinmap_confirmed_by_this_analysis': False,
              'sample_filtering_or_driver_remapping_applied': False,
              'source': str(args.logic.resolve()), 'source_sha256': sha(args.logic),
              'sample_count': before[0]//unit, 'samplerate_hz': args.samplerate,
              'pinmap_decoder_input_bits': pinmap, 'uart_baud': args.uart_baud,
              'decoder_engine': {'cli': sha(PACKAGE/'sigrok-cli.exe'),
                  'libsigrokdecode': sha(PACKAGE/'libsigrokdecode-4.dll'),
                  'modules': {p: sha(PACKAGE/'decoders'/p/'pd.py') for p in specifications}},
              'analysis_sha256': sha(Path(__file__)), 'commands': commands,
              'annotation_counts': {p: dict(Counter(e['kind'] for e in events))
                                    for p, events in all_events.items()},
              'streams': checks, 'i2c_transactions': transactions,
              'spi_CS_transfers': spi_transfers, 'warnings': warnings,
              'lossless_continuous_stream_proven': False,
              'limitations': ['Finite captures; packet CRC/counter checks do not prove every sample or idle interval',
                              'Source self-check and physical wiring must be assessed separately']}
    if (args.logic.stat().st_size, args.logic.stat().st_mtime_ns) != before:
        raise RuntimeError('Input changed during analysis')
    (args.out/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'verdict': verdict, 'report': str(args.out/'report.json'),
                      'streams': {p: {k: c.get(k) for k in
                        ('verdict', 'complete_packets', 'valid_packets', 'crc_failures')}
                                  for p, c in checks.items()},
                      'i2c_transactions': transactions['verdict'], 'warnings': len(warnings)}))


if __name__ == '__main__':
    main()
