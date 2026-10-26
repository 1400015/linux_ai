"""Tests for src.local_backends — native protocol support for local LLM servers."""

import unittest

from src.local_backends import (
    BACKEND_LABELS,
    DEFAULT_PORTS,
    LOCAL_BACKENDS,
    build_chat_payload,
    chat_path,
    discovery_path,
    extract_chat_text,
    extract_chat_usage,
    extract_error_message,
    is_stream_done,
    iter_native_chat_events,
    normalize_base_url,
    parse_discovery_response,
)
from src.provider_modes import LocalModelError


class TestLocalBackendsConstant(unittest.TestCase):
    def test_all_four_backends(self):
        self.assertEqual(set(LOCAL_BACKENDS),
                         {"ollama", "openai", "ollama-native", "lmstudio"})

    def test_labels_cover_all_backends(self):
        for backend in LOCAL_BACKENDS:
            self.assertIn(backend, BACKEND_LABELS)
            self.assertTrue(BACKEND_LABELS[backend])

    def test_default_ports_cover_all_backends(self):
        for backend in LOCAL_BACKENDS:
            self.assertIn(backend, DEFAULT_PORTS)
            self.assertIsInstance(DEFAULT_PORTS[backend], int)
            self.assertGreater(DEFAULT_PORTS[backend], 0)


class TestNormalizeBaseUrl(unittest.TestCase):
    def test_ollama_appends_v1(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:11434", "ollama"),
                         "http://127.0.0.1:11434/v1")

    def test_ollama_keeps_existing_v1(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:11434/v1", "ollama"),
                         "http://127.0.0.1:11434/v1")

    def test_openai_appends_v1(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:1234", "openai"),
                         "http://127.0.0.1:1234/v1")

    def test_ollama_native_keeps_root(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:11434", "ollama-native"),
                         "http://127.0.0.1:11434")

    def test_ollama_native_strips_v1(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:11434/v1", "ollama-native"),
                         "http://127.0.0.1:11434")

    def test_lmstudio_keeps_root(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:1234", "lmstudio"),
                         "http://127.0.0.1:1234")

    def test_lmstudio_strips_v1(self):
        self.assertEqual(normalize_base_url("http://127.0.0.1:1234/v1", "lmstudio"),
                         "http://127.0.0.1:1234")

    def test_unknown_backend_raises(self):
        with self.assertRaises(LocalModelError):
            normalize_base_url("http://127.0.0.1:1234", "bogus")


class TestPaths(unittest.TestCase):
    def test_discovery_paths(self):
        self.assertEqual(discovery_path("ollama"), "/models")
        self.assertEqual(discovery_path("openai"), "/models")
        self.assertEqual(discovery_path("ollama-native"), "/api/tags")
        self.assertEqual(discovery_path("lmstudio"), "/api/v0/models")

    def test_chat_paths(self):
        self.assertEqual(chat_path("ollama"), "/chat/completions")
        self.assertEqual(chat_path("openai"), "/chat/completions")
        self.assertEqual(chat_path("ollama-native"), "/api/chat")
        self.assertEqual(chat_path("lmstudio"), "/api/v0/chat")


class TestBuildChatPayload(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "user", "content": "hi"}]

    def test_ollama_native_payload(self):
        payload = build_chat_payload("ollama-native", "llama3.2", self.messages,
                                     0.7, 2000, stream=False)
        self.assertEqual(payload["model"], "llama3.2")
        self.assertEqual(payload["messages"], self.messages)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["temperature"], 0.7)
        self.assertEqual(payload["options"]["num_predict"], 2000)

    def test_ollama_native_stream(self):
        payload = build_chat_payload("ollama-native", "m", self.messages, 0.5, 100, stream=True)
        self.assertTrue(payload["stream"])

    def test_lmstudio_payload(self):
        payload = build_chat_payload("lmstudio", "publisher/model", self.messages,
                                     0.3, 500, stream=True)
        self.assertEqual(payload["model"], "publisher/model")
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["max_tokens"], 500)
        self.assertTrue(payload["stream"])

    def test_openai_compatible_payload(self):
        payload = build_chat_payload("openai", "m", self.messages, 0.7, 100)
        self.assertEqual(payload["model"], "m")
        self.assertNotIn("stream", payload)
        payload_stream = build_chat_payload("ollama", "m", self.messages, 0.7, 100, stream=True)
        self.assertTrue(payload_stream["stream"])

    def test_unknown_backend_raises(self):
        with self.assertRaises(LocalModelError):
            build_chat_payload("bogus", "m", self.messages, 0.7, 100)

    def test_invalid_model_raises(self):
        with self.assertRaises(LocalModelError):
            build_chat_payload("ollama-native", "", self.messages, 0.7, 100)
        with self.assertRaises(LocalModelError):
            build_chat_payload("ollama-native", "bad\x00model", self.messages, 0.7, 100)


