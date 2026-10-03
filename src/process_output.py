"""Linux subprocess output with a retained-output cap and a deadline.

The child process's own RAM is not limited. Output is consumed directly from
pipes, with no unbounded temporary file. Group cleanup is attempted; an
elevated child may refuse the caller's signal, so timeout is not proof of
cancellation or rollback.

The leader is observed without reaping before group cleanup. This keeps its
PID reserved even after exit, so cleanup cannot address a reused group ID.
run_bounded owns reaping; a detected external reap disables group signalling.
"""

import os
import math
import selectors
import signal
import subprocess
import threading
import time


CLEANUP_UNCERTAINTY = (
    'Process-group cleanup could not be confirmed; the command or its descendants '
    'may still be running. Check the target state before repeating a change.'
)

_PROC_SCAN_TIMEOUT = .2
_PROC_SCAN_MAX_PIDS = 4096
_PROC_STAT_LIMIT = 4096
_PROC_MOUNTINFO_LIMIT = 128 * 1024


def _read_proc(path, limit):
    with open(path, 'rb') as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Proc observation exceeded its read limit')
    return data


def _proc_tasks_visible():
    """Require a normal, unrestricted proc mount before trusting a scan."""
    mounts = _read_proc('/proc/self/mountinfo', _PROC_MOUNTINFO_LIMIT)
    proc_seen = False
    for line in mounts.splitlines():
        fields = line.split()
        separator = fields.index(b'-')
        mountpoint = fields[4]
        if mountpoint == b'/proc':
            if proc_seen or fields[3] != b'/' or fields[separator + 1] != b'proc':
                return False
            options = fields[5].split(b',') + fields[separator + 3].split(b',')
            if any(option.startswith(b'hidepid=') and option != b'hidepid=0'
                   for option in options):
                return False
            proc_seen = True
        elif mountpoint.startswith(b'/proc/'):
            component = mountpoint[len(b'/proc/'):].split(b'/')[0]
            if component.isdigit() or component in (b'self', b'thread-self'):
                return False
    # A proc mount belonging to a different PID namespace is insufficient.
    own_stat = _read_proc('/proc/self/stat', _PROC_STAT_LIMIT)
    return proc_seen and int(own_stat.split(b' ', 1)[0]) == os.getpid()


def _proc_stat(row, expected_pid):
    end_name = row.rfind(b')')
    if end_name < 0 or not row.startswith(str(expected_pid).encode('ascii') + b' ('):
        raise ValueError('Invalid proc identity')
    fields = row[end_name + 1:].split()
    if (len(fields) < 4 or len(fields[0]) != 1 or fields[0] not in b'RSDTtZXxKWPI'
            or any(int(value) < 0 for value in fields[1:4])):
        raise ValueError('Invalid proc state')
    return fields[0], int(fields[3])


def _proc_session_empty(session_id):
    """Bounded observation of an exited leader's original Linux session.

    EPERM may be caused solely by an inaccessible zombie, which cannot execute
    or fork.
    Preserve its real exit status only when a complete visible proc scan finds
    no live member anywhere in the session. Other groups can rejoin the original
    group, so inspecting only the original PGID would be insufficient. This is
    an observation, not a guarantee against concurrently changing processes.
    """
    deadline = time.monotonic() + _PROC_SCAN_TIMEOUT
    leader_seen = False
    entries = 0
    try:
        if not _proc_tasks_visible():
            return False
        with os.scandir('/proc') as processes:
            for entry in processes:
                if time.monotonic() >= deadline:
                    return False
                if not entry.name.isdigit():
                    continue
                entries += 1
                if entries > _PROC_SCAN_MAX_PIDS:
                    return False
                row = _read_proc('/proc/' + entry.name + '/stat', _PROC_STAT_LIMIT)
                pid = int(entry.name)
                state, observed_session = _proc_stat(row, pid)
                if observed_session == session_id:
                    if state not in (b'Z', b'X', b'x'):
                        return False
                    if pid == session_id:
                        leader_seen = True
                    else:
                        # A zombie thread-group leader can still have live
                        # threads. The original leader is already waitid-exited;
                        # other members require a bounded thread observation.
                        leader_thread_seen = False
                        task_path = '/proc/' + entry.name + '/task'
                        with os.scandir(task_path) as tasks:
                            for task in tasks:
                                if time.monotonic() >= deadline or not task.name.isdigit():
                                    return False
                                entries += 1
                                if entries > _PROC_SCAN_MAX_PIDS:
                                    return False
                                row = _read_proc(task_path + '/' + task.name + '/stat',
                                                 _PROC_STAT_LIMIT)
                                thread_state, thread_session = _proc_stat(row, int(task.name))
                                if (thread_state not in (b'Z', b'X', b'x')
                                        or thread_session != session_id):
                                    return False
                                if int(task.name) == pid:
                                    leader_thread_seen = True
                        if not leader_thread_seen:
                            return False
        return (leader_seen and time.monotonic() < deadline
                and _proc_tasks_visible() and time.monotonic() < deadline)
    except (OSError, ValueError, IndexError):
        # Disappearing/unreadable rows, restricted mounts, malformed data, and
        # incomplete observations cannot establish that a session is empty.
        return False


