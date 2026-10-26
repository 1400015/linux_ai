"""Tests for src.chat_controller — GTK-free chat orchestration."""

import unittest
from types import SimpleNamespace
from unittest import mock

from src.chat_controller import (
    ChatRoute,
    ProcessResult,
    StreamCoalescer,
    build_context_message,
    build_full_history,
    build_request_messages,
    check_credential_shadow,
    select_route,
    should_use_offline,
)


class TestBuildRequestMessages(unittest.TestCase):
    def _config(self, **overrides):
        defaults = {
            "context.max_messages": 20,
            "context.max_chars": 12000,
        }
        defaults.update(overrides)
        return lambda key, default=None: defaults.get(key, default)

    def test_filters_empty_and_missing_fields(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "", "content": "no role"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "hi"},
            {"content": "no role key"},
        ]
        result = build_request_messages(history, self._config())
        self.assertEqual(result, [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])

    def test_strips_timestamp(self):
        history = [
            {"role": "user", "content": "hi", "timestamp": 1234.5},
        ]
        result = build_request_messages(history, self._config())
        self.assertEqual(result, [{"role": "user", "content": "hi"}])
        self.assertNotIn("timestamp", result[0])

    def test_trims_to_max_messages(self):
        history = [
            {"role": "user", "content": f"msg{i}"} for i in range(30)
        ]
        result = build_request_messages(history, self._config(**{"context.max_messages": 5}))
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0]["content"], "msg25")
        self.assertEqual(result[-1]["content"], "msg29")

    def test_trims_to_max_chars(self):
        # safe_number enforces minimum=1000 for max_chars, so use a budget
        # above that floor. Each message is 500 chars; budget 1200 keeps 2.
        history = [
            {"role": "user", "content": "x" * 500},
            {"role": "assistant", "content": "y" * 500},
            {"role": "user", "content": "z" * 500},
        ]
        result = build_request_messages(history, self._config(**{"context.max_chars": 1200}))
        total = sum(len(m["content"]) for m in result)
        self.assertLessEqual(total, 1200)
        self.assertGreaterEqual(len(result), 1)

    def test_keeps_at_least_one_message(self):
        history = [{"role": "user", "content": "x" * 20000}]
        result = build_request_messages(history, self._config(**{"context.max_chars": 100}))
        self.assertEqual(len(result), 1)

    def test_empty_history(self):
        self.assertEqual(build_request_messages([], self._config()), [])

    def test_invalid_config_values_use_defaults(self):
        history = [{"role": "user", "content": "hi"}]

        def config(key, default=None):
            return 0 if key == "context.max_messages" else default

        result = build_request_messages(history, config)
        self.assertEqual(len(result), 1)


class TestBuildContextMessage(unittest.TestCase):
    def test_returns_system_message(self):
        history = [
            {"role": "user", "content": "how do I check disk space?"},
            {"role": "assistant", "content": "use df -h"},
        ]
        distro = SimpleNamespace(
            pretty_name="TestOS",
            version_id="1.0",
            pkg_manager="apt",
            service_manager="systemd",
        )
        result = build_context_message(False, distro, history, "en")
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "system")
        self.assertIn("Linux assistant", result["content"])

    def test_empty_history_returns_message_with_empty_query(self):
        distro = SimpleNamespace(
            pretty_name="TestOS", version_id="", pkg_manager="apt", service_manager="systemd",
        )
        result = build_context_message(False, distro, [], "en")
        self.assertIsNotNone(result)
        self.assertEqual(result["role"], "system")


