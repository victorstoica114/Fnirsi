"""Promote Windows WCH pending STOP ownership to SDK0, preserving SDK1."""
from pathlib import Path
from datetime import datetime, timezone
import difflib
import hashlib
import json
import shutil

root = Path(__file__).resolve().parent.parent
original = root / "artifacts/dla32-wch-upload-v4-source/libsigrok"
directory = root / "artifacts/dla32-wch-stop-v5-source"
source = directory / "libsigrok"
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def tree(path): return {p.relative_to(path).as_posix():sha(p) for p in sorted(path.rglob("*")) if p.is_file()}
if directory.exists():
    assert tree(source) == tree(original) and not (directory/"source-manifest.json").exists(), "Refuse altered/frozen source"
else:
    shutil.copytree(original,source)
previous = json.loads((root / "artifacts/dla32-wch-upload-v4-source/original-provenance.json").read_text())
protected = dict(previous["protected_dll_sha256"])
protected["artifacts/dla32-wch-upload-v4/libsigrok-4.dll"] = sha(root / "artifacts/dla32-wch-upload-v4/libsigrok-4.dll")
provenance = {"created_utc":datetime.now(timezone.utc).isoformat(),"original_source":str(original),
    "isolated_source":str(source),"original_source_sha256":tree(original),"protected_dll_sha256":protected,
    "kernel_sys_sha256":previous["kernel_sys_sha256"],"hardware_accessed":False,
    "frozen_hardware_harness_sha256":sha(root / "artifacts/test_dla32_drain_ab.exe")}
api = source / "src/hardware/fnirsi-dla32/api.c"
before = api.read_text(); after=before
def replace(old,new):
    global after
    assert after.count(old) == 1, old
    after=after.replace(old,new,1)
replace('''#if DLA_WCH_UPLOAD_DIAGNOSTIC
	/* Survives application END until capture STOP succeeds. */
	gboolean upload_stop_pending;
#endif''', '''	/* Hardware STOP ownership is independent of the optional SDK queue.
	 * Keep the existing internal name; survives END in both SDK0 and SDK1. */
	gboolean upload_stop_pending;''')
old='''static gboolean has_acquisition_resources(const struct dev_context *devc)
{
	return has_application_resources(devc)
#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
		|| (devc->use_wch && (devc->wch.upload_dirty || devc->upload_stop_pending))
#endif
		;
}'''
new='''#ifdef _WIN32
static gboolean has_wch_stop_ownership(const struct dev_context *devc)
{
	return devc->upload_stop_pending
#if DLA_WCH_UPLOAD_DIAGNOSTIC
		|| devc->wch.upload_dirty
#endif
		;
}
#endif

static gboolean has_acquisition_resources(const struct dev_context *devc)
{
	return has_application_resources(devc)
#ifdef _WIN32
		|| (devc->use_wch && has_wch_stop_ownership(devc))
#endif
		;
}'''
replace(old,new)
replace('#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC\nstatic void drain_stopped_endpoint', '#ifdef _WIN32\nstatic void drain_stopped_endpoint')
replace('''	devc->upload_stop_pending = TRUE;
	ret = dla_wch_upload_retire(&devc->wch, phase);''', '''	devc->upload_stop_pending = TRUE;
#if DLA_WCH_UPLOAD_DIAGNOSTIC
	ret = dla_wch_upload_retire(&devc->wch, phase);''')
replace('''		sr_err("WCH UPLOAD capture STOP deferred: phase %s; SDK retirement failed.", phase);
		return ret;
	}
	dla_stop(packet);
	ret = send_command(sdi, packet);''', '''		sr_err("WCH UPLOAD capture STOP deferred: phase %s; SDK retirement failed.", phase);
		return ret;
	}
#endif

	dla_stop(packet);
	ret = send_command(sdi, packet);''')
replace('''		sr_err("WCH UPLOAD capture STOP failed: phase %s; pending ownership retained.", phase);''', '''#if DLA_WCH_UPLOAD_DIAGNOSTIC
		sr_err("WCH UPLOAD capture STOP failed: phase %s; pending ownership retained.", phase);
#else
		sr_err("WCH capture STOP failed: phase %s; pending ownership retained.", phase);
#endif''')
replace('''	sr_info("WCH UPLOAD capture STOP complete: phase %s; disable precedes STOP.", phase);''', '''#if DLA_WCH_UPLOAD_DIAGNOSTIC
	sr_info("WCH UPLOAD capture STOP complete: phase %s; disable precedes STOP.", phase);
#else
	sr_info("WCH capture STOP complete: phase %s; command accepted.", phase);
#endif''')
replace('''			if (pending)
				drain_stopped_endpoint(sdi);
		}
#endif
		dla_wch_close(&devc->wch);''', '''			if (pending)
				drain_stopped_endpoint(sdi);
		}
#else
		if (devc->upload_stop_pending) {
			int ret = stop_wch_capture(sdi, "close");
			if (ret != SR_OK) {
				sr_err("WCH close refused: pending capture STOP retirement failed.");
				sdi->status = SR_ST_ACTIVE;
				return ret;
			}
			drain_stopped_endpoint(sdi);
		}
#endif
		dla_wch_close(&devc->wch);''')
