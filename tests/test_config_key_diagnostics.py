"""Tests for the config-key diagnostics added after the 'always offline' case.

A real diagnosis on an Ubuntu VM found a stored API key silently disabled by
an empty .env placeholder (documented contract: an empty canonical variable
disables the stored key), while the chat status line only said 'provider not
configured'. These tests pin the helpers that surface that condition.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config_manager import (
    ConfigManager,
    _dotenv_bindings,
    _read_owned_dotenv,
    _API_KEY_ENV_RE,
)

CANONICAL = "LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY"


class StoredKeyShadowingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = ConfigManager(os.path.join(self._tmp.name, "config.json"))

    def test_stored_key_shadowed_by_empty_env(self):
        self.config.set_api_key("google_ai_studio", "sk-test-123")
        with patch.dict(os.environ, {CANONICAL: ""}):
            # Placeholder vazio desativa a chave armazenada (contrato do .env)
            self.assertTrue(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))
        # Sem a variável no ambiente: a chave armazenada conta
        self.assertFalse(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))

    def test_legacy_non_empty_override_wins_over_empty_canonical(self):
        self.config.set_api_key("google_ai_studio", "sk-test-123")
        with patch.dict(os.environ, {CANONICAL: "", "GOOGLE_AI_STUDIO_KEY": "sk-real"}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))

    def test_no_stored_key_is_not_shadowing(self):
        with patch.dict(os.environ, {CANONICAL: ""}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))

    def test_api_key_env_var_name(self):
        self.assertEqual(self.config.api_key_env_var("google_ai_studio"), CANONICAL)
        self.assertRegex(CANONICAL, _API_KEY_ENV_RE)

    def test_dotenv_reader_never_leaks_attributeerror(self):
        # Antes usava os.O_DIRECTORY/os.O_NOFOLLOW diretamente: AttributeError
        # em plataformas sem os flags, fora dos except previstos. Contrato:
        # em POSIX lê e valida; noutras plataformas degrada com OSError —
        # nunca AttributeError.
        env_path = Path(self._tmp.name) / ".env"
        env_path.write_text(
            "LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY=\n"
            "OTHER=value\n",
            encoding="utf-8",
        )
        try:
            contents, _identity = _read_owned_dotenv(env_path)
        except (OSError, ValueError):
            return  # degradação esperada onde os dirfd não existem (Windows)
        self.assertIn("GOOGLE_AI_STUDIO_API_KEY=", contents)
        bindings = {b.key: b.value for b in _dotenv_bindings(contents)}
        self.assertEqual(bindings.get("LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY"), "")
        self.assertEqual(bindings.get("OTHER"), "value")

    def test_empty_placeholder_detected_at_startup(self):
        # O caso real: .env com placeholder vazio criado pelo instalador antigo
        env_path = Path(self._tmp.name) / ".env"
        env_path.write_text(CANONICAL + "=\n", encoding="utf-8")
        with patch.dict(os.environ, {CANONICAL: ""}):
            config = ConfigManager(os.path.join(self._tmp.name, "config.json"))
            config.set_api_key("google_ai_studio", "sk-stored")
            self.assertTrue(config.stored_key_shadowed_by_empty_env("google_ai_studio"))
        self.assertFalse(config.stored_key_shadowed_by_empty_env("google_ai_studio"))


if __name__ == "__main__":
    unittest.main()
