"""Package fresh SDK0/SDK1 WCH STOP candidates; copy frozen harness only."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import subprocess
import sys

assert len(sys.argv)==2 and sys.argv[1] in ['0','1']
sdk=int(sys.argv[1]); variant=f"wch-stop-v5-sdk{sdk}"
root=Path(__file__).resolve().parent.parent
msys=Path((root/"tools/toolchain-path.txt").read_text().strip())
reference=root/"artifacts/pulseview-la1010-reference"
destination=root/f"artifacts/dla32-wch-stop-v5-sdk{sdk}"
source_dir=root/"artifacts/dla32-wch-stop-v5-source";source=source_dir/"libsigrok"
build=msys/f"tmp/dla32-wch-stop-v5-sdk{sdk}-build/libsigrok"
prefix=msys/f"tmp/dla32-wch-stop-v5-sdk{sdk}-prefix"
provenance=json.loads((source_dir/"original-provenance.json").read_text())
source_manifest=json.loads((source_dir/"source-manifest.json").read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path): return {p.relative_to(path).as_posix():sha(p) for p in sorted(path.rglob("*")) if p.is_file()}
native_path=root/f"logs/build-dla32-wch-stop-v5-sdk{sdk}.status.json"
status=json.loads(native_path.read_text());assert status['native_bash_exit_code']==0 and status['sdk_policy']==sdk
source_hashes=json.loads((source_dir/"source-sha256.json").read_text())
assert tree(source)==source_hashes
assert tree(Path(provenance['original_source']))==provenance['original_source_sha256']
changed=[name for name in source_hashes if source_hashes[name]!=provenance['original_source_sha256'][name]]
assert changed==['src/hardware/fnirsi-dla32/api.c']
assert {name:sha(root/name) for name in provenance['protected_dll_sha256']}==provenance['protected_dll_sha256']
unit_path=root/f"logs/dla32-wch-stop-v5-sdk{sdk}-unit.log";unit_text=unit_path.read_text()
unit=re.search(r'Audit: (\d+) checks, (\d+) pending behavioral failures',unit_text)
assert unit and unit.group(2)=='0' and 'FAIL:' not in unit_text
assert int(unit.group(1))==unit_text.count('PASS:') and int(unit.group(1))>=(598 if sdk else 551)
if sdk: assert 'V4 inherited checks: 585 (expected585).' in unit_text
explicit_path=root/'logs/dla32-wch-stop-v5-sdk0-explicit-unit.log'
if not sdk:
    explicit=explicit_path.read_text()
    assert re.search(r'Audit: '+unit.group(1)+r' checks, 0 pending behavioral failures',explicit)
    assert explicit.count('PASS:')==int(unit.group(1)) and 'FAIL:' not in explicit
timeout_path=root/f'logs/dla32-wch-stop-v5-sdk{sdk}-timeout.log'
timeout_text=timeout_path.read_text()
assert 'Timeout audit: 47 checks, 0 failures.' in timeout_text and timeout_text.count('PASS:')==47
make_path=build/'tests/main.log'
assert re.search(r'100%: Checks: 92, Failures: 0, Errors: 0',make_path.read_text())
config_text=(build/'config.status').read_text()
assert '-DDLA_PREARM_EMPTY_READS=1' in config_text
assert ('-DDLA_WCH_UPLOAD_DIAGNOSTIC=1' in config_text)==bool(sdk)
if not sdk: assert '-DDLA_WCH_UPLOAD_DIAGNOSTIC=0' not in config_text
build_log=root/f'logs/build-dla32-wch-stop-v5-sdk{sdk}.log'
for text in ['default retains WCH STOP ownership','explicit0 retains WCH STOP ownership','SDK1 retains exact accepted STOP marker',
             f'actual SDK{sdk} runtime upload-export dependency','libusb I/O/cancellation/callback retirement',
             'DLA_WCH_UPLOAD_DIAGNOSTIC must be 0 or 1','baseline one-empty pre-arm policy']:
    assert text in build_log.read_text(),text
frozen=root/'artifacts/test_dla32_drain_ab.exe'
assert sha(frozen)=='65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82'
vendor=Path(r'C:\Program Files\FNIRSI\DLA Logic\CH375DLL64.dll')
assert sha(vendor)=='db19ce17fe9b731cc82b9c40f2c7da23b55f19661c46ce1cfba9952b8a8621f7'
destination.mkdir(exist_ok=False)
for path in reference.iterdir():
    if path.is_file() and (path.suffix.lower()=='.dll' or path.name in {'sigrok-cli.exe','LICENSE-libsigrok.txt','LICENSE-sigrok-cli.txt','sources.lock.json'}) and path.name!='libsigrok-4.dll':
        shutil.copy2(path,destination/path.name)
shutil.copy2(prefix/'bin/libsigrok-4.dll',destination/'libsigrok-4.dll')
for name in [b'CH375SetBufUploadEx',b'CH375ClearBufUpload']:
    assert (name in (destination/'libsigrok-4.dll').read_bytes())==bool(sdk)
for name in ['wch-stop-v5-diagnostic.patch','source-manifest.json','source-sha256.json','original-provenance.json']:
    shutil.copy2(source_dir/name,destination/name)
shutil.copy2(frozen,destination/frozen.name)
assert sha(destination/frozen.name)==sha(frozen)
(destination/'Sigrok-DLA32-WCH-Stop-V5.ps1').write_text("$env:PATH=$PSScriptRoot+';'+$env:PATH\n& (Join-Path $PSScriptRoot 'sigrok-cli.exe') @args\nexit $LASTEXITCODE\n",encoding='utf-8')
(destination/'README.md').write_text(f'''# DLA32 STOP V5 SDK{sdk}

Separate candidate based on frozen V4; only api.c changes. WCH pending hardware
STOP ownership now applies to default SDK0 as well as diagnostic SDK1. A false
or short STOP write, including failed timeout preparation, retains pending
ownership across application END. STOP is accepted only after successful I/O.
New acquisition, context clear and library cleanup refuse that state. Explicit
inactive acquisition_stop or close retries STOP then the unchanged bounded drain;
failed close restores ACTIVE status and preserves handle/context. Startup returns
IO even if its single cleanup retry succeeds. No automatic repeated-stop loop.

Configuration/PWM setters reject WCH pending ownership and, in SDK1, SDKdirty
ownership before any cached mutation or output packet. This sends no automatic
PWM-disable commands. Valid cached generator settings remain intact through
capture stop/recovery. Linux/libusb I/O, cancel and callback ownership code remains
unchanged; this patch generalizes ownership only for Windows WCH transport.

SDK0 is the omitted/default upload macro; it never requires/calls upload exports
and its STOP-complete log says command accepted. SDK1 retains disable-before-STOP
and its exact frozen success markers/order for the queued-v3 audit. Queue helpers
stay in the byte-identical V4 transport header. Protocol/decoder, one empty prearm
read, 1 MiB reads, masks/triggers/limits and corrected operation deadlines are
unchanged. Writes1000 ms, samples1000 ms, drain20 ms are requested SDK deadlines,
not independently measured absolute wall-clock bounds.

This package compiles SDK{sdk}. The frozen657 harness is copied unchanged, not
rebuilt. Source/build/tests/packaging did not access hardware or change outputs.
Root owns hardware validation and any decision to promote a runtime.

Tests: {int(unit.group(1))} actual driver/transport checks,47 real helper checks,
92 general makecheck cases and seven preprocessing/runtime/libusb guards.
SDK0 tests also run with explicit macro0; SDK1 retains inherited585 cases.

Hardware PASS requires outer acquisition.exit_code=0, explicit successful STOP
markers, exact requested count, strict appropriate SDK/log audit and independent
RAW-to-LOGIC all32 comparison. Close/sr_exit errors may be outside per-capture
logs. The postdrain function remains void/warning-only: clean application buffers
alone do not certify quiet or successful capture lifecycle. Persistent hardware
failure intentionally retains context until explicit recovery; no forced
abandon/disconnect cleanup policy is added here.

SDK Clear/disable in SDK1 erases an unknown queue-data amount. Postdrain accounts
only returned bytes, not all discarded data. Quiet/accepted commands do not prove
physical FIFO reset, FPGA alignment, lossless transport or throughput performance.
The frozen audit matrix conservatively requires aggregateRAW length divisible32;
fragmented reads/carry remain allowed, unused partial overshoot tails need separate
investigation. No decoder bytes are dropped, rotated or duty-compensated. V5 is a
reliability correction; no channel-alignment fix is established.

Source: artifacts/dla32-wch-stop-v5-source/libsigrok.
Fresh build: /tmp/dla32-wch-stop-v5-sdk{sdk}-build/libsigrok.
Prefix: /tmp/dla32-wch-stop-v5-sdk{sdk}-prefix.
All earlier sources, packages, evidence and protected runtimes remain unchanged.
''',encoding='utf-8')
cli_checks={}
for argument,label in [('--version','version'),('--list-supported','drivers'),('--self-test','harness-self-test')]:
    executable=destination/(frozen.name if argument=='--self-test' else 'sigrok-cli.exe')
    result=subprocess.run([str(executable),argument],text=True,capture_output=True)
    path=root/f'logs/dla32-wch-stop-v5-sdk{sdk}-{label}.log';assert not path.exists()
    path.write_text(result.stdout+result.stderr,encoding='utf-8');assert result.returncode==0
    if argument=='--self-test': assert 'PASS self-test:' in result.stdout and 'no hardware' in result.stdout
    cli_checks[argument]={'exit_code':0,'log_sha256':sha(path)}
api='src/hardware/fnirsi-dla32/api.c';header='src/hardware/fnirsi-dla32/transport-wch.h';protocol='src/hardware/fnirsi-dla32/protocol.h'
manifest={'variant':variant,'created_utc':datetime.now(timezone.utc).isoformat(),'sdk_policy':sdk,
    'upload_diagnostic_enabled':bool(sdk),'default_upload_macro_omitted':not bool(sdk),
    'sdk_upload_exports_required':bool(sdk),'sdk_disable_precedes_hardware_stop':bool(sdk),
    'sdk0_pending_stop_retention':True,'pending_hardware_stop_survives_application_end':True,
    'wch_configuration_pwm_blocked_while_transport_ownership_pending':True,
    'sdk1_success_markers_and_order_preserved':True,'libusb_stop_cancel_behavior_unchanged':True,
    'transport_header_unchanged_vs_v4':True,'protocol_decoder_unchanged_vs_v4':True,
    'api_sha256':source_hashes[api],'transport_header_sha256':source_hashes[header],'protocol_sha256':source_hashes[protocol],
    'read_size_bytes':1048576,'prearm_empty_reads':1,'sample_read_timeout_ms':1000,'drain_read_timeout_ms':20,'command_write_timeout_ms':1000,
    'per_operation_timeout_setter_required':True,'failed_timeout_setter_suppresses_io':True,
    'upload_pipe':1 if sdk else None,'upload_length_bytes':1048576 if sdk else None,
    'sdk_queue_erased_byte_count':None,'decoder_or_lane_correction':False,'alignment_fix_established':False,
    'unit_checks':int(unit.group(1)),'unit_failures':0,'inherited_sdk1_v4_checks':585 if sdk else None,
    'inherited_sdk0_a07_source_checks':443 if not sdk else None,'sdk0_explicit_macro_unit_checks':int(unit.group(1)) if not sdk else None,
    'timeout_helper_checks':47,'timeout_helper_failures':0,'compile_runtime_guards':7,
    'make_check_cases':92,'make_check_failures':0,'make_check_errors':0,'native_bash_exit_code':0,
    'unit_fixture_sha256':sha(root/f'artifacts/dla32-wch-stop-v5-tests/test_dla32_wch_stop_v5_sdk{sdk}_unit.c'),
    'unit_log_sha256':sha(unit_path),'timeout_helper_log_sha256':sha(timeout_path),'make_check_log_sha256':sha(make_path),
    'native_build_status_sha256':sha(native_path),'source_patch_sha256':sha(source_dir/'wch-stop-v5-diagnostic.patch'),
    'source_directory':str(source),'source_manifest':source_manifest,'build_directory':str(build),'prefix_directory':str(prefix),
    'config_status_sha256':sha(build/'config.status'),'vendor_sdk_sha256':sha(vendor),'kernel_sys_sha256':provenance['kernel_sys_sha256'],
    'hardware_harness_sha256':sha(frozen),'hardware_harness_source_sha256':sha(root/'tools/test_dla32_drain_ab.c'),
    'hardware_harness_copied_unchanged':True,'hardware_accessed_during_build_or_packaging':False,
    'cli_checks':cli_checks,'protected_dll_sha256':provenance['protected_dll_sha256'],
    'files':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(destination.iterdir()) if p.is_file()}}
if not sdk: manifest['sdk0_explicit_log_sha256']=sha(explicit_path)
(destination/'package-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
assert tree(source)==source_hashes and tree(Path(provenance['original_source']))==provenance['original_source_sha256']
assert {name:sha(root/name) for name in provenance['protected_dll_sha256']}==provenance['protected_dll_sha256']
print(json.dumps({'variant':variant,'dll_sha256':sha(destination/'libsigrok-4.dll'),'unit_checks':int(unit.group(1)),
    'package_manifest_sha256':sha(destination/'package-manifest.json'),'hardware_accessed':False},indent=2))