replace('''	if (devc->acquiring)
		return SR_ERR;
	if (cg) {''', '''	if (devc->acquiring
#ifdef _WIN32
			|| (devc->use_wch && has_wch_stop_ownership(devc))
#endif
			)
		return SR_ERR;
	if (cg) {''')
replace('''#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	if (devc->use_wch && (devc->wch.upload_dirty || devc->upload_stop_pending)) {
		sr_err("WCH UPLOAD post-stop drain skipped: SDK queue dirty or capture STOP pending.");''', '''#ifdef _WIN32
	if (devc->use_wch && has_wch_stop_ownership(devc)) {
#if DLA_WCH_UPLOAD_DIAGNOSTIC
		sr_err("WCH UPLOAD post-stop drain skipped: SDK queue dirty or capture STOP pending.");
#else
		sr_err("WCH post-stop drain skipped: capture STOP pending.");
#endif''')
replace('''#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	if (devc->use_wch) {
		devc->stop_sent = stop_wch_capture''', '''#ifdef _WIN32
	if (devc->use_wch) {
		devc->stop_sent = stop_wch_capture''')
replace('''#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	if (devc->use_wch) {
		if (stop_wch_capture(sdi, "pre-arm")''', '''#ifdef _WIN32
	if (devc->use_wch) {
		if (stop_wch_capture(sdi, "pre-arm")''')
replace('''			if (stop_wch_capture(sdi, "failed-start") != SR_OK)
				sr_err("WCH UPLOAD failed-start retains SDK or pending STOP ownership.");''', '''			if (stop_wch_capture(sdi, "failed-start") != SR_OK) {
#if DLA_WCH_UPLOAD_DIAGNOSTIC
				sr_err("WCH UPLOAD failed-start retains SDK or pending STOP ownership.");
#else
				sr_err("WCH failed-start retains pending STOP ownership.");
#endif
			}''')
replace('''#if defined(_WIN32) && DLA_WCH_UPLOAD_DIAGNOSTIC
	else if (devc->use_wch && (devc->wch.upload_dirty || devc->upload_stop_pending)) {''', '''#ifdef _WIN32
	else if (devc->use_wch && has_wch_stop_ownership(devc)) {''')
api.write_text(after,encoding="utf-8",newline="\n")
relative=api.relative_to(source).as_posix()
(directory/"wch-stop-v5-diagnostic.patch").write_text("".join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile="a/"+relative,tofile="b/"+relative)),encoding="utf-8")
hashes=tree(source)
changed=[name for name in hashes if hashes[name]!=provenance["original_source_sha256"][name]]
assert changed==[relative]
record={"variant":"wch-stop-v5","created_utc":datetime.now(timezone.utc).isoformat(),"source_directory":str(source),
    "changed_files_vs_v4":changed,"source_sha256":{name:hashes[name] for name in [relative,"src/hardware/fnirsi-dla32/transport-wch.h","src/hardware/fnirsi-dla32/protocol.h"]},
    "source_patch_sha256":sha(directory/"wch-stop-v5-diagnostic.patch"),"transport_header_unchanged_vs_v4":True,
    "protocol_decoder_unchanged_vs_v4":True,"sdk_upload_default":0,"sdk0_pending_stop_retention":True,
    "sdk0_configuration_and_pwm_blocked_while_pending":True,"sdk1_configuration_and_pwm_blocked_while_dirty_or_pending":True,
    "sdk1_success_markers_and_disable_before_stop_preserved":True,"libusb_stop_cancel_callback_behavior_unchanged":True,
    "pending_hardware_stop_survives_application_end":True,"read_size_bytes":1048576,"prearm_empty_reads":1,
    "sample_read_timeout_ms":1000,"drain_read_timeout_ms":20,"command_write_timeout_ms":1000,
    "timeout_policy_unchanged_vs_v4":True,"decoder_or_lane_correction":False,"alignment_fix_established":False,"hardware_accessed":False,
    "protected_dll_sha256":protected}
for name,value in [("original-provenance.json",provenance),("source-sha256.json",hashes),("source-manifest.json",record)]:
    (directory/name).write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8")
print(json.dumps(record,indent=2))
