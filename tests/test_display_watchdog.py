"""Real worker processes, private fake Sway IPC, and a real frontend SIGKILL.

No command in these tests accesses an actual compositor or desktop.
"""

import ctypes
import json
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.display_actions import DisplayMode, DisplayService
from src.display_watchdog import _Reader, _bounded_snapshot, _send, _session_identity


_TOOL = '''import json, os, sys, time
path = os.path.join(os.path.dirname(__file__), "state.json")
with open(path) as stream:
    state = json.load(stream)
modes = [{"width": 1920, "height": 1080, "refresh": 60000},
         {"width": 1280, "height": 720, "refresh": 60000}]
if sys.argv[1:] == ["-r", "-t", "get_outputs"]:
    current = next(mode for mode in modes if mode["width"] == state["width"])
    print(json.dumps([{"name": "screen", "active": True, "scale": 1.0,
        "transform": "normal", "power": True, "rect": {"x": 0, "y": 0},
        "modes": modes, "current_mode": current}]))
else:
    command = sys.argv[4]
    restoring = " enable " in command
    if not restoring:
        with open(path + ".applying", "w"):
            pass
        time.sleep(state.get("delay", 0))
    state["width"] = 1920 if restoring else 1280
    state["commands"].append(command)
    temporary = path + ".tmp"
    with open(temporary, "w") as stream:
        json.dump(state, stream)
    os.replace(temporary, path)
    print('[{"success": true}]')
'''


