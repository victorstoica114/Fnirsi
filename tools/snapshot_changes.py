"""Save reviewable local patches without staging, committing or publishing."""
import json
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parent.parent
destination=root/'artifacts/source-changes'
destination.mkdir(parents=True,exist_ok=True)
summary={}
for name in ('libsigrok','pulseview'):
    repo=root/'src'/name
    command=['git','-c',f'safe.directory={repo.as_posix()}','-C',str(repo)]
    patch=subprocess.check_output(command+['diff','--binary','HEAD'])
    untracked=subprocess.check_output(command+['ls-files','--others','--exclude-standard'],text=True).splitlines()
    for file in untracked:
        result=subprocess.run(command+['diff','--no-index','--binary','--','/dev/null',file],stdout=subprocess.PIPE)
        if result.returncode not in (0,1): raise SystemExit(f'Cannot snapshot {file}')
        patch+=result.stdout
    (destination/f'{name}.patch').write_bytes(patch)
    summary[name]=dict(base_commit=subprocess.check_output(command+['rev-parse','HEAD'],text=True).strip(),
        untracked_included=untracked,patch_bytes=len(patch))
(destination/'manifest.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary,indent=2))
