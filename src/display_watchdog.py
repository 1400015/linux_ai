"""Experimental, opt-in display recovery in a separate process.

The worker owns both mutation and recovery. Its only authority comes from a
private inherited pipe carrying one enumerated mode, never a command string.
It deliberately does not load saved snapshots or recover into a new session.
"""

import json
import os
import re
import select
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid


_LIMIT = 4096
_FAILURE = "Independent display recovery could not be confirmed; check the desktop display settings."
_TERMINAL = {"kept", "reverted", "failed"}
_ENV = {"PATH", "DISPLAY", "XAUTHORITY", "SWAYSOCK", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "WAYLAND_DISPLAY"}


class _Reader:
    def __init__(self, fd):
        self.fd = fd
        self.buffer = bytearray()

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Invalid display control record.")
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], max(0, remaining))[0]:
                return None
            block = os.read(self.fd, _LIMIT + 1)
            if not block:
                raise EOFError("Display control channel closed.")
            self.buffer.extend(block)
            if len(self.buffer) > _LIMIT:
                raise ValueError("Display control record is too large.")


def _send(fd, value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > _LIMIT:
        raise ValueError("Display control record is too large.")
    # Frames fit PIPE_BUF; each channel has one writer (the parent serializes).
    if os.write(fd, raw) != len(raw):
        raise OSError("Incomplete display control record.")


def _file_identity(path, socket=False, root_allowed=False):
    if not os.path.isabs(path):
        raise ValueError("A local absolute display endpoint is required.")
    info = os.lstat(path)
    owners = {os.getuid(), 0} if root_allowed else {os.getuid()}
    expected = stat.S_ISSOCK(info.st_mode) if socket else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid not in owners:
        raise ValueError("The graphical session endpoint has unsafe ownership or type.")
    if not socket and info.st_mode & 0o077:
        raise ValueError("The X11 authentication file must be private.")
    return path, info.st_dev, info.st_ino, info.st_ctime_ns, info.st_uid


def _session_identity(environ):
    if environ.get("SWAYSOCK"):
        return (_file_identity(environ["SWAYSOCK"], socket=True),)
    display = environ.get("DISPLAY", "")
    matched = re.fullmatch(r":(\d{1,5})(?:\.\d{1,3})?", display)
    if not matched or not environ.get("XAUTHORITY"):
        raise ValueError("Independent recovery requires local X11 with an explicit private XAUTHORITY file, or Sway.")
    return (
        _file_identity("/tmp/.X11-unix/X" + str(int(matched[1])), socket=True, root_allowed=True),
        _file_identity(environ["XAUTHORITY"]),
    )


def _bounded_snapshot(snapshot):
    if len(snapshot.outputs) > 8 or len(snapshot.modes) > 256:
        raise ValueError("The display layout exceeds the independent recovery limits.")
    for state in snapshot.outputs.values():
        if any(type(state[key]) is not int or abs(state[key]) > 131072 for key in ("x", "y")):
            raise ValueError("The display position exceeds the independent recovery limits.")
    return snapshot


def _receipt(directory_fd, token, status):
    """A private outcome only; never executable recovery data or credentials."""
    descriptor = os.open("outcome.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
    try:
        raw = json.dumps({"operation": token, "status": status}).encode("ascii")
        if os.write(descriptor, raw) != len(raw):
            raise OSError("Incomplete display recovery receipt.")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _worker(command_fd, reply_fd, directory_fd):
    # Imports happen only in the isolated worker, with a trusted package root.
    from src.display_actions import DisplayMode, DisplayService, _number

    reader = _Reader(command_fd)
    service = None
    change = None
    operation = ""
    status = "failed"
    try:
        request = reader.read(10)
        if not request or set(request) != {"op", "token", "mode", "timeout"} or request["op"] != "prepare":
            raise ValueError("Invalid display preparation.")
        operation = request["token"]
        if not isinstance(operation, str) or not re.fullmatch(r"[0-9a-f]{32}", operation):
            raise ValueError("Invalid display operation token.")
        mode = DisplayMode.from_dict(request["mode"])
        timeout = request["timeout"]
        if not _number(timeout, 1, 120):
            raise ValueError("Invalid display confirmation period.")
        env = dict(os.environ)
        identity = _session_identity(env)

        class GuardedService(DisplayService):
            def _call(self, argv):
                try:
                    if _session_identity(self.environ) != identity:
                        return False, "The graphical session changed; automatic recovery was stopped."
                except (OSError, ValueError):
                    return False, "The graphical session is unavailable; automatic recovery was stopped."
                return super()._call(argv)

            def _discover(self):
                snapshot, error = super()._discover()
                if snapshot:
                    try:
                        _bounded_snapshot(snapshot)
                    except ValueError as exc:
                        return None, str(exc)
                return snapshot, error

        service = GuardedService(environ=env)
        snapshot, error = service._discover()
        if snapshot is None or not any(item._key() == mode._key() for item in snapshot.modes):
            raise ValueError(error or "The requested mode is unavailable.")
        # No mutation has occurred. The parent must explicitly acknowledge readiness.
        _send(reply_fd, {"event": "ready", "token": operation})
        request = reader.read(10)
        if request != {"op": "apply", "token": operation}:
            raise ValueError("Display preparation was not acknowledged.")
        change, error = service.apply_mode(mode, timeout)
        if change is None:
            raise ValueError(error or _FAILURE)
        _send(reply_fd, {"event": "applied", "token": operation, "deadline": change._deadline})
        while change.status == "pending":
            try:
                request = reader.read(0.05)
            except EOFError:
                change.revert()
                break
            if request is None:
                continue
            if set(request) != {"op", "token"} or request["token"] != operation:
                raise ValueError("Invalid display control token.")
            if request["op"] == "confirm":
                change.confirm()
            elif request["op"] == "revert":
                change.revert()
            else:
                raise ValueError("Invalid display control operation.")
        status = change.status
    except (OSError, ValueError, TypeError, EOFError):
        if service is not None:
            service.close()
        if change is not None:
            status = change.status
    finally:
        if service is not None:
            service.close()
        try:
            if operation:
                _receipt(directory_fd, operation, status)
            _send(reply_fd, {"event": "finished", "token": operation, "status": status})
        except (OSError, ValueError):
            pass
        for fd in (command_fd, reply_fd, directory_fd):
            os.close(fd)


class ProcessDisplayChange:
    """Parent-side handle. Only the worker can execute a display command."""

    def __init__(self, service, process, command_fd, reader, directory, token, deadline, is_current):
        from .action_audit import _sink
        self.token = token
        self.status = "pending"
        self.audit_sink = _sink.get()
        self._service = service
        self._process = process
        self._command_fd = command_fd
        self._reader = reader
        self._directory = directory
        self._deadline = deadline
        self._is_current = is_current
        self._message = ""
        self._write_lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    @property
    def remaining_seconds(self):
        return max(0, self._deadline - time.monotonic()) if self.status == "pending" else 0

    def _listen(self):
        try:
            while True:
                value = self._reader.read(1)
                if value is None:
                    if self._process.poll() is not None:
                        raise EOFError()
                    continue
                if (value.get("event") != "finished" or value.get("token") != self.token
                        or value.get("status") not in _TERMINAL):
                    raise ValueError("Invalid display worker reply.")
                self.status = value["status"]
                break
        except (OSError, ValueError, EOFError):
            self.status = "failed"
        finally:
            self._message = {"kept": "The display configuration was kept.",
                             "reverted": "The previous display configuration was restored.",
                             "failed": _FAILURE}[self.status]
            if self.status == "failed":
                self._service._failed_recovery = self._message
            if self.audit_sink:
                try:
                    self.audit_sink({"kept": "confirmed", "reverted": "reverted",
                                     "failed": "recovery_failed"}[self.status], self._message)
                except Exception:
                    pass
            with self._write_lock:
                if self._command_fd is not None:
                    os.close(self._command_fd)
                    self._command_fd = None
            os.close(self._reader.fd)
            self._done.set()
            self._process.wait()
            try:
                os.unlink(os.path.join(self._directory, "outcome.json"))
                os.rmdir(self._directory)
            except OSError:
                pass

    def _finish(self, keep):
        with self._service._lock:
            pending = self.status == "pending"
            if pending:
                allowed = keep and not self._service._closed and self._service._allowed(self._is_current)
                operation = "confirm" if allowed else "revert"
                try:
                    with self._write_lock:
                        if self._command_fd is not None:
                            _send(self._command_fd, {"op": operation, "token": self.token})
                except (OSError, ValueError):
                    pass
        if pending:
            if not self._done.wait(90):
                return False, _FAILURE
        return self.status == ("kept" if keep else "reverted"), self._message

    def confirm(self):
        return self._finish(True)

    def revert(self):
        return self._finish(False)

    def close(self):
        return self.revert() if self.status == "pending" else (self.status != "failed", self._message)


def launch(service, mode, timeout, is_current=None):
    """Fail closed until the worker acknowledges readiness and then mutation."""
    from .display_actions import _number
    process = None
    fds = []
    directory = None
    try:
        env = {key: value for key, value in service.environ.items() if key in _ENV}
        env["LC_ALL"] = "C"
        env.setdefault("PATH", os.defpath)
        _session_identity(env)
        directory = tempfile.mkdtemp(prefix="linux-ai-display-")
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(directory_fd)
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Unsafe recovery receipt directory.")
        command_read, command_write = os.pipe()
        fds.extend([command_read, command_write])
        reply_read, reply_write = os.pipe()
        fds.extend([reply_read, reply_write])
        script = os.path.abspath(__file__)
        process = subprocess.Popen([sys.executable, "-I", script, str(command_read), str(reply_write), str(directory_fd)],
                                   pass_fds=(command_read, reply_write, directory_fd), close_fds=True,
                                   start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env)
        for fd in (command_read, reply_write, directory_fd):
            os.close(fd)
            fds.remove(fd)
        token = uuid.uuid4().hex
        reader = _Reader(reply_read)
        _send(command_write, {"op": "prepare", "token": token, "mode": mode.to_dict(), "timeout": timeout})
        if reader.read(20) != {"event": "ready", "token": token}:
            raise ValueError("The display worker did not acknowledge readiness.")
        if not service._allowed(is_current):
            raise ValueError("The display request was cancelled.")
        _send(command_write, {"op": "apply", "token": token})
        reply = reader.read(30)
        if (not reply or reply.get("event") != "applied" or reply.get("token") != token
                or not _number(reply.get("deadline"), 0, time.monotonic() + 120)):
            raise ValueError("The display worker could not apply the selected mode.")
        change = ProcessDisplayChange(service, process, command_write, reader, directory,
                                      token, reply["deadline"], is_current)
        fds.clear()  # Ownership was transferred to the handle.
        if not service._allowed(is_current):
            ok, _ = change.revert()
            if not ok:
                service._failed_recovery = _FAILURE
                return None, _FAILURE
            return None, "The display request was cancelled."
        return change, ""
    except (OSError, ValueError, TypeError, EOFError):
        # Closing the private parent channel requests recovery. Never kill the
        # worker to implement a frontend timeout or cancellation.
        for fd in fds:
            os.close(fd)
        if process is None and directory:
            os.rmdir(directory)
        elif process is not None:
            # The worker may still be completing a bounded native operation.
            # A second worker must not race its eventual recovery.
            service._failed_recovery = _FAILURE
            threading.Thread(target=process.wait, daemon=True).start()
        return None, _FAILURE


if __name__ == "__main__":
    # -I excludes cwd/PYTHONPATH; admit only this installed package's parent.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if len(sys.argv) != 4:
        raise SystemExit(2)
    _worker(*(int(value) for value in sys.argv[1:]))
