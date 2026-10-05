"""Bounded SSE framing independent of any provider or GTK."""

import json
import time
from .bounded_http import iter_chunks, HTTPReadLimitError, HTTPReadCancelled


MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 256 * 1024
MAX_STREAM_EVENTS = 8192
MAX_STREAM_SECONDS = 300
MAX_RESPONSE_CHARS = 128 * 1024


class StreamLimitError(ValueError):
    """The provider exceeded a local response limit."""


class StreamReadCancelled(Exception):
    """The caller cancelled reading a provider response."""


def iter_bounded_lines(response, cancel_event=None, max_bytes=MAX_STREAM_BYTES,
                       max_line_bytes=MAX_FRAME_BYTES,
                       max_seconds=MAX_STREAM_SECONDS):
    """Bound lines even when the provider never sends a newline.

    The bounded HTTP reader also interrupts a blocked read and limits gzip
    expansion. The deadline and cancellation are checked between every line.
    The caller owns closing the response in its finally block.
    """
    deadline = time.monotonic() + max_seconds
    total = 0

    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise StreamReadCancelled("AI request cancelled")
        if time.monotonic() >= deadline:
            raise StreamLimitError("Provider stream exceeded the total time limit")

    # Provider fixtures and third-party response shims may only expose
    # iter_lines. Real requests responses take the bounded chunk path.
    if not callable(getattr(response, "iter_content", None)):
        for raw in response.iter_lines():
            check()
            raw = raw.encode("utf-8") if isinstance(raw, str) else raw
            total += len(raw) + 1
            if total > max_bytes or len(raw) > max_line_bytes:
                raise StreamLimitError("Provider stream exceeded the local byte limit")
            yield raw
        check()
        return

    pending = bytearray()
    chunks = iter_chunks(response, max_bytes, max_seconds, cancel_event, chunk_size=8192)
    try:
        while True:
            check()
            try:
                chunk = next(chunks)
            except StopIteration:
                break
            check()
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise StreamLimitError("Provider stream exceeded the local byte limit")
            pending.extend(chunk)
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    if len(pending) > max_line_bytes:
                        raise StreamLimitError("Provider stream frame exceeded the local byte limit")
                    break
                if newline > max_line_bytes:
                    raise StreamLimitError("Provider stream frame exceeded the local byte limit")
                line = bytes(pending[:newline]).rstrip(b"\r")
                del pending[:newline + 1]
                yield line
            check()
    except HTTPReadCancelled as error:
        raise StreamReadCancelled("AI request cancelled") from error
    except HTTPReadLimitError as error:
        raise StreamLimitError(str(error)) from error
    finally:
        chunks.close()
    if pending:
        yield bytes(pending).rstrip(b"\r")


def iter_sse_json(response, cancel_event=None, max_frame_bytes=MAX_FRAME_BYTES,
                  max_events=MAX_STREAM_EVENTS, **line_limits):
    fields = []
    field_bytes = 0
    events = 0

    def parse(value):
        nonlocal events
        events += 1
        if events > max_events:
            raise StreamLimitError("Provider stream exceeded the local event limit")
        return json.loads(value)

    for raw in iter_bounded_lines(response, cancel_event, **line_limits):
        line = raw.decode('utf-8', errors='replace')
        if not line:
            if fields:
                value = '\n'.join(fields)
                fields = []
                field_bytes = 0
                if value.strip() == '[DONE]':
                    return
                data = parse(value)
                if isinstance(data, dict):
                    yield data
            continue
        if not line.startswith('data:'):
            continue
        value = line[5:]
        if value.startswith(' '):
            value = value[1:]
        # Accept adjacent complete JSON events while retaining multiline SSE.
        if fields:
            previous = '\n'.join(fields)
            try:
                data = json.loads(previous)
            except json.JSONDecodeError:
                pass
            else:
                fields = []
                field_bytes = 0
                events += 1
                if events > max_events:
                    raise StreamLimitError("Provider stream exceeded the local event limit")
                if isinstance(data, dict):
                    yield data
        if value.strip() == '[DONE]':
            return
        field_bytes += len(value.encode('utf-8')) + 1
        if field_bytes > max_frame_bytes:
            raise StreamLimitError("Provider SSE frame exceeded the local byte limit")
        fields.append(value)
    if fields:
        data = parse('\n'.join(fields))
        if isinstance(data, dict):
            yield data