class TestBuildFullHistory(unittest.TestCase):
    def test_prepends_context(self):
        context = {"role": "system", "content": "sys"}
        messages = [{"role": "user", "content": "hi"}]
        result = build_full_history(context, messages)
        self.assertEqual(result[0], context)
        self.assertEqual(result[1], messages[0])

    def test_no_context(self):
        messages = [{"role": "user", "content": "hi"}]
        result = build_full_history(None, messages)
        self.assertEqual(result, messages)

    def test_document_context_appended(self):
        context = {"role": "system", "content": "sys"}
        messages = [{"role": "user", "content": "hi"}]
        with mock.patch("src.chat_controller.with_document_context") as mock_wdc:
            mock_wdc.return_value = [{"role": "user", "content": "hi\n\n[doc]"}]
            result = build_full_history(context, messages, document_context="some doc")
            mock_wdc.assert_called_once()
            self.assertEqual(len(result), 1)

    def test_empty_document_context_skipped(self):
        messages = [{"role": "user", "content": "hi"}]
        result = build_full_history(None, messages, document_context="")
        self.assertEqual(result, messages)


class TestChatRoute(unittest.TestCase):
    def test_goes_offline_when_not_ready(self):
        route = ChatRoute(ready=False)
        self.assertTrue(route.goes_offline)

    def test_not_offline_when_ready(self):
        route = ChatRoute(ready=True)
        self.assertFalse(route.goes_offline)

    def test_offline_blocked_for_images(self):
        route = ChatRoute(ready=False, has_images=True)
        self.assertEqual(route.offline_blocked, "image_offline")

    def test_offline_blocked_for_documents(self):
        route = ChatRoute(ready=False, has_documents=True)
        self.assertEqual(route.offline_blocked, "document_offline")

    def test_offline_allowed(self):
        route = ChatRoute(ready=False)
        self.assertIsNone(route.offline_blocked)

    def test_offline_not_blocked_when_ready(self):
        route = ChatRoute(ready=True, has_images=True)
        self.assertIsNone(route.offline_blocked)


class TestSelectRoute(unittest.TestCase):
    def test_uses_explicit_ready(self):
        client = SimpleNamespace(provider_ready=lambda: True)
        route = select_route(client, provider_ready=False, provider="openrouter")
        self.assertFalse(route.ready)
        self.assertEqual(route.provider, "openrouter")

    def test_queries_client_when_ready_is_none(self):
        client = SimpleNamespace(provider_ready=lambda: True)
        route = select_route(client, provider="google", model="gemini", has_images=True)
        self.assertTrue(route.ready)
        self.assertEqual(route.model, "gemini")
        self.assertTrue(route.has_images)

    def test_client_not_ready(self):
        client = SimpleNamespace(provider_ready=lambda: False)
        route = select_route(client)
        self.assertFalse(route.ready)
        self.assertTrue(route.goes_offline)


class TestCheckCredentialShadow(unittest.TestCase):
    def test_no_provider(self):
        config = SimpleNamespace()
        self.assertIsNone(check_credential_shadow(config, None))

    def test_not_shadowed(self):
        config = SimpleNamespace(stored_key_shadowed_by_empty_env=lambda p: False)
        self.assertIsNone(check_credential_shadow(config, "openrouter"))

    def test_shadowed(self):
        config = SimpleNamespace(
            stored_key_shadowed_by_empty_env=lambda p: True,
            api_key_env_var=lambda p: "OPENROUTER_API_KEY",
            can_remove_empty_api_key_override=lambda p: True,
        )
        warning = check_credential_shadow(config, "openrouter")
        self.assertIsNotNone(warning)
        self.assertEqual(warning.provider, "openrouter")
        self.assertEqual(warning.env_var, "OPENROUTER_API_KEY")
        self.assertTrue(warning.can_remove)

    def test_exception_returns_none(self):
        config = SimpleNamespace(
            stored_key_shadowed_by_empty_env=mock.Mock(side_effect=RuntimeError("boom")),
        )
        self.assertIsNone(check_credential_shadow(config, "openrouter"))


