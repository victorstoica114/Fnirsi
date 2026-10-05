"""Copy an explicit source-only V6/ESP32 update into an existing Git worktree.

Does not read private notes, keys, captures, email, compiled images or ESP flash.
Does not commit or push. Preserve the frozen source files in the main workspace.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
TOOLS = '''analyze_dla32_mask_capture.py audit_dla32_wch_worker_v6.py
build_dla32_wch_worker_v6.sh build_dla32_stop_worker_v6_cancel.sh
test_dla32_wch_worker_v6.sh test_dla32_wch_worker_v6_inherited.sh
test_audit_dla32_wch_worker_v6.py test_run_dla32_worker_v6_capture.py
test_run_dla32_stop_worker_v6_cancel.py test_dla32_stop_worker_v6_cancel_offline.py
test_dla32_stop_worker_v6_cancel.c run_dla32_stop_worker_v6_cancel.py
run_dla32_worker_v6_capture.py run_dla32_worker_v6_benchmark.py
build_dla32_pulseview_v5_cxx.sh package_dla32_pulseview_v5_cxx.py
package_dla32_pulseview_v6_cxx.py run_pulseview_v6_gui_validation.py
run_autonomous_pulseview_v6.py pulseview_ui.ps1 pulseview_inspect_controls.ps1
pulseview_click_control.ps1 pulseview_control_key.ps1 pulseview_select_option.ps1
pulseview_cancel_capture.ps1 pulseview_native_file_dialog.py
build_esp32_protocol_source.py esp32_protocol_console.py
decode_esp32_protocol_capture.py test_esp32_protocol_frame.c
test_dla32_v6_timer_probe.c build_dla32_v6_timer_probe.sh run_dla32_v6_timer_probe.py
restore_dla32_worker_v6_source.py verify_sigrok_archive.py
prepare_dla32_v6_code_backup.py validate_dla32_v6_backup.sh'''.split()
FIRMWARE = '''README.md platformio.ini CMakeLists.txt sdkconfig.defaults
sdkconfig.protocols src/CMakeLists.txt src/main.c src/frame.h existing-wiring-map.json'''.split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worktree', type=Path)
    args = parser.parse_args()
    dest = args.worktree.resolve(strict=True)
    if not dest.is_relative_to(ROOT/'backups') or not (dest/'.git').is_file():
        parser.error('An existing isolated workspace Git worktree is required')
    manifest = dest/'backup/v6-protocol-source-update.sha256.json'
    if manifest.exists():
        raise RuntimeError('Refusing to replace a finalized source manifest')
    selected = ['tools/'+p for p in TOOLS]
    selected += ['firmware/esp32-protocol-source/'+p for p in FIRMWARE]
    selected += ['artifacts/dla32-wch-worker-v6-overlay/'+p for p in ('api.c','transport-wch.h','worker-wch.h','manifest.json')]
    selected += ['artifacts/dla32-wch-worker-v6-tests/'+p for p in (
        'test_dla32_wch_worker_v6_unit.c','test_dla32_wch_worker_v6_inherited_sdk0.c',
        'test_dla32_wch_worker_v6_inherited_sdk1.c','test_dla32_wch_worker_v6_inherited_timeout.c')]
    record = []
    for name in selected:
        src, target = ROOT/name, dest/name
        if not src.is_file() or src.is_symlink() or target.is_symlink():
            raise RuntimeError('Source missing or symlink destination: '+name)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        original_sha = hashlib.sha256(data).hexdigest()
        trimmed = False
        if name.startswith('firmware/esp32-protocol-source/') and data.endswith((b'\n\n', b'\r\n\r\n')):
            newline = b'\r\n' if data.endswith(b'\r\n') else b'\n'
            data = data.rstrip(b'\r\n') + newline
            trimmed = True
        portable = name.startswith('artifacts/dla32-wch-worker-v6-tests/')
        if portable:
            old = b'D:/Documente/analizor logic/artifacts/dla32-wch-worker-v6-source/libsigrok/src/hardware/fnirsi-dla32/'
            if old not in data:
                raise RuntimeError('Expected include prefix missing: '+name)
            data = data.replace(old,b'../dla32-wch-worker-v6-source/libsigrok/src/hardware/fnirsi-dla32/')
        previous_sha = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        with target.open('wb') as stream:
            stream.write(data)
        record.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                       'original_source_sha256':original_sha,'fixture_include_made_relative':portable,
                       'previous_worktree_sha256':previous_sha,'trailing_blank_lines_removed_for_backup':trimmed})
    with manifest.open('x',encoding='utf-8') as output:
        json.dump({'schema':1,'source_files':record,'captures_private_notes_vendor_materials_copied':False,
                   'existing_encrypted_notes_archive_changed':False},output,indent=2)
        output.write('\n')
    print(json.dumps({'worktree':str(dest),'files':len(record),'bytes':sum(r['bytes'] for r in record),
                      'private_materials_copied':False,'committed':False,'pushed':False}))


if __name__ == '__main__':
    main()
