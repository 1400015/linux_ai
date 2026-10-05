"""Model discovery deadlines include headers, sockets and decoded gzip data."""

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import threading
import time
import unittest
from unittest.mock import patch


from src import remote_models
from src.remote_models import ModelDiscoveryError, discover_remote_models


class TestRemoteModelHTTP(unittest.TestCase):
    def setUp(self):
        self.body = json.dumps({'data': [{'id': 'fixture-chat'}], 'padding': 'x' * 764000}).encode()
        self.mode = 'plain'
        self.processes = []
        self.started = threading.Event()
        self.release = threading.Event()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                compressed = fixture.mode in ('gzip', 'gzip_header', 'gzip_drip')
                body = gzip.compress(fixture.body) if compressed else fixture.body
                if fixture.mode == 'gzip_header':
                    # gzip FNAME never terminates: no decoded output arrives.
                    body = b'\x1f\x8b\x08\x08' + b'\x00' * 6 + b'x' * 10000
                if fixture.mode == 'headers':
                    fixture.started.set()
                    fixture.release.wait(3)
                if fixture.mode == 'drip_headers':
                    fixture.started.set()
                    try:
                        for byte in b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n':
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            if fixture.release.wait(0.05):
                                return
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                if compressed:
                    self.send_header('Content-Encoding', 'gzip')
                self.end_headers()
                try:
                    if fixture.mode in ('body', 'gzip_header'):
                        self.wfile.write(body[:1] if fixture.mode == 'body' else body[:100])
                        self.wfile.flush()
                        fixture.started.set()
                        fixture.release.wait(3)
                        self.wfile.write(body[1:] if fixture.mode == 'body' else body[100:])
                    elif fixture.mode in ('plain_drip', 'gzip_drip'):
                        fixture.started.set()
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            if fixture.release.wait(0.05):
                                return
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.endpoint = 'http://127.0.0.1:{}/models'.format(self.server.server_port)

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        for process in self.processes:
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ProcessLookupError):
                os.kill(process.pid, 0)

    def discover(self, **kwargs):
        # The subprocess receives the ordinary proxy/CA environment. This
        # loopback fixture bypasses only its proxy, without disabling TLS.
        start_worker = remote_models._start_worker
        def start(*args):
            process, command_fd, command = start_worker(*args)
            self.processes.append(process)
            self.assertIn('-I', process.args)
            self.assertNotIn('synthetic-key', repr(process.args))
            return process, command_fd, command
        with patch('src.remote_models._endpoint', return_value=self.endpoint), \
                patch('src.remote_models._start_worker', side_effect=start), \
                patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
            return discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                          'synthetic-key', **kwargs)

    def test_large_plain_and_gzip_lists_finish_within_short_deadline(self):
        for mode in ('plain', 'gzip'):
            with self.subTest(mode=mode):
                self.mode = mode
                self.assertEqual(self.discover(timeout=2), ['fixture-chat'])

    def test_gzip_limit_applies_to_decoded_bytes(self):
        self.mode = 'gzip'
        with patch('src.remote_models.MAX_BYTES', 1024):
            with self.assertRaises(ModelDiscoveryError):
                self.discover(timeout=1)

    def test_stalled_headers_respect_total_deadline(self):
        self.mode = 'headers'
        began = time.monotonic()
        with self.assertRaises(ModelDiscoveryError):
            self.discover(timeout=0.5)
        self.assertTrue(self.started.is_set())
        self.assertLess(time.monotonic() - began, 1.4)

    def test_partial_body_cannot_extend_deadline(self):
        self.mode = 'body'
        began = time.monotonic()
        with self.assertRaises(ModelDiscoveryError):
            self.discover(timeout=0.5)
        self.assertTrue(self.started.is_set())
        self.assertLess(time.monotonic() - began, 1.4)

    def test_trickled_headers_and_bodies_cannot_reset_total_deadline(self):
        for mode in ('drip_headers', 'plain_drip', 'gzip_drip', 'gzip_header'):
            with self.subTest(mode=mode):
                self.mode = mode
                self.started.clear()
                began = time.monotonic()
                with self.assertRaises(ModelDiscoveryError):
                    self.discover(timeout=0.5)
                self.assertTrue(self.started.is_set())
                self.assertLess(time.monotonic() - began, 1.4)

    def test_worker_deadline_survives_absent_frontend_supervision(self):
        self.mode = 'drip_headers'
        read_fd, write_fd = os.pipe()
        command_fd = None
        process = None
        try:
            arguments = ('groq', self.endpoint, {'Accept': 'application/json'}, 0.5,
                         remote_models.requests.Session, time.monotonic)
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                process, command_fd, command = remote_models._start_worker(read_fd, write_fd, arguments)
            self.processes.append(process)
            os.close(write_fd)
            write_fd = None
            os.write(command_fd, command)
            os.close(command_fd)
            command_fd = None
            process.wait(timeout=2)
            self.assertTrue(self.started.is_set())
            self.assertEqual(process.returncode, -signal.SIGALRM)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
            if command_fd is not None:
                os.close(command_fd)
            if process is not None:
                remote_models._reap_worker(process)

    def test_cancel_returns_while_socket_waits_for_body(self):
        self.mode = 'body'
        cancelled = threading.Event()

        def cancel():
            if self.started.wait(2):
                cancelled.set()

        worker = threading.Thread(target=cancel, daemon=True)
        worker.start()
        began = time.monotonic()
        try:
            with self.assertRaises(ModelDiscoveryError) as error:
                self.discover(timeout=2, cancel_event=cancelled)
            self.assertIn('cancelled', str(error.exception))
            self.assertLess(time.monotonic() - began, 1.4)
        finally:
            worker.join(2)


if __name__ == '__main__':
    unittest.main()
