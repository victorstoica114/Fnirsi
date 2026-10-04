"""Port frozen V4 SDK1 fixture without changing inherited585 cases."""
from pathlib import Path
root=Path(__file__).resolve().parent.parent
directory=root/"artifacts/dla32-wch-stop-v5-tests"
directory.mkdir(exist_ok=True)
target=directory/"test_dla32_wch_stop_v5_sdk1_unit.c"
assert not target.exists()
source=(root/"artifacts/dla32-wch-upload-v4-tests/test_dla32_wch_upload_v4_unit.c").read_text()
source=source.replace("/dla32-wch-upload-v4-source/","/dla32-wch-stop-v5-source/")
source=source.replace("int main(void)",(root/"tools/dla32_wch_stop_v5_sdk1_cases.c.inc").read_text()+"\nint main(void)",1)
old='    printf("Audit: %u checks, %u pending behavioral failures.'
assert source.count(old)==1
source=source.replace(old,'''    printf("V4 inherited checks: %u (expected585).\\n", checks);
    if (checks != 585) { failures++; printf("FAIL: inherited585-check coverage changed.\\n"); }
    test_v5_sdk1_configuration_ownership(FALSE);
    test_v5_sdk1_configuration_ownership(TRUE);
'''+old,1)
target.write_text(source,encoding="utf-8",newline="\n")
helper=(root/"artifacts/dla32-wch-upload-v4-tests/test_dla32_wch_upload_v4_timeout_unit.c").read_text().replace("/dla32-wch-upload-v4-source/","/dla32-wch-stop-v5-source/")
with (directory/"test_dla32_wch_stop_v5_timeout_unit.c").open("x",encoding="utf-8",newline="\n") as stream: stream.write(helper)
print(target)
