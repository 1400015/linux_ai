"""Credentials must survive cryptographic failures without plaintext fallback."""

import base64
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.config_manager import ConfigManager, CredentialEncryptionError

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None


@unittest.skipIf(Fernet is None, "optional cryptography dependency is absent")
class TestCredentialEncryption(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.config = ConfigManager(str(self.root / 'config.json'))

    def tearDown(self):
        self.config.flush()
        self.env.stop()
        self.directory.cleanup()

    def enabled(self):
        self.config.enable_encryption()

    def test_tagged_and_both_legacy_formats_decrypt(self):
        self.enabled()
        secret = 'synthetic-credential'
        token = Fernet(self.config._encryption_key).encrypt(secret.encode())
        for value in ('fernet:v1:' + token.decode(), token.decode(), base64.b64encode(token).decode()):
            self.assertEqual(self.config._decrypt_value(value), secret)

    def test_wrong_key_and_damaged_ciphertext_are_unset(self):
        self.enabled()
        token = Fernet(Fernet.generate_key()).encrypt(b'synthetic')
        for value in ('fernet:v1:' + token.decode(), base64.b64encode(token).decode(),
                      'fernet:v1:broken', 'Z0FBQU!!'):
            self.assertIsNone(self.config._decrypt_value(value))

    def test_failure_preserves_old_key_and_does_not_log_secret(self):
        self.enabled()
        self.config.set_api_key('openrouter', 'old-synthetic')
        before = copy.deepcopy(self.config.config)
        with patch('cryptography.fernet.Fernet.encrypt', side_effect=RuntimeError('new-synthetic')), \
                self.assertLogs('src.config_manager', 'ERROR') as logs:
            with self.assertRaises(CredentialEncryptionError):
                self.config.set_api_key('openrouter', 'new-synthetic')
        self.assertEqual(self.config.config, before)
        self.assertNotIn('new-synthetic', '\n'.join(logs.output))

    def test_missing_key_blocks_writes_even_when_encryption_remains_enabled(self):
        self.enabled()
        self.config._encryption_key = None
        with self.assertRaises(CredentialEncryptionError):
            self.config.set_api_key('openrouter', 'synthetic')
        self.assertTrue(self.config.get('app.encryption_enabled'))

    def test_lost_key_is_not_regenerated_for_existing_ciphertext(self):
        self.enabled()
        self.config.set_api_key('openrouter', 'synthetic')
        self.config.flush()
        (self.root / '.encryption_key').unlink()
        self.config._encryption_key = None
        self.config._load_encryption_key()
        self.assertFalse((self.root / '.encryption_key').exists())
        self.assertTrue(self.config.get('app.encryption_enabled'))
        self.assertIsNone(self.config.get_api_key('openrouter'))

    def test_enabling_encrypts_stored_value_and_never_environment_override(self):
        self.config.set_api_key('openrouter', 'stored-synthetic')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-synthetic'}):
            self.enabled()
        raw = self.config.config['api']['providers']['openrouter']['api_key']
        self.assertTrue(raw.startswith('fernet:v1:'))
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')

    def test_enable_failure_is_atomic_for_all_keys(self):
        self.config.set_api_key('openrouter', 'synthetic-first')
        self.config.set_api_key('anthropic', 'synthetic-second')
        before = copy.deepcopy(self.config.config)
        with patch('cryptography.fernet.Fernet.encrypt', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(CredentialEncryptionError):
                self.enabled()
        self.assertEqual(self.config.config, before)

    def test_disable_failure_preserves_flag_and_ciphertext(self):
        self.enabled()
        self.config.set_api_key('openrouter', 'synthetic')
        before = copy.deepcopy(self.config.config)
        self.config._encryption_key = Fernet.generate_key()
        with self.assertRaises(CredentialEncryptionError):
            self.config.enable_encryption(False)
        self.assertEqual(self.config.config, before)

    def test_disable_preserves_stored_value_under_env_override(self):
        self.enabled()
        self.config.set_api_key('openrouter', 'stored-synthetic')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-synthetic'}):
            self.config.enable_encryption(False)
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')

    def test_ciphertext_never_becomes_effective_key_when_flag_disabled(self):
        self.config.config['api']['providers']['openrouter']['api_key'] = 'fernet:v1:broken'
        self.assertIsNone(self.config.get_api_key('openrouter'))

    def test_canonical_override_including_empty_wins_over_legacy(self):
        canonical = 'LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY'
        for value in ('synthetic-canonical', ''):
            with patch.dict(os.environ, {canonical: value, 'OPENROUTER_API_KEY': 'synthetic-legacy'}):
                self.assertEqual(self.config.get_api_key('openrouter'), value)
                self.assertEqual(self.config.get_api_key_env_override('openrouter'), canonical)

    def test_custom_and_existing_models_are_not_migrated_automatically(self):
        for provider, model in [('anthropic', 'custom-model'), ('groq', 'llama-3.1-8b-instant')]:
            self.config.set('api.providers.{}.model'.format(provider), model)
        self.config.flush()
        self.config.reload()
        self.assertEqual(self.config.get('api.providers.anthropic.model'), 'custom-model')
        self.assertEqual(self.config.get('api.providers.groq.model'), 'llama-3.1-8b-instant')
