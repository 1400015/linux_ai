"""Provider discovery uses fixed endpoints and bounded, unauthoritative data."""

import json
import multiprocessing
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ai_client import AIClient
from src.remote_models import ModelDiscoveryError, discover_remote_models


class ProcessFlag:
    """Kill-safe fixture flag: no child can retain a shared lock on SIGTERM."""
    def __init__(self):
        self.value = multiprocessing.Value('b', 0, lock=False)
    def set(self):
        self.value.value = 1
    def is_set(self):
        return bool(self.value.value)
    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set() and time.monotonic() < deadline:
            time.sleep(0.005)
        return self.is_set()


class Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.body = json.dumps(payload).encode()
        self._sizes = multiprocessing.SimpleQueue()
        self._cached_sizes = []
        self.closed = ProcessFlag()
    @property
    def chunk_sizes(self):
        while self._sizes._reader.poll():
            self._cached_sizes.append(self._sizes.get())
        return self._cached_sizes
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def iter_content(self, chunk_size):
        self._sizes.put(chunk_size)
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]
    def close(self):
        self.closed.set()


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self._calls = multiprocessing.SimpleQueue()
        self._cached_calls = []
    @property
    def calls(self):
        while self._calls._reader.poll():
            self._cached_calls.append(self._calls.get())
        return self._cached_calls
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def get(self, url, **kwargs):
        self._calls.put((url, kwargs))
        return self.responses.pop(0)


