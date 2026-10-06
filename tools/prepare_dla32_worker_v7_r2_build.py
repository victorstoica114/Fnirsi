"""Copy the verified native V7 build for an isolated incremental r2 build.

No frozen source/runtime/test evidence is modified. Only generated build paths
change; the one edited API file is rebuilt and the complete checks are rerun.
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
MSYS = Path((ROOT/'tools/toolchain-path.txt').read_text().strip()).resolve()
OLD = MSYS/'tmp/dla32-wch-worker-v7-build/libsigrok'
NEW = MSYS/'tmp/dla32-wch-worker-v7-r2-build/libsigrok'


def main():
    if NEW.exists() or not OLD.is_dir() or not NEW.resolve().is_relative_to(MSYS/'tmp'):
        raise RuntimeError('Fresh explicitly scoped native build required')
    shutil.copytree(OLD,NEW)
    mapping = {
        'dla32-wch-worker-v7-build':'dla32-wch-worker-v7-r2-build',
        'dla32-wch-worker-v7-prefix':'dla32-wch-worker-v7-r2-prefix',
        'dla32-wch-worker-v7-source':'dla32-wch-worker-v7-r2-source',
    }
    names = {'Makefile','config.status','libtool','libsigrok.pc','libsigrokcxx.pc'}
    for path in NEW.rglob('*'):
        if path.is_file() and (path.name in names or path.suffix in ('.Plo','.Po','.la','.lo')):
            text = path.read_text()
            for old,new in mapping.items():
                text=text.replace(old,new)
            path.write_text(text,newline='\n')
    # Rewriting libtool .lo metadata gives it a newer timestamp. Force the
    # edited API source newer still, so the copied object cannot be reused.
    api = ROOT/'artifacts/dla32-wch-worker-v7-r2-source/libsigrok/src/hardware/fnirsi-dla32/api.c'
    if not api.is_file():
        raise RuntimeError('Prepared r2 API source missing')
    api.touch()
    print('Isolated r2 incremental native build prepared; V7 unchanged.')


if __name__=='__main__':
    main()
