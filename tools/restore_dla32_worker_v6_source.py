"""Restore the exact V6 source tree from the archived V5 base and small overlay.

Offline only: no builds, DLL loads, hardware access, deletion or overwrites.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / 'artifacts/dla32-wch-worker-v6-overlay'
BASE = ROOT / 'artifacts/dla32-wch-stop-v5-source/libsigrok'


def tree(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if '.git' in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise RuntimeError('Symlink source is unsupported: ' + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path,
                        default=ROOT/'artifacts/dla32-wch-worker-v6-source/libsigrok')
    args = parser.parse_args()
    dest = args.destination.resolve()
    if dest.exists() or not dest.is_relative_to(ROOT) or dest == ROOT:
        parser.error('A fresh workspace destination is required')
    manifest = json.loads((OVERLAY/'manifest.json').read_text(encoding='utf-8'))
    if tree(BASE) != manifest['base_files']:
        raise RuntimeError('V5 base differs; no source copy created')
    for name, entry in manifest['overlay_files'].items():
        path = (OVERLAY/name).resolve(strict=True)
        if not path.is_relative_to(OVERLAY) or path.stat().st_size != entry['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise RuntimeError('Overlay differs: ' + name)
    shutil.copytree(BASE, dest)
    expected = dict(manifest['base_files'])
    for name, entry in manifest['overlay_files'].items():
        relative = 'src/hardware/fnirsi-dla32/' + name
        shutil.copyfile(OVERLAY/name, dest/relative)
        expected[relative] = entry
    restored = tree(dest)
    if restored != expected:
        raise RuntimeError('Restored tree differs; retained for investigation')
    print(json.dumps({'restored':str(dest),'files':len(restored),'all_files_verified':True,
                      'source_tree_identity': manifest['source_tree_identity'], 'hardware_accessed':False}))


if __name__ == '__main__':
    main()
