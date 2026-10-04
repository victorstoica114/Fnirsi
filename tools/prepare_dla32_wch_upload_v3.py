"""Isolate SDK disable-before-STOP ordering and retained deferred-STOP ownership."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent.parent
original = root / "artifacts/dla32-wch-upload-v2-source/libsigrok"
directory = root / "artifacts/dla32-wch-upload-v3-source"
source = directory / "libsigrok"
if directory.exists():
    raise SystemExit(f"Refusing to overwrite {directory}")
shutil.copytree(original, source)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

provenance = {
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "original_source": str(original), "isolated_source": str(source),
    "original_source_sha256": {p.relative_to(original).as_posix(): sha(p)
                               for p in sorted(original.rglob("*")) if p.is_file()},
    "protected_dll_sha256": {name: sha(root / name) for name in [
        "artifacts/pulseview-dla32-reliability/libsigrok-4.dll",
        "artifacts/pulseview-la1010-reference/libsigrok-4.dll",
        "artifacts/pulseview-dla32-wiretrace/libsigrok-4.dll",
        "artifacts/dla32-drain-ab-baseline/libsigrok-4.dll",
        "artifacts/dla32-drain-ab-two-empty/libsigrok-4.dll",
        "artifacts/dla32-wch-upload/libsigrok-4.dll",
        "artifacts/dla32-wch-upload-v2/libsigrok-4.dll",
    ]},
    "kernel_sys_sha256": sha(Path(r"C:\Windows\System32\drivers\CH375W64.SYS")),
    "hardware_accessed": False,
}
(directory / "original-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
api = source / "src/hardware/fnirsi-dla32/api.c"
before = api.read_text()
after = before

def replace(old, new):
    global after
    assert after.count(old) == 1, old
    after = after.replace(old, new, 1)

replace("\tgboolean use_wch;\n", "\tgboolean use_wch;\n#if DLA_WCH_UPLOAD_DIAGNOSTIC\n\t/* Survives application END until capture STOP succeeds. */\n\tgboolean upload_stop_pending;\n#endif\n")
replace("|| (devc->use_wch && devc->wch.upload_dirty)", "|| (devc->use_wch && (devc->wch.upload_dirty || devc->upload_stop_pending))")
helper = '''#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
static void drain_stopped_endpoint(const struct sr_dev_inst *sdi);

static int stop_wch_capture(const struct sr_dev_inst *sdi, const char *phase)
{
	struct dev_context *devc = sdi->priv;
	uint8_t packet[DLA_COMMAND_SIZE];
	int ret;

	/* An attempted disable or STOP may leave device capture state unknown.
	 * This ownership is separate from SDK queue and application resources. */
	devc->upload_stop_pending = TRUE;
	ret = dla_wch_upload_retire(&devc->wch, phase);
	if (ret != SR_OK) {
		sr_err("WCH UPLOAD capture STOP deferred: phase %s; SDK retirement failed.", phase);
		return ret;
	}
	dla_stop(packet);
	ret = send_command(sdi, packet);
	if (ret != SR_OK) {
		sr_err("WCH UPLOAD capture STOP failed: phase %s; pending ownership retained.", phase);
		return ret;
	}
	devc->upload_stop_pending = FALSE;
	sr_info("WCH UPLOAD capture STOP complete: phase %s; disable precedes STOP.", phase);
	return SR_OK;
}
#endif

'''
replace("static int send_pwm_config(", helper + "static int send_pwm_config(")
old = '''		if (devc->wch.opened || devc->wch.upload_dirty) {
			int ret = dla_wch_upload_retire(&devc->wch, "close");
			if (ret != SR_OK) {
				sr_err("WCH UPLOAD close refused: SDK queue retirement failed.");
				sdi->status = SR_ST_ACTIVE;
				return ret;
			}
		}'''
new = '''		if (devc->wch.opened || devc->wch.upload_dirty || devc->upload_stop_pending) {
			gboolean pending = devc->upload_stop_pending;
			int ret = pending ? stop_wch_capture(sdi, "close") :
				dla_wch_upload_retire(&devc->wch, "close");
			if (ret != SR_OK) {
				sr_err("WCH UPLOAD close refused: SDK or pending capture STOP retirement failed.");
				sdi->status = SR_ST_ACTIVE;
				return ret;
			}
			if (pending)
				drain_stopped_endpoint(sdi);
		}'''
replace(old, new)
replace('if (devc->use_wch && devc->wch.upload_dirty) {\n\t\tsr_err("WCH UPLOAD post-stop drain skipped: SDK queue state remains dirty.");',
        'if (devc->use_wch && (devc->wch.upload_dirty || devc->upload_stop_pending)) {\n\t\tsr_err("WCH UPLOAD post-stop drain skipped: SDK queue dirty or capture STOP pending.");')
old = '''	cancel_transfers(devc);
	dla_stop(packet);
	devc->stop_sent = TRUE;
	if (send_command(sdi, packet) != SR_OK)
		sr_err("Failed to send the acquisition stop command.");
#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	if (devc->use_wch && dla_wch_upload_retire(&devc->wch, "stop") != SR_OK)
		sr_err("WCH UPLOAD stop retirement failed; SDK dirty ownership retained.");
#endif'''
new = '''	cancel_transfers(devc);
#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	if (devc->use_wch) {
		devc->stop_sent = stop_wch_capture(sdi, "stop") == SR_OK;
		return;
	}
#endif
	dla_stop(packet);
	devc->stop_sent = TRUE;
	if (send_command(sdi, packet) != SR_OK)
		sr_err("Failed to send the acquisition stop command.");'''
replace(old, new)
begin = after.index("\t/* Stop and drain stale data before arming the new asynchronous queue. */")
end = after.index("\tdrain_deadline =", begin)
after = after[:begin] + '''	/* Retire SDK read ownership before writing capture STOP; the observed
	 * active-upload STOP failure is not assumed to be a universal SDK rule. */
#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	if (devc->use_wch) {
		if (stop_wch_capture(sdi, "pre-arm") != SR_OK) {
			/* Startup still fails if its one explicit cleanup retry succeeds. */
			if (stop_wch_capture(sdi, "failed-start") != SR_OK)
				sr_err("WCH UPLOAD failed-start retains SDK or pending STOP ownership.");
			else
				drain_stopped_endpoint(sdi);
			free_prepared_transfers(devc);
			return SR_ERR_IO;
		}
	} else
#endif
	{
		dla_stop(packet);
		if (send_command(sdi, packet) != SR_OK) {
			free_prepared_transfers(devc);
			return SR_ERR_IO;
		}
	}
''' + after[end:]
old = '''	else if (devc->use_wch && devc->wch.upload_dirty)
		return SR_ERR_IO;'''
new = '''	else if (devc->use_wch && (devc->wch.upload_dirty || devc->upload_stop_pending)) {
		int ret = stop_wch_capture(sdi, "inactive-stop");
		if (ret == SR_OK)
			drain_stopped_endpoint(sdi);
		return ret;
	}'''
replace(old, new)
api.write_text(after, encoding="utf-8", newline="\n")
relative = api.relative_to(source).as_posix()
patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  fromfile="a/" + relative, tofile="b/" + relative))
(directory / "wch-upload-v3-diagnostic.patch").write_text(patch, encoding="utf-8")
hashes = {p.relative_to(source).as_posix(): sha(p) for p in sorted(source.rglob("*")) if p.is_file()}
changed = [name for name in hashes if hashes[name] != provenance["original_source_sha256"][name]]
assert changed == [relative]
(directory / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
record = {
    "variant": "wch-upload-v3", "created_utc": datetime.now(timezone.utc).isoformat(),
    "source_directory": str(source), "changed_files_vs_v2": changed,
    "source_sha256": {name: hashes[name] for name in [relative, "src/hardware/fnirsi-dla32/transport-wch.h"]},
    "source_patch_sha256": sha(directory / "wch-upload-v3-diagnostic.patch"),
    "prearm_empty_reads": 1, "upload_diagnostic_enabled": True,
    "read_size_bytes": 1048576, "sample_read_timeout_ms": 1000, "drain_read_timeout_ms": 20,
    "sample_read_timeout_ms_is_request_argument": True, "drain_read_timeout_ms_is_request_argument": True,
    "timeout_policy_unchanged": True, "upload_pipe": 1, "upload_length_bytes": 1048576,
    "start_order": "disable -> STOP -> baseline drain -> SETUP/source/HEADER -> enable -> enabled Clear -> ARM",
    "retire_order": "successful disable -> successful STOP -> bounded post-stop drain",
    "failed_disable_stop_suppressed": True, "pending_stop_survives_application_end": True,
    "upload_clear_erased_byte_count": None, "upload_retirement_erased_byte_count": None,
    "alignment_fix_established": False, "transport_header_unchanged_vs_v2": True,
    "protocol_unchanged": True, "hardware_accessed": False,
    "protected_dll_sha256": provenance["protected_dll_sha256"],
}
(directory / "source-manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
