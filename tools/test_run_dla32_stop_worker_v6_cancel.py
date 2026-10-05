"""Offline runner fixtures; synthetic children only, never analyzer hardware."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("v6_cancel_runner",
    ROOT / "tools/run_dla32_stop_worker_v6_cancel.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
FIXTURES = ROOT / "artifacts" / ("worker-v6-cancel-runner-fixtures-" + uuid.uuid4().hex)
FIXTURES.mkdir()


def make_capture(name):
    directory = FIXTURES / name
    directory.mkdir()
    seq = {"harness": "separate_v6_worker_cancel_recovery",
        "expected_loaded_DLL_SHA256": runner.EXPECTED_DLL, "completed_capture_stages": 3,
        "expected_capture_stages": 3, "device_close_attempted": True,
        "device_close_status": 0, "sr_exit_attempted": True, "sr_exit_status": 0,
        "outer_lifecycle_and_count_pass": True}
    (directory / "sequence-result.json").write_text(json.dumps(seq))
    for stem, kind, mode, limit, rate in (
        ("cancel001-Buffer", "programmatic-cancel", "Buffer", 10000000, 1000000),
        ("rep001-step1-Buffer", "finite-recovery", "Buffer", 5000000, 50000000),
        ("rep001-step2-Stream", "finite-recovery", "Stream", 5000000, 50000000)):
        n = 0 if kind == "programmatic-cancel" else limit
        stage = {"capture": stem, "capture_kind": kind, "mode": mode,
            "samplerate_hz": rate, "configured_sample_limit": limit,
            "threshold_volts": 1.6, "physical_channel_mask": "ffffffff",
            "raw_trigger_position": 500, "unitsize": 4,
            "lifecycle_and_count_pass": True, "headers": 1, "ends": 1,
            "stopped_callbacks": 1, "logic_after_stop": 0, "context_mismatches": 0,
            "run_status": 0, "running_after_run": 0, "stop_status": 0,
            "destroy_status": 0, "invalid_packet": 0, "guard_expired": 0,
            "logic_close_failed": 0,
            "driver_error_log_count": 0, "driver_log_write_failures": 0,
            "driver_log_close_failed": 0, "worker_unconsumed_bytes_are_decoder_input": False,
            "actual_samples": n, "samples_at_stop": n, "stop_requested": kind == "programmatic-cancel",
            "stop_call_count": int(kind == "programmatic-cancel"),
            "cancel_timer_fired": int(kind == "programmatic-cancel"),
            "stop_reason": "cancel-timer" if kind == "programmatic-cancel" else "none",
            "logic_file": stem + ".logic.bin", "wire_file": stem + ".wire.bin",
            "worker_unconsumed_file": stem + ".wire.bin.worker-unconsumed.bin",
            "wire_trace_bytes": n * 4, "worker_unconsumed_bytes": 0,
            "written_logic_bytes": n * 4, "wire_trace_tail_mod32": 0,
            "worker_unconsumed_tail_mod32": 0, "driver_log_file": stem + ".driver.log"}
        for key in ("logic_file", "wire_file", "worker_unconsumed_file"):
            # Sparse fixture files, without reconstructing or emulating hardware.
            with (directory / stage[key]).open("xb") as stream:
                stream.truncate(n * 4 if key != "worker_unconsumed_file" else 0)
        (directory / (stem + ".json")).write_text(json.dumps(stage))
        (directory / (stem + ".driver.log")).write_text("offline fixture log")
    return directory


def change(directory, filename, key, value):
    path = directory / filename
    data = json.loads(path.read_text())
    data[key] = value
    path.write_text(json.dumps(data))


class Offline(unittest.TestCase):
    def test_success_child_drains_both_large_pipes(self):
        code = "import sys; sys.stdout.buffer.write(b'A'*200000); sys.stderr.buffer.write(b'B'*200000)"
        r, out, err = runner.bounded_process([sys.executable, "-B", "-c", code], dict(os.environ), 5)
        self.assertTrue(r["passed"])
        self.assertEqual(out, b"A" * 200000)
        self.assertEqual(err, b"B" * 200000)
        self.assertFalse(r["kill_attempted"])

    def test_watchdog_kills_only_synthetic_child_and_preserves_both_pipes(self):
        code = "import time; print('stdout marker',flush=True); import sys; print('stderr marker',file=sys.stderr,flush=True); time.sleep(10)"
        r, out, err = runner.bounded_process([sys.executable, "-B", "-c", code], dict(os.environ), .5)
        self.assertEqual(r["exit_code"], 124)
        self.assertTrue(all(r[k] for k in ("timed_out", "kill_attempted", "kill_succeeded", "process_exited", "output_drained")))
        self.assertIn(b"stdout marker", out)
        self.assertIn(b"stderr marker", err)
        self.assertLess(r["elapsed_seconds"], 6)
        self.assertFalse(r["passed"])

    def test_native_failure_is_preserved(self):
        r, _, _ = runner.bounded_process([sys.executable, "-c", "raise SystemExit(7)"], dict(os.environ), 5)
        self.assertEqual(r["exit_code"], 7)
        self.assertEqual(r["native_exit_code"], 7)
        self.assertFalse(r["passed"])
        self.assertFalse(r["kill_attempted"])

    def test_launch_failure_is_not_success(self):
        r, _, _ = runner.bounded_process([str(FIXTURES / "nonexistent.exe")], dict(os.environ), 5)
        self.assertFalse(r["process_started"])
        self.assertIsNotNone(r["process_error"])
        self.assertFalse(r["passed"])

    @unittest.skipUnless(os.name == "nt", "Windows named mutex fixture")
    def test_same_boundary_mutex_excludes_other_process(self):
        ready = FIXTURES / "mutex-child.ready"
        code = ("import importlib.util,time; from pathlib import Path; "
            f"s=importlib.util.spec_from_file_location('m', {str(ROOT / 'tools/run_dla32_stop_worker_v6_cancel.py')!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            f"\nwith m.exclusive_runner():\n Path({str(ready)!r}).write_text('held'); time.sleep(2)\n")
        child = subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists(), "synthetic child did not acquire shared mutex")
            with self.assertRaisesRegex(RuntimeError, "mutex unavailable"):
                with runner.exclusive_runner():
                    self.fail("cross-process ownership was granted")
        finally:
            if child.poll() is None:
                out, err = child.communicate(timeout=5)
            else:
                out, err = child.communicate()
        self.assertEqual(child.returncode, 0, (out, err))
        with runner.exclusive_runner():
            pass

    def test_hash_guard_rejects_changed_binary(self):
        path = FIXTURES / "pin.bin"
        path.write_bytes(b"original")
        digest = runner.sha(path)
        self.assertEqual(runner.verified_files({path: digest})[str(path)], digest)
        path.write_bytes(b"different")
        with self.assertRaises(RuntimeError):
            runner.verified_files({path: digest})

    def test_final_proof_missing_pin_blocks_without_launch(self):
        with patch.object(runner, "FINAL_PROOF_SHA", ""):
            with self.assertRaisesRegex(RuntimeError, "not frozen"):
                runner.final_proof_files()

    def test_relative_proof_file_rejects_escape(self):
        for name in ("../outside", "C:/absolute", "sub\\..\\name"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                runner.workspace_file(name)

    def test_frozen_windows_relative_validation_paths_supported(self):
        path = FIXTURES / "windows-anchor.bin"
        path.write_bytes(b"fixture")
        relative = str(path.relative_to(ROOT)).replace("/", "\\")
        self.assertEqual(runner.workspace_file(relative), path.resolve())

    def test_final_proof_checks_and_owner_review_anchors_required(self):
        owner = FIXTURES / "owner.json"
        review = FIXTURES / "review.md"
        owner.write_text("offline fixture")
        review.write_text("review fixture")
        proof = {"schema_version": 1, "limited_hardware_gate_ready": True,
            "package_manifest_sha256": runner.EXPECTED[runner.PACKAGE / "package-manifest.json"],
            "core_dll_sha256": runner.EXPECTED_DLL,
            "owner_offline_validation_sha256": runner.sha(owner),
            "independent_review_sha256": runner.sha(review),
            "checks": [{"name": "final_fixture", "passed": True}],
            "files": [{"path": str(p.relative_to(ROOT)), "sha256": runner.sha(p)} for p in (owner, review)]}
        path = FIXTURES / "proof.json"
        path.write_text(json.dumps(proof))
        with patch.object(runner, "FINAL_PROOF", path), patch.object(runner, "FINAL_PROOF_SHA", runner.sha(path)):
            self.assertEqual(set(runner.final_proof_files()), {owner.resolve(), review.resolve()})
        for i, mutation in enumerate((
            lambda p: p["checks"][0].update(passed=False),
            lambda p: p.update(core_dll_sha256="0" * 64),
            lambda p: p.update(independent_review_sha256="0" * 64),
            lambda p: p["files"].append(p["files"][0]),
        )):
            bad = json.loads(json.dumps(proof))
            mutation(bad)
            path.write_text(json.dumps(bad))
            with patch.object(runner, "FINAL_PROOF", path), patch.object(runner, "FINAL_PROOF_SHA", runner.sha(path)):
                with self.subTest(i=i), self.assertRaises(RuntimeError):
                    runner.final_proof_files()

    def test_fresh_paths_reject_any_existing_evidence(self):
        base = FIXTURES / "fresh-paths"
        base.mkdir()
        with patch.object(runner, "BASE", base):
            self.assertEqual(runner.new_paths("fresh")["directory"], base / "fresh")
            (base / "fresh.stderr.log").write_text("original")
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                runner.new_paths("fresh")
            self.assertEqual((base / "fresh.stderr.log").read_text(), "original")

    def test_bad_block_names_fail(self):
        for name in ("../x", "", "a/b", "a\\b", "a:x"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                runner.new_paths(name)

    def test_exclusive_report_preserves_original(self):
        path = FIXTURES / "exclusive.json"
        runner.write_json(path, {"original": True})
        with self.assertRaises(FileExistsError):
            runner.write_json(path, {"replacement": True})
        self.assertEqual(json.loads(path.read_text()), {"original": True})

    def test_zero_cancel_and_shifted_lanes_remain_valid_diagnostics(self):
        d = make_capture("zero-cancel")
        change(d, "rep001-step2-Stream.json", "observed_sample_bits_or", "30000000")
        result = runner.verify_sequence(d)
        self.assertTrue(result["passed"])
        self.assertEqual([r["samples"] for r in result["stages"]], [0, 5000000, 5000000])
        self.assertFalse(result["physical_integrity_verified"])

    def test_close_and_exit_failure_block_recovery_success(self):
        for i, key in enumerate(("device_close_status", "sr_exit_status")):
            d = make_capture("close-fail-" + str(i))
            change(d, "sequence-result.json", key, -1)
            with self.assertRaises(RuntimeError):
                runner.verify_sequence(d)

    def test_partial_finite_is_not_recovery_pass(self):
        d = make_capture("partial-finite")
        change(d, "rep001-step1-Buffer.json", "actual_samples", 1048576)
        with self.assertRaisesRegex(RuntimeError, "Finite recovery"):
            runner.verify_sequence(d)

    def test_full_long_capture_is_not_early_cancel(self):
        d = make_capture("full-cancel")
        change(d, "cancel001-Buffer.json", "actual_samples", 10000000)
        with self.assertRaises(RuntimeError):
            runner.verify_sequence(d)

    def test_duplicate_stop_or_poststop_logic_fail(self):
        for i, key in enumerate(("stop_call_count", "logic_after_stop")):
            d = make_capture("stop-fail-" + str(i))
            change(d, "cancel001-Buffer.json", key, 2)
            with self.assertRaises(RuntimeError):
                runner.verify_sequence(d)

    def test_missing_unconsumed_sidecar_fails(self):
        d = make_capture("missing-sidecar")
        (d / "cancel001-Buffer.wire.bin.worker-unconsumed.bin").unlink()
        with self.assertRaises(OSError):
            runner.verify_sequence(d)

    def test_positive_unconsumed_preserved_and_size_checked(self):
        d = make_capture("positive-sidecar")
        path = d / "cancel001-Buffer.wire.bin.worker-unconsumed.bin"
        path.write_bytes(bytes(range(64)))
        change(d, "cancel001-Buffer.json", "worker_unconsumed_bytes", 64)
        result = runner.verify_sequence(d)
        self.assertTrue(result["passed"])
        self.assertEqual(result["stages"][0]["files"][path.name]["bytes"], 64)
        path.write_bytes(bytes(range(63)))
        with self.assertRaisesRegex(RuntimeError, "byte count"):
            runner.verify_sequence(d)

    def test_original_raw_overshoot_preserved(self):
        d = make_capture("overshoot")
        path = d / "rep001-step1-Buffer.wire.bin"
        with path.open("r+b") as stream:
            stream.truncate(20971520)
        change(d, "rep001-step1-Buffer.json", "wire_trace_bytes", 20971520)
        self.assertTrue(runner.verify_sequence(d)["passed"])
        self.assertEqual(path.stat().st_size, 20971520)

    def test_unsafe_stage_path_fails(self):
        d = make_capture("unsafe-stage")
        change(d, "cancel001-Buffer.json", "worker_unconsumed_file", "../escape")
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            runner.verify_sequence(d)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    # Only this process's newly created synthetic fixture directory is removed.
    resolved = FIXTURES.resolve(strict=True)
    if resolved.parent != (ROOT / "artifacts").resolve(strict=True) or not resolved.name.startswith("worker-v6-cancel-runner-fixtures-"):
        raise RuntimeError("Refusing fixture cleanup outside the verified artifact directory")
    shutil.rmtree(resolved)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
