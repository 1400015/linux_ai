"""Regression tests for the security and correctness bugs that were fixed.

Each test documents a defect that existed and must not come back.
These tests run without GTK.
"""

import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai_client import AIClient, redact_url
from src.config_manager import ConfigManager
from src.render_core import (
    placeholder_span,
    valid_span,
    header_offset,
)
from src.system_utils import SystemUtils


class FakeConfig:
    """ConfigManager minimo para exercitar SystemUtils."""

    def __init__(self, commands=(), edit_dirs=(), **extra):
        self._values = {
            "permissions.allowed_commands": list(commands),
            "permissions.allowed_edit_dirs": list(edit_dirs),
        }
        self._values.update(extra)

    def get(self, key, default=None):
        return self._values.get(key, default)


# --- Bug 1: injecao de comandos em search_files (shell=True) -----------------

class TestSearchFilesIsNotShellBased(unittest.TestCase):
    def test_source_does_not_use_shell(self):
        source = inspect.getsource(SystemUtils.search_files)
        self.assertNotIn("shell=True", source)

    def test_metacharacters_in_search_term_are_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A file whose name contains the "payload"
            (Path(tmp) / "a b;touch pwned.txt").write_text("x")
            utils = SystemUtils(FakeConfig())
            results = utils.search_files("a b;touch", search_path=tmp)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].endswith("a b;touch pwned.txt"))
            # O shell nunca foi chamado, logo nada foi criado
            self.assertFalse(Path(tmp, "pwned.txt").exists())

    def test_command_substitution_is_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "$(id).txt").write_text("x")
            utils = SystemUtils(FakeConfig())
            results = utils.search_files("$(id)", search_path=tmp)
            self.assertEqual(len(results), 1)

    def test_invalid_search_path_returns_empty(self):
        utils = SystemUtils(FakeConfig())
        self.assertEqual(utils.search_files("x", search_path="/no/such/dir"), [])

    def test_respects_max_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(10):
                (Path(tmp) / f"match{i}.txt").write_text("x")
            utils = SystemUtils(FakeConfig())
            self.assertLessEqual(len(utils.search_files("match", tmp, max_results=3)), 3)


# --- Bug 2: bypass de allowed_edit_dirs em _validate_path --------------------

class TestValidatePath(unittest.TestCase):
    def setUp(self):
        self.utils = SystemUtils(FakeConfig(edit_dirs=["/etc", "/home"]))

    def test_prefix_siblings_are_rejected(self):
        # Regression: `startswith` accepted "/etcfoo" and "/homeX".
        for path in ("/etcfoo/evil", "/homeX/secrets", "/etc../shadow",
                     "/etcbackup/passwd", "/homeless"):
            with self.subTest(path=path):
                self.assertFalse(self.utils._validate_path(path))

    def test_paths_inside_allowed_dirs_are_accepted(self):
        for path in ("/etc/passwd", "/etc/ssh/sshd_config", "/home/user/file.txt"):
            with self.subTest(path=path):
                self.assertTrue(self.utils._validate_path(path))

    def test_the_allowed_dir_itself_is_accepted(self):
        self.assertTrue(self.utils._validate_path("/etc"))
        self.assertTrue(self.utils._validate_path("/home"))

    def test_traversal_out_of_allowed_dir_is_rejected(self):
        # "/etc/../root/..." resolves to /root/... which is NOT allowed
        self.assertFalse(self.utils._validate_path("/etc/../root/.ssh/authorized_keys"))
        self.assertFalse(self.utils._validate_path("/home/user/../../../root/x"))
        # Normalization back into an allowed directory stays valid
        self.assertTrue(self.utils._validate_path("/home/user/../../etc/shadow"))

    def test_dotdot_escaping_through_home(self):
        self.assertFalse(self.utils._validate_path("/home/../root"))

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as allowed:
            secret = Path(outside) / "secret.txt"
            secret.write_text("top secret")
            link = Path(allowed) / "link.txt"
            os.symlink(secret, link)

            utils = SystemUtils(FakeConfig(edit_dirs=[allowed]))
            # realpath resolves the link to outside of `allowed`
            self.assertFalse(utils._validate_path(str(link)))

    def test_symlink_inside_allowed_dir_is_accepted(self):
        with tempfile.TemporaryDirectory() as allowed:
            real = Path(allowed) / "real.txt"
            real.write_text("ok")
            link = Path(allowed) / "link.txt"
            os.symlink(real, link)
            utils = SystemUtils(FakeConfig(edit_dirs=[allowed]))
            self.assertTrue(utils._validate_path(str(link)))


