"""Vault migration and environment precedence without any live secrets."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.config_manager import ConfigManager
from src.credential_store import CredentialStoreError, SecretServiceCredentialStore
from tests.test_credential_store import FakeCollection


class TestCredentialConfig(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.collection = FakeCollection()
        self.store = SecretServiceCredentialStore('synthetic-profile', lambda: self.collection)
        self.config._credential_store = self.store

    def tearDown(self):
        self.config.flush()
        self.environment.stop()
        self.directory.cleanup()

    def test_migration_copies_stored_key_and_removes_json_key_only_after_verification(self):
        self.config.set_api_key('openrouter', 'stored-synthetic')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-synthetic'}):
            self.config.set_api_key_storage('openrouter', 'secret-service')
        self.assertEqual(self.store.lookup('openrouter'), 'stored-synthetic')
        data = json.loads((self.root / 'config.json').read_text())
        record = data['api']['providers']['openrouter']
        self.assertEqual(record['api_key'], '')
        self.assertEqual(record['key_storage'], 'secret-service')
        self.assertNotIn('environment-synthetic', json.dumps(data))

    def test_verification_failure_keeps_original_json_key(self):
        self.config.set_api_key('openrouter', 'stored-synthetic')
        with patch.object(self.store, 'store', side_effect=CredentialStoreError('verification')):
            with self.assertRaises(CredentialStoreError):
                self.config.set_api_key_storage('openrouter', 'secret-service')
        self.assertEqual(self.config.get_api_key_storage('openrouter'), 'config')
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')

    def test_locked_vault_has_no_plaintext_fallback(self):
        self.config.set_api_key('openrouter', 'stored-synthetic')
        self.collection.locked = True
        with self.assertRaises(CredentialStoreError):
            self.config.set_api_key_storage('openrouter', 'secret-service')
        self.assertEqual(self.collection.unlocks, 0)
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')

    def test_lookup_locked_vault_is_unconfigured_without_prompt(self):
        self.config.set('api.providers.openrouter.key_storage', 'secret-service')
        self.collection.locked = True
        self.assertIsNone(self.config.get_api_key('openrouter'))
        self.assertEqual(self.collection.unlocks, 0)

    def test_environment_lookup_does_not_contact_vault(self):
        self.config.set('api.providers.openrouter.key_storage', 'secret-service')
        self.config._credential_store = Mock()
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'synthetic-env'}):
            self.assertEqual(self.config.get_api_key('openrouter'), 'synthetic-env')
        self.config._credential_store.lookup.assert_not_called()

    def test_explicit_unlock_only_unlocks_when_requested(self):
        self.collection.locked = True
        self.config.unlock_api_key_store()
        self.assertEqual(self.collection.unlocks, 1)

    def test_vault_write_and_clear_never_enter_json(self):
        self.config.set_api_key_storage('openrouter', 'secret-service')
        self.config.set_api_key('openrouter', 'synthetic-vault-only')
        self.config.flush()
        self.assertNotIn('synthetic-vault-only', (self.root / 'config.json').read_text())
        self.assertEqual(self.config.get_api_key('openrouter'), 'synthetic-vault-only')
        self.config.set_api_key('openrouter', '')
        self.assertIsNone(self.config.get_api_key('openrouter'))

    def test_reverse_migration_is_explicit_and_ignores_env(self):
        self.config.set_api_key_storage('openrouter', 'secret-service')
        self.config.set_api_key('openrouter', 'synthetic-stored')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'synthetic-env'}):
            self.config.set_api_key_storage('openrouter', 'config')
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'synthetic-stored')

    def test_json_save_failure_retains_verified_vault_copy_and_previous_selection(self):
        self.config.set_api_key('openrouter', 'stored-synthetic')
        def fail():
            self.config.last_save_error = 'synthetic IO failure'
        with patch.object(self.config, 'save', side_effect=fail):
            with self.assertRaises(CredentialStoreError):
                self.config.set_api_key_storage('openrouter', 'secret-service')
        self.assertEqual(self.config.get_api_key_storage('openrouter'), 'config')
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')
        self.assertEqual(self.store.lookup('openrouter'), 'stored-synthetic')

    def test_new_profile_is_saved_only_on_explicit_unlock(self):
        self.config._credential_store = None
        factory = Mock(return_value=self.store)
        with patch('src.credential_store.SecretServiceCredentialStore', factory):
            self.config.get_api_key('openrouter')
            factory.assert_not_called()
            self.config.unlock_api_key_store()
        self.assertTrue(self.config.get_config_value('app.credential_profile'))
        data = json.loads((self.root / 'config.json').read_text())
        self.assertEqual(data['app']['credential_profile'], factory.call_args.args[0])

    def test_invalid_backend_is_unconfigured_without_fallback(self):
        self.config.set_api_key('openrouter', 'synthetic-stored')
        self.config.set('api.providers.openrouter.key_storage', 'invalid-backend')
        self.assertIsNone(self.config.get_api_key('openrouter'))

    def test_reload_discards_a_cached_profile_connection(self):
        self.config.flush()
        self.config.reload()
        self.assertIsNone(self.config._credential_store)
