"""Assemble an isolated GUI candidate with the verified V7 r2 core."""
import json
from pathlib import Path
import shutil
import run_autonomous_pulseview_v6 as v6gui
import run_dla32_worker_v7_r2_probes as probes

ROOT=probes.ROOT
PACKAGE=ROOT/'artifacts/pulseview-dla32-v7-r2-cxx'


def main():
    if PACKAGE.exists():
        raise RuntimeError('Fresh GUI candidate required')
    g=probes.v7.v6.guards()
    with g.exclusive_runner():
        protected,old=v6gui.package_preflight()
        core_protected,core=probes.core_preflight(g)
        protected.update(core_protected)
        PACKAGE.mkdir()
        for name,item in old['files'].items():
            path=PACKAGE/name
            path.parent.mkdir(parents=True,exist_ok=True)
            if name=='libsigrok-4.dll':
                shutil.copyfile(probes.v7.PACKAGE/name,path)
            else:
                shutil.copyfile(v6gui.PACKAGE/name,path)
        # Existing launcher is updated only in this separate candidate.
        launch=PACKAGE/'launch-pulseview-v6.ps1'
        if launch.exists():
            text=launch.read_text().replace(g.EXPECTED_DLL,core['core_dll_sha256'])
            launch.write_text(text,newline='\n')
        manifest={'variant':'pulseview-dla32-v7-r2-cxx','core_DLL_sha256':core['core_dll_sha256'],
            'CXX_and_GUI_rebuilt':False,'public_headers_changed':False,
            'lossless_200MB_per_second_verified':False,'main_runtime_promoted':False,
            'files':{str(p.relative_to(PACKAGE)).replace('\\','/'):
                {'bytes':p.stat().st_size,'sha256':g.sha(p)} for p in sorted(PACKAGE.rglob('*')) if p.is_file()}}
        g.write_json(PACKAGE/'package-manifest.json',manifest)
        if any(g.sha(Path(p))!=h for p,h in protected.items()):
            raise RuntimeError('Protected original file changed during packaging')
        print(json.dumps({'passed':True,'candidate_files':len(manifest['files']),
                          'core_DLL_sha256':core['core_dll_sha256'],'hardware_accessed':False}))


if __name__=='__main__':
    main()
