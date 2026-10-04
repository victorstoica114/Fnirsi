"""Record the exact checked-out sources without publishing anything."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sources = []
for directory in sorted((root / 'src').iterdir()):
    if not (directory / '.git').exists():
        continue
    def git(*args):
        return subprocess.check_output(['git', '-c', 'safe.directory='+directory.as_posix(),
                                        '-C', str(directory), *args], text=True).strip()
    sources.append(dict(path=directory.relative_to(root).as_posix(),
                        remote=git('remote', 'get-url', 'origin'),
                        base_commit=git('rev-parse', 'HEAD')))
(root / 'sources.lock.json').write_text(
    json.dumps(dict(recorded_at=datetime.now(timezone.utc).isoformat(), sources=sources),
               indent=2) + '\n', encoding='utf-8')
print(json.dumps(sources, indent=2))
