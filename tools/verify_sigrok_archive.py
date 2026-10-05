"""Verify a logic-only sigrok archive against its complete original binary.

No hardware, filtering or channel remapping. Counts, metadata, archive CRC and
every byte must agree; a zero exporter exit code alone is insufficient.
"""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
import zipfile


def verify(archive, original, samplerate):
    problems = []
    first = None
    archive_hash, original_hash = hashlib.sha256(), hashlib.sha256()
    count = 0
    stamps = {p:(p.stat().st_size,p.stat().st_mtime_ns) for p in (archive, original)}
    with zipfile.ZipFile(archive) as z, original.open('rb') as source:
        names = z.namelist()
        if len(names) != len(set(names)):
            problems.append('Duplicate ZIP names')
        metadata = configparser.ConfigParser()
        metadata.read_string(z.read('metadata').decode('utf-8'))
        device = metadata['device 1']
        rate = re.fullmatch(r'([0-9.]+)\s*(Hz|kHz|MHz|GHz)', device['samplerate'])
        actual_rate = float(rate[1])*{'Hz':1,'kHz':1000,'MHz':1e6,'GHz':1e9}[rate[2]] if rate else None
        if device.get('unitsize') != '4' or device.get('total probes') != '32' or actual_rate != samplerate:
            problems.append('Expected 32-channel, 4-byte sample metadata or samplerate differs')
        if [device.get('probe'+str(i+1)) for i in range(32)] != ['D'+str(i) for i in range(32)]:
            problems.append('Physical channel labels differ')
        chunks = sorted((n for n in names if re.fullmatch(r'logic-1-[0-9]+', n)), key=lambda n:int(n.rsplit('-',1)[1]))
        if not chunks or [int(n.rsplit('-',1)[1]) for n in chunks] != list(range(1,len(chunks)+1)):
            problems.append('Missing or nonconsecutive logic chunks')
        for name in chunks:
            with z.open(name) as entry:
                for data in iter(lambda:entry.read(1048576), b''):
                    expected = source.read(len(data))
                    archive_hash.update(data); original_hash.update(expected)
                    if expected != data and first is None:
                        first = count + next((i for i,(a,b) in enumerate(zip(data,expected)) if a != b), min(len(data),len(expected)))
                    count += len(data)
        for data in iter(lambda:source.read(1048576), b''):
            original_hash.update(data)
        if count != stamps[original][0] or count % 4:
            problems.append('Archive byte count differs from full original samples')
        if first is not None or archive_hash.hexdigest() != original_hash.hexdigest():
            problems.append('Archive bytes differ from original')
    if any((p.stat().st_size,p.stat().st_mtime_ns) != stamp for p,stamp in stamps.items()):
        problems.append('Inputs changed during analysis')
    return {'passed':not problems,'issues':problems,'archive':str(archive),'original':str(original),
            'original_bytes':stamps[original][0],'archived_logic_bytes':count,
            'original_sha256':original_hash.hexdigest(),'archived_logic_sha256':archive_hash.hexdigest(),
            'first_mismatch_byte':first,'sample_integrity_or_acquisition_validated':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('original', type=Path)
    parser.add_argument('--samplerate', type=int, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists() or args.report.resolve() in (args.archive.resolve(), args.original.resolve()):
        parser.error('Fresh report distinct from inputs required')
    try:
        result = verify(args.archive,args.original,args.samplerate)
    except (OSError,KeyError,ValueError,zipfile.BadZipFile) as exc:
        result = {'passed':False,'issues':[repr(exc)]}
    with args.report.open('x',encoding='utf-8') as output:
        json.dump(result,output,indent=2);output.write('\n')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
