"""Synthetic offline invariants; imports only the new audit module."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import tempfile
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("v6audit", Path(__file__).with_name("audit_dla32_wch_worker_v6.py"))
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
STOP = "00000000000000000a1502000be6fc24d700000000000000000000000000000000000000000000000000000000000000000000000000"
SETUP = "00000000000000000a11" + "00" * 44
ARM = "00000000000000000a12" + "00" * 44

def fixture(cancel=False):
    count = 32 if cancel else 64
    wire = b"" if cancel else bytes([0x55]) * count
    discarded = bytes([0x55]) * count if cancel else b""
    meta = dict(samplerate_hz=1000000 if cancel else 50000000, configured_sample_limit=128 if cancel else 16,
        unitsize=4, physical_channel_mask="ffffffff", headers=1, ends=1, stopped_callbacks=1,
        lifecycle_and_count_pass=True, worker_unconsumed_bytes_are_decoder_input=False,
        frame_origin_validated=False, trigger_behavior_validated=False, signal_integrity_verified=False,
        threshold_volts=1.6, trigger="D0 rising hardware request", raw_trigger_position=500,
        actual_samples=0 if cancel else 16, written_logic_bytes=0 if cancel else 64,
        wire_trace_bytes=len(wire), wire_trace_tail_mod32=len(wire) % 32,
        worker_unconsumed_bytes=len(discarded), worker_unconsumed_tail_mod32=len(discarded) % 32,
        capture_kind="programmatic-cancel" if cancel else "finite-recovery", stop_requested=cancel,
        stop_call_count=1 if cancel else 0, cancel_timer_fired=1 if cancel else 0,
        cancel_timer_nominal_ms=100 if cancel else 0, stop_reason="cancel-timer" if cancel else "",
        samples_at_stop=0, timer_attached_us=100, timer_fired_us=100100,
        stop_begin_us=100100, stop_end_us=100120, stop_call_duration_us=20,
        actual_timer_delay_us=100000, HEADER_us=40, END_us=200000, stopped_callback_us=200010)
    for key in ("logic_after_stop", "context_mismatches", "run_status", "running_after_run", "destroy_status",
                "invalid_packet", "guard_expired", "logic_close_failed", "driver_error_log_count",
                "driver_log_write_failures", "driver_log_close_failed", "stop_status"): meta[key] = 0
    rows = [f"WIRE command: opcode 15, elapsed_us 0, prefix54 {STOP}.",
        "WCH capture STOP complete: phase pre-arm; command accepted.",
        f"WIRE command: opcode 11, elapsed_us 2, prefix54 {SETUP}.",
        "Received SR_DF_HEADER packet.", f"WIRE command: opcode 12, elapsed_us 4, prefix54 {ARM}."]
    read = f"WCH WORKER read: generation 1, read_id 1, io_start_us {90000 if cancel else 1000}, duration_us {11000 if cancel else 100}, status 0, requested 1048576, timeout_ms 1000, count {count}, Windows error 0, payload_offset 0, prefix32 {'55' * 32}."
    stop_start = 101100 if cancel else 1200
    retirement = [f"WCH WORKER command: generation 1, opcode 15, io_start_us {stop_start}, prefix54 {STOP}.",
        "WCH capture STOP complete: phase stop; command accepted.",
        f"WCH WORKER drain read: generation 1, io_start_us {stop_start + 20}, duration_us 20, status 0, requested 1048576, timeout_ms 20, count 0.",
        f"WCH WORKER drain read: generation 1, io_start_us {stop_start + 40}, duration_us 20, status 0, requested 1048576, timeout_ms 20, count 0."]
    if cancel:
        rows += retirement + [read, f"WCH WORKER unconsumed read: generation 1, read_id 1, offset 0, count 32, prefix32 {'55' * 32}; stop requested, no LOGIC."]
    else:
        rows += [read, f"WIRE payload: offset 0, count 64, offset_mod 0, count_mod 0, pending 0, elapsed_us 9, prefix32 {'55' * 32}."] + retirement
    rows += [f"WCH WORKER terminal: generation 1, reads 1, stop_status 0, pending 0, stop_start_us {stop_start}, stop_duration_us 20, drain_bytes 0, drain_reads 2, empty_reads 2, quiet 1, diagnostics_failed 0, unconsumed_bytes {len(discarded)}; native thread exited.",
        f"WCH WORKER unconsumed trace closed: {len(discarded)} bytes, failed 0; these bytes were not decoded.",
        f"WIRE trace closed: {len(wire)} decoder-input bytes, tail 0, failed 0.", "Received SR_DF_END packet."]
    return meta, "\n".join("time_us=200000 level=4 " + row for row in rows), wire, wire, discarded

class Tests(unittest.TestCase):
    def run_case(self, values):
        m = values[0]
        return audit.stage(*values, 1000000 if m["capture_kind"] == "programmatic-cancel" else 50000000,
                           128 if m["capture_kind"] == "programmatic-cancel" else 16)
    def assert_fail(self, values): self.assertFalse(self.run_case(values)["pass"])
    def test_finite_complete(self): self.assertTrue(self.run_case(fixture())["pass"])
    def test_cancel_zero_with_late_positive_and_deferred_stop_logs(self): self.assertTrue(self.run_case(fixture(True))["pass"])
    def test_wrong_timeout(self):
        x=list(fixture()); x[1]=x[1].replace("timeout_ms 1000", "timeout_ms 20"); self.assert_fail(x)
    def test_busy_is_not_timeout(self):
        x=list(fixture()); x[1]=x[1].replace("status 0, requested 1048576, timeout_ms 1000", "status -6, requested 1048576, timeout_ms 1000"); self.assert_fail(x)
    def test_partial_timeout_status_is_accepted(self):
        x=list(fixture()); x[1]=x[1].replace("status 0, requested 1048576, timeout_ms 1000", "status -7, requested 1048576, timeout_ms 1000"); self.assertTrue(self.run_case(x)["pass"])
    def test_missing_read(self):
        x=list(fixture()); x[1]="\n".join(y for y in x[1].splitlines() if "WCH WORKER read:" not in y); self.assert_fail(x)
    def test_duplicate_read(self):
        x=list(fixture()); x[1]+="\n"+next(y for y in x[1].splitlines() if "WCH WORKER read:" in y); self.assert_fail(x)
    def test_stop_before_read_completed(self):
        x=list(fixture()); x[1]=x[1].replace("duration_us 100, status", "duration_us 1000, status"); self.assert_fail(x)
    def test_missing_stop_complete(self):
        x=list(fixture()); x[1]=x[1].replace("phase stop; command accepted.", "phase unknown; command accepted."); self.assert_fail(x)
    def test_pending_terminal(self):
        x=list(fixture()); x[1]=x[1].replace("pending 0, stop_start_us", "pending 1, stop_start_us"); self.assert_fail(x)
    def test_failed_terminal(self):
        x=list(fixture()); x[1]=x[1].replace("stop_status 0", "stop_status -6"); self.assert_fail(x)
    def test_diagnostic_truncation(self):
        x=list(fixture()); x[1]=x[1].replace("diagnostics_failed 0", "diagnostics_failed 1"); self.assert_fail(x)
    def test_one_empty_drain(self):
        x=list(fixture()); x[1]="\n".join(y for y in x[1].splitlines() if "io_start_us 1240" not in y); self.assert_fail(x)
    def test_positive_between_empty_polls(self):
        x=list(fixture()); x[1]=x[1].replace("io_start_us 1240, duration_us 20, status 0, requested 1048576, timeout_ms 20, count 0", "io_start_us 1240, duration_us 20, status 0, requested 1048576, timeout_ms 20, count 32"); self.assert_fail(x)
    def test_sidecar_truncated(self):
        x=list(fixture(True)); x[4]=x[4][:-1]; self.assert_fail(x)
    def test_sidecar_prefix_corrupted(self):
        x=list(fixture(True)); x[4]=b"\x00"+x[4][1:]; self.assert_fail(x)
    def test_payload_before_own_read(self):
        x=list(fixture()); lines=x[1].splitlines(); a=next(i for i,y in enumerate(lines) if "WCH WORKER read:" in y); lines[a],lines[a+1]=lines[a+1],lines[a]; x[1]="\n".join(lines); self.assert_fail(x)
    def test_unconsumed_not_decoder_input(self):
        x=list(fixture(True)); x[0]["worker_unconsumed_bytes_are_decoder_input"]=True; self.assert_fail(x)
    def test_error_even_when_metadata_clean(self):
        x=list(fixture()); x[1]+="\ntime_us=300000 level=1 hidden transport failure"; self.assert_fail(x)
    def test_boolean_integer_not_coerced(self):
        x=list(fixture()); x[0]["ends"]=True; self.assert_fail(x)
    def test_late_logic_metadata(self):
        x=list(fixture(True)); x[0]["logic_after_stop"]=1; self.assert_fail(x)
    def test_cancel_count_not_full_limit(self):
        x=list(fixture(True)); x[0]["actual_samples"]=128; self.assert_fail(x)
    def test_missing_native_exit_marker(self):
        x=list(fixture()); x[1]=x[1].replace("native thread exited.", "terminal published."); self.assert_fail(x)
    def test_duplicate_end(self):
        x=list(fixture()); x[1]+="\nReceived SR_DF_END packet."; self.assert_fail(x)
    def test_wrong_saved_prefix(self):
        x=list(fixture()); x[2]=b"\x00"+x[2][1:]; self.assert_fail(x)
    def test_unmapped_tail(self):
        x=list(fixture()); x[2]+=b"\x55"; self.assert_fail(x)
    def test_unknown_json_duplicate_keys(self):
        with self.assertRaises(ValueError): audit.read_json(b'{"a":1,"a":2}')
    def test_path_traversal(self):
        with self.assertRaises(ValueError): audit.valid_name("../wire.bin")
    def test_terminal_acceptance_before_attempt(self):
        x=list(fixture()); rows=x[1].splitlines(); marker=next(y for y in rows if "phase stop; command accepted" in y); rows.remove(marker); rows.insert(0,marker); x[1]="\n".join(rows); self.assert_fail(x)
    def test_duplicate_stop_acceptance(self):
        x=list(fixture()); x[1]+="\n"+next(y for y in x[1].splitlines() if "phase stop; command accepted" in y); self.assert_fail(x)
    def test_prearm_acceptance_after_setup(self):
        x=list(fixture()); rows=x[1].splitlines(); marker=rows.pop(1); rows.insert(3,marker); x[1]="\n".join(rows); self.assert_fail(x)
    def test_drain_logs_after_end(self):
        x=list(fixture()); rows=x[1].splitlines(); drains=[y for y in rows if "WCH WORKER drain read:" in y]; rows=[y for y in rows if y not in drains]; x[1]="\n".join(rows+drains); self.assert_fail(x)
    def test_read_result_after_terminal(self):
        x=list(fixture(True)); rows=x[1].splitlines(); read=next(y for y in rows if "WCH WORKER read:" in y); rows.remove(read); x[1]="\n".join(rows+[read]); self.assert_fail(x)

class OuterTests(unittest.TestCase):
    """Real temp files; simulated successful stage audits isolate outer gates."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v6-audit-offline-")
        self.root=Path(self.temp.name); self.captures=self.root/"captures"; self.captures.mkdir()
        self.dll="a"*64
        self.seq=dict(harness="separate_v6_worker_cancel_recovery", expected_loaded_DLL_SHA256=self.dll,
            completed_capture_stages=3, expected_capture_stages=3, device_close_attempted=True,
            device_close_status=0, sr_exit_attempted=True, sr_exit_status=0, outer_lifecycle_and_count_pass=True)
        self.outer=dict(process_started=True,process_exited=True,native_exit_code=0,exit_code=0,timed_out=False,
            kill_attempted=False,kill_succeeded=False,output_drained=True,process_error=None,
            evidence_write_errors=[],protected_files_changed=[],harness_process_passed=True)
        self.outer_path=self.root/"outer.json"
        for idx,stem in enumerate(audit.STAGES):
            m=dict(capture=stem,mode="Stream" if idx==2 else "Buffer",driver_log_file=stem+".driver.log",
                wire_file=stem+".wire.bin",logic_file=stem+".logic.bin",worker_unconsumed_file=stem+".wire.bin.worker-unconsumed.bin")
            (self.captures/(stem+".json")).write_text(json.dumps(m))
            for key in ("driver_log_file","wire_file","logic_file","worker_unconsumed_file"):
                (self.captures/m[key]).write_bytes(b"")
        self.write()
    def tearDown(self): self.temp.cleanup()
    def write(self):
        (self.captures/"sequence-result.json").write_text(json.dumps(self.seq)); self.outer_path.write_text(json.dumps(self.outer))
    def result(self):
        with patch.object(audit,"stage",return_value={"pass":True,"issues":[],"details":{}}):
            return audit.audit(self.captures,self.outer_path,self.dll)
    def test_clean_outer_and_coverage(self): self.assertEqual(self.result()["verdict"],"PASS")
    def test_kill_even_with_native_zero(self):
        self.outer["kill_attempted"]=True;self.write();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_missing_drained_field(self):
        self.outer.pop("output_drained");self.write();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_process_error_despite_clean_exit(self):
        self.outer["process_error"]="injected communicate exception";self.write();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_close_failure(self):
        self.seq["device_close_status"]=-6;self.write();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_wrong_expected_dll(self):
        self.seq["expected_loaded_DLL_SHA256"]="b"*64;self.write();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_missing_whole_stage(self):
        (self.captures/(audit.STAGES[2]+".json")).unlink();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_missing_sidecar(self):
        (self.captures/(audit.STAGES[0]+".wire.bin.worker-unconsumed.bin")).unlink();self.assertEqual(self.result()["verdict"],"FAIL")
    def test_unmapped_extra_raw(self):
        (self.captures/"extra.wire.bin").write_bytes(b"x");self.assertEqual(self.result()["verdict"],"FAIL")
    def test_role_alias(self):
        p=self.captures/(audit.STAGES[1]+".json");m=json.loads(p.read_text());m["wire_file"]=audit.STAGES[0]+".wire.bin";p.write_text(json.dumps(m));self.assertEqual(self.result()["verdict"],"FAIL")
    def test_wrong_capture_mode(self):
        p=self.captures/(audit.STAGES[2]+".json");m=json.loads(p.read_text());m["mode"]="Buffer";p.write_text(json.dumps(m));self.assertEqual(self.result()["verdict"],"FAIL")
    def test_file_changed_during_audit(self):
        p=self.captures/(audit.STAGES[0]+".wire.bin")
        def mutate(*unused):
            p.write_bytes(b"changed");return {"pass":True,"issues":[],"details":{}}
        with patch.object(audit,"stage",side_effect=mutate): report=audit.audit(self.captures,self.outer_path,self.dll)
        self.assertEqual(report["verdict"],"FAIL")

if __name__ == "__main__": unittest.main(verbosity=2)
