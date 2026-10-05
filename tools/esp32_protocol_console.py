"""Bounded CH340 console access; no reset, flash read, backup or output stop."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import serial
from serial.tools import list_ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM33')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--command', action='append', default=[])
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60 or args.log.exists():
        parser.error('Use 1..60 seconds and a fresh log')
    commands = args.command or ['STATUS']
    allowed = {'STATUS', 'UART=115200', 'UART=1000000', 'SPI=1000000',
               'SPI=5000000', 'SPI=10000000', 'I2C=100000', 'I2C=400000',
               'TOPOLOGY', 'I2C_MODE=full', 'I2C_MODE=write_nack',
               'I2C_SLAVE=normal', 'I2C_SLAVE=swapped',
               'MISO_PULL=up', 'MISO_PULL=none'}
    allowed.update('GAP=' + str(value) for value in range(1, 101))
    if any(command not in allowed for command in commands):
        parser.error('Command is outside the source firmware whitelist')
    targets = [p for p in list_ports.comports() if p.device == args.port
               and p.vid == 0x1a86 and p.pid == 0x7523]
    if len(targets) != 1:
        raise RuntimeError('Expected the known CH340 on ' + args.port)
    port = serial.Serial(port=None, baudrate=115200, timeout=0.15,
                         write_timeout=1)
    port.dtr = False
    port.rts = False
    port.port = args.port
    start = time.monotonic()
    lines = []
    with args.log.open('x', encoding='utf-8', newline='\n') as log:
        def record(direction, value):
            item = {'utc': datetime.now(timezone.utc).isoformat(),
                    'elapsed_seconds': time.monotonic()-start,
                    'direction': direction, 'text': value}
            log.write(json.dumps(item) + '\n'); log.flush()
        with port:
            for command in commands:
                record('TX', command)
                port.write((command + '\n').encode('ascii'))
            while time.monotonic()-start < args.seconds:
                raw = port.readline()
                if raw:
                    line = raw.decode('utf-8', errors='replace').strip()
                    record('RX', line)
                    lines.append(line)
    print(json.dumps({'port': args.port, 'log': str(args.log),
                      'received_lines': len(lines), 'last_lines': lines[-4:],
                      'reset_requested': False, 'outputs_stopped': False}))


if __name__ == '__main__':
    main()