class TestStreamCoalescer(unittest.TestCase):
    def test_add_returns_none_before_interval(self):
        coalescer = StreamCoalescer(interval=999)
        self.assertIsNone(coalescer.add("hello"))
        self.assertEqual(coalescer.pending, "hello")

    def test_add_flushes_after_interval(self):
        coalescer = StreamCoalescer(interval=0)
        result = coalescer.add("hello")
        self.assertEqual(result, "hello")
        self.assertEqual(coalescer.pending, "")

    def test_total_accumulates(self):
        coalescer = StreamCoalescer(interval=0)
        coalescer.add("a")
        coalescer.add("b")
        coalescer.add("c")
        self.assertEqual(coalescer.total, "abc")

    def test_flush_returns_and_clears_pending(self):
        coalescer = StreamCoalescer(interval=999)
        coalescer.add("hello")
        coalescer.add(" world")
        result = coalescer.flush()
        self.assertEqual(result, "hello world")
        self.assertEqual(coalescer.pending, "")
        self.assertEqual(coalescer.total, "hello world")

    def test_clear_discards_pending(self):
        coalescer = StreamCoalescer(interval=999)
        coalescer.add("discard me")
        coalescer.clear()
        self.assertEqual(coalescer.pending, "")

    def test_reset_clears_all(self):
        coalescer = StreamCoalescer(interval=0)
        coalescer.add("abc")
        coalescer.reset()
        self.assertEqual(coalescer.total, "")
        self.assertEqual(coalescer.pending, "")

    def test_exceeds_limit_raises(self):
        coalescer = StreamCoalescer(interval=999)
        with self.assertRaises(ValueError) as ctx:
            coalescer.add("x" * 200, max_chars=100)
        self.assertEqual(str(ctx.exception), "response_exceeds_limit")

    def test_pending_setter(self):
        coalescer = StreamCoalescer(interval=999)
        coalescer.pending = "replaced"
        self.assertEqual(coalescer.pending, "replaced")

    def test_pending_setter_empty(self):
        coalescer = StreamCoalescer(interval=999)
        coalescer.add("something")
        coalescer.pending = ""
        self.assertEqual(coalescer.pending, "")

    def test_flush_empty_returns_empty(self):
        coalescer = StreamCoalescer(interval=999)
        self.assertEqual(coalescer.flush(), "")

    def test_multiple_flushes(self):
        coalescer = StreamCoalescer(interval=0)
        self.assertEqual(coalescer.add("a"), "a")
        self.assertEqual(coalescer.add("b"), "b")
        self.assertEqual(coalescer.total, "ab")


class TestShouldUseOffline(unittest.TestCase):
    def test_offline_when_not_ready(self):
        route = ChatRoute(ready=False)
        self.assertTrue(should_use_offline(route, "", cancelled=False))

    def test_not_offline_when_ready_with_text(self):
        route = ChatRoute(ready=True)
        self.assertFalse(should_use_offline(route, "response", cancelled=False))

    def test_offline_when_ready_but_no_text(self):
        route = ChatRoute(ready=True)
        self.assertTrue(should_use_offline(route, "", cancelled=False))

    def test_not_offline_when_cancelled(self):
        route = ChatRoute(ready=False)
        self.assertFalse(should_use_offline(route, "", cancelled=True))

    def test_not_offline_for_images(self):
        route = ChatRoute(ready=True, has_images=True)
        self.assertFalse(should_use_offline(route, "", cancelled=False))

    def test_offline_when_provider_failed(self):
        # Provider was ready but returned no text (e.g. structured error)
        route = ChatRoute(ready=True)
        self.assertTrue(should_use_offline(route, "", cancelled=False))


class TestProcessResult(unittest.TestCase):
    def test_defaults(self):
        result = ProcessResult()
        self.assertEqual(result.response_text, "")
        self.assertFalse(result.cancelled)
        self.assertIsNone(result.offline_reply)
        self.assertIsNone(result.action)
        self.assertEqual(result.pending_chunks, "")
        self.assertIsNone(result.error)
        self.assertFalse(result.early_return)

    def test_with_values(self):
        result = ProcessResult(
            response_text="hello",
            cancelled=False,
            pending_chunks="hel",
        )
        self.assertEqual(result.response_text, "hello")
        self.assertEqual(result.pending_chunks, "hel")


if __name__ == "__main__":
    unittest.main()
