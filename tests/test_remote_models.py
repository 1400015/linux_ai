"""Provider discovery uses fixed endpoints and bounded, unauthoritative data."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ai_client import AIClient
from src.remote_models import ModelDiscoveryError, discover_remote_models


class Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.body = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def iter_content(self, chunk_size):
        for byte in self.body:
            yield bytes([byte])


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
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
        ticks = iter([0.0, 0.0, 9.0])
        with self.assertRaises(ModelDiscoveryError):
            self.discover({'data': []}, clock=lambda: next(ticks))

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

    def test_backend_exceptions_do_not_disclose_keys(self):
        session = Mock()
        session.__enter__ = Mock(side_effect=RuntimeError('synthetic-key'))
        session.__exit__ = Mock()
        with self.assertRaises(ModelDiscoveryError) as error:
            discover_remote_models('groq', {'base_url': 'https://api.groq.com/openai/v1'},
                                   'synthetic-key', session_factory=lambda: session)
        self.assertNotIn('synthetic-key', str(error.exception))