def _observe_child(process):
    """Observe exit while leaving the PID reserved for safe group cleanup."""
    if process.returncode is not None:
        raise ChildProcessError('Child already reaped')
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def _wait_without_reaping(process, argv, timeout, deadline):
    while True:
        status = _observe_child(process)
        if status is not None:
            if status.si_code == os.CLD_EXITED:
                return status.si_status
            return -status.si_status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        time.sleep(min(.01, remaining))


def _cleanup_process(process):
    warning = ''
    try:
        # WNOWAIT leaves both running and exited children owned by this caller.
        # Never poll()/wait() before killpg(): reaping would release the PID.
        status = _observe_child(process)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except PermissionError:
            # _observe_child uses WEXITED: a returned status is always an
            # exited child. Python 3.8 does not expose all CLD_* constants.
            exited = status is not None
            if not exited or not _proc_session_empty(process.pid):
                warning = CLEANUP_UNCERTAINTY
    except ProcessLookupError:
        pass
    except (ChildProcessError, OSError):
        # An external reaper may have released the PID, or an elevated child
        # may refuse the signal. Neither permits assuming successful cleanup.
        warning = CLEANUP_UNCERTAINTY
    try:
        process.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        warning = CLEANUP_UNCERTAINTY
        # Reap later without extending the foreground deadline indefinitely.
        threading.Thread(target=process.wait, daemon=True).start()
    except OSError:
        warning = CLEANUP_UNCERTAINTY
    return warning


def run_bounded(argv, timeout, limit, env=None):
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(item, str) for item in argv)):
        raise ValueError('argv must be a nonempty sequence of strings')
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('timeout must be positive and finite')
    if type(limit) is not int or limit <= 0:
        raise ValueError('output limit must be a positive integer')
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, env=env)
    selector = None
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + timeout
    total = 0
    truncated = False
    returncode = None
    failure = None
    try:
        selector = selectors.DefaultSelector()
        for name in output:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fileobj.fileno(), min(65536, limit - total + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = max(0, limit - total)
                output[key.data].extend(chunk[:available])
                total += len(chunk)
                if total > limit:
                    truncated = True
                    break
            if truncated:
                break
        if not truncated:
            try:
                returncode = _wait_without_reaping(process, argv, timeout, deadline)
            except ChildProcessError:
                # Cleanup rechecks ownership and reports uncertainty instead
                # of using an exit status fabricated after an external reap.
                pass
    except BaseException as error:
        failure = error
        raise
    finally:
        # Also clean up descendants that closed their pipes before the leader
        # exited. A successful foreground command must not leave these alive.
        try:
            cleanup_warning = _cleanup_process(process)
            if failure is not None and cleanup_warning:
                failure.cleanup_uncertainty = cleanup_warning
                if isinstance(failure, subprocess.TimeoutExpired):
                    failure.stderr = output['stderr'].decode('utf-8', errors='replace')
                    failure.stderr += '\n' + cleanup_warning
        finally:
            if selector is not None:
                selector.close()
            process.stdout.close()
            process.stderr.close()
    decoded = {name: data.decode('utf-8', errors='replace') for name, data in output.items()}
    if truncated:
        decoded['stdout'] += '\n... (output truncated at {} bytes)'.format(limit)
    if cleanup_warning:
        decoded['stderr'] += '\n' + cleanup_warning
    code = 1 if truncated or returncode is None else returncode
    if cleanup_warning and code == 0:
        code = 1
    return code, decoded['stdout'], decoded['stderr']