class TestReadWriteFileRespectsSandbox(unittest.TestCase):
    def test_write_outside_allowed_dir_is_denied(self):
        with tempfile.TemporaryDirectory() as allowed:
            utils = SystemUtils(FakeConfig(edit_dirs=[allowed]))
            ok, msg = utils.write_file("/etc/passwd", "pwned")
            self.assertFalse(ok)
            self.assertIn("denied", msg)

    def test_read_outside_allowed_dir_is_denied(self):
        utils = SystemUtils(FakeConfig(edit_dirs=["/nonexistent-dir"]))
        ok, msg = utils.read_file("/etc/hostname")
        self.assertFalse(ok)


# --- Bug 3 (new): an allowlisted `cat` bypassed the sandbox --------------------

class TestFileReadingCommandsRespectSandbox(unittest.TestCase):
    def setUp(self):
        self.utils = SystemUtils(
            FakeConfig(commands=["cat", "ls", "echo"], edit_dirs=["/tmp/allowed-only"])
        )

    def test_cat_outside_allowed_dir_is_blocked(self):
        ok, msg = self.utils.execute_command("cat /etc/shadow")
        self.assertFalse(ok)
        self.assertIn("not allowed", msg)

    def test_cat_inside_allowed_dir_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.addCleanup(shutil.rmtree, "/tmp/allowed-only", ignore_errors=True)
            os.makedirs("/tmp/allowed-only", exist_ok=True)
            target = Path("/tmp/allowed-only") / "ok.txt"
            target.write_text("hello")
            utils = SystemUtils(
                FakeConfig(commands=["cat"], edit_dirs=["/tmp/allowed-only"])
            )
            ok, out = utils.execute_command(f"cat {target}")
            self.assertTrue(ok, out)
            self.assertIn("hello", out)

    def test_commands_without_path_args_are_unaffected(self):
        ok, out = self.utils.execute_command("echo teste")
        self.assertTrue(ok, out)
        self.assertIn("teste", out)


# --- Bug 3: assinatura de _chat_local_llm / _stream_local_llm ---------------

class TestProviderDispatchSignatures(unittest.TestCase):
    EXPECTED = [
        "self", "messages", "model", "api_key", "base_url",
        "temperature", "max_tokens", "timeout",
    ]

    def test_all_providers_accept_the_dispatch_signature(self):
        # Regression: `_chat_local_llm` did not accept `api_key` and the
        # dispatcher called it with 7 arguments -> swallowed TypeError.
        for provider in AIClient.SUPPORTED_PROVIDERS:
            for prefix in ("_chat_", "_stream_"):
                method = getattr(AIClient, prefix + provider, None)
                if method is None:
                    continue
                with self.subTest(method=prefix + provider):
                    params = list(inspect.signature(method).parameters)
                    self.assertEqual(params, self.EXPECTED)

    def test_local_llm_chat_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            config.set("api.default_provider", "local_llm")
            # Patch Path.home like the neighbouring usage tests so the token
            # usage recorded by chat() lands in the temp dir, not the real
            # ~/.config/linux_ai_assistant/usage.json.
            with patch("src.ai_client.Path.home", return_value=Path(d)):
                client = AIClient(config)
                try:
                    captured = {}

                    def fake_request(url, payload, headers=None, timeout=None, stream=False):
                        captured["url"] = url
                        class R:
                            status_code = 200
                            def json(self):
                                return {
                                    "choices": [{"message": {"content": "resposta local"}}],
                                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                                }
                            def close(self):
                                pass
                        return R()

                    client._make_request = fake_request
                    out = client.chat([{"role": "user", "content": "oi"}])
                    self.assertEqual(out, "resposta local")
                    self.assertIn("/chat/completions", captured["url"])
                finally:
                    client.session.close()


# --- Bug 4: resposta em streaming apagada do ecra ----------------------------

