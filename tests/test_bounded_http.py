"""Real transport regressions for bounded HTTP reads and decompression."""

import gzip
import threading
import time
import tracemalloc
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests

from src import bounded_http
from src.bounded_http import (
    HTTPReadCancelled,
    HTTPReadError,
    HTTPReadLimitError,
    bounded_request,
    close_response,
    iter_chunks,
)


class BoundedHTTPTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = b'{"models": [' + b'"synthetic-model", ' * 42000 + b'"last"]}'
        cls.compressed = gzip.compress(cls.payload)
        cls.release = threading.Event()
        cls.first_write = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                encoding = None
                body = cls.payload
                if self.path == "/slow-headers":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    self.wfile.flush()
                    cls.first_write.set()
                    cls.release.wait(5)
                    try:
                        self.wfile.write(b"Content-Length: 4\r\n\r\nbody")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if self.path == "/gzip" or self.path == "/slow-gzip":
                    encoding, body = "gzip", cls.compressed
                elif self.path == "/deflate":
                    encoding, body = "deflate", zlib.compress(cls.payload)
                elif self.path == "/raw-deflate":
                    encoding, body = "deflate", zlib.compress(cls.payload)[2:-4]
                elif self.path == "/members":
                    encoding, body = "gzip", gzip.compress(b"one") + gzip.compress(b"two")
                elif self.path == "/bomb":
                    encoding, body = "gzip", gzip.compress(b"X" * (8 * 1024 * 1024))
                elif self.path == "/truncated":
                    encoding, body = "gzip", cls.compressed[:-4]
                elif self.path == "/unsupported":
                    encoding, body = "br", b"not-decoded"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                self.end_headers()
                try:
                    if self.path in ("/slow-gzip", "/slow-plain"):
                        prefix_length = 10 if self.path == "/slow-gzip" else 8192
                        self.wfile.write(body[:prefix_length])
                        self.wfile.flush()
                        cls.first_write.set()
                        cls.release.wait(5)
                        self.wfile.write(body[prefix_length:])
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=lambda: cls.server.serve_forever(poll_interval=0.05),
                                      daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.release.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(1)

    def setUp(self):
        self.release.clear()
        self.first_write.clear()

    def tearDown(self):
        self.release.set()

    def response(self, path):
        # Public-provider fixtures are unnecessary: these exercise real Requests
        # and urllib3 with synthetic data and no credentials.
        session = requests.Session()
        session.trust_env = False
        self.addCleanup(session.close)
        response = session.get(self.base_url + path, stream=True, timeout=5)
        self.addCleanup(close_response, response)
        return response

    def test_plain_gzip_and_deflate_are_read_in_chunks(self):
        for path in ("/plain", "/gzip", "/deflate", "/raw-deflate"):
            with self.subTest(path=path):
                start = time.monotonic()
                chunks = list(iter_chunks(self.response(path), len(self.payload), 2))
                self.assertEqual(b"".join(chunks), self.payload)
                self.assertTrue(all(0 < len(chunk) <= 8192 for chunk in chunks))
                self.assertLess(time.monotonic() - start, 1)

    def test_gzip_read_deadline_interrupts_incomplete_compressed_chunk(self):
        response = self.response("/slow-gzip")
        self.assertTrue(self.first_write.wait(1))
        start = time.monotonic()
        with self.assertRaisesRegex(HTTPReadLimitError, "time limit"):
            list(iter_chunks(response, len(self.payload), 0.1))
        close_response(response)
        self.assertLess(time.monotonic() - start, 0.5)
        state = getattr(response, bounded_http._STATE_ATTRIBUTE)
        self.assertTrue(state.done.wait(1), "socket shutdown should release the reader")

    def test_cancel_interrupts_a_blocked_gzip_read(self):
        response = self.response("/slow-gzip")
        cancelled = threading.Event()
        timer = threading.Timer(0.08, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        with self.assertRaises(HTTPReadCancelled):
            list(iter_chunks(response, len(self.payload), 5, cancelled))
        close_response(response)
        self.assertLess(time.monotonic() - start, 0.5)

    def test_closing_iterator_interrupts_its_incomplete_read(self):
        response = self.response("/slow-plain")
        chunks = iter_chunks(response, len(self.payload), 5)
        first = next(chunks)
        self.assertGreater(len(first), 0)
        self.assertEqual(first, self.payload[:len(first)])
        chunks.close()
        self.assertTrue(getattr(response, bounded_http._STATE_ATTRIBUTE).done.wait(1))

    def test_compression_bomb_stops_without_allocating_the_expanded_body(self):
        response = self.response("/bomb")
        # Request and fixture construction precede tracking; this specifically
        # guards the old decoded-iter_content allocation of the entire bomb.
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(HTTPReadLimitError, "body byte limit"):
                list(iter_chunks(response, 4096, 2, chunk_size=1024))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1024 * 1024)

    def test_wire_limit_is_checked_even_when_decoded_output_is_small(self):
        with self.assertRaisesRegex(HTTPReadLimitError, "wire byte limit"):
            list(iter_chunks(self.response("/gzip"), len(self.payload), 2, max_wire_bytes=4))

    def test_concatenated_gzip_members(self):
        self.assertEqual(b"".join(iter_chunks(self.response("/members"), 6, 1)), b"onetwo")

    def test_incomplete_and_unsupported_compression_are_rejected(self):
        for path in ("/truncated", "/unsupported"):
            with self.subTest(path=path), self.assertRaises(HTTPReadError):
                list(iter_chunks(self.response(path), len(self.payload), 2))

    def test_request_headers_deadline_and_late_response_cleanup(self):
        session = requests.Session()
        session.trust_env = False
        self.addCleanup(session.close)
        late_responses = []
        returned = threading.Event()

        def request():
            response = session.get(self.base_url + "/slow-headers", stream=True, timeout=5)
            late_responses.append(response)
            returned.set()
            return response

        start = time.monotonic()
        with self.assertRaisesRegex(HTTPReadLimitError, "request exceeded its total time limit"):
            bounded_request(request, 0.1)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertTrue(self.first_write.is_set())
        self.release.set()
        self.assertTrue(returned.wait(1))
        deadline = time.monotonic() + 1
        while not late_responses[0].raw.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(late_responses[0].raw.closed)

    def test_request_headers_cancel_and_normal_handoff(self):
        session = requests.Session()
        session.trust_env = False
        self.addCleanup(session.close)
        cancelled = threading.Event()
        timer = threading.Timer(0.08, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        with self.assertRaises(HTTPReadCancelled):
            bounded_request(lambda: session.get(self.base_url + "/slow-headers", stream=True, timeout=5),
                            5, cancelled)
        self.assertLess(time.monotonic() - start, 0.5)
        self.release.set()
        response = bounded_request(lambda: session.get(self.base_url + "/plain", stream=True, timeout=5), 1)
        self.assertEqual(b"".join(iter_chunks(response, len(self.payload), 1)), self.payload)


class BoundedHTTPShimTests(unittest.TestCase):
    def test_decoded_response_shim_and_mock_do_not_fabricate_raw_transport(self):
        response = Mock()
        response.iter_content.return_value = iter([b"one", b"two"])
        self.assertEqual(b"".join(iter_chunks(response, 6, 1)), b"onetwo")
        response.iter_content.assert_called_once_with(chunk_size=8192)

    def test_unexpected_transport_exception_is_sanitized(self):
        response = Mock()
        response.iter_content.side_effect = RuntimeError("synthetic-secret-that-must-not-be-reported")
        with self.assertRaises(HTTPReadError) as result:
            list(iter_chunks(response, 6, 1))
        self.assertNotIn("synthetic-secret", str(result.exception))

    def test_stuck_readers_are_globally_bounded_and_released_after_transport_returns(self):
        release = threading.Event()

        class BlockingResponse:
            def iter_content(self, chunk_size):
                release.wait(2)
                yield b"late"

            def close(self):
                pass

        responses = [BlockingResponse() for _ in range(3)]
        readers = threading.BoundedSemaphore(2)
        with patch.object(bounded_http, "_READERS", readers):
            try:
                for response in responses[:2]:
                    with self.assertRaisesRegex(HTTPReadLimitError, "time limit"):
                        list(iter_chunks(response, 4, 0.03))
                with self.assertRaisesRegex(HTTPReadLimitError, "reader is available"):
                    list(iter_chunks(responses[2], 4, 0.03))
            finally:
                release.set()
                for response in responses[:2]:
                    state = getattr(response, bounded_http._STATE_ATTRIBUTE)
                    self.assertTrue(state.done.wait(1))
            self.assertTrue(readers.acquire(blocking=False))
            self.assertTrue(readers.acquire(blocking=False))
            readers.release()
            readers.release()

    def test_slow_close_does_not_block_deadline_or_close_response(self):
        release_close = threading.Event()

        class SlowCloseResponse:
            def iter_content(self, chunk_size):
                yield b"body"

            def close(self):
                release_close.wait(2)

        response = SlowCloseResponse()
        try:
            self.assertEqual(b"".join(iter_chunks(response, 4, 1)), b"body")
            start = time.monotonic()
            close_response(response)
            self.assertLess(time.monotonic() - start, 0.1)
        finally:
            release_close.set()
            self.assertTrue(getattr(response, bounded_http._STATE_ATTRIBUTE).done.wait(1))

    def test_chunk_and_size_parameters_are_validated_before_starting_reader(self):
        for kwargs in ({"max_bytes": 0}, {"max_seconds": float("inf")},
                       {"max_seconds": 0}, {"chunk_size": 65537}, {"max_wire_bytes": 0}):
            options = {"max_bytes": 5, "max_seconds": 1}
            options.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                list(iter_chunks(Mock(), **options))

    def test_thread_start_failure_closes_response_and_releases_slot(self):
        response = Mock()
        readers = threading.BoundedSemaphore(1)
        with patch.object(bounded_http, "_READERS", readers), \
                patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start thread")):
            with self.assertRaises(RuntimeError):
                list(iter_chunks(response, 4, 1))
            self.assertTrue(readers.acquire(blocking=False))
            readers.release()
        response.close.assert_called_once()

    def test_request_errors_are_sanitized(self):
        def request():
            raise RuntimeError("https://synthetic-url/?secret=must-not-be-reported")

        with self.assertRaisesRegex(HTTPReadError, "HTTP request failed") as result:
            bounded_request(request, 1)
        self.assertNotIn("synthetic-url", str(result.exception))

    def test_request_network_failure_kind_retains_no_original_exception(self):
        for error_type, expected in ((requests.exceptions.Timeout, "timeout"),
                                     (requests.exceptions.ConnectionError, "connection")):
            def request():
                raise error_type("https://synthetic-url/?secret=must-not-be-reported")

            with self.subTest(error_type=error_type), self.assertRaises(HTTPReadError) as result:
                bounded_request(request, 1)
            self.assertEqual(result.exception.retryable_network_failure, expected)
            self.assertNotIn("synthetic-url", str(result.exception))
            self.assertIsNone(result.exception.__cause__)
            self.assertIsNone(result.exception.__context__)

    def test_stuck_header_callbacks_share_the_reader_cap(self):
        release = threading.Event()
        closed = threading.Event()
        response = Mock()
        response.close.side_effect = closed.set

        def request():
            release.wait(2)
            return response

        readers = threading.BoundedSemaphore(1)
        with patch.object(bounded_http, "_READERS", readers):
            try:
                with self.assertRaises(HTTPReadLimitError):
                    bounded_request(request, 0.03)
                with self.assertRaisesRegex(HTTPReadLimitError, "reader is available"):
                    list(iter_chunks(Mock(), 4, 1))
            finally:
                release.set()
                self.assertTrue(closed.wait(1))
                deadline = time.monotonic() + 1
                while not readers.acquire(blocking=False) and time.monotonic() < deadline:
                    time.sleep(0.01)
                readers.release()


if __name__ == "__main__":
    unittest.main()
