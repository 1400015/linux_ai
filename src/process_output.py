"""Linux subprocess output with a real memory cap and deadline."""

import os
import selectors
import signal
import subprocess
import time


def run_bounded(argv, timeout, limit):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    for name in output:
        selector.register(getattr(process, name), selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout
    total = 0
    truncated = False
    try:
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
        return (process.returncode if process.returncode is not None else 1,
                decoded['stdout'], decoded['stderr'])
    finally:
        # Kill the entire group, including a child still holding the pipes.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
