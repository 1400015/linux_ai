"""Shared request preparation: image, documents and limits in one place."""

import unittest

from src.ai_client import AIProviderError, AIImageRequestError
from src.document_context import MAX_DOCUMENT_CONTEXT_CHARS
from src.history_store import MAX_MESSAGE_CHARS
from src.i18n import set_language
from src.request_preparation import RequestPreparationError, prepare_request


class FakeClient:
    def __init__(self, provider='openrouter', ready=True, destination=('openrouter', 'openai/gpt-4o-mini')):
        self.provider = provider
        self.ready = ready
        self.destination = destination
        self.calls = []
        self.image_requests = []

    def active_provider(self, provider=None):
        self.calls.append(('active_provider', provider))
        if self.provider is None:
            raise AIProviderError('no provider')
        return self.provider

    def provider_ready(self, provider=None):
        self.calls.append(('provider_ready', provider))
        return self.ready

    def validate_image_request(self, images, provider=None, model=None):
        self.image_requests.append((tuple(images), provider, model))
        if isinstance(self.destination, Exception):
            raise self.destination
        return self.destination


class MinimalClient:
    """CLI test shims only expose what the CLI itself used before."""

    def __init__(self, ready=True):
        self.ready = ready

    def provider_ready(self, provider=None):
        return self.ready


class PrepareRequestTests(unittest.TestCase):
    def setUp(self):
        set_language('en')

    def test_empty_prompt_without_image_returns_none_and_touches_no_provider(self):
        client = FakeClient()
        self.assertIsNone(prepare_request('  ', ai_client=client))
        self.assertEqual(client.calls, [])

    def test_image_only_prompt_gets_the_explicit_default_text(self):
        client = FakeClient()
        image = object()
        plan = prepare_request('', ai_client=client, image=image, confirm_image=lambda *args: True)
        self.assertEqual(plan.text, 'Describe this image.')
        self.assertEqual(plan.images, (image,))
        self.assertEqual(plan.image_destination, ('openrouter', 'openai/gpt-4o-mini'))

    def test_message_limit_is_shared(self):
        client = FakeClient()
        with self.assertRaises(RequestPreparationError) as raised:
            prepare_request('x' * (MAX_MESSAGE_CHARS + 1), ai_client=client)
        self.assertIn('local history limit', str(raised.exception))
        self.assertEqual(client.calls, [])

    def test_route_uses_active_provider_for_display_and_caller_selection_for_readiness(self):
        client = FakeClient()
        plan = prepare_request('hello', ai_client=client, provider='openrouter')
        self.assertEqual(plan.provider, 'openrouter')
        self.assertTrue(plan.provider_ready)
        self.assertEqual(client.calls, [('active_provider', 'openrouter'), ('provider_ready', 'openrouter')])

    def test_route_probe_can_be_skipped_for_local_first_callers(self):
        client = FakeClient()
        plan = prepare_request('hello', ai_client=client, resolve_route=False)
        self.assertEqual(client.calls, [])
        self.assertFalse(plan.provider_ready)

    def test_client_without_active_provider_still_gets_readiness(self):
        plan = prepare_request('hello', ai_client=MinimalClient())
        self.assertIsNone(plan.provider)
        self.assertTrue(plan.provider_ready)

    def test_provider_error_during_resolution_marks_route_unready(self):
        client = FakeClient(provider=None, ready=False)
        plan = prepare_request('hello', ai_client=client)
        self.assertIsNone(plan.provider)
        self.assertFalse(plan.provider_ready)

    def test_image_review_receives_the_validated_destination_and_can_decline(self):
        client = FakeClient()
        image = object()
        seen = []
        plan = prepare_request(
            'Describe', ai_client=client, image=image,
            confirm_image=lambda attachment, provider, model: seen.append(
                (attachment, provider, model)) or False,
        )
        self.assertIsNone(plan)
        self.assertEqual(seen, [(image, 'openrouter', 'openai/gpt-4o-mini')])

    def test_image_destination_validation_failure_is_surfaced(self):
        client = FakeClient(destination=AIImageRequestError('Choose a confirmed image model'))
        with self.assertRaises(AIImageRequestError) as raised:
            prepare_request('Describe', ai_client=client, image=object(), confirm_image=lambda *args: True)
        self.assertIn('confirmed image model', str(raised.exception))

    def test_image_requires_a_ready_provider_before_review(self):
        client = FakeClient(ready=False)
        reviewed = []
        with self.assertRaises(RequestPreparationError) as raised:
            prepare_request('Describe', ai_client=client, image=object(),
                            confirm_image=lambda *args: reviewed.append(args) or True)
        self.assertIn('usable OpenRouter provider', str(raised.exception))
        self.assertEqual(reviewed, [])

    def test_documents_are_read_with_the_shared_limit(self):
        client = FakeClient()
        seen = []

        def reader(text, max_chars):
            seen.append((text, max_chars))
            return 'excerpt'

        plan = prepare_request('network', ai_client=client, documents_enabled=True,
                               read_document_context=reader,
                               confirm_documents=lambda context, provider: True)
        self.assertEqual(seen, [('network', MAX_DOCUMENT_CONTEXT_CHARS)])
        self.assertEqual(plan.document_context, 'excerpt')

    def test_empty_document_selection_is_a_user_facing_error(self):
        with self.assertRaises(RequestPreparationError) as raised:
            prepare_request('network', ai_client=FakeClient(), documents_enabled=True,
                            read_document_context=lambda text, max_chars: '',
                            confirm_documents=lambda *args: True)
        self.assertIn('No matching passages', str(raised.exception))

    def test_document_review_is_skipped_when_the_route_is_not_ready(self):
        reviewed = []
        plan = prepare_request('network', ai_client=FakeClient(ready=False), documents_enabled=True,
                               read_document_context=lambda text, max_chars: 'excerpt',
                               confirm_documents=lambda *args: reviewed.append(args) or True)
        self.assertEqual(reviewed, [])
        self.assertEqual(plan.document_context, 'excerpt')

    def test_declined_document_review_returns_none(self):
        plan = prepare_request('network', ai_client=FakeClient(), documents_enabled=True,
                               read_document_context=lambda text, max_chars: 'excerpt',
                               confirm_documents=lambda context, provider: False)
        self.assertIsNone(plan)

    def test_documents_without_a_reader_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            prepare_request('network', ai_client=FakeClient(), documents_enabled=True)


if __name__ == '__main__':
    unittest.main()
