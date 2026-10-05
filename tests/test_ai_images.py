"""Mocked multimodal HTTP contracts: no credentials, network or OCR."""

import io
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
from requests import Response

from src.ai_client import (
    AIClient, AIProviderError, AIImageRequestError, AIUnsupportedImageProvider,
    AIUnsupportedImageModel, AIRequestCancelled, _REQUEST_CONTEXT,
)
from src.image_attachments import prepare_image_bytes


class ImageResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.closed_event = threading.Event()

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True
        self.closed_event.set()


def client_for_images(mode="remote", provider="openrouter", model="openai/gpt-4o-mini",
                      base_url="https://openrouter.ai/api/v1"):
    options = {"model": model, "base_url": base_url, "timeout": 1}
    values = {"assistance.mode": mode, "api.default_provider": provider,
              "api.providers": {provider: options}}
    client = AIClient.__new__(AIClient)
    client.config = SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                                    get_api_key=lambda provider: "synthetic-test-key")
    client._record_usage = Mock()
    client._update_token_usage = Mock()
    client._count_tokens = Mock(return_value=1)
    client._local_settings = Mock(return_value={"base_url": ""})
    client._strict_local = Mock(return_value=True)
    client.session = Mock(headers={})
    return client


class AIImageTests(unittest.TestCase):
    def setUp(self):
        self.client = client_for_images()
        output = io.BytesIO()
        with Image.new("RGB", (8, 5), "blue") as image:
            image.save(output, format="PNG")
        self.attachment = prepare_image_bytes(output.getvalue(), "private-local-name.png")
        self.messages = [{"role": "system", "content": "Help with Linux"},
                         {"role": "user", "content": "old question"},
                         {"role": "assistant", "content": "old answer"},
                         {"role": "user", "content": "What does this image show?"}]

    def test_nonstream_image_payload_and_original_history_unchanged(self):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "A blue image"}}],
                                      "usage": {"prompt_tokens": 12, "completion_tokens": 5}}
        self.client._make_request = Mock(return_value=response)
        before = json.dumps(self.messages)
        result = self.client.chat(self.messages, images=[self.attachment])
        self.assertEqual(result, "A blue image")
        args = self.client._make_request.call_args.args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        payload = args[1]
        self.assertEqual(payload["messages"][:-1], self.messages[:-1])
        self.assertEqual(payload["messages"][-1]["content"], [
            {"type": "text", "text": self.messages[-1]["content"]},
            {"type": "image_url", "image_url": {"url": self.attachment.data_url}}])
        self.assertEqual(payload["provider"], {"allow_fallbacks": False})
        self.assertNotIn(self.attachment.filename, json.dumps(payload))
        self.assertEqual(json.dumps(self.messages), before)
        response.close.assert_called_once()
        self.client._record_usage.assert_called_once()

    def test_stream_payload_and_real_usage(self):
        usage = {"prompt_tokens": 123, "completion_tokens": 4}
        response = ImageResponse([
            b'data: {"choices":[{"delta":{"content":"A blue "}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"image"}}]}\n\n',
            ("data: " + json.dumps({"usage": usage, "choices": []}) + "\n\n").encode(),
            b'data: [DONE]\n\n'])
        self.client._make_request = Mock(return_value=response)
        self.assertEqual(''.join(self.client.stream_chat(self.messages, images=(self.attachment,))), "A blue image")
        self.assertTrue(response.closed_event.wait(.5))
        call = self.client._make_request.call_args
        self.assertTrue(call.kwargs["stream"])
        self.assertTrue(call.args[1]["stream"])
        self.assertEqual(call.args[1]["messages"][-1]["content"][1]["image_url"]["url"], self.attachment.data_url)
        self.client._record_usage.assert_called_once_with("openrouter", {"usage": usage})

    def test_stream_does_not_estimate_image_tokens_from_text(self):
        response = ImageResponse([b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n',
                                  b'data: [DONE]\n\n'])
        self.client._make_request = Mock(return_value=response)
        self.assertEqual(list(self.client.stream_chat(self.messages, images=[self.attachment])), ["answer"])
        self.client._update_token_usage.assert_not_called()

    def test_old_chat_and_stream_call_signatures_unchanged(self):
        self.client._chat_response = Mock(return_value="plain")
        self.client._stream_chat_response = Mock(return_value=(part for part in ["plain"]))
        self.assertEqual(self.client.chat(self.messages, None, None, .5, 23), "plain")
        self.assertEqual(list(self.client.stream_chat(self.messages, None, None, .5, 23)), ["plain"])
        self.client._chat_response.assert_called_once_with(self.messages, None, None, .5, 23)
        self.client._stream_chat_response.assert_called_once_with(self.messages, None, None, .5, 23)

    def test_offline_local_other_providers_have_typed_errors_and_no_network(self):
        for mode, provider in (("offline", "openrouter"), ("local", "openrouter"),
                               ("remote", "google_ai_studio"), ("auto", "local_llm")):
            client = client_for_images(mode=mode, provider=provider)
            for stream in (False, True):
                with self.subTest(mode=mode, provider=provider, stream=stream):
                    with self.assertRaises(AIUnsupportedImageProvider):
                        if stream:
                            list(client.stream_chat(self.messages, images=[self.attachment]))
                        else:
                            client.chat(self.messages, images=[self.attachment])
                    client.session.post.assert_not_called()

    def test_unknown_or_text_only_models_have_typed_error(self):
        for model in ("meta-llama/llama-3.1-8b-instruct", "openai/gpt-4o-unknown", "", "vendor/custom"):
            client = client_for_images(model=model)
            with self.assertRaises(AIUnsupportedImageModel):
                client.chat(self.messages, images=[self.attachment])
            client.session.post.assert_not_called()

    def test_custom_endpoint_or_missing_key_not_sent(self):
        for endpoint in ("http://openrouter.ai/api/v1", "https://proxy.invalid/api/v1",
                         "https://openrouter.ai/api/v1@evil.invalid"):
            client = client_for_images(base_url=endpoint)
            with self.assertRaises(AIUnsupportedImageProvider):
                client.chat(self.messages, images=[self.attachment])
            client.session.post.assert_not_called()
        self.client.config.get_api_key = lambda provider: None
        with self.assertRaises(AIImageRequestError):
            self.client.chat(self.messages, images=[self.attachment])
        self.client.session.post.assert_not_called()

    def test_single_prepared_image_and_final_user_message_required(self):
        for images in ([self.attachment, self.attachment], [b"raw"], {"url": "https://invalid"}):
            with self.assertRaises(AIImageRequestError):
                self.client.chat(self.messages, images=images)
        with self.assertRaises(AIImageRequestError):
            self.client.chat(self.messages[:-1], images=[self.attachment])
        self.client.session.post.assert_not_called()

    def test_cancellation_before_request_is_not_fallback(self):
        event = threading.Event()
        event.set()
        for stream in (False, True):
            with self.assertRaises(AIRequestCancelled):
                if stream:
                    list(self.client.stream_chat(self.messages, images=[self.attachment], cancel_event=event))
                else:
                    self.client.chat(self.messages, images=[self.attachment], cancel_event=event)
        self.client.session.post.assert_not_called()

    def test_provider_failure_typed_safe_and_never_uses_text_fallback(self):
        self.client._make_request = Mock(side_effect=AIProviderError(self.attachment.data_url))
        self.client._chat_response = Mock(return_value="fallback")
        with self.assertRaises(AIImageRequestError) as caught:
            self.client.chat(self.messages, images=[self.attachment])
        self.assertNotIn("base64", str(caught.exception))
        self.client._chat_response.assert_not_called()
        self.assertFalse(getattr(_REQUEST_CONTEXT, "image_request", False))

    def test_partial_stream_failure_closes_response_and_preserves_safe_error(self):
        response = ImageResponse([
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            ("data: " + json.dumps({"error": self.attachment.data_url}) + "\n\n").encode()])
        self.client._make_request = Mock(return_value=response)
        stream = self.client.stream_chat(self.messages, images=[self.attachment])
        self.assertEqual(next(stream), "partial")
        with self.assertRaises(AIImageRequestError) as caught:
            next(stream)
        self.assertNotIn("base64", str(caught.exception))
        self.assertTrue(response.closed_event.wait(.5))
        self.assertFalse(getattr(_REQUEST_CONTEXT, "image_request", False))

    def test_http_error_does_not_log_echoed_pixels_or_filename(self):
        response = Response()
        response.status_code = 400
        response.url = "https://openrouter.ai/api/v1/chat/completions"
        response._content = (self.attachment.data_url + " " + self.attachment.filename).encode()
        response._content_consumed = True
        self.client.session.post.return_value = response
        with self.assertLogs('src.ai_client', level='ERROR') as logs:
            with self.assertRaises(AIImageRequestError):
                self.client.chat(self.messages, images=[self.attachment])
        text = '\n'.join(logs.output)
        self.assertNotIn("base64", text)
        self.assertNotIn(self.attachment.filename, text)
        self.assertEqual(self.client.session.post.call_count, 1)

    def test_image_redirects_disabled_and_refused_before_reading_a_body(self):
        for status in (301, 302, 303, 307, 308):
            client = client_for_images()
            response = Mock(status_code=status, headers={"Location": "https://other-provider.invalid"})
            client.session.post.return_value = response
            with self.subTest(status=status):
                with self.assertRaises(AIImageRequestError):
                    client.chat(self.messages, images=[self.attachment])
                self.assertFalse(client.session.post.call_args.kwargs['allow_redirects'])
                self.assertEqual(client.session.post.call_count, 1)
                response.iter_content.assert_not_called()
                response.json.assert_not_called()
                response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
