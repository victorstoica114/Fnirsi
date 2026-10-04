"""Hardware-free integrity audit; write only one new V5 validation record."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json

root=Path(__file__).resolve().parent.parent
source_dir=root/'artifacts/dla32-wch-stop-v5-source';source=source_dir/'libsigrok'
provenance=json.loads((source_dir/'original-provenance.json').read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path): return {p.relative_to(path).as_posix():sha(p) for p in sorted(path.rglob('*')) if p.is_file()}
hashes=json.loads((source_dir/'source-sha256.json').read_text())
assert tree(source)==hashes and tree(Path(provenance['original_source']))==provenance['original_source_sha256']
changed=[name for name in hashes if hashes[name]!=provenance['original_source_sha256'][name]]
assert changed==['src/hardware/fnirsi-dla32/api.c']
assert hashes['src/hardware/fnirsi-dla32/transport-wch.h']=='c45ce7d4c48b5892b267d3a2b01e1b61addf95a6d581d9253596ddb86da4fa85'
assert hashes['src/hardware/fnirsi-dla32/protocol.h']=='8d09566555230a0d66ced4abb2d5d9230dae93c66f953ed0acc2e1fc81bc4cd3'
assert {name:sha(root/name) for name in provenance['protected_dll_sha256']}==provenance['protected_dll_sha256']
old_sources={};old_packages={};files=[];packages={}
for name in ['dla32-drain-ab-source','dla32-wch-upload-source','dla32-wch-upload-v2-source','dla32-wch-upload-v3-source','dla32-wch-timeout-source','dla32-wch-upload-v4-source']:
    directory=root/'artifacts'/name;path=directory/'source-sha256.json'
    saved=json.loads(path.read_text());assert tree(directory/'libsigrok')==saved,name
    old_sources[name]={'files':len(saved),'source_record_sha256':sha(path)}
for name in ['dla32-drain-ab-baseline','dla32-drain-ab-two-empty','dla32-wch-upload','dla32-wch-upload-v2','dla32-wch-upload-v3','dla32-wch-timeout','dla32-wch-upload-v4']:
    directory=root/'artifacts'/name;path=directory/'package-manifest.json'
    saved=json.loads(path.read_text())
    for filename,record in saved['files'].items():
        assert sha(directory/filename)==(record['sha256'] if isinstance(record,dict) else record),(name,filename)
    old_packages[name]={'verified_files':len(saved['files']),'manifest_sha256':sha(path)}
frozen=root/'artifacts/test_dla32_drain_ab.exe'
assert sha(frozen)=='65724e62638bae72bae16b87cd001ba4655eac68d167043dc430fd031c201e82'
for sdk in [0,1]:
    directory=root/f'artifacts/dla32-wch-stop-v5-sdk{sdk}';path=directory/'package-manifest.json'
    manifest=json.loads(path.read_text());expected=551 if not sdk else 598
    assert manifest['variant']==f'wch-stop-v5-sdk{sdk}' and manifest['sdk_policy']==sdk
    assert manifest['unit_checks']==expected and not manifest['unit_failures']
    assert manifest['timeout_helper_checks']==47 and not manifest['timeout_helper_failures']
    assert manifest['make_check_cases']==92 and not manifest['make_check_failures'] and not manifest['make_check_errors']
    assert manifest['compile_runtime_guards']==7 and manifest['native_bash_exit_code']==0
    assert manifest['transport_header_unchanged_vs_v4'] and manifest['protocol_decoder_unchanged_vs_v4']
    assert manifest['sdk0_pending_stop_retention'] and manifest['pending_hardware_stop_survives_application_end']
    assert manifest['wch_configuration_pwm_blocked_while_transport_ownership_pending']
    assert manifest['upload_diagnostic_enabled']==bool(sdk) and manifest['sdk_upload_exports_required']==bool(sdk)
    assert manifest['default_upload_macro_omitted']==(not bool(sdk))
    assert not manifest['hardware_accessed_during_build_or_packaging']
    assert sha(directory/frozen.name)==sha(frozen)==manifest['hardware_harness_sha256']
    assert sha(root/'tools/test_dla32_drain_ab.c')==manifest['hardware_harness_source_sha256']
    for filename,record in manifest['files'].items():
        assert sha(directory/filename)==record['sha256'] and (directory/filename).stat().st_size==record['bytes']
    assert {p.name for p in directory.iterdir() if p.is_file()}==set(manifest['files'])|{'package-manifest.json'}
    fixture=root/f'artifacts/dla32-wch-stop-v5-tests/test_dla32_wch_stop_v5_sdk{sdk}_unit.c'
    assert sha(fixture)==manifest['unit_fixture_sha256']
    unit_path=root/f'logs/dla32-wch-stop-v5-sdk{sdk}-unit.log';text=unit_path.read_text()
    assert f'Audit: {expected} checks, 0 pending behavioral failures.' in text and text.count('PASS:')==expected
    assert sha(unit_path)==manifest['unit_log_sha256']
    if sdk: assert 'V4 inherited checks: 585 (expected585).' in text
    else:
        assert 'SDK0 inherited checks: 444 (443 source checks plus one explicit retry).' in text
        explicit=root/'logs/dla32-wch-stop-v5-sdk0-explicit-unit.log'
        assert explicit.read_text()==text and sha(explicit)==manifest['sdk0_explicit_log_sha256']
        files.append(explicit)
    packages[str(sdk)]={'package_manifest_sha256':sha(path),'dll_sha256':sha(directory/'libsigrok-4.dll'),
        'unit_checks':expected,'unit_failures':0,'verified_package_files':len(manifest['files']),
        'timeout_helper_checks':47,'make_check_cases':92,'compile_runtime_guards':7,'native_bash_exit_code':0}
    files.extend([path,fixture,unit_path,root/f'logs/dla32-wch-stop-v5-sdk{sdk}-timeout.log',
        root/f'logs/build-dla32-wch-stop-v5-sdk{sdk}.log',root/f'logs/build-dla32-wch-stop-v5-sdk{sdk}.status.json',
        Path(manifest['build_directory'])/'tests/main.log',Path(manifest['build_directory'])/'config.status'])
guard_path=root/'logs/dla32-wch-stop-v5-runner-guards.log'
text=guard_path.read_text(encoding='utf-16' if guard_path.read_bytes().startswith(b'\xff\xfe') else 'utf-8')
assert text.count('PASS:')==18 and 'V5 runner guard audit: 18 checks, 0 failures.' in text
files.extend([guard_path,source_dir/'wch-stop-v5-diagnostic.patch',source_dir/'source-manifest.json',source_dir/'source-sha256.json',source_dir/'original-provenance.json',
    root/'artifacts/dla32-wch-stop-v5-tests/test_dla32_wch_stop_v5_timeout_unit.c'])
for name in ['prepare_dla32_wch_stop_v5.py','prepare_dla32_wch_stop_v5_sdk1_tests.py','prepare_dla32_wch_stop_v5_sdk0_tests.py',
    'dla32_wch_stop_v5_sdk1_cases.c.inc','dla32_wch_stop_v5_sdk0_cases.c.inc','build_dla32_wch_stop_v5.sh',
    'run_dla32_wch_stop_v5_build_with_status.sh','test_dla32_wch_stop_v5.sh','test_dla32_wch_stop_v5_guards.sh',
    'package_dla32_wch_stop_v5.py','run_dla32_wch_stop_v5_sdk0.ps1','test_dla32_wch_stop_v5_runner_guards.ps1','validate_dla32_wch_stop_v5.py']:
    files.append(root/'tools'/name)
record={'variant':'wch-stop-v5','created_utc':datetime.now(timezone.utc).isoformat(),
    'hardware_accessed':False,'capture_runner_executed':False,'source_files_verified':len(hashes),'changed_files_vs_v4':changed,
    'transport_header_protocol_decoder_unchanged':True,'libusb_stop_cancel_behavior_unchanged':True,
    'sdk0_pending_stop_and_configuration_guard':True,'sdk1_queue_order_success_markers_preserved':True,
    'packages':packages,'runner_guard_checks':18,'old_sources_verified':old_sources,'old_packages_verified':old_packages,
    'protected_runtime_count':len(provenance['protected_dll_sha256']),'protected_dll_sha256':provenance['protected_dll_sha256'],
    'hardware_harness_copied_unchanged_sha256':sha(frozen),'alignment_fix_established':False,
    'review_roles':{'esp32_register_pwm':'Authored NEW SDK0 fixture; independently reviewed API/source and SDK1 adaptation/guards/package/runner',
                    'stream_drain_build':'Authored API/source and SDK1 adaptation/build/package/runner; independently reviewed SDK0 authored fixture and executed all tests'},
    'normal_capture_hardware_gates':['outer acquisition.exit_code=0','neutral accepted prearm/terminal STOP markers forSDK0; frozen queued-v3 forSDK1','strict frozen V3 capture audit','exact requested N','independent RAW-to-LOGIC all32 comparison'],
    'limitations':['poststop drain stays void/warning-only; app clean is not capture PASS','SDK1 queue erased-byte count unknown','persistent hardware failure retains context until explicit recovery','libusb STOP-failure ownership remains separate','current aggregate RAW tail0 check is conservative matrix scope'],
    'files_sha256':{str(path.relative_to(root)) if path.is_relative_to(root) else str(path):sha(path) for path in files}}
target=root/'artifacts/dla32-wch-stop-v5-tests/validation-manifest.json'
with target.open('x',encoding='utf-8') as stream: stream.write(json.dumps(record,indent=2)+'\n')
print(json.dumps({'validation_manifest':str(target),'validation_manifest_sha256':sha(target),'packages':packages,
    'source_files_verified':len(hashes),'protected_runtime_count':len(provenance['protected_dll_sha256'])},indent=2))
