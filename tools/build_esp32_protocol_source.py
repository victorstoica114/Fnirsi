"""Build cached ESP-IDF source; optionally write only newly built ESP32 images."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / 'firmware/esp32-protocol-source'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block', required=True)
    parser.add_argument('--upload', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}', args.block):
        parser.error('Use a simple fresh block name')
    paths = {name: ROOT/'logs'/f'esp32-protocol-{args.block}.{name}'
             for name in ('build.log', 'upload.log', 'manifest.json')}
    if any(p.exists() for p in paths.values()):
        parser.error('Refusing to overwrite build/upload evidence')
    junction = Path(os.environ['TEMP']) / 'dla32-esp32-protocol-source'
    if junction.resolve() != PROJECT.resolve() or ' ' in str(junction):
        raise RuntimeError('Expected the existing no-spaces project junction')
    env = os.environ.copy()
    env['PLATFORMIO_OFFLINE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    def run(command, log, seconds):
        with log.open('xb') as handle:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=handle,
                                    stderr=subprocess.STDOUT, timeout=seconds)
        if result.returncode:
            print(log.read_text(encoding='utf-8', errors='replace')[-4000:])
            raise RuntimeError(f'Command failed with {result.returncode}: {log}')
    run([sys.executable, '-X', 'utf8', '-m', 'platformio', 'run', '-d',
         str(junction), '-e', 'protocols'], paths['build.log'], 180)
    build = PROJECT / '.pio/build/protocols'
    inputs = [PROJECT/p for p in ('src/main.c', 'src/frame.h', 'CMakeLists.txt',
              'src/CMakeLists.txt', 'platformio.ini', 'sdkconfig.defaults',
              'sdkconfig.protocols')]
    inputs.extend(build/p for p in ('firmware.bin', 'bootloader.bin', 'partitions.bin'))
    files = {str(p.relative_to(PROJECT)):
             {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in inputs}
    manifest = {'source_project': str(PROJECT), 'files': files,
                'upload_requested': args.upload, 'original_flash_backup': False}
    paths['manifest.json'].write_text(json.dumps(manifest, indent=2)+'\n')
    if args.upload:
        from serial.tools import list_ports
        known = [p for p in list_ports.comports()
                 if p.device == 'COM33' and p.vid == 0x1a86 and p.pid == 0x7523]
        if len(known) != 1:
            raise RuntimeError('Known ESP32 CH340 on COM33 not found')
        command = [sys.executable, '-X', 'utf8', '-m', 'esptool', '--chip', 'esp32',
                   '--port', 'COM33', '--baud', '460800', '--before', 'default-reset',
                   '--after', 'hard-reset', 'write-flash', '--flash-mode', 'dio',
                   '--flash-freq', '40m', '--flash-size', '4MB',
                   '0x1000', str(build/'bootloader.bin'),
                   '0x8000', str(build/'partitions.bin'),
                   '0x10000', str(build/'firmware.bin')]
        run(command, paths['upload.log'], 90)
        manifest['write_command'] = command
        manifest['upload_exit_code'] = 0
        manifest['signals_restart_after_upload'] = True
        paths['manifest.json'].write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'manifest': str(paths['manifest.json']),
                      'build_passed': True, 'upload_passed': args.upload}))


if __name__ == '__main__':
    main()
