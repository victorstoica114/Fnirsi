"""Verify all original LOGIC bytes while extracting physical D0..D7 for decoders.

The compressed all-32-channel original is retained. This is an explicit byte
projection for offline protocol decoding, without timing/sample corrections.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent.parent


def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    directory=args.directory.resolve()
    if not directory.is_relative_to(ROOT): parser.error('Workspace input required')
    metadata=json.loads((directory/'capture.json').read_text())
    source=directory/'capture.logic.zst'; output=directory/'capture.D0-D7.logic.bin'
    report=directory/'projection.json'
    if output.exists() or report.exists() or metadata.get('LOGIC_encoding')!='zstd':
        parser.error('Fresh projection output and compressed original required')
    msys=Path((ROOT/'tools/toolchain-path.txt').read_text().strip())
    exe=msys/'ucrt64/bin/zstd.exe'
    env=dict(os.environ);env['PATH']=str(exe.parent)+os.pathsep+env.get('PATH','')
    before=(source.stat().st_size,source.stat().st_mtime_ns,sha(source))
    digest=hashlib.sha256();total=projected=0;tail=b''
    with output.open('xb') as target:
        process=subprocess.Popen([str(exe),'-d','-c','--',str(source)],env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while data:=process.stdout.read(4*1024*1024):
                digest.update(data);total+=len(data)
                data=tail+data;whole=len(data)//4*4
                subset=data[:whole:4];target.write(subset);projected+=len(subset);tail=data[whole:]
            stderr=process.stderr.read();status=process.wait(timeout=10)
        finally:
            if process.poll() is None: process.kill();process.wait(timeout=10)
    passed=status==0 and not tail and total==metadata['LOGIC_uncompressed_bytes'] and \
        digest.hexdigest()==metadata['LOGIC_uncompressed_sha256'] and projected==metadata['actual_samples'] and \
        before==(source.stat().st_size,source.stat().st_mtime_ns,sha(source))
    record={'passed':passed,'source':str(source),'compressed_source_sha256':before[2],
        'all_32_channels_uncompressed_bytes':total,'all_32_channels_uncompressed_sha256':digest.hexdigest(),
        'projected_file':str(output),'projected_channels':'D0..D7','projection':'byte 0 of each original four-byte sample',
        'projected_bytes':projected,'projected_sha256':sha(output),'zstd_cli_sha256':sha(exe),
        'zstd_exit_code':status,'stderr':stderr.decode(errors='replace'),
        'sample_filtering_or_timing_correction_applied':False,'original_compressed_capture_retained':True}
    with report.open('x',encoding='utf-8',newline='\n') as stream:json.dump(record,stream,indent=2);stream.write('\n')
    print(json.dumps({k:record[k] for k in ('passed','all_32_channels_uncompressed_bytes','projected_bytes','projected_sha256')}))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
