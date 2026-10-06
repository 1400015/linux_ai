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
from unittest.mock import Mock, patch

from src.config_manager import (
    ConfigManager,
    _dotenv_bindings,
    _read_owned_dotenv,
    _API_KEY_ENV_RE,
)
from src.credential_store import SecretServiceCredentialStore
from tests.test_credential_store import FakeCollection

CANONICAL = "LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY"


class StoredKeyShadowingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('network request'))
        network.start()
        self.addCleanup(network.stop)
        self.config = ConfigManager(os.path.join(self._tmp.name, "config.json"))
        self.addCleanup(self.config.flush)

    def test_stored_key_shadowed_by_empty_env(self):
        self.config.set_api_key("google_ai_studio", "synthetic-stored-key")
        with patch.dict(os.environ, {CANONICAL: ""}):
            # Placeholder vazio desativa a chave armazenada (contrato do .env)
            self.assertTrue(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))
        # Sem a variável no ambiente: a chave armazenada conta
        self.assertFalse(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))

    def test_empty_canonical_blocks_stored_and_legacy_keys(self):
        for provider, legacy in (
            ('google_ai_studio', 'GOOGLE_AI_STUDIO_KEY'),
            ('openrouter', 'OPENROUTER_API_KEY'),
        ):
            with self.subTest(provider=provider):
                self.config.set_api_key(provider, "synthetic-stored-key")
                canonical = self.config.api_key_env_var(provider)
                with patch.dict(os.environ, {canonical: "", legacy: "synthetic-legacy-key"}):
                    self.assertEqual(self.config.get_api_key(provider), "")
                    self.assertTrue(self.config.stored_key_shadowed_by_empty_env(provider))

    def test_nonempty_legacy_without_canonical_is_not_shadowing(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-stored-key')
        with patch.dict(os.environ, {'GOOGLE_AI_STUDIO_KEY': 'synthetic-legacy-key'}):
            self.assertEqual(self.config.get_api_key('google_ai_studio'), 'synthetic-legacy-key')
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))

    def test_no_stored_key_is_not_shadowing(self):
        with patch.dict(os.environ, {CANONICAL: ""}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env("google_ai_studio"))

    def test_api_key_env_var_name(self):
        self.assertEqual(self.config.api_key_env_var("google_ai_studio"), CANONICAL)
        self.assertRegex(CANONICAL, _API_KEY_ENV_RE)

    def test_owned_dotenv_reader_preserves_empty_assignments(self):
        env_path = Path(self._tmp.name) / ".env"
        env_path.write_text(
            "LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY=\n"
            "OTHER=value\n",
            encoding="utf-8",
        )
        contents, _identity = _read_owned_dotenv(env_path)
        self.assertIn("GOOGLE_AI_STUDIO_API_KEY=", contents)
        bindings = {b.key: b.value for b in _dotenv_bindings(contents)}
        self.assertEqual(bindings.get("LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY"), "")
        self.assertEqual(bindings.get("OTHER"), "value")

    def test_empty_placeholder_detected_at_startup(self):
        # O caso real: .env com placeholder vazio criado pelo instalador antigo
        env_path = Path(self._tmp.name) / ".env"
        env_path.write_text(CANONICAL + "=\n", encoding="utf-8")
        self.config.set_api_key("google_ai_studio", "synthetic-stored-key")
        self.config.flush()
        self.assertNotIn(CANONICAL, os.environ)
        config = ConfigManager(os.path.join(self._tmp.name, "config.json"))
        self.addCleanup(config.flush)
        self.assertEqual(os.environ.get(CANONICAL), "")
        self.assertEqual(config.get_api_key('google_ai_studio'), "")
        self.assertTrue(config.stored_key_shadowed_by_empty_env("google_ai_studio"))
        self.assertTrue(config.can_remove_empty_api_key_override('google_ai_studio'))

    def vault(self, stored_key=None):
        collection = FakeCollection()
        store = SecretServiceCredentialStore('synthetic-profile', lambda: collection)
        if stored_key:
            store.store('google_ai_studio', stored_key)
        self.config._credential_store = store
        self.config.set('api.providers.google_ai_studio.key_storage', 'secret-service')
        return collection

    def test_locked_selected_vault_does_not_use_residual_json_key_or_unlock(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-json-decoy')
        collection = self.vault('synthetic-vault-key')
        collection.locked = True
        with patch.dict(os.environ, {CANONICAL: ''}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))
        self.assertIsNone(self.config.get_api_key('google_ai_studio'))
        self.assertEqual(collection.unlocks, 0)

    def test_empty_selected_vault_does_not_use_residual_json_key_or_create_item(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-json-decoy')
        collection = self.vault()
        with patch.dict(os.environ, {CANONICAL: ''}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))
        self.assertIsNone(self.config.get_api_key('google_ai_studio'))
        self.assertEqual(collection.creates, [])
        self.assertEqual(collection.unlocks, 0)

    def test_unlocked_selected_vault_is_shadowed_despite_unusable_json_key(self):
        # A leftover ciphertext cannot hide the usable key in the selected vault.
        self.config.config['api']['providers']['google_ai_studio']['api_key'] = 'fernet:v1:broken'
        collection = self.vault('synthetic-vault-key')
        with patch.dict(os.environ, {CANONICAL: ''}):
            self.assertEqual(self.config.get_api_key('google_ai_studio'), '')
            with patch.object(self.config, '_decrypt_value', side_effect=AssertionError('JSON fallback')):
                self.assertTrue(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))
        self.assertEqual(self.config.get_api_key('google_ai_studio'), 'synthetic-vault-key')
        self.assertEqual(collection.unlocks, 0)

    def test_missing_or_nonempty_canonical_does_not_inspect_vault(self):
        self.config.set('api.providers.google_ai_studio.key_storage', 'secret-service')
        store = Mock()
        self.config._credential_store = store
        self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))
        with patch.dict(os.environ, {CANONICAL: 'synthetic-env-key'}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))
        store.lookup.assert_not_called()
        store.unlock.assert_not_called()

    def test_invalid_selected_storage_does_not_claim_residual_json_key_is_usable(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-json-decoy')
        self.config.set('api.providers.google_ai_studio.key_storage', 'invalid-backend')
        with patch.dict(os.environ, {CANONICAL: ''}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))

    def test_unreadable_encrypted_key_is_not_reported_as_usable(self):
        self.config.config['api']['providers']['google_ai_studio']['api_key'] = 'fernet:v1:broken'
        self.config._encryption_key = None
        with patch.dict(os.environ, {CANONICAL: ''}):
            self.assertFalse(self.config.stored_key_shadowed_by_empty_env('google_ai_studio'))


if __name__ == "__main__":
    unittest.main()