class TestLoadingPlaceholderRemoval(unittest.TestCase):
    """
    Reproduz a semantica do Gtk.TextBuffer (uma lista de caracteres) para
    validar a aritmetica de offsets sem depender de GTK.
    """

    class FakeBuffer:
        def __init__(self):
            self.data = []

        def get_char_count(self):
            return len(self.data)

        def insert(self, _iter, text):
            self.data.extend(text)

        def text(self):
            return "".join(self.data)

        def delete(self, start, end):
            del self.data[start:end]

    def _append(self, buffer, text):
        start = buffer.get_char_count()
        buffer.insert(None, text)
        return placeholder_span(start, text)

    def test_streamed_answer_survives_placeholder_removal(self):
        buffer = self.FakeBuffer()
        self._append(buffer, "\n[User]\nola\n\n")

        # Placeholder registado
        loading = self._append(buffer, "\n[AI]\nThinking...")

        # Resposta em streaming inserida DEPOIS do placeholder
        for chunk in ("A ", "resposta ", "do ", "modelo."):
            buffer.insert(None, chunk)

        # Remover apenas o placeholder
        span = valid_span(loading[0], loading[1], buffer.get_char_count())
        buffer.delete(span[0], span[1])

        self.assertEqual(buffer.text(), "\n[User]\nola\n\nA resposta do modelo.")

    def test_removal_twice_is_a_noop(self):
        buffer = self.FakeBuffer()
        loading = self._append(buffer, "\n[AI]\nThinking...")
        first = valid_span(loading[0], loading[1], buffer.get_char_count())
        buffer.delete(first[0], first[1])
        # Segunda tentativa com offsets obsoletos
        self.assertIsNone(valid_span(loading[0], loading[1], buffer.get_char_count()))

    def test_stale_span_is_rejected(self):
        self.assertIsNone(valid_span(0, 100, 10))
        self.assertIsNone(valid_span(None, None, 10))
        self.assertIsNone(valid_span(5, 5, 10))
        self.assertIsNone(valid_span(-1, 5, 10))

    def test_header_offset_matches_inserted_layout(self):
        text = "\n[AI]\n"
        self.assertEqual(header_offset(0, "AI"), len(text))
        self.assertEqual(header_offset(100, "User"), 100 + len("\n[User]\n"))


# --- Bug 5: DEFAULT_CONFIG com copia rasa ------------------------------------

