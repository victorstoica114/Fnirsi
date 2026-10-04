"""Prepare isolated diagnostic source and a hardware-free pre-arm fixture."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent.parent
original = root / "artifacts/la1010-reference-source/libsigrok"
destination = root / "artifacts/dla32-drain-ab-source/libsigrok"
if destination.exists():
    raise SystemExit(f"Refusing to overwrite source tree: {destination}")
shutil.copytree(original, destination)
relative = "src/hardware/fnirsi-dla32/api.c"
driver = destination / relative
before = driver.read_text(encoding="utf-8")
source = before
source = source.replace(
    "#define STOP_EMPTY_READS 2\n",
    "#define STOP_EMPTY_READS 2\n"
    "/* Isolated A/B diagnostic; the production policy remains one empty read. */\n"
    "#ifndef DLA_PREARM_EMPTY_READS\n"
    "#define DLA_PREARM_EMPTY_READS 1\n"
    "#endif\n"
    "#if DLA_PREARM_EMPTY_READS != 1 && DLA_PREARM_EMPTY_READS != 2\n"
    '#error "DLA_PREARM_EMPTY_READS must be 1 or 2"\n'
    "#endif\n",
    1,
)
source = source.replace("\tuint64_t drain_total = 0;\n", "\tuint64_t drain_total = 0;\n\tunsigned int drain_empty = 0;\n", 1)
source = source.replace(
    "\t/* Stop and drain stale data before arming the new asynchronous queue. */\n",
    "\tsr_info(\"PREARM drain policy: %u consecutive empty reads.\",\n"
    "\t\t(unsigned int)DLA_PREARM_EMPTY_READS);\n\n"
    "\t/* Stop and drain stale data before arming the new asynchronous queue. */\n",
    1,
)
source = source.replace(
    "\t\tif ((ret == LIBUSB_SUCCESS || ret == LIBUSB_ERROR_TIMEOUT) && !count)\n\t\t\tbreak;\n",
    "\t\tif ((ret == LIBUSB_SUCCESS || ret == LIBUSB_ERROR_TIMEOUT) && !count) {\n"
    "\t\t\tdrain_empty++;\n"
    "\t\t\tif (drain_empty >= DLA_PREARM_EMPTY_READS)\n"
    "\t\t\t\tbreak;\n"
    "\t\t} else {\n"
    "\t\t\tdrain_empty = 0;\n"
    "\t\t}\n",
    1,
)
source = source.replace(
    '\t\tsr_dbg("WIRE pre-arm drain summary: discarded %" PRIu64 ", mod32 %u, reads %d.",\n'
    '\t\t\tdrain_total, (unsigned int)(drain_total % 32), i + 1);\n',
    '\t\tsr_dbg("WIRE pre-arm drain summary: discarded %" PRIu64 ", mod32 %u, reads %d,"\n'
    '\t\t\t" empty_reads %u, required_empty_reads %u.",\n'
    '\t\t\tdrain_total, (unsigned int)(drain_total % 32), i + 1,\n'
    '\t\t\tdrain_empty, (unsigned int)DLA_PREARM_EMPTY_READS);\n',
    1,
)
assert source != before and "drain_empty >= DLA_PREARM_EMPTY_READS" in source
driver.write_text(source, encoding="utf-8", newline="\n")
patch = "".join(difflib.unified_diff(before.splitlines(True), source.splitlines(True),
                                  fromfile="a/" + relative, tofile="b/" + relative))
(destination.parent / "prearm-drain-ab.patch").write_text(patch, encoding="utf-8")

tests = root / "artifacts/dla32-drain-ab-tests"
tests.mkdir(exist_ok=False)
fixture = (root / "artifacts/dla32-wiretrace-tests/test_dla32_wiretrace.c").read_text(encoding="utf-8")
fixture = fixture.replace(str((original / relative).as_posix()), str(driver.as_posix()))
assert str(driver.as_posix()) in fixture
fixture = fixture.replace("static struct sr_trigger *fake_trigger;", r'''static struct sr_trigger *fake_trigger;
struct scripted_read { int count, status; gint64 elapsed_us; };
static struct scripted_read prearm_script[64];
static unsigned int prearm_script_length, prearm_script_position;
static unsigned int reads_at_arm;
''', 1)
fixture = fixture.replace("    commands[opcode]++;", "    commands[opcode]++;\n    if (opcode == 0x12) reads_at_arm = read_calls;", 1)
fixture = fixture.replace(
    "    read_calls++; fake_now += 1000; *actual = 0;",
    r'''    if (prearm_script_position < prearm_script_length) {
        const struct scripted_read *item = &prearm_script[prearm_script_position++];
        read_calls++; fake_now += item->elapsed_us;
        *actual = MIN(item->count, length);
        if (*actual > 0) memset(data, 0, *actual);
        return item->status;
    }
    read_calls++; fake_now += 1000; *actual = 0;''', 1)
fixture = fixture.replace(
    "    memset(commands, 0, sizeof(commands)); fake_now = 1000000;",
    "    memset(commands, 0, sizeof(commands)); fake_now = 1000000;\n"
    "    prearm_script_length = prearm_script_position = reads_at_arm = 0;", 1)
extra = r'''
static void scripted_prearm_case(gboolean wch, const char *name,
    const struct scripted_read *sequence, unsigned int length,
    int expected_result, unsigned int expected_reads)
{
    struct fixture f;
    char label[256];
    int result;
    fixture_init(&f, wch);
    memcpy(prearm_script, sequence, sizeof(*sequence) * length);
    prearm_script_length = length;
    result = sr_dev_acquisition_start(f.sdi);
    snprintf(label, sizeof(label), "PREARM policy %u %s: return code", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, result == expected_result);
    snprintf(label, sizeof(label), "PREARM policy %u %s: exact read count", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, (result == SR_OK ? reads_at_arm : read_calls) == expected_reads
        && prearm_script_position == expected_reads);
    snprintf(label, sizeof(label), "PREARM policy %u %s: ARM only after criterion", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, result == SR_OK ? commands[0x12] == 1 : !commands[0x12]);
    snprintf(label, sizeof(label), "PREARM policy %u %s: setup follows successful drain", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, result == SR_OK ? commands[0x11] == 1 : !commands[0x11]);
    if (result == SR_OK) {
        /* Clear remaining simulated stale bytes before normal post-stop cleanup. */
        prearm_script_position = prearm_script_length;
        sr_dev_acquisition_stop(f.sdi); fixture_pump(&f);
    }
    snprintf(label, sizeof(label), "PREARM policy %u %s: clean resources", DLA_PREARM_EMPTY_READS, name);
    expect_transport(&f, label, resources_clean(&f));
    fixture_clear(&f);
}

static void test_prearm_scripted(gboolean wch)
{
    const unsigned int policy = DLA_PREARM_EMPTY_READS;
    struct scripted_read bound[64];
    unsigned int i;
    const struct scripted_read empty[] = {{0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read data_empty[] = {{32, 0, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read empty_data[] = {{0, 0, 1000}, {32, 0, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read intermittent[] = {{32, 0, 1000}, {0, 0, 1000}, {32, 0, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read timeout_empty[] = {{0, LIBUSB_ERROR_TIMEOUT, 1000}, {0, LIBUSB_ERROR_TIMEOUT, 1000}};
    const struct scripted_read timeout_data[] = {{32, LIBUSB_ERROR_TIMEOUT, 1000}, {0, 0, 1000}, {0, 0, 1000}};
    const struct scripted_read io_first[] = {{0, LIBUSB_ERROR_IO, 1000}};
    const struct scripted_read io_after_data[] = {{32, 0, 1000}, {0, LIBUSB_ERROR_IO, 1000}};
    const struct scripted_read deadline_data[] = {{32, 0, STOP_DRAIN_USEC}};
    const struct scripted_read deadline_empty[] = {{0, 0, STOP_DRAIN_USEC}};
    scripted_prearm_case(wch, "empty", empty, ARRAY_SIZE(empty), SR_OK, policy);
    scripted_prearm_case(wch, "data then empty", data_empty, ARRAY_SIZE(data_empty), SR_OK, policy + 1);
    scripted_prearm_case(wch, "empty then stale data", empty_data, ARRAY_SIZE(empty_data), SR_OK, policy == 1 ? 1 : 4);
    scripted_prearm_case(wch, "nonconsecutive empties reset", intermittent, ARRAY_SIZE(intermittent), SR_OK, policy == 1 ? 2 : 5);
    scripted_prearm_case(wch, "timeout empty counts", timeout_empty, ARRAY_SIZE(timeout_empty), SR_OK, policy);
    scripted_prearm_case(wch, "timeout with data resets", timeout_data, ARRAY_SIZE(timeout_data), SR_OK, policy + 1);
    scripted_prearm_case(wch, "first read IO", io_first, ARRAY_SIZE(io_first), SR_ERR_IO, 1);
    scripted_prearm_case(wch, "IO after data", io_after_data, ARRAY_SIZE(io_after_data), SR_ERR_IO, 2);
    scripted_prearm_case(wch, "deadline with data", deadline_data, ARRAY_SIZE(deadline_data), SR_ERR_TIMEOUT, 1);
    scripted_prearm_case(wch, "deadline empty preserves baseline", deadline_empty, ARRAY_SIZE(deadline_empty), policy == 1 ? SR_OK : SR_ERR_TIMEOUT, 1);
    for (i = 0; i < ARRAY_SIZE(bound); i++) bound[i] = (struct scripted_read){32, 0, 1000};
    scripted_prearm_case(wch, "64 nonempty reads reject", bound, ARRAY_SIZE(bound), SR_ERR_IO, 64);
    bound[62].count = bound[63].count = 0;
    scripted_prearm_case(wch, "criterion at read bound", bound, ARRAY_SIZE(bound), SR_OK, policy == 1 ? 63 : 64);
}
'''
fixture = fixture.replace("int main(void)\n{", extra + "\nint main(void)\n{", 1)
fixture = fixture.replace("        test_normal_capture(wch);", "        test_prearm_scripted(wch);\n        test_normal_capture(wch);", 1)
(tests / "test_dla32_drain_ab_unit.c").write_text(fixture, encoding="utf-8", newline="\n")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

changed = []
for path in sorted(destination.rglob("*")):
    if path.is_file() and sha(path) != sha(original / path.relative_to(destination)):
        changed.append(str(path.relative_to(destination)).replace("\\", "/"))
assert changed == [relative], changed
manifest = {
    "source_original": str(original),
    "source_isolated": str(destination),
    "source_file_count": sum(p.is_file() for p in original.rglob("*")),
    "changed_files": changed,
    "original_api_sha256": sha(original / relative),
    "isolated_api_sha256": sha(driver),
    "patch_sha256": sha(destination.parent / "prearm-drain-ab.patch"),
    "fixture_sha256": sha(tests / "test_dla32_drain_ab_unit.c"),
    "existing_runtime_dll_sha256": {
        "DLA32": sha(root / "artifacts/pulseview-dla32-reliability/libsigrok-4.dll"),
        "Kingst": sha(root / "artifacts/pulseview-la1010-reference/libsigrok-4.dll"),
        "diagnostic": sha(root / "artifacts/pulseview-dla32-wiretrace/libsigrok-4.dll"),
    },
}
(destination.parent / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2))
