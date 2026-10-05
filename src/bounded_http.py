"""Bounded HTTP body reads, including blocking reads and gzip expansion.

The request itself still belongs to the caller: Requests keeps its usual TLS,
proxy and connection timeouts. This module bounds reading the response body.
Only a fixed number of daemon readers can exist, even if a broken transport
never returns after cancellation.
"""

import math
import queue
import socket
import threading
import time
import zlib

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout


MAX_ACTIVE_READERS = 8
_READERS = threading.BoundedSemaphore(MAX_ACTIVE_READERS)
_STATE_ATTRIBUTE = "_linux_ai_bounded_http_reader"
_POLL_SECONDS = 0.05
_END = object()


class HTTPReadError(ValueError):
    """A body could not be read; messages contain no provider body or URL."""

    def __init__(self, message, retryable_network_failure=None):
        super().__init__(message)
        # Preserve only a fixed classification, never the original exception,
        # whose message/traceback may contain a URL, response or credentials.
        self.retryable_network_failure = retryable_network_failure


class HTTPReadLimitError(HTTPReadError):
    """The body exceeded a byte, time or available-reader limit."""


class HTTPReadCancelled(Exception):
    """The caller cancelled the HTTP body read."""


class _ReaderState:
    def __init__(self):
        self.stop = threading.Event()
        self.done = threading.Event()
        self.read_finished = threading.Event()


def _response_raw(response):
    # Mock/shim objects may fabricate every queried attribute dynamically.
    # Requests stores raw explicitly, as do supported custom transports.
    attributes = getattr(response, "__dict__", {})
    return attributes.get("raw") if isinstance(attributes, dict) else None


def abort_response(response):
    """Interrupt an active Requests socket without waiting for its read lock.

    Calling Response.close while another thread reads its buffered HTTP file
    can wait for that reader. Socket shutdown wakes that read instead; its
    reader thread subsequently closes the response. Unknown response shims
    are stopped cooperatively and remain subject to the global reader limit.
    """
    state = getattr(response, _STATE_ATTRIBUTE, None)
    if isinstance(state, _ReaderState):
        state.stop.set()
        if state.read_finished.is_set():
            return
    raw = _response_raw(response)
    connection = getattr(raw, "_connection", None)
    transport_socket = getattr(connection, "sock", None)
    if transport_socket is None:
        http_file = getattr(raw, "_fp", None)
        buffered_file = getattr(http_file, "fp", None)
        socket_file = getattr(buffered_file, "raw", None)
        transport_socket = getattr(socket_file, "_sock", None)
    if isinstance(transport_socket, socket.socket):
        try:
            transport_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def close_response(response):
    """Close a response without waiting for a managed reader to finish.

    Use this instead of a Requests response context manager when cancellation
    must return promptly. Managed responses are closed by their reader. Before
    any body reader starts, closing the ordinary response remains synchronous.
    """
    state = getattr(response, _STATE_ATTRIBUTE, None)
    if isinstance(state, _ReaderState):
        if not state.done.is_set():
            abort_response(response)
        return
    response.close()


