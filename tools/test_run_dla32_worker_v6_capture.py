"""Bounded offline settings/file/log mutations; no preflight or device calls."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("capture_runner", Path(__file__).with_name("run_dla32_worker_v6_capture.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CaptureTests(unittest.TestCase):
    def test_physical_formats_and_limits(self):
        for channels, mode, unit, frame in (("D0", "Buffer", 1, 1), ("D0,D31", "Buffer", 4, 8),
            ("D0,D31", "Stream", 4, 2), ("D0,D1", "Buffer", 1, 8), ("all", "Buffer", 4, 32), ("all", "Stream", 4, 32)):
            with self.subTest(channels=channels, mode=mode):
                c = runner.settings(mode, 50000000, 5000000, channels, "1.6", "rising")
                self.assertEqual((c["unitsize"], c["wire_frame_bytes"]), (unit, frame))
        self.assertEqual(runner.settings("Buffer", 1000000000, 5000000, "D0", "0.6", "falling")["physical_channel_mask"], "00000001")
        self.assertEqual(runner.settings("Buffer", 250000000, 5000000, "all", "2.5", "none")["trigger_position_raw"], 0)

    def test_unsafe_or_impossible_settings_reject(self):
        for changes in ({"channels":"D32"}, {"channels":"D0,D0"}, {"channels":"D31"}, {"channels":"D0;bad"},
            {"samples":True}, {"samples":0}, {"samples":100000001}, {"rate":1000000000},
            {"mode":"Stream", "rate":100000000}, {"threshold":"1.6001"}, {"trigger":"arbitrary"}):
            d = dict(mode="Buffer", rate=250000000, samples=5000000, channels="all", threshold="1.6", trigger="rising")
            d.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): runner.settings(**d)

    def result(self, mutation=None, channels="all", mode="Stream"):
        c = runner.settings(mode, 50000000, 8, channels, "1.6", "rising")
        frame, unit = c["wire_frame_bytes"], c["unitsize"]
        log = "\n".join([
            f"fnirsi-dla32: WIRE trace enabled: 'capture.wire.bin', frame {frame} bytes, mask {c['physical_channel_mask']}, {mode}, 50000000 Hz.",
            "fnirsi-dla32: WIRE command: opcode 15, elapsed_us 1, prefix54 " + "00"*54 + ".",
            "fnirsi-dla32: WCH capture STOP complete: phase pre-arm; command accepted.",
            "fnirsi-dla32: WIRE command: opcode 11, elapsed_us 2, prefix54 " + "00"*54 + ".",
            "session: bus: Received SR_DF_HEADER packet.",
            "fnirsi-dla32: WIRE command: opcode 12, elapsed_us 3, prefix54 " + "00"*54 + ".",
            f"session: bus: Received SR_DF_LOGIC packet ({8*unit} bytes, unitsize = {unit}).",
            "fnirsi-dla32: WCH WORKER command: generation 1, opcode 15, io_start_us 5, prefix54 " + "00"*54 + ".",
            "fnirsi-dla32: WCH capture STOP complete: phase stop; command accepted.",
            "fnirsi-dla32: WCH WORKER drain read: generation 1, io_start_us 6, duration_us 1, status 0, requested 1048576, timeout_ms 20, count 0.",
            "fnirsi-dla32: WCH WORKER drain read: generation 1, io_start_us 7, duration_us 1, status 0, requested 1048576, timeout_ms 20, count 0.",
            "fnirsi-dla32: WCH WORKER terminal: generation 1, reads 1, stop_status 0, pending 0, stop_start_us 5, stop_duration_us 1, drain_bytes 0, drain_reads 2, empty_reads 2, quiet 1, diagnostics_failed 0, unconsumed_bytes 0; native thread exited.",
            "fnirsi-dla32: WCH WORKER unconsumed trace closed: 0 bytes, failed 0; these bytes were not decoded.",
            f"fnirsi-dla32: WIRE trace closed: {frame} decoder-input bytes, tail 0, failed 0.",
            "session: bus: Received SR_DF_END packet.",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            output = {role:p/name for role,name in {"wire":"wire.bin", "logic":"logic.bin", "unconsumed":"unused.bin"}.items()}
            output["wire"].write_bytes(bytes(frame));output["logic"].write_bytes(bytes(unit*8));output["unconsumed"].write_bytes(b"")
            if mutation: log = mutation(log, output)
            return runner.verify(c, output, log.encode(), lambda path:hashlib.sha256(path.read_bytes()).hexdigest())

    def test_clean_all32_and_sparse_formats(self):
        for channels,mode in (("all","Stream"),("all","Buffer"),("D0","Buffer"),("D0,D31","Buffer"),("D0,D31","Stream")):
            with self.subTest(channels=channels,mode=mode): self.assertTrue(self.result(channels=channels,mode=mode)["passed"])
    def test_failed_stop_even_with_count(self): self.assertFalse(self.result(lambda s,p:s.replace("stop_status 0","stop_status -1"))["passed"])
    def test_pending_stop(self): self.assertFalse(self.result(lambda s,p:s.replace("pending 0, stop_start", "pending 1, stop_start"))["passed"])
    def test_missing_native_exit(self): self.assertFalse(self.result(lambda s,p:s.replace("native thread exited", "thread publication only"))["passed"])
    def test_missing_accepted_stop(self): self.assertFalse(self.result(lambda s,p:s.replace("phase stop; command accepted.","phase stop; attempt only."))["passed"])
    def test_drain_after_end(self):
        def mutate(s,p):
            lines=s.splitlines();drains=[line for line in lines if "drain read:" in line]
            return "\n".join([line for line in lines if "drain read:" not in line]+drains)
        self.assertFalse(self.result(mutate)["passed"])
    def test_count_truncated(self):
        def mutate(s,p):p["logic"].write_bytes(b"x");return s
        self.assertFalse(self.result(mutate)["passed"])
    def test_wrong_driver_unitsize(self): self.assertFalse(self.result(lambda s,p:s.replace("unitsize = 4","unitsize = 1"))["passed"])
    def test_missing_sidecar(self):
        def mutate(s,p):p["unconsumed"].unlink();return s
        self.assertFalse(self.result(mutate)["passed"])
    def test_hidden_driver_error(self): self.assertFalse(self.result(lambda s,p:s+"\nfnirsi-dla32: WCH sample stream failed: LIBUSB_ERROR_IO.")["passed"])
    def test_raw_tail_preserved(self):
        def mutate(s,p):p["wire"].write_bytes(bytes(33));return s.replace("32 decoder-input bytes, tail 0","33 decoder-input bytes, tail 1")
        r=self.result(mutate);self.assertTrue(r["passed"]);self.assertEqual(r["files"]["wire"]["bytes"],33)
    def test_command_is_read_only_configuration(self):
        class G:PACKAGE=Path("package")
        c=runner.settings("Buffer",1000000000,8,"D0","0.6","falling")
        cmd=runner.command(G,c,Path("out.bin"))
        self.assertIn("data_source=Buffer:samplerate=1000000000:voltage_threshold=0.6-0.6",cmd)
        self.assertEqual(cmd[-2:],["--triggers","D0=f"])
        self.assertNotIn("--wait-trigger",cmd)
    def test_read_only_etw_gate(self):
        with patch.object(runner.subprocess,"run",return_value=SimpleNamespace(stdout="Unrelated Session Running",stderr="",returncode=0)) as query:
            self.assertFalse(runner.no_active_dla_etw()["active_DLA32_ETW_session"])
            self.assertEqual(query.call_args.args[0],["logman.exe","query","-ets"])
        with patch.object(runner.subprocess,"run",return_value=SimpleNamespace(stdout="DLA32_ETW_old_block Running",stderr="",returncode=0)):
            with self.assertRaises(RuntimeError):runner.no_active_dla_etw()
    def test_etw_query_error_fails_closed(self):
        with patch.object(runner.subprocess,"run",side_effect=runner.subprocess.TimeoutExpired("logman",10)):
            with self.assertRaises(runner.subprocess.TimeoutExpired):runner.no_active_dla_etw()
    def test_post_capture_etw_failure_retains_failure_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reports=[]
            output={"directory":root/"out", "start":root/"start.json", "record":root/"record.json",
                "stdout":root/"stdout.log", "stderr":root/"stderr.log", "logic":root/"out/logic.bin", "wire":root/"out/wire.bin"}
            result={"passed":True,"kill_attempted":False,"exit_code":0,"native_exit_code":0,"elapsed_seconds":0.1}
            g=SimpleNamespace(PACKAGE=root,EXPECTED_DLL="a"*64,sha=lambda p:"b"*64,
                write_json=lambda p,r:reports.append(dict(r)), bounded_process=lambda *a:(result,b"stdout marker",b"stderr marker"))
            config=runner.settings("Stream",50000000,8,"all","1.6","rising")
            with patch.object(runner,"BASE",root),patch.object(runner.shutil,"disk_usage",return_value=SimpleNamespace(free=10**12)), \
                 patch.object(runner,"verify",return_value={"passed":True}), \
                 patch.object(runner,"no_active_dla_etw",side_effect=RuntimeError("DLA32_ETW_late active")):
                self.assertEqual(runner.execute(g,config,output,{}, {"active_DLA32_ETW_session":False}),1)
            self.assertFalse(reports[-1]["harness_process_passed"])
            self.assertFalse(reports[-1]["ETW_postflight"]["passed"])
            self.assertEqual(output["stderr"].read_bytes(),b"stderr marker")


if __name__ == "__main__":unittest.main(verbosity=2)