class TestDisplayWatchdog(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(str(self.path / "sway.sock"))
        executable = self.path / "swaymsg"
        executable.write_text("#!" + sys.executable + "\n" + _TOOL, encoding="utf-8")
        executable.chmod(0o700)
        self.state = self.path / "state.json"
        self.state.write_text(json.dumps({"width": 1920, "commands": []}), encoding="utf-8")
        self.env = {"PATH": str(self.path) + os.pathsep + os.defpath,
                    "SWAYSOCK": str(self.path / "sway.sock"), "XDG_SESSION_TYPE": "wayland"}
        self.service = DisplayService(environ=self.env, independent_watchdog=True,
                                      which=lambda name: str(self.path / name))
        self.changes = []

    def tearDown(self):
        self.service.close()
        for change in self.changes:
            change._process.wait(timeout=8)
        self.socket.close()
        self.temporary.cleanup()

    def read_state(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def apply(self, timeout=2, is_current=None):
        mode = DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz")
        change, error = self.service.apply_mode(mode, timeout, is_current)
        self.assertIsNotNone(change, error)
        self.changes.append(change)
        return change

    def test_confirmation_keeps_mode_and_worker_reaps(self):
        change = self.apply()
        self.assertEqual(os.getsid(change._process.pid), change._process.pid)
        self.assertTrue(change.confirm()[0])
        self.assertEqual(change.status, "kept")
        self.assertEqual(self.read_state()["width"], 1280)
        change._process.wait(timeout=5)

    def test_audit_callback_exception_does_not_break_cleanup_or_completion(self):
        change = self.apply()

        def broken_audit(phase, message):
            raise RuntimeError("audit consumer stopped")

        change.audit_sink = broken_audit
        self.assertTrue(change.revert()[0])
        self.assertTrue(change._done.is_set())
        change._process.wait(timeout=5)
        self.assertIsNone(change._command_fd)
        self.assertEqual(self.read_state()["width"], 1920)

    def test_service_shutdown_serializes_confirmation_and_restores(self):
        change = self.apply(timeout=5)
        closing = threading.Event()
        release_close = threading.Event()
        confirming = threading.Event()
        original_close = change.close
        results = {}

        def held_close():
            closing.set()
            if not release_close.wait(5):
                raise RuntimeError("test close barrier expired")
            return original_close()

        def close_service():
            results["close"] = self.service.close()

        def confirm_change():
            confirming.set()
            results["confirm"] = change.confirm()

        with patch.object(change, "close", side_effect=held_close):
            closer = threading.Thread(target=close_service)
            confirmer = threading.Thread(target=confirm_change)
            closer.start()
            self.assertTrue(closing.wait(2))
            self.assertTrue(self.service._closed)
            confirmer.start()
            self.assertTrue(confirming.wait(2))
            confirmer.join(0.1)
            self.assertTrue(confirmer.is_alive(), "confirmation bypassed the service shutdown lock")
            release_close.set()
            closer.join(5)
            confirmer.join(5)
        self.assertFalse(closer.is_alive())
        self.assertFalse(confirmer.is_alive())
        self.assertTrue(results["close"][0])
        self.assertFalse(results["confirm"][0])
        self.assertEqual(change.status, "reverted")
        self.assertEqual(self.read_state()["width"], 1920)

    def test_expiry_restores_without_frontend_confirmation(self):
        change = self.apply(timeout=1)
        self.assertTrue(change._done.wait(5))
        self.assertEqual(change.status, "reverted")
        self.assertEqual(self.read_state()["width"], 1920)
        self.assertFalse(change.confirm()[0])
        self.assertEqual(len(self.read_state()["commands"]), 2)

    def test_cancel_after_ready_prevents_mutation(self):
        calls = []

        def is_current():
            calls.append(True)
            return len(calls) == 1

        change, error = self.service.apply_mode(DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz"),
                                                 1, is_current)
        self.assertIsNone(change)
        self.assertIn("recovery", error.lower())
        self.assertEqual(self.read_state()["commands"], [])

    def test_delayed_apply_cannot_defeat_expired_watchdog(self):
        self.state.write_text(json.dumps({"width": 1920, "commands": [], "delay": 1.2}), encoding="utf-8")
        change, error = self.service.apply_mode(DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz"), 1)
        self.assertIsNone(change)
        self.assertIn("recovery", error.lower())
        self.assertEqual(self.read_state()["width"], 1920)
        self.assertEqual(len(self.read_state()["commands"]), 2)

    def test_session_replacement_blocks_stale_restore(self):
        change = self.apply(timeout=1)
        self.socket.close()
        os.unlink(self.env["SWAYSOCK"])
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(self.env["SWAYSOCK"])
        self.assertTrue(change._done.wait(5))
        self.assertEqual(change.status, "failed")
        self.assertEqual(len(self.read_state()["commands"]), 1)
        self.assertFalse(self.service.close()[0])

    def test_private_parent_pipe_eof_restores(self):
        change = self.apply(timeout=5)
        with change._write_lock:
            os.close(change._command_fd)
            change._command_fd = None
        self.assertTrue(change._done.wait(5))
        self.assertEqual(change.status, "reverted")
        self.assertEqual(self.read_state()["width"], 1920)

    def test_parent_sigkill_survivor_restores_and_leaves_private_receipt(self):
        self._exercise_parent_sigkill(delay=0)

    def test_parent_sigkill_during_delayed_apply_still_restores(self):
        self._exercise_parent_sigkill(delay=1.2)

    def _exercise_parent_sigkill(self, delay):
        # Adopt the orphan worker so this test also proves it is reaped.
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous), 0, 0, 0), 0)  # GET_CHILD_SUBREAPER
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)  # SET_CHILD_SUBREAPER
        code = '''import json, time, os, subprocess
from src.display_actions import DisplayService, DisplayMode
original_popen = subprocess.Popen
def reported_popen(*args, **kwargs):
    process = original_popen(*args, **kwargs)
    descriptor = int(args[0][-1])
    print(json.dumps({"worker": process.pid, "directory": os.readlink("/proc/self/fd/" + str(descriptor))}), flush=True)
    return process
subprocess.Popen = reported_popen
service = DisplayService(independent_watchdog=True)
change, error = service.apply_mode(DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz"), 10)
if change is None:
    raise RuntimeError(error)
time.sleep(60)
'''
        self.state.write_text(json.dumps({"width": 1920, "commands": [], "delay": delay}), encoding="utf-8")
        parent = subprocess.Popen([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]),
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        worker = None
        directory = None
        try:
            self.assertTrue(select.select([parent.stdout], [], [], 15)[0])
            line = parent.stdout.readline()
            self.assertTrue(line, parent.stderr.read() if parent.poll() is not None else "No frontend reply")
            result = json.loads(line)
            worker, directory = result["worker"], result["directory"]
            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
            deadline = time.monotonic() + 8
            while not (self.path / "state.json.applying").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.path / "state.json.applying").exists())
            if delay:
                self.assertEqual(self.read_state()["width"], 1920)
            else:
                while self.read_state()["width"] != 1280 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.read_state()["width"], 1280)
            os.kill(parent.pid, signal.SIGKILL)
            self.assertEqual(parent.wait(timeout=5), -signal.SIGKILL)
            deadline = time.monotonic() + 8
            receipt = Path(directory) / "outcome.json"
            while not receipt.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(receipt.exists())
            self.assertEqual(json.loads(receipt.read_text())["status"], "reverted")
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            self.assertEqual(self.read_state()["width"], 1920)
            os.waitpid(worker, 0)
            worker = None
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            if worker is not None:
                os.kill(worker, signal.SIGKILL)
                os.waitpid(worker, 0)
            libc.prctl(36, previous.value, 0, 0, 0)
            parent.stdout.close()
            parent.stderr.close()
            if directory:
                receipt = Path(directory) / "outcome.json"
                if receipt.exists():
                    receipt.unlink()
                os.rmdir(directory)

    def test_snapshot_limits_fail_closed(self):
        modes, error = self.service.list_modes()
        self.assertFalse(error)
        snapshot, error = self.service._discover()
        self.assertFalse(error)
        snapshot.modes = modes * 257
        with self.assertRaises(ValueError):
            _bounded_snapshot(snapshot)

    def test_symlink_endpoint_is_rejected(self):
        alias = self.path / "alias.sock"
        alias.symlink_to(self.path / "sway.sock")
        with self.assertRaises(ValueError):
            _session_identity(dict(self.env, SWAYSOCK=str(alias)))

    def test_oversized_control_record_is_rejected(self):
        read, write = os.pipe()
        try:
            os.write(write, b"x" * 4097)
            with self.assertRaises(ValueError):
                _Reader(read).read(1)
            with self.assertRaises(ValueError):
                _send(write, {"data": "x" * 4097})
        finally:
            os.close(read)
            os.close(write)

    def test_opt_in_factory_and_custom_runner_fail_closed(self):
        def runner(argv, timeout):
            return True, ""
        service = DisplayService(runner=runner, independent_watchdog=True)
        change, error = service.apply_mode(DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz"))
        self.assertIsNone(change)
        self.assertIn("in-process", error)
        with patch("src.display_watchdog.launch", return_value=(None, "not ready")) as factory:
            service = DisplayService(independent_watchdog=True)
            self.assertEqual(service.apply_mode(DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz")),
                             (None, "not ready"))
            factory.assert_called_once()

    def test_nonfinite_apply_reply_blocks_retry_while_worker_recovers(self):
        token = "a" * 32
        directory = self.path / "receipt"
        directory.mkdir(mode=0o700)
        replies = [{"event": "ready", "token": token},
                   {"event": "applied", "token": token, "deadline": float("nan")}]
        processes = []
        real_popen = subprocess.Popen

        def capture_process(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch("src.display_watchdog._Reader.read", side_effect=replies), \
                patch("src.display_watchdog.uuid.uuid4", return_value=SimpleNamespace(hex=token)), \
                patch("src.display_watchdog.tempfile.mkdtemp", return_value=str(directory)), \
                patch("src.display_watchdog.subprocess.Popen", side_effect=capture_process) as spawn:
            mode = DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz")
            change, error = self.service.apply_mode(mode, 2)
            self.assertIsNone(change)
            self.assertIn("recovery", error.lower())
            self.assertTrue(self.service._failed_recovery)
            self.assertIsNone(self.service.apply_mode(mode, 2)[0])
            self.assertEqual(spawn.call_count, 1)
        # The real worker sees EOF, either before applying or after recovering.
        processes[0].wait(timeout=8)
        self.assertTrue((directory / "outcome.json").exists())
        self.assertEqual(self.read_state()["width"], 1920)

    def test_second_pipe_allocation_failure_closes_first_pipe(self):
        first_pipe = os.pipe()
        directory = self.path / "receipt"
        directory.mkdir(mode=0o700)
        with patch("src.display_watchdog.os.pipe", side_effect=[first_pipe, OSError("fd limit")]), \
                patch("src.display_watchdog.tempfile.mkdtemp", return_value=str(directory)):
            change, _ = self.service.apply_mode(DisplayMode("screen", 1280, 720, 60, "1280x720@60Hz"))
        self.assertIsNone(change)
        for fd in first_pipe:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertFalse(directory.exists())
        self.assertEqual(self.read_state()["commands"], [])