class TestDefaultConfigIsolation(unittest.TestCase):
    def test_api_key_does_not_leak_between_instances(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            c1 = ConfigManager(str(Path(d1) / "config.json"))
            c1.set("api.providers.openrouter.api_key", "SEGRED-1")

            c2 = ConfigManager(str(Path(d2) / "config.json"))
            self.assertEqual(c1.get("api.providers.openrouter.api_key"), "SEGRED-1")
            self.assertEqual(
                c2.get("api.providers.openrouter.api_key"), "",
                "An API key from another instance must not leak",
            )

    def test_class_constant_is_not_polluted(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            config.set("api.providers.mistral.api_key", "SEGRED-2")
            self.assertEqual(
                ConfigManager.DEFAULT_CONFIG["api"]["providers"]["mistral"]["api_key"], ""
            )

    def test_returned_config_is_independent(self):
        a = ConfigManager._get_default_config(ConfigManager.__new__(ConfigManager))
        a["app"]["width"] = 9999
        self.assertEqual(ConfigManager.DEFAULT_CONFIG["app"]["width"], 400)


# --- Bug 6: fuga da chave da API Google nos logs ----------------------------

class TestSecretRedaction(unittest.TestCase):
    def test_google_style_url_is_redacted(self):
        url = "https://generativelanguage.googleapis.com/v1/models/x:generateContent?key=AIzaSECRET"
        redacted = redact_url(url)
        self.assertNotIn("AIzaSECRET", redacted)
        self.assertIn("key=***", redacted)

    def test_other_query_params_are_preserved(self):
        url = "https://x/y?key=abc&model=gemini"
        redacted = redact_url(url)
        self.assertNotIn("abc", redacted)
        self.assertIn("model=gemini", redacted)

    def test_access_token_is_redacted(self):
        self.assertNotIn("tok123", redact_url("https://x?access_token=tok123"))

    def test_url_without_secrets_is_unchanged(self):
        url = "https://api.openai.com/v1/chat/completions"
        self.assertEqual(redact_url(url), url)

    def test_no_provider_logs_raw_key(self):
        source = inspect.getsource(AIClient._make_request)
        # The logger uses `safe_url` (already sanitized), never the raw url
        self.assertIn("safe_url = redact_url(url)", source)
        self.assertNotIn("{url}", source)

    def test_google_provider_does_not_log_its_url(self):
        source = inspect.getsource(AIClient._chat_google_ai_studio)
        for line in source.splitlines():
            if "logger." in line:
                self.assertNotIn("url", line)


# --- Bug 7/8: config.json reescrito a cada evento e sem atomicidade ----------

class TestConfigSaveDebounce(unittest.TestCase):
    def test_rapid_set_calls_produce_a_single_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config = ConfigManager(str(path))
            config.save()

            real_save = config.save
            calls = []
            config.save = lambda: (calls.append(1), real_save())[1]

            for i in range(300):
                config.set("app.x_position", i)
            config.flush()

            self.assertLessEqual(len(calls), 2, "300 set() calls must not produce 300 writes")

    def test_flush_persists_pending_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config = ConfigManager(str(path))
            config.set("app.opacity", 0.42)
            config.flush()

            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["app"]["opacity"], 0.42)

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config = ConfigManager(str(path))
            config.set("app.opacity", 0.5)
            config.flush()
            self.assertEqual([p.name for p in Path(d).iterdir()], ["config.json"])

    def test_save_is_atomic_and_keeps_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config = ConfigManager(str(path))
            for i in range(20):
                config.set("app.x_position", i)
                config.flush()
                with open(path, encoding="utf-8") as f:
                    json.load(f)  # nunca pode estar truncado


# --- Bug 9/10: contexto sem trim e timestamps enviados ao provider ----------

class TestContextTrimming(unittest.TestCase):
    def test_timestamps_are_not_sent_to_the_provider(self):
        source_path = ROOT / "src" / "main_window.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertIn("_build_request_messages", source)
        self.assertIn('"role": m["role"], "content": m["content"]', source)

    def test_history_normalisation_drops_timestamp(self):
        try:
            from src.main_window import MainWindow
        except ImportError:
            self.skipTest("GTK is not available")

        with tempfile.TemporaryDirectory() as d:
            history = Path(d) / "history.json"
            history.write_text(json.dumps([
                {"timestamp": 1.0, "role": "user", "content": "ola"},
                {"timestamp": 2.0, "role": "assistant", "content": "bom dia"},
            ]))

            with patch("src.main_window.Path.home", return_value=Path(d)):
                window = MainWindow.__new__(MainWindow)
                window.conversation_history = []
                window._load_conversation_history()

            self.assertEqual(
                window.conversation_history,
                [{"role": "user", "content": "ola"},
                 {"role": "assistant", "content": "bom dia"}],
            )

    def test_trimming_respects_limits(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            config.set("context.max_messages", 4)
            config.set("context.max_chars", 5000)

            try:
                from src.main_window import MainWindow
            except ImportError:
                self.skipTest("GTK is not available")

            window = MainWindow.__new__(MainWindow)
            window.config = config
            window.conversation_history = [
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
                for i in range(50)
            ]
            messages = window._build_request_messages()
            self.assertLessEqual(len(messages), 4)
            # Deve manter as mensagens mais recentes
            self.assertEqual(messages[-1]["content"], "msg 49")


# --- Bug 11/12: estatisticas de tokens e Anthropic -------------------------

class TestTokenUsagePersistence(unittest.TestCase):
    def test_usage_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            with patch("src.ai_client.Path.home", return_value=Path(d)):
                client = AIClient(config)
                try:
                    client._update_token_usage("openrouter", 10, 5)
                    usage_file = Path(d) / ".config" / "linux_ai_assistant" / "usage.json"
                    self.assertTrue(usage_file.exists())
                    self.assertEqual(json.loads(usage_file.read_text())["openrouter"]["total"], 15)
                finally:
                    client.session.close()

    def test_usage_survives_a_new_process(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            with patch("src.ai_client.Path.home", return_value=Path(d)):
                first = AIClient(config)
                first._update_token_usage("groq", 100, 50)
                first.session.close()

                second = AIClient(config)
                try:
                    usage = second.get_token_usage("groq")
                    self.assertEqual(usage["total"], 150)
                finally:
                    second.session.close()

    def test_real_api_usage_is_preferred_over_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            with patch("src.ai_client.Path.home", return_value=Path(d)):
                client = AIClient(config)
                try:
                    client._record_usage("groq", {
                        "usage": {"prompt_tokens": 11, "completion_tokens": 4}
                    })
                    self.assertEqual(client.get_token_usage("groq")["total"], 15)
                finally:
                    client.session.close()

    def test_google_usage_metadata_is_understood(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            with patch("src.ai_client.Path.home", return_value=Path(d)):
                client = AIClient(config)
                try:
                    client._record_usage("google_ai_studio", {
                        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 2}
                    })
                    self.assertEqual(client.get_token_usage("google_ai_studio")["total"], 11)
                finally:
                    client.session.close()

    def test_reset_clears_persisted_usage(self):
        with tempfile.TemporaryDirectory() as d:
            config = ConfigManager(str(Path(d) / "config.json"))
            with patch("src.ai_client.Path.home", return_value=Path(d)):
                client = AIClient(config)
                client._update_token_usage("groq", 10, 5)
                client.reset_token_usage()
                self.assertEqual(client.get_token_usage(), {})
                client.session.close()


class TestAnthropicMessageMapping(unittest.TestCase):
    def test_system_prompt_is_extracted(self):
        system, messages = AIClient._split_anthropic_messages([
            {"role": "system", "content": "You are a Linux expert"},
            {"role": "user", "content": "ola"},
        ])
        self.assertEqual(system, "You are a Linux expert")
        self.assertEqual(messages, [{"role": "user", "content": "ola"}])

    def test_system_is_not_converted_to_assistant(self):
        # Regression: the system prompt was converted to `assistant`.
        _, messages = AIClient._split_anthropic_messages([
            {"role": "system", "content": "instrucoes"},
            {"role": "user", "content": "pergunta"},
        ])
        self.assertTrue(all(m["role"] != "assistant" for m in messages))

    def test_version_is_a_header_not_a_body_field(self):
        source = inspect.getsource(AIClient._chat_anthropic)
        self.assertIn('"anthropic-version": "2023-06-01"', source)
        self.assertNotIn('"anthropic_version"', source)

    def test_bearer_header_removed(self):
        source = inspect.getsource(AIClient._chat_anthropic)
        self.assertNotIn("Authorization", source)


# --- Bug 15: tratamento de erro do GTK -------------------------------------

class TestGracefulGtkFailure(unittest.TestCase):
    def test_app_reports_missing_gtk_clearly(self):
        import src.app as app
        if app.GTK_AVAILABLE:
            self.skipTest("GTK is available in this environment")
        self.assertIsNone(app.Gtk)
        # Regression: GTK_IMPORT_ERROR must hold a real exception (or nothing),
        # not an arbitrary placeholder that any assertion would accept.
        self.assertTrue(
            app.GTK_IMPORT_ERROR is None or isinstance(app.GTK_IMPORT_ERROR, Exception)
        )

    def test_main_window_is_not_imported_without_gtk(self):
        import src.app as app
        if app.GTK_AVAILABLE:
            self.skipTest("GTK is available in this environment")
        self.assertNotIn("src.main_window", sys.modules)


# --- Bug 16: CSS sanitisation ---------------------------------------------

class TestThemeSanitisation(unittest.TestCase):
    def setUp(self):
        try:
            from src.main_window import safe_color, safe_font_family, safe_number
        except ImportError:
            self.skipTest("GTK is not available")
        self.safe_color = safe_color
        self.safe_font_family = safe_font_family
        self.safe_number = safe_number

    def test_valid_colors_pass_through(self):
        self.assertEqual(self.safe_color("#1e1e1e"), "#1e1e1e")
        self.assertEqual(self.safe_color("#FFF"), "#FFF")

    def test_css_injection_in_color_is_rejected(self):
        payload = "red; } * { background-image: url(http://evil/x); } #x {"
        self.assertEqual(self.safe_color(payload), "#1e1e1e")

    def test_non_string_color_is_rejected(self):
        self.assertEqual(self.safe_color(None), "#1e1e1e")
        self.assertEqual(self.safe_color(123), "#1e1e1e")

    def test_font_family_injection_is_rejected(self):
        payload = "Monospace; } * { color: red"
        self.assertEqual(self.safe_font_family(payload), "Monospace")

    def test_normal_font_family_passes(self):
        self.assertEqual(self.safe_font_family("DejaVu Sans Mono"), "DejaVu Sans Mono")

    def test_number_bounds_are_enforced(self):
        self.assertEqual(self.safe_number(9999, 12, int, minimum=4, maximum=72), 12)
        self.assertEqual(self.safe_number("abc", 12, int, minimum=4, maximum=72), 12)
        self.assertEqual(self.safe_number(14, 12, int, minimum=4, maximum=72), 14)


if __name__ == "__main__":
    unittest.main()
