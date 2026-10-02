"""Provider selection and local inference integration, with no external I/O."""

import json
import os
import copy
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from src.ai_client import AIClient, AIProviderError, ProviderNotConfigured
from src.config_manager import ConfigManager
from src.provider_modes import DISCOVERY_MAX_BYTES, validate_local_url, LocalModelError


class LocalServer:
    """A real HTTP boundary: requests go only to a loopback test server."""

    def __init__(self):
        self.calls = []
        self.models = [{"name": "llama3.2:latest"}, {"name": "small:latest"}]
        self.response = None
        self.redirect = False
        self.delay = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, body, content_type="application/json"):
                if fixture.delay:
                    time.sleep(fixture.delay)
                if fixture.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/forbidden")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                fixture.calls.append(("GET", self.path, None))
                body = fixture.response
                if body is None:
                    data = ({"data": [{"id": entry["name"]} for entry in fixture.models]}
                            if self.path.endswith("/models") else {"models": fixture.models})
                    body = json.dumps(data).encode()
                self._reply(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(body)
                fixture.calls.append(("POST", self.path, payload))
                if payload.get("stream"):
                    self._reply(b'data: {"choices":[{"delta":{"content":"local answer"}}]}\n\n'
                                b'data: [DONE]\n\n', "text/event-stream")
                else:
                    self._reply(json.dumps({"choices": [{"message": {"content": "local answer"}}]}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port) + "/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class TestProviderModes(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = ConfigManager(str(Path(self.directory.name) / "config.json"))
        self.addCleanup(self.config.flush)
        with patch("src.ai_client.Path.home", return_value=Path(self.directory.name)):
            self.client = AIClient(self.config)
        self.addCleanup(self.client.flush_usage)
        self.addCleanup(self.client.session.close)

    def start_server(self):
        fixture = LocalServer()
        self.addCleanup(fixture.close)
        self.config.set_local_model_settings(fixture.url, "llama3.2")
        return fixture

    def test_legacy_config_is_auto_without_network_probe(self):
        self.assertEqual(self.config.get_assistance_mode(), "auto")
        self.config.set_api_key("openrouter", "test")
        self.client.session.request = Mock(side_effect=AssertionError("Unexpected traffic"))
        self.assertTrue(self.client.provider_ready())
        status = self.client.provider_status(check_connection=True)
        self.assertEqual(status.state, "configured")
        self.assertIsNone(status.reachable)
        self.assertFalse(status.ready)

    def test_offline_blocks_even_explicit_cloud_provider(self):
        self.config.set_api_key("openrouter", "test")
        self.config.set_assistance_mode("offline")
        self.client._make_request = Mock(side_effect=AssertionError("Unexpected traffic"))
        self.assertFalse(self.client.provider_ready())
        self.assertEqual(self.client.provider_status(check_connection=True).state, "offline")
        self.assertIsNone(self.client.chat([{"role": "user", "content": "hello"}], provider="openrouter"))
        with self.assertRaises(ProviderNotConfigured):
            list(self.client.stream_chat([{"role": "user", "content": "hello"}]))
        self.client._make_request.assert_not_called()

    def test_local_overrides_legacy_remote_selection_and_blocks_override(self):
        self.config.set_assistance_mode("local")
        self.assertEqual(self.client.active_provider(), "local_llm")
        with self.assertRaises(ProviderNotConfigured):
            self.client.active_provider("openrouter")
        self.assertFalse(self.client.provider_ready("openrouter"))

    def test_remote_requires_remote_provider(self):
        self.config.set("api.default_provider", "local_llm")
        self.config.set_assistance_mode("remote")
        self.assertEqual(self.client.provider_status().state, "blocked")

    def test_configured_local_is_not_claimed_reachable(self):
        self.config.set_assistance_mode("local")
        with patch("src.provider_modes.requests.Session", side_effect=AssertionError("Unexpected traffic")):
            status = self.client.provider_status()
        self.assertEqual(status.state, "configured")
        self.assertIsNone(status.reachable)
        self.assertFalse(status.ready)

    def test_local_settings_and_mode_survive_reload(self):
        self.config.set_assistance_mode("local")
        self.config.set_local_model_settings("http://localhost:11434", "small:latest")
        self.config.flush()
        reloaded = ConfigManager(self.config.config_path)
        self.addCleanup(reloaded.flush)
        self.assertEqual(reloaded.get_assistance_mode(), "local")
        self.assertEqual(reloaded.get_local_model_settings()["base_url"], "http://127.0.0.1:11434/v1")
        self.assertEqual(reloaded.get_local_model_settings()["model"], "small:latest")

    def test_unsaved_settings_probe_never_changes_persisted_config(self):
        fixture = self.start_server()
        self.config.flush()
        before = Path(self.config.config_path).read_bytes()
        stored = self.config.get_local_model_settings().copy()
        preview = {"base_url": fixture.url, "model": "small:latest", "backend": "ollama"}
        self.assertEqual(self.client.test_local_connection(settings=preview).state, "ready")
        self.assertEqual(self.client.test_local_connection(settings=preview).model, "small:latest")
        self.assertIn("small:latest", self.client.list_local_models(settings=preview))
        self.assertEqual(self.config.get_local_model_settings(), stored)
        self.assertEqual(Path(self.config.config_path).read_bytes(), before)

    def test_failed_settings_validation_does_not_save_partial_values(self):
        stored = self.config.get_local_model_settings().copy()
        with self.assertRaises(ValueError):
            self.config.set_local_model_settings("http://localhost:11435/v1", "large:cloud")
        self.assertEqual(self.config.get_local_model_settings(), stored)

    def test_draft_local_blocks_external_probe_before_any_request(self):
        self.config.set_local_model_settings("http://192.168.1.2:11434/v1", "small", strict_local=False)
        stored = copy.deepcopy(self.config.config)
        with patch("src.provider_modes.requests.Session", side_effect=AssertionError("External request")):
            status = self.client.test_local_connection(mode="local")
            self.assertEqual(status.state, "blocked")
            with self.assertRaises(AIProviderError):
                self.client.list_local_models(mode="local")
        self.assertEqual(self.config.config, stored)

    def test_draft_auto_can_probe_relaxed_settings_while_saved_mode_is_local(self):
        self.config.set_assistance_mode("local")
        preview = {"base_url": "http://192.168.1.2:11434/v1", "model": "small", "strict_local": False}
        stored = copy.deepcopy(self.config.config)
        with patch("src.ai_client.discover_local_models", return_value=(["small"], [])) as discover:
            status = self.client.test_local_connection(mode="auto", settings=preview)
        self.assertEqual(status.state, "ready")
        self.assertFalse(discover.call_args.args[2])
        self.assertEqual(self.config.config, stored)

    def test_atomic_mode_settings_validate_desired_mode_and_survive_reload(self):
        self.config.set_assistance_mode("local")
        self.config.set_assistance_settings("auto", "http://192.168.1.2:11434/v1", "small", strict_local=False)
        self.assertEqual(self.config.get_assistance_mode(), "auto")
        self.assertEqual(self.config.get_local_model_settings()["base_url"], "http://192.168.1.2:11434/v1")
        stored = copy.deepcopy(self.config.config)
        with self.assertRaises(ValueError):
            self.config.set_assistance_settings("local", "http://192.168.1.2:11434/v1", "small", strict_local=False)
        self.assertEqual(self.config.config, stored)
        self.config.set_assistance_settings("local", "http://localhost:11434", "small", strict_local=False)
        self.config.flush()
        reloaded = ConfigManager(self.config.config_path)
        self.addCleanup(reloaded.flush)
        self.assertEqual(reloaded.get_assistance_mode(), "local")
        self.assertEqual(reloaded.get_local_model_settings()["base_url"], "http://127.0.0.1:11434/v1")

    def test_environment_local_policy_prevents_saving_relaxed_external_draft(self):
        stored = copy.deepcopy(self.config.config)
        with patch.dict(os.environ, {"LINUX_AI_ASSISTANCE_MODE": "local"}), self.assertRaises(ValueError):
            self.config.set_assistance_settings("auto", "http://192.168.1.2:11434/v1", "small", strict_local=False)
        self.assertEqual(self.config.config, stored)

    def test_desired_local_policy_is_not_relaxed_by_environment_auto(self):
        stored = copy.deepcopy(self.config.config)
        with patch.dict(os.environ, {"LINUX_AI_ASSISTANCE_MODE": "auto"}), self.assertRaises(ValueError):
            self.config.set_assistance_settings("local", "http://192.168.1.2:11434/v1", "small", strict_local=False)
        self.assertEqual(self.config.config, stored)

    def test_invalid_draft_mode_never_changes_config_or_sends_requests(self):
        stored = copy.deepcopy(self.config.config)
        with patch("src.provider_modes.requests.Session", side_effect=AssertionError("Unexpected request")):
            self.assertEqual(self.client.test_local_connection(mode="invalid").state, "blocked")
        with self.assertRaises(ValueError):
            self.config.set_assistance_settings("invalid", "http://localhost:11434", "small")
        self.assertEqual(self.config.config, stored)

    def test_invalid_mode_fails_closed(self):
        self.config.set("assistance.mode", "invalid")
        self.assertEqual(self.config.get_assistance_mode(), "offline")
        self.assertEqual(self.client.provider_status().state, "offline")
        with self.assertRaises(ValueError):
            self.config.set_assistance_mode("invalid")

    def test_external_urls_and_cloud_tags_block_before_request(self):
        self.config.set_assistance_mode("local")
        with patch("src.provider_modes.requests.Session", side_effect=AssertionError("Unexpected traffic")):
            for url in ("https://example.invalid/v1", "http://192.168.1.2:11434/v1", "http://localhost.evil/v1"):
                self.config.set("api.providers.local_llm.base_url", url)
                self.assertEqual(self.client.test_local_connection().state, "blocked")
            self.config.set("api.providers.local_llm.base_url", "http://localhost:11434/v1")
            self.config.set("api.providers.local_llm.model", "large:70b-cloud")
            self.assertEqual(self.client.test_local_connection().state, "blocked")

    def test_url_validation_rejects_ambiguous_hosts_and_secrets(self):
        for url in ("http://localhost@evil.invalid", "http://user:secret@localhost/v1", "file:///tmp/v1",
                    "http://127.0.0.1/v1?key=secret", "http://127.0.0.1/v1#fragment", "http://127.0.0.1:bad/v1"):
            with self.subTest(url=url), self.assertRaises(LocalModelError):
                validate_local_url(url)
        self.assertEqual(validate_local_url("http://[::1]:11434/v1/"), "http://[::1]:11434/v1")

    def test_installed_discovery_and_model_missing_are_distinct(self):
        fixture = self.start_server()
        self.assertEqual(self.client.list_local_models(), ["llama3.2:latest", "small:latest"])
        self.assertEqual(self.client.test_local_connection().state, "ready")
        self.config.set("api.providers.local_llm.model", "missing")
        status = self.client.test_local_connection()
        self.assertEqual(status.state, "model_missing")
        self.assertTrue(status.reachable)
        self.assertTrue(all(method == "GET" and path == "/api/tags" for method, path, _ in fixture.calls))

    def test_cloud_metadata_blocks_renamed_alias_before_inference(self):
        fixture = self.start_server()
        fixture.models = [{"name": "llama3.2:latest", "remote_host": "https://example.invalid",
                           "remote_model": "large"}, {"name": "small:latest"}]
        self.config.set_assistance_mode("local")
        self.assertEqual(self.client.list_local_models(), ["small:latest"])
        self.assertEqual(self.client.test_local_connection().state, "blocked")
        messages = [{"role": "user", "content": "private prompt"}]
        self.assertIsNone(self.client.chat(messages))
        with self.assertRaises(ProviderNotConfigured):
            list(self.client.stream_chat(messages))
        self.assertFalse(any(method == "POST" for method, _, _ in fixture.calls))

    def test_real_loopback_chat_and_stream_do_not_use_environment_proxy(self):
        fixture = self.start_server()
        self.config.set_assistance_mode("local")
        self.client.session.post = Mock(side_effect=AssertionError("Cloud transport used"))
        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:1", "HTTPS_PROXY": "http://proxy.invalid:1", "NO_PROXY": ""}):
            messages = [{"role": "user", "content": "private prompt"}]
            self.assertEqual(self.client.chat(messages), "local answer")
            self.assertEqual(list(self.client.stream_chat(messages)), ["local answer"])
        inference = [(path, payload) for method, path, payload in fixture.calls if method == "POST"]
        self.assertEqual(len(inference), 2)
        self.assertTrue(all(path == "/v1/chat/completions" for path, _ in inference))
        self.assertTrue(all(payload["model"] == "llama3.2" for _, payload in inference))

    def test_remote_alias_changed_after_connection_test_is_rechecked(self):
        fixture = self.start_server()
        self.config.set_assistance_mode("local")
        self.assertEqual(self.client.test_local_connection().state, "ready")
        fixture.models[0]["remote_model"] = "cloud-upstream"
        self.assertIsNone(self.client.chat([{"role": "user", "content": "private"}]))
        self.assertFalse(any(method == "POST" for method, _, _ in fixture.calls))

    def test_missing_model_never_auto_downloads_or_falls_back_remote(self):
        fixture = self.start_server()
        self.config.set_assistance_mode("local")
        self.config.set_api_key("openrouter", "test")
        self.config.set("api.providers.local_llm.model", "missing")
        self.client._chat_openrouter = Mock(side_effect=AssertionError("Remote fallback"))
        self.assertIsNone(self.client.chat([{"role": "user", "content": "hello"}]))
        self.client._chat_openrouter.assert_not_called()
        self.assertTrue(all(method == "GET" for method, _, _ in fixture.calls))

    def test_generic_openai_backend_discovers_installed_ids(self):
        fixture = self.start_server()
        self.config.set_local_model_settings(fixture.url, "llama3.2:latest", backend="openai")
        self.assertEqual(self.client.test_local_connection().state, "ready")
        self.assertEqual(fixture.calls[0][1], "/v1/models")

    def test_redirect_is_not_followed_even_for_inference(self):
        fixture = self.start_server()
        fixture.redirect = True
        self.assertEqual(self.client.test_local_connection().state, "error")
        # Legacy auto has no discovery preflight, exercising the POST boundary.
        self.config.set("api.default_provider", "local_llm")
        self.assertIsNone(self.client.chat([{"role": "user", "content": "hello"}]))
        self.assertEqual(len(fixture.calls), 2)

    def test_malformed_and_oversized_lists_fail_without_inference(self):
        fixture = self.start_server()
        for body in (b"not json", json.dumps({"models": "invalid"}).encode(), b" " * (DISCOVERY_MAX_BYTES + 1)):
            fixture.response = body
            self.assertEqual(self.client.test_local_connection().state, "error")
        self.assertFalse(any(method == "POST" for method, _, _ in fixture.calls))

    def test_connection_timeout_and_refusal_report_unreachable(self):
        fixture = self.start_server()
        fixture.delay = 0.15
        started = time.monotonic()
        self.assertEqual(self.client.test_local_connection(timeout=0.03).state, "unreachable")
        self.assertLess(time.monotonic() - started, 1)
        with patch("src.provider_modes.requests.Session.get", side_effect=__import__("requests").ConnectionError("refused")):
            self.assertEqual(self.client.test_local_connection().state, "unreachable")

    def test_discovery_timeout_is_capped_and_invalid_limits_rejected(self):
        with patch("src.provider_modes._fetch_json", return_value={"models": []}) as fetch:
            self.client.list_local_models(timeout=1000)
        self.assertEqual(fetch.call_args.args[1], 10.0)
        for value in (0, -1, float("inf"), "not a number"):
            with self.subTest(value=value), self.assertRaises(AIProviderError):
                self.client.list_local_models(timeout=value)


if __name__ == "__main__":
    unittest.main()
