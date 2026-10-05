"""Real HTTP error/JSON bounds using synthetic loopback responses only."""

import gzip
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests

from src import ai_client
from src.ai_client import AIClient, AIProviderError, AIRequestCancelled, AIResponseLimitError
from src.bounded_http import close_response


class AIHTTPBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = cls
        cls.release = threading.Event()
        cls.first_write = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                fixture.calls.append(self.path)
                body, status, compressed, stalled = fixture.body, fixture.status, fixture.compressed, fixture.stalled
                wire = gzip.compress(body) if compressed else body
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(wire)))
                if compressed:
                    self.send_header('Content-Encoding', 'gzip')
                self.end_headers()
                try:
                    if stalled:
                        count = 10 if compressed else fixture.prefix_bytes
                        self.wfile.write(wire[:count])
                        self.wfile.flush()
                        fixture.first_write.set()
                        fixture.release.wait(3)
                        self.wfile.write(wire[count:])
                    else:
                        self.wfile.write(wire)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=lambda: cls.server.serve_forever(poll_interval=0.05), daemon=True)
        cls.thread.start()
        cls.endpoint = 'http://127.0.0.1:{}/chat'.format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.release.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(1)

    def setUp(self):
        cls = type(self)
        cls.body = b'{"error":{"message":"invalid model"}}'
        cls.status = 400
        cls.compressed = cls.stalled = False
        cls.prefix_bytes = 1
        cls.calls = []
        cls.release.clear()
        cls.first_write.clear()
        self.client = AIClient.__new__(AIClient)
        self.client.session = requests.Session()
        self.client.session.trust_env = False
        self.client._local_settings = Mock(return_value={'base_url': ''})
        self.client._strict_local = Mock(return_value=True)
        self.client._record_usage = Mock()
        self.addCleanup(self.client.session.close)

    def tearDown(self):
        self.release.set()
        # Allow a blocked handler/reader to finish before changing fixture data.
        time.sleep(0.01)

    def test_small_error_body_remains_useful_without_reading_response_text(self):
        class NoTextResponse(requests.Response):
            @property
            def text(self):
                raise AssertionError('Error logging must not read response.text')
        response = NoTextResponse()
        response.status_code, response.url = 400, self.endpoint
        response._content, response._content_consumed = self.body, True
        self.client.session.post = Mock(return_value=response)
        with self.assertRaises(AIProviderError) as error:
            self.client._make_request(self.endpoint, {}, stream=True)
        self.assertIn('invalid model', str(error.exception))
        self.assertIs(error.exception.response, response)
        self.client.session.post.assert_called_once()

    def test_error_labels_and_bare_tokens_are_redacted_in_logs_and_feedback(self):
        secrets = ['SYNTHETIC_DB_PASS_77', 'SYNTHETIC_SECRET_KEY_77', 'SYNTHETIC_AUTH_77',
                   'SYNTHETIC_PGPASS_77', 'xoxb-1234567890-synthetic-token']
        type(self).body = json.dumps({'error': {'message': 'invalid model'},
                                     'DB_PASSWORD': secrets[0], 'SECRET_KEY': secrets[1],
                                     'AUTH_TOKEN': secrets[2], 'PGPASSWORD': secrets[3],
                                     'detail': secrets[4]}).encode()
        with self.assertLogs('src.ai_client', level='ERROR') as logs, self.assertRaises(AIProviderError) as error:
            self.client._make_request(self.endpoint, {}, stream=True)
        visible = str(error.exception) + '\n' + '\n'.join(logs.output)
        self.assertIn('invalid model', visible)
        self.assertTrue(all(secret not in visible for secret in secrets))
        self.assertEqual(len(self.calls), 1)

    def test_oversized_error_body_is_omitted_without_waiting_for_completion(self):
        type(self).body = b'{"error":"invalid model","padding":"' + b'x' * 1000000 + b'"}'
        type(self).stalled = True
        type(self).prefix_bytes = 32768
        began = time.monotonic()
        with self.assertRaises(AIProviderError) as error:
            self.client._make_request(self.endpoint, {}, stream=True)
        self.assertIn('400', str(error.exception))
        self.assertNotIn('invalid model', str(error.exception))
        self.assertLess(len(str(error.exception)), 1000)
        self.assertLess(time.monotonic() - began, 0.5)

    def test_gzip_error_expansion_stays_bounded_and_omits_incomplete_body(self):
        type(self).compressed = True
        type(self).body = b'{"error":"invalid model","padding":"' + b'x' * (8 * 1024 * 1024) + b'"}'
        with self.assertRaises(AIProviderError) as error:
            self.client._make_request(self.endpoint, {}, stream=True)
        self.assertIn('400', str(error.exception))
        self.assertNotIn('invalid model', str(error.exception))
        self.assertLess(len(str(error.exception)), 1000)

    def test_truncated_bare_token_does_not_escape_redaction(self):
        # A JWT whose payload crosses the read limit has no signature in the
        # prefix. Redacting that prefix alone cannot recognize the full token.
        token = 'eyJhbGciOiJIUzI1NiJ9.' + 'eyJ' + 'A' * 20000 + '.synthetic_signature'
        type(self).body = token.encode()
        with self.assertLogs('src.ai_client', level='ERROR') as logs, self.assertRaises(AIProviderError) as error:
            self.client._make_request(self.endpoint, {}, stream=True)
        visible = str(error.exception) + '\n' + '\n'.join(logs.output)
        self.assertIn('400', visible)
        self.assertNotIn('eyJhbGciOiJIUzI1NiJ9', visible)
        self.assertNotIn('synthetic_signature', visible)

    def test_stalled_error_diagnostic_has_short_deadline(self):
        type(self).stalled = True
        began = time.monotonic()
        with patch.object(ai_client, 'MAX_ERROR_BODY_SECONDS', 0.06), self.assertRaises(AIProviderError):
            self.client._make_request(self.endpoint, {}, stream=True)
        self.assertLess(time.monotonic() - began, 0.4)
        self.assertEqual(len(self.calls), 1)

    def cancel_at_body(self, event):
        def cancel():
            if self.first_write.wait(1):
                event.set()
        timer = threading.Thread(target=cancel, daemon=True)
        timer.start()
        self.addCleanup(lambda: timer.join(1))

    def test_cancel_during_gzip_error_header_propagates_without_retry(self):
        type(self).stalled = type(self).compressed = True
        event = threading.Event()
        self.cancel_at_body(event)
        began = time.monotonic()
        with self.assertRaises(AIRequestCancelled):
            self.client._make_request(self.endpoint, {}, stream=True, cancel_event=event)
        self.assertLess(time.monotonic() - began, 0.4)
        self.assertEqual(len(self.calls), 1)

    def test_nonstream_plain_and_gzip_json_preserve_unicode_and_usage(self):
        type(self).status = 200
        payload = {'choices': [{'message': {'content': 'Olá, ação'}}],
                   'usage': {'prompt_tokens': 2, 'completion_tokens': 3}}
        type(self).body = json.dumps(payload, ensure_ascii=False).encode()
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                type(self).compressed = compressed
                result = self.client._chat_openai_style('fixture', self.endpoint, {}, {}, 1)
                self.assertEqual(result, 'Olá, ação')
                self.client._record_usage.assert_called_with('fixture', payload)

    def test_oversized_nonstream_gzip_json_fails_before_parsing(self):
        type(self).status, type(self).compressed = 200, True
        type(self).body = b'{"choices":[],"padding":"' + b'x' * (3 * 1024 * 1024) + b'"}'
        with patch.object(requests.Response, 'json', side_effect=AssertionError('Unbounded JSON parsed')):
            with self.assertRaises(AIResponseLimitError):
                self.client._chat_openai_style('fixture', self.endpoint, {}, {}, 1)
        self.client._record_usage.assert_not_called()
        self.assertEqual(len(self.calls), 1)

    def test_cancel_stalled_nonstream_json_propagates_and_does_not_retry(self):
        type(self).status, type(self).stalled = 200, True
        type(self).body = b'{"choices":[]}'
        event = threading.Event()
        self.cancel_at_body(event)
        with self.assertRaises(AIRequestCancelled):
            self.client._make_request(self.endpoint, {}, cancel_event=event)
        self.assertEqual(len(self.calls), 1)

    def test_nonstream_body_deadline_applies_without_cancel_event(self):
        type(self).status, type(self).stalled = 200, True
        type(self).body = b'{"choices":[]}'
        began = time.monotonic()
        with self.assertRaises(AIResponseLimitError):
            self.client._make_request(self.endpoint, {}, timeout=0.06)
        self.assertLess(time.monotonic() - began, 0.4)
        self.assertEqual(len(self.calls), 1)

    def test_stream_error_feedback_redacts_body_secrets(self):
        class Response:
            def iter_content(self, chunk_size):
                yield b'data: {"error":{"message":"AUTH_TOKEN=SYNTHETIC_STREAM_SECRET"}}\n\n'
            def close(self):
                pass
        response = Response()
        try:
            with self.assertRaises(AIProviderError) as error:
                list(AIClient._iter_sse_openai_style(response))
            self.assertNotIn('SYNTHETIC_STREAM_SECRET', str(error.exception))
            self.assertIn('[redacted]', str(error.exception))
        finally:
            close_response(response)


if __name__ == '__main__':
    unittest.main()