class TestRemoteModels(unittest.TestCase):
    def discover(self, payload, provider='groq', base='https://api.groq.com/openai/v1', **kwargs):
        self.session = Session(Response(payload))
        return discover_remote_models(provider, {'base_url': base}, 'synthetic-key',
                                      session_factory=lambda: self.session, **kwargs)

    def test_openai_shapes_filter_speech_and_sort_deduplicate(self):
        result = self.discover({'data': [{'id': 'z-chat'}, {'id': 'whisper-large'}, {'id': 'a-chat'}, {'id': 'z-chat'}]})
        self.assertEqual(result, ['a-chat', 'z-chat'])
        url, request = self.session.calls[0]
        self.assertEqual(url, 'https://api.groq.com/openai/v1/models')
        self.assertEqual(request['headers']['Authorization'], 'Bearer synthetic-key')
        self.assertFalse(request['allow_redirects'])
        self.assertTrue(request['stream'])
        self.assertIs(request['auth'](request), request)
        self.assertEqual(request['headers']['Accept-Encoding'], 'gzip, deflate')

    def test_google_uses_header_and_generate_content_filter(self):
        result = self.discover({'models': [
            {'name': 'models/chat', 'supportedGenerationMethods': ['generateContent']},
            {'name': 'models/embed', 'supportedGenerationMethods': ['embedContent']}]},
            'google_ai_studio', 'https://generativelanguage.googleapis.com/v1')
        self.assertEqual(result, ['chat'])
        _, request = self.session.calls[0]
        self.assertEqual(request['headers']['x-goog-api-key'], 'synthetic-key')
        self.assertNotIn('synthetic-key', repr(request['params']))

    def test_anthropic_headers_and_pagination_do_not_follow_server_urls(self):
        session = Session(Response({'data': [{'id': 'chat-1'}], 'has_more': True, 'last_id': 'cursor'}),
                          Response({'data': [{'id': 'chat-2'}], 'has_more': False}))
        result = discover_remote_models('anthropic', {'base_url': 'https://api.anthropic.com/v1'},
                                        'synthetic-key', session_factory=lambda: session)
        self.assertEqual(result, ['chat-1', 'chat-2'])
        self.assertEqual(session.calls[1][1]['params'], {'after_id': 'cursor'})
        self.assertEqual(session.calls[0][1]['headers']['anthropic-version'], '2023-06-01')
        self.assertEqual(session.calls[0][0], session.calls[1][0])

    def test_cohere_chat_endpoint_filter(self):
        result = self.discover({'models': [{'name': 'chat', 'endpoints': ['chat']},
                                          {'name': 'embed', 'endpoints': ['embed']}]},
                               'cohere', 'https://api.cohere.ai/v1')
        self.assertEqual(result, ['chat'])
        self.assertEqual(self.session.calls[0][1]['params'], {'endpoint': 'chat'})

    def test_cohere_v2_is_not_advertised_by_v1_chat_client(self):
        factory = Mock()
        with self.assertRaises(ModelDiscoveryError):
            discover_remote_models('cohere', {'base_url': 'https://api.cohere.ai/v2'},
                                   'synthetic-key', session_factory=factory)
        factory.assert_not_called()

    def test_model_body_uses_chunks_and_closes_response(self):
        response = Response({'data': [{'id': 'chat'}], 'padding': 'x' * 40000})
        session = Session(response)
        result = discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                        'synthetic-key', session_factory=lambda: session)
        self.assertEqual(result, ['chat'])
        self.assertEqual(response.chunk_sizes, [8192])
        self.assertTrue(response.closed.wait(1))

    def test_byte_limit_applies_across_pages(self):
        responses = [Response({'data': [{'id': 'chat-1'}], 'has_more': True, 'last_id': 'next'}),
                     Response({'data': [{'id': 'chat-2'}], 'has_more': False})]
        session = Session(*responses)
        with patch('src.remote_models.MAX_BYTES', len(responses[0].body) + len(responses[1].body) - 1):
            with self.assertRaises(ModelDiscoveryError):
                discover_remote_models('anthropic', {'base_url': 'https://api.anthropic.com/v1'},
                                       'synthetic-key', session_factory=lambda: session)
        self.assertEqual(len(session.calls), 2)

    def test_cancelled_before_request_has_no_network(self):
        cancelled = threading.Event()
        cancelled.set()
        session = Session(Response({'data': []}))
        with self.assertRaises(ModelDiscoveryError) as error:
            discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                   'synthetic-key', session_factory=lambda: session,
                                   cancel_event=cancelled)
        self.assertIn('cancelled', str(error.exception))
        self.assertEqual(session.calls, [])

    def test_deadline_covers_connection_and_headers(self):
        released = ProcessFlag()
        started = ProcessFlag()
        response = Response({'data': []})
        session = Session()
        child_pid = multiprocessing.Value('i', 0, lock=False)
        def get(*args, **kwargs):
            child_pid.value = os.getpid()
            started.set()
            released.wait(2)
            return response
        session.get = get
        began = time.monotonic()
        try:
            with self.assertRaises(ModelDiscoveryError):
                discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                       'synthetic-key', session_factory=lambda: session, timeout=0.1)
            self.assertTrue(started.is_set())
            self.assertLess(time.monotonic() - began, 0.8)
        finally:
            released.set()
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid.value, 0)

    def test_cancel_interrupts_blocked_request_and_reaps_worker(self):
        cancelled = threading.Event()
        released, started = (ProcessFlag() for _ in range(2))
        response = Response({'data': []})
        session = Session()
        child_pid = multiprocessing.Value('i', 0, lock=False)
        def get(*args, **kwargs):
            child_pid.value = os.getpid()
            started.set()
            released.wait(2)
            return response
        session.get = get
        def cancel():
            if started.wait(1):
                cancelled.set()
        worker = threading.Thread(target=cancel, daemon=True)
        worker.start()
        began = time.monotonic()
        try:
            with self.assertRaises(ModelDiscoveryError) as error:
                discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                       'synthetic-key', session_factory=lambda: session,
                                       cancel_event=cancelled)
            self.assertIn('cancelled', str(error.exception))
            self.assertLess(time.monotonic() - began, 0.8)
        finally:
            released.set()
            worker.join(1)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid.value, 0)

    def test_repeated_uncooperative_requests_leave_no_children_or_fds(self):
        session = Session()
        child_pid = multiprocessing.Value('i', 0, lock=False)
        def get(*args, **kwargs):
            child_pid.value = os.getpid()
            while True:
                time.sleep(1)  # Ignore every Requests timeout deliberately.
        session.get = get
        before_fds = len(os.listdir('/proc/self/fd'))
        before_children = {child.pid for child in multiprocessing.active_children()}
        for _ in range(12):  # More than the old eight-reader thread pool.
            with self.assertRaises(ModelDiscoveryError):
                discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                       'synthetic-key', session_factory=lambda: session, timeout=0.03)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid.value, 0)
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before_children)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before_fds)

    def test_timeout_validation_rejects_non_finite_values(self):
        for timeout in (float('nan'), float('inf'), True, 0, 31):
            with self.subTest(timeout=timeout), self.assertRaises(ModelDiscoveryError):
                self.discover({'data': []}, timeout=timeout)

    def test_mistral_and_openrouter_capabilities(self):
        for provider, base in [('mistral', 'https://api.mistral.ai/v1'), ('openrouter', 'https://openrouter.ai/api/v1')]:
            result = self.discover({'data': [
                {'id': 'chat', 'capabilities': {'completion_chat': True}},
                {'id': 'no-chat', 'capabilities': {'completion_chat': False}},
                {'id': 'image-only', 'architecture': {'output_modalities': ['image']}}]}, provider, base)
            self.assertEqual(result, ['chat'])

    def test_unknown_or_untrusted_endpoint_is_rejected_without_request(self):
        for base in ('http://api.groq.com/openai/v1', 'https://evil.example/openai/v1',
                     'https://user:password@api.groq.com/openai/v1', 'https://api.groq.com:444/openai/v1',
                     'https://api.groq.com/openai/v1?key=bad', 'https://api.groq.com/openai/v1/../v1'):
            factory = Mock()
            with self.assertRaises(ModelDiscoveryError):
                discover_remote_models('groq', {'base_url': base}, 'synthetic', session_factory=factory)
            factory.assert_not_called()

    def test_redirect_is_not_followed_or_exposed(self):
        session = Session(Response({'secret': 'synthetic'}, 302))
        with self.assertRaises(ModelDiscoveryError) as error:
            discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                   'synthetic-key', session_factory=lambda: session)
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn('synthetic', str(error.exception))

    def test_byte_item_and_page_limits(self):
        with patch('src.remote_models.MAX_BYTES', 10):
            with self.assertRaises(ModelDiscoveryError):
                self.discover({'data': [{'id': 'chat'}]})
        with patch('src.remote_models.MAX_MODELS', 1):
            with self.assertRaises(ModelDiscoveryError):
                self.discover({'data': [{'id': 'chat'}, {'id': 'chat2'}]})
        with patch('src.remote_models.MAX_PAGES', 1):
            with self.assertRaises(ModelDiscoveryError):
                self.discover({'models': [], 'nextPageToken': 'next'}, 'google_ai_studio',
                              'https://generativelanguage.googleapis.com/v1')

    def test_overall_deadline_checked_while_reading(self):
        elapsed = multiprocessing.Value('d', 0, lock=False)
        response = Response({'data': []})
        original = response.iter_content
        def read(chunk_size):
            for chunk in original(chunk_size):
                elapsed.value = 9.0
                yield chunk
        response.iter_content = read
        session = Session(response)
        with self.assertRaises(ModelDiscoveryError) as error:
            discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                   'synthetic-key', session_factory=lambda: session,
                                   clock=lambda: elapsed.value)
        self.assertIn('deadline', str(error.exception))

    def test_malformed_and_repeating_pagination_are_errors(self):
        for payload in ([], {'data': 'bad'}, {'data': [{'id': 'bad\nidentifier'}]}):
            with self.assertRaises(ModelDiscoveryError):
                self.discover(payload)
        payload = {'data': [], 'has_more': True, 'last_id': 'repeat'}
        session = Session(Response(payload), Response(payload))
        with self.assertRaises(ModelDiscoveryError):
            discover_remote_models('anthropic', {'base_url': 'https://api.anthropic.com/v1'},
                                   'synthetic', session_factory=lambda: session)

    def test_remote_listing_is_blocked_by_offline_and_local_modes(self):
        client = object.__new__(AIClient)
        for mode in ('offline', 'local'):
            client.config = SimpleNamespace(get=lambda key, default=None: mode)
            with patch('src.remote_models.discover_remote_models') as discovery:
                with self.assertRaises(ModelDiscoveryError):
                    client.list_remote_models('groq')
                discovery.assert_not_called()

    def test_client_forwards_optional_listing_cancellation(self):
        client = object.__new__(AIClient)
        values = {'assistance.mode': 'remote',
                  'api.providers': {'groq': {'base_url': 'https://api.groq.com/openai/v1'}}}
        client.config = SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                                        get_api_key=lambda provider: 'synthetic-key')
        cancelled = threading.Event()
        with patch('src.remote_models.discover_remote_models', return_value=['fixture-chat']) as discovery:
            self.assertEqual(client.list_remote_models('groq', cancel_event=cancelled), ['fixture-chat'])
        self.assertIs(discovery.call_args.kwargs['cancel_event'], cancelled)

    def test_backend_exceptions_do_not_disclose_keys(self):
        session = Mock()
        session.__enter__ = Mock(side_effect=RuntimeError('synthetic-key'))
        session.__exit__ = Mock()
        with self.assertRaises(ModelDiscoveryError) as error:
            discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                   'synthetic-key', session_factory=lambda: session)
        self.assertNotIn('synthetic-key', str(error.exception))
