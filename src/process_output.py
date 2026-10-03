"""Linux subprocess output with a retained-output cap and a deadline.

The child process's own RAM is not limited. Output is consumed directly from
pipes, with no unbounded temporary file. Group cleanup is attempted; an
elevated child may refuse the caller's signal, so timeout is not proof of
cancellation or rollback.
"""

import os
import math
import selectors
import signal
import subprocess
import threading
import time


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
            process.wait(timeout=max(.001, deadline - time.monotonic()))
        decoded = {name: data.decode('utf-8', errors='replace') for name, data in output.items()}
        if truncated:
            decoded['stdout'] += '\n... (output truncated at {} bytes)'.format(limit)
        return (1 if truncated else process.returncode,
                decoded['stdout'], decoded['stderr'])
    finally:
        # Also clean up descendants that closed their pipes before the leader
        # exited. A successful foreground command must not leave these alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # A pkexec child may have changed UID. The caller cannot promise
            # cancellation after elevation, and must report uncertain state.
            pass
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            # Reap later without turning a foreground timeout into an
            # indefinite wait when the elevated process cannot be signalled.
            threading.Thread(target=process.wait, daemon=True).start()
        finally:
            if selector is not None:
                selector.close()
            process.stdout.close()
            process.stderr.close()