def bounded_request(call, max_seconds, cancel_event=None):
    """Obtain response headers within a total deadline without changing TLS.

    ``call`` performs the ordinary streaming Requests call, including its own
    connect/read timeouts, proxy policy and redirect policy. A callback stuck
    before returning its response cannot be forcibly stopped by Python; it
    occupies one of the eight shared reader slots and closes a late response.
    The caller returns promptly on timeout or cancellation nevertheless.
    """
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("HTTP request time limit must be finite and positive.")
    deadline = time.monotonic() + max_seconds
    stopped = threading.Event()
    accepted = threading.Event()
    abandoned = threading.Event()
    handoff = threading.Lock()
    finished = threading.Event()
    mailbox = queue.Queue(maxsize=1)

    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise HTTPReadCancelled("HTTP request was cancelled.")
        if time.monotonic() >= deadline:
            raise HTTPReadLimitError("HTTP request exceeded its total time limit.")

    check()
    slots = _READERS
    if not slots.acquire(blocking=False):
        raise HTTPReadLimitError("No bounded HTTP request reader is available.")

    def request_headers():
        response = None
        try:
            try:
                response = call()
                if not callable(getattr(response, "close", None)):
                    response = None
                    raise HTTPReadError("HTTP request returned an invalid response.")
                result = response
            except Exception as error:
                failure = None
                if isinstance(error, RequestsTimeout):
                    failure = "timeout"
                elif isinstance(error, RequestsConnectionError):
                    failure = "connection"
                result = HTTPReadError("HTTP request failed.", retryable_network_failure=failure)
            if not stopped.is_set():
                mailbox.put_nowait(result)
            while not accepted.is_set() and not stopped.is_set() and time.monotonic() < deadline:
                accepted.wait(_POLL_SECONDS)
        finally:
            with handoff:
                close_late_response = response is not None and not accepted.is_set()
                if close_late_response:
                    abandoned.set()
            try:
                if close_late_response:
                    response.close()
            except Exception:
                pass
            finally:
                slots.release()
                finished.set()

    worker = threading.Thread(target=request_headers, name="linux-ai-http-request", daemon=True)
    started = False
    try:
        worker.start()
        started = True
        while True:
            check()
            try:
                result = mailbox.get(timeout=min(_POLL_SECONDS, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            check()
            if isinstance(result, Exception):
                raise result
            with handoff:
                check()
                if abandoned.is_set():
                    raise HTTPReadLimitError("HTTP request exceeded its total time limit.")
                accepted.set()
            # The body reader uses the same pool. Release this worker's slot
            # before returning rather than causing a transient pool exhaustion.
            finished.wait(max(0, deadline - time.monotonic()))
            return result
    finally:
        stopped.set()
        if not started:
            slots.release()


def _raw_chunks(response, chunk_size):
    raw = _response_raw(response)
    read = getattr(raw, "read1", None)
    if not callable(read):
        read = getattr(raw, "read", None)
    if callable(read):
        while True:
            chunk = read(chunk_size, decode_content=False)
            if not chunk:
                return
            if not isinstance(chunk, bytes) or len(chunk) > chunk_size:
                raise HTTPReadError("HTTP response returned invalid bytes.")
            yield chunk
    else:
        # Third-party response shims may expose already-decoded iter_content.
        # Real Requests responses always use the bounded raw decoder above.
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                if not isinstance(chunk, bytes):
                    raise HTTPReadError("HTTP response returned invalid bytes.")
                yield chunk


def _decoded_chunks(response, chunk_size, max_bytes, max_wire_bytes, check):
    raw = _response_raw(response)
    raw_read = callable(getattr(raw, "read", None)) or callable(getattr(raw, "read1", None))
    encoding = "identity"
    if raw_read:
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("", "identity", "gzip", "deflate"):
        raise HTTPReadError("HTTP response uses an unsupported content encoding.")
    decoder = None
    if encoding == "gzip":
        decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
    deflate_prefix = bytearray()
    wire_bytes = 0
    body_bytes = 0
    members = 0

    for raw_chunk in _raw_chunks(response, chunk_size):
        check()
        wire_bytes += len(raw_chunk)
        if wire_bytes > max_wire_bytes:
            raise HTTPReadLimitError("HTTP response exceeded its wire byte limit.")
        pending = raw_chunk
        if encoding == "deflate" and decoder is None:
            # HTTP servers use both RFC 1950-wrapped and bare RFC 1951 deflate.
            # Keep only the first bounded input chunk until its header is known.
            deflate_prefix.extend(pending)
            if len(deflate_prefix) < 2:
                continue
            pending = bytes(deflate_prefix)
            deflate_prefix.clear()
            has_zlib_header = pending[0] & 15 == 8 and (pending[0] * 256 + pending[1]) % 31 == 0
            decoder = zlib.decompressobj(zlib.MAX_WBITS if has_zlib_header else -zlib.MAX_WBITS)
        if decoder is None:
            body_bytes += len(pending)
            if body_bytes > max_bytes:
                raise HTTPReadLimitError("HTTP response exceeded its body byte limit.")
            for offset in range(0, len(pending), chunk_size):
                yield pending[offset:offset + chunk_size]
            continue
        while True:
            check()
            if decoder.eof:
                if not pending:
                    break
                if encoding != "gzip":
                    raise HTTPReadError("HTTP response contains invalid compressed data.")
                members += 1
                if members > 64:
                    raise HTTPReadLimitError("HTTP response exceeded its compression member limit.")
                decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
            # max_length bounds each allocation, including compression bombs.
            decoded = decoder.decompress(pending, min(chunk_size, max_bytes - body_bytes + 1))
            pending = decoder.unused_data if decoder.eof else decoder.unconsumed_tail
            if decoded:
                body_bytes += len(decoded)
                if body_bytes > max_bytes:
                    raise HTTPReadLimitError("HTTP response exceeded its body byte limit.")
                yield decoded
            if not pending and len(decoded) < chunk_size:
                break
    check()
    if (decoder is not None and not decoder.eof) or (encoding == "deflate" and decoder is None):
        raise HTTPReadError("HTTP response contains incomplete compressed data.")


def iter_chunks(response, max_bytes, max_seconds, cancel_event=None, chunk_size=8192,
                max_wire_bytes=None):
    """Yield byte chunks within a total deadline and a decoded-body limit.

    The deadline includes waiting for socket data and decompression. Cancellation
    is observed within roughly 50 ms even while a transport read is blocked.
    Advertise ``Accept-Encoding: gzip, deflate`` on requests; other compression
    formats are rejected rather than decompressed with an unbounded allocation.
    The wire limit defaults to the decoded limit plus 64 KiB framing overhead.
    The body reader closes the response, including on early generator closure.
    """
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("HTTP body byte limit must be a positive integer.")
    if type(chunk_size) is not int or not 0 < chunk_size <= 65536:
        raise ValueError("HTTP chunk size must be between 1 and 65536 bytes.")
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("HTTP body time limit must be finite and positive.")
    if max_wire_bytes is None:
        max_wire_bytes = max_bytes + 65536
    if type(max_wire_bytes) is not int or max_wire_bytes <= 0:
        raise ValueError("HTTP wire byte limit must be a positive integer.")
    deadline = time.monotonic() + max_seconds
    state = _ReaderState()

    def check():
        if state.stop.is_set() or (cancel_event is not None and cancel_event.is_set()):
            raise HTTPReadCancelled("HTTP response reading was cancelled.")
        if time.monotonic() >= deadline:
            raise HTTPReadLimitError("HTTP response exceeded its total time limit.")

    check()
    slots = _READERS
    if not slots.acquire(blocking=False):
        raise HTTPReadLimitError("No bounded HTTP body reader is available.")
    existing = getattr(response, _STATE_ATTRIBUTE, None)
    if isinstance(existing, _ReaderState) and not existing.done.is_set():
        slots.release()
        raise HTTPReadError("HTTP response already has an active body reader.")
    try:
        setattr(response, _STATE_ATTRIBUTE, state)
    except Exception:
        slots.release()
        raise HTTPReadError("HTTP response does not support bounded reading.") from None
    mailbox = queue.Queue(maxsize=1)

    def send(item):
        while not state.stop.is_set():
            try:
                mailbox.put(item, timeout=_POLL_SECONDS)
                return
            except queue.Full:
                check()

    def read_body():
        try:
            for chunk in _decoded_chunks(response, chunk_size, max_bytes, max_wire_bytes, check):
                check()
                send(chunk)
            state.read_finished.set()
            send(_END)
        except (HTTPReadError, HTTPReadCancelled) as error:
            try:
                send(error)
            except (HTTPReadError, HTTPReadCancelled):
                pass
        except Exception:
            try:
                send(HTTPReadError("HTTP response could not be read."))
            except (HTTPReadError, HTTPReadCancelled):
                pass
        finally:
            state.read_finished.set()
            try:
                response.close()
            except Exception:
                pass
            finally:
                slots.release()
                state.done.set()

    reader = threading.Thread(target=read_body, name="linux-ai-http-body", daemon=True)
    started = False
    try:
        reader.start()
        started = True
        while True:
            check()
            try:
                item = mailbox.get(timeout=min(_POLL_SECONDS, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            check()
            if item is _END:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        abort_response(response)
        if not started:
            try:
                response.close()
            finally:
                slots.release()
                state.done.set()
