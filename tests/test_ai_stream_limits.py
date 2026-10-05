"""Local limits and cancellation: synthetic providers, no remote calls."""

import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests.exceptions import Timeout

from src.ai_client import (
    AIClient, AIRequestCancelled, AIResponseLimitError, MAX_RESPONSE_CHARS,
    _request_scope,
)
from src.stream_events import iter_sse_json, iter_bounded_lines, StreamLimitError, StreamReadCancelled


class ChunkResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 8192
        yield from self.chunks

    def close(self):
        self.closed = True


class StreamBoundsTests(unittest.TestCase):
    def test_line_without_newline_stops_at_byte_limit(self):
        response = ChunkResponse([b'x' * 8] * 10)
        with self.assertRaisesRegex(StreamLimitError, 'frame'):
            list(iter_bounded_lines(response, max_line_bytes=20))

    def test_total_bytes_include_comments_and_non_data_lines(self):
        response = ChunkResponse([b': keepalive\n'] * 10)
        with self.assertRaisesRegex(StreamLimitError, 'byte'):
            list(iter_sse_json(response, max_bytes=25))

    def test_multiline_frame_is_bounded_independent_of_line_size(self):
        response = ChunkResponse([b'data: {\ndata: "long":\ndata: "abcdef"}\n\n'])
        with self.assertRaisesRegex(StreamLimitError, 'frame'):
            list(iter_sse_json(response, max_frame_bytes=10))

    def test_event_limit_including_adjacent_frames(self):
        response = ChunkResponse([b'data: {}\ndata: {}\ndata: {}\n'])
        with self.assertRaisesRegex(StreamLimitError, 'event'):
            list(iter_sse_json(response, max_events=2))

    def test_multibyte_utf8_across_chunks_is_preserved(self):
        event = 'data: ' + json.dumps({'text': 'Olá'}, ensure_ascii=False) + '\n\n'
        encoded = event.encode()
        at = encoded.index('á'.encode()) + 1
        self.assertEqual(list(iter_sse_json(ChunkResponse([encoded[:at], encoded[at:]]))), [{'text': 'Olá'}])

    def test_stream_deadline_is_checked_after_read(self):
        with patch('src.stream_events.iter_chunks', return_value=(chunk for chunk in [b'data: {}\n\n'])), \
                patch('src.stream_events.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaisesRegex(StreamLimitError, 'time'):
                list(iter_sse_json(ChunkResponse([b'data: {}\n\n']), max_seconds=1))

    def test_cancel_before_stream_read_consumes_no_bytes(self):
        event = threading.Event()
        event.set()
        consumed = []
        def chunks():
            consumed.append(True)
            yield b'data: {}\n\n'
        with self.assertRaises(StreamReadCancelled):
            list(iter_sse_json(ChunkResponse(chunks()), cancel_event=event))
        self.assertEqual(consumed, [])


class CancelRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = AIClient.__new__(AIClient)
        self.client.session = Mock(headers={})
        self.client._local_settings = Mock(return_value={'base_url': ''})
        self.client._strict_local = Mock(return_value=True)

    def test_cancel_before_post_has_no_network_side_effect(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(AIRequestCancelled):
            self.client._make_request('https://provider.invalid/chat', {}, cancel_event=event)
        self.client.session.post.assert_not_called()

    def test_cancel_429_wait_closes_response_and_does_not_retry(self):
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = True
        response = Mock(status_code=429, headers={'Retry-After': '60'})
        self.client.session.post.return_value = response
        with self.assertRaises(AIRequestCancelled):
            self.client._make_request('https://provider.invalid/chat', {}, cancel_event=event)
        response.close.assert_called_once()
        event.wait.assert_called_once_with(60.0)
        self.assertEqual(self.client.session.post.call_count, 1)

    def test_cancel_network_backoff_does_not_make_second_post(self):
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = True
        self.client.session.post.side_effect = Timeout('synthetic timeout')
        with self.assertRaises(AIRequestCancelled):
            self.client._make_request('https://provider.invalid/chat', {}, cancel_event=event)
        event.wait.assert_called_once_with(4.0)
        self.assertEqual(self.client.session.post.call_count, 1)

    def test_cancel_while_waiting_for_headers_returns_and_closes_late_response(self):
        event, release, closed = threading.Event(), threading.Event(), threading.Event()
        response = Mock(status_code=200)
        response.close.side_effect = closed.set
        def post(*args, **kwargs):
            release.wait(2)
            return response
        self.client.session.post.side_effect = post
        timer = threading.Timer(.04, event.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(AIRequestCancelled):
                self.client._make_request('https://provider.invalid/chat', {}, cancel_event=event)
            self.assertLess(time.monotonic() - started, .4)
        finally:
            release.set()
            timer.join()
        self.assertTrue(closed.wait(.5))
        self.assertEqual(self.client.session.post.call_count, 1)

    def test_total_request_deadline_also_limits_retry_after_wait(self):
        response = Mock(status_code=429, headers={'Retry-After': '60'})
        self.client.session.post.return_value = response
        event = threading.Event()
        started = time.monotonic()
        with patch('src.ai_client.MAX_STREAM_SECONDS', .04):
            with _request_scope(event), self.assertRaises(AIResponseLimitError):
                self.client._make_request('https://provider.invalid/chat', {}, cancel_event=event)
        self.assertLess(time.monotonic() - started, .4)
        self.assertEqual(self.client.session.post.call_count, 1)
        response.close.assert_called_once()

    def test_cancel_blocked_stream_body_closes_provider_without_fallback(self):
        event, release, closed = threading.Event(), threading.Event(), threading.Event()
        def chunks():
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            release.wait(2)
            yield b'data: [DONE]\n\n'
        response = ChunkResponse(chunks())
        response.close = closed.set
        self.client.config = SimpleNamespace(get=lambda *args: False)
        self.client._make_request = Mock(return_value=response)
        self.client._finish_stream_usage = Mock()
        self.client._stream_chat_response = lambda *args: self.client._stream_openai_style(
            'test', 'https://provider.invalid/chat', {}, {}, 1, [])
        stream = self.client.stream_chat([], cancel_event=event)
        self.assertEqual(next(stream), 'first')
        timer = threading.Timer(.04, event.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(AIRequestCancelled):
                next(stream)
            self.assertLess(time.monotonic() - started, .4)
        finally:
            release.set()
            timer.join()
        self.assertTrue(closed.wait(.5))

    def test_chat_cancel_is_not_converted_to_none(self):
        self.client._chat_response = Mock(side_effect=AIRequestCancelled('cancelled'))
        with self.assertRaises(AIRequestCancelled):
            self.client.chat([{'role': 'user', 'content': 'hello'}], cancel_event=threading.Event())

    def test_stream_scope_is_restored_after_cancellation(self):
        outer = threading.Event()
        inner = threading.Event()
        def chunks():
            yield 'first'
            inner.set()
            yield 'second'
        self.client._stream_chat_response = Mock(side_effect=lambda *args: chunks())
        with _request_scope(outer):
            stream = self.client.stream_chat([], cancel_event=inner)
            self.assertEqual(next(stream), 'first')
            with self.assertRaises(AIRequestCancelled):
                next(stream)
            from src.ai_client import _request_event
            self.assertIs(_request_event(), outer)

    def test_oversized_plugin_stream_is_closed_and_cannot_reach_history(self):
        closed = []
        def chunks():
            try:
                yield 'x' * MAX_RESPONSE_CHARS
                yield 'extra'
            finally:
                closed.append(True)
        self.client._stream_chat_response = Mock(side_effect=lambda *args: chunks())
        stream = self.client.stream_chat([])
        self.assertEqual(len(next(stream)), MAX_RESPONSE_CHARS)
        with self.assertRaises(AIResponseLimitError):
            next(stream)
        self.assertEqual(closed, [True])

    def test_provider_text_limit_closes_response_and_bounds_usage_text(self):
        self.client.config = SimpleNamespace(get=lambda *args: False)
        response = ChunkResponse([b'data: {}\n\n'])
        self.client._make_request = Mock(return_value=response)
        self.client._iter_sse_openai_style = Mock(return_value=iter(['x' * MAX_RESPONSE_CHARS, 'extra']))
        self.client._finish_stream_usage = Mock()
        with self.assertRaises(AIResponseLimitError):
            list(self.client._stream_openai_style('test', 'https://provider.invalid/chat', {}, {}, 1, []))
        self.assertTrue(response.closed)
        self.assertEqual(len(self.client._finish_stream_usage.call_args.args[-1]), MAX_RESPONSE_CHARS)


if __name__ == '__main__':
    unittest.main()
