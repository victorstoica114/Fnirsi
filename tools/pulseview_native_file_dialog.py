"""Fill only an owned PulseView native file dialog with a workspace path."""
import argparse
import ctypes as C
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid-file', type=Path, required=True)
    parser.add_argument('--path', type=Path, required=True)
    parser.add_argument('--action', choices=('save', 'open'), default='save')
    args = parser.parse_args()
    target = args.path.resolve()
    if not target.is_relative_to(ROOT) or not target.parent.is_dir():
        parser.error('Existing workspace parent required')
    if (args.action == 'save' and target.exists()) or (args.action == 'open' and not target.is_file()):
        parser.error('Save must be fresh; open must already exist')
    pid = int(args.pid_file.read_text())
    user = C.WinDLL('user32', use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [C.c_void_p, C.POINTER(C.c_uint)]
    user.GetWindowTextW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_int]
    user.GetClassNameW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_int]
    user.IsWindowVisible.argtypes = [C.c_void_p]
    user.SendMessageTimeoutW.argtypes = [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t, C.c_uint, C.c_uint, C.POINTER(C.c_size_t)]
    user.SendMessageTimeoutW.restype = C.c_void_p
    callback_type = C.WINFUNCTYPE(C.c_bool, C.c_void_p, C.c_void_p)
    rows = []
    @callback_type
    def visit(handle, unused):
        owner = C.c_uint()
        user.GetWindowThreadProcessId(handle, C.byref(owner))
        if owner.value == pid and user.IsWindowVisible(handle):
            title, kind = C.create_unicode_buffer(256), C.create_unicode_buffer(80)
            user.GetWindowTextW(handle, title, 256)
            user.GetClassNameW(handle, kind, 80)
            rows.append((handle, title.value, kind.value))
        return True
    user.EnumWindows(visit, 0)
    expected_title = 'Save File' if args.action == 'save' else 'Open File'
    dialogs = [r for r in rows if r[1] == expected_title and r[2] == '#32770']
    if len(dialogs) != 1:
        raise RuntimeError('Exactly one owned native file dialog required')
    rows.clear()
    user.EnumChildWindows(C.c_void_p(dialogs[0][0]), visit, 0)
    edits = [r for r in rows if r[2] == 'Edit']
    expected_button = '&Save' if args.action == 'save' else '&Open'
    buttons = [r for r in rows if r[1] == expected_button and r[2] == 'Button']
    if len(edits) != 1 or len(buttons) != 1:
        raise RuntimeError('Ambiguous filename edit or accept button')
    buffer = C.create_unicode_buffer(str(target))
    result = C.c_size_t()
    if not user.SendMessageTimeoutW(edits[0][0], 0x000c, 0, C.addressof(buffer), 2, 5000, C.byref(result)) or result.value != 1:
        raise RuntimeError('Filename WM_SETTEXT failed')
    if not user.SendMessageTimeoutW(buttons[0][0], 0x00f5, 0, 0, 2, 5000, C.byref(result)):
        raise RuntimeError('Owned dialog accept failed or timed out')
    print(json.dumps({'PID': pid, 'action': args.action, 'path': str(target), 'dialog_submitted': True,
                      'file_contents_validated': False}))


if __name__ == '__main__':
    main()