class TestParseDiscoveryResponse(unittest.TestCase):
    def test_ollama_native(self):
        data = {"models": [{"name": "llama3.2:latest"}, {"name": "small:latest"}]}
        entries, key = parse_discovery_response("ollama-native", data)
        self.assertEqual(key, "name")
        self.assertEqual(len(entries), 2)

    def test_lmstudio(self):
        data = {"object": "list", "data": [{"id": "publisher/model"}]}
        entries, key = parse_discovery_response("lmstudio", data)
        self.assertEqual(key, "id")
        self.assertEqual(entries, [{"id": "publisher/model"}])

    def test_openai(self):
        data = {"data": [{"id": "model-a"}, {"id": "model-b"}]}
        entries, key = parse_discovery_response("openai", data)
        self.assertEqual(key, "id")

    def test_ollama_shim(self):
        data = {"data": [{"id": "model-a"}]}
        entries, key = parse_discovery_response("ollama", data)
        self.assertEqual(key, "id")

    def test_missing_list_raises(self):
        with self.assertRaises(LocalModelError):
            parse_discovery_response("lmstudio", {"object": "list"})

    def test_unknown_backend_raises(self):
        with self.assertRaises(LocalModelError):
            parse_discovery_response("bogus", {})


class _FakeResponse:
    """Minimal response stand-in for iter_native_chat_events (iter_lines path)."""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)


class TestIterNativeChatEvents(unittest.TestCase):
    def test_parses_ndjson_lines(self):
        response = _FakeResponse([
            b'{"message": {"content": "Hello"}}',
            b'',
            b'{"message": {"content": " world"}, "done": false}',
            b'{"done": true, "prompt_eval_count": 5, "eval_count": 2}',
        ])
        events = list(iter_native_chat_events(response))
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["message"]["content"], "Hello")
        self.assertTrue(events[2]["done"])

    def test_skips_empty_lines(self):
        response = _FakeResponse([b'', b'{"a": 1}', b'   ', b'{"b": 2}'])
        events = list(iter_native_chat_events(response))
        self.assertEqual(len(events), 2)

    def test_invalid_json_raises(self):
        response = _FakeResponse([b'not json'])
        with self.assertRaises(LocalModelError):
            list(iter_native_chat_events(response))

    def test_non_dict_lines_skipped(self):
        response = _FakeResponse([b'[1, 2, 3]', b'"string"', b'{"ok": true}'])
        events = list(iter_native_chat_events(response))
        self.assertEqual(events, [{"ok": True}])


class TestExtractChatText(unittest.TestCase):
    def test_ollama_native(self):
        event = {"message": {"role": "assistant", "content": "hi"}, "done": False}
        self.assertEqual(extract_chat_text("ollama-native", event), "hi")

    def test_ollama_native_empty(self):
        self.assertIsNone(extract_chat_text("ollama-native", {"message": {"content": ""}}))
        self.assertIsNone(extract_chat_text("ollama-native", {"done": True}))

    def test_lmstudio(self):
        event = {"choices": [{"delta": {"content": "chunk"}}]}
        self.assertEqual(extract_chat_text("lmstudio", event), "chunk")

    def test_lmstudio_empty(self):
        self.assertIsNone(extract_chat_text("lmstudio", {"choices": []}))
        self.assertIsNone(extract_chat_text("lmstudio", {}))


class TestExtractChatUsage(unittest.TestCase):
    def test_ollama_native_done_event(self):
        event = {"done": True, "prompt_eval_count": 10, "eval_count": 5}
        usage = extract_chat_usage("ollama-native", event)
        self.assertEqual(usage["prompt_tokens"], 10)
        self.assertEqual(usage["completion_tokens"], 5)
        self.assertEqual(usage["total_tokens"], 15)

    def test_ollama_native_non_done_returns_none(self):
        event = {"done": False, "prompt_eval_count": 10, "eval_count": 5}
        self.assertIsNone(extract_chat_usage("ollama-native", event))

    def test_ollama_native_missing_counts(self):
        self.assertIsNone(extract_chat_usage("ollama-native", {"done": True}))

    def test_lmstudio_usage(self):
        event = {"usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}}
        self.assertEqual(extract_chat_usage("lmstudio", event)["total_tokens"], 10)

    def test_lmstudio_no_usage(self):
        self.assertIsNone(extract_chat_usage("lmstudio", {"choices": []}))


class TestIsStreamDone(unittest.TestCase):
    def test_ollama_native(self):
        self.assertTrue(is_stream_done("ollama-native", {"done": True}))
        self.assertFalse(is_stream_done("ollama-native", {"done": False}))

    def test_lmstudio_finish_reason(self):
        self.assertTrue(is_stream_done("lmstudio", {"choices": [{"finish_reason": "stop"}]}))
        self.assertFalse(is_stream_done("lmstudio", {"choices": [{"finish_reason": None}]}))
        self.assertFalse(is_stream_done("lmstudio", {"choices": []}))


class TestExtractErrorMessage(unittest.TestCase):
    def test_string_error(self):
        self.assertEqual(extract_error_message({"error": "model not found"}), "model not found")

    def test_dict_error(self):
        event = {"error": {"message": "bad request", "code": 400}}
        self.assertEqual(extract_error_message(event), "bad request")

    def test_no_error(self):
        self.assertIsNone(extract_error_message({"message": {"content": "ok"}}))
        self.assertIsNone(extract_error_message({}))


if __name__ == "__main__":
    unittest.main()
