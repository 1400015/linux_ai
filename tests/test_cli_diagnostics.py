"""CLI diagnostics distinguish stored settings from effective credentials."""

import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.cli import CLIApp
from src.ai_client import AIClient
from src.config_manager import ConfigManager
from src.credential_store import SecretServiceCredentialStore
from tests.test_credential_store import FakeCollection


GOOGLE = 'LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY'
MISTRAL = 'LINUX_AI_API_PROVIDERS_MISTRAL_API_KEY'


class TestCliDiagnostics(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('network request'))
        self.network.start()
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.app = CLIApp.__new__(CLIApp)
        self.app.config = self.config
        self.app.ai_client = SimpleNamespace(get_supported_providers=lambda: [
            'google_ai_studio', 'mistral', 'local_llm',
        ])

    def tearDown(self):
        self.config.flush()
        self.network.stop()
        self.environment.stop()
        self.directory.cleanup()

    def command(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            code = self.app._run_command(CLIApp.parse_args(list(arguments)))
        return code, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def provider_line(output, provider):
        return next(line for line in output.splitlines() if provider in line and 'Model:' in line)

    def test_empty_environment_overrides_stored_key_consistently(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-google-stored')
        with patch.dict(os.environ, {GOOGLE: ''}):
            code, output, _ = self.command('providers')
            self.assertEqual(code, 0)
            line = self.provider_line(output, 'google_ai_studio')
            self.assertIn('Key: ✗', line)
            self.assertIn(GOOGLE, line)
            self.assertIn('empty override blocks stored key', line)
            self.assertNotIn('synthetic-google-stored', output)
            code, output, _ = self.command('config', 'get', 'api.providers.google_ai_studio.api_key')
            self.assertEqual(code, 0)
            self.assertEqual(output, 'api.providers.google_ai_studio.api_key: \n')
            _, output, _ = self.command('config', 'list')
            self.assertIn('Stored configuration', output)
            self.assertIn('api_key: ********', output)
            self.assertNotIn('synthetic-google-stored', output)

    def test_nonempty_environment_key_bypasses_vault_and_stays_masked(self):
        self.config.set('api.providers.google_ai_studio.key_storage', 'secret-service')
        store = Mock()
        self.config._credential_store = store
        with patch.dict(os.environ, {GOOGLE: 'synthetic-environment-key'}):
            _, output, _ = self.command('providers')
            line = self.provider_line(output, 'google_ai_studio')
            self.assertIn('Key: ✓', line)
            self.assertIn('environment: ' + GOOGLE, line)
            self.assertNotIn('synthetic-environment-key', output)
            _, output, _ = self.command('config', 'get', 'api.providers.google_ai_studio.api_key')
            self.assertEqual(output, 'api.providers.google_ai_studio.api_key: ************\n')
        store.lookup.assert_not_called()
        store.unlock.assert_not_called()

    def test_legacy_environment_key_is_effective_even_with_empty_json_key(self):
        with patch.dict(os.environ, {'GOOGLE_AI_STUDIO_KEY': 'synthetic-legacy-key'}):
            _, output, _ = self.command('providers')
            line = self.provider_line(output, 'google_ai_studio')
            self.assertIn('Key: ✓', line)
            self.assertIn('environment: GOOGLE_AI_STUDIO_KEY', line)
            _, key_output, _ = self.command('config', 'get', 'api.providers.google_ai_studio.api_key')
        self.assertEqual(key_output, 'api.providers.google_ai_studio.api_key: ************\n')
        self.assertNotIn('synthetic-legacy-key', output + key_output)

    def test_stored_key_presence_is_not_claimed_as_authentication(self):
        self.config.set_api_key('mistral', 'synthetic-stored-key')
        _, output, _ = self.command('providers')
        self.assertIn('Key: ✓ (config)', self.provider_line(output, 'mistral'))
        self.assertIn('not proof that the provider accepts it', output)
        self.assertIn('no API request or key-store unlock was performed', output)
        self.assertIn('Key: N/A (not required)', self.provider_line(output, 'local_llm'))
        self.assertNotIn('synthetic-stored-key', output)

    def vault(self, locked=False):
        collection = FakeCollection()
        store = SecretServiceCredentialStore('synthetic-profile', lambda: collection)
        self.config._credential_store = store
        store.store('google_ai_studio', 'synthetic-vault-key')
        self.config.set('api.providers.google_ai_studio.key_storage', 'secret-service')
        collection.locked = locked
        return collection

    def test_locked_vault_never_reports_json_fallback_or_requests_unlock(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-json-decoy')
        collection = self.vault(locked=True)
        _, output, _ = self.command('providers')
        self.assertIn('Key: ✗ (Secret Service; not set or unavailable)',
                      self.provider_line(output, 'google_ai_studio'))
        _, key_output, _ = self.command('config', 'get', 'api.providers.google_ai_studio.api_key')
        self.assertEqual(key_output, 'api.providers.google_ai_studio.api_key: None\n')
        self.assertEqual(collection.unlocks, 0)
        self.assertNotIn('synthetic-json-decoy', output + key_output)
        self.assertNotIn('synthetic-vault-key', output + key_output)

    def test_unlocked_vault_is_effective_despite_empty_json_key(self):
        collection = self.vault()
        _, output, _ = self.command('providers')
        self.assertIn('Key: ✓ (Secret Service)', self.provider_line(output, 'google_ai_studio'))
        _, key_output, _ = self.command('config', 'get', 'api.providers.google_ai_studio.api_key')
        self.assertEqual(key_output, 'api.providers.google_ai_studio.api_key: ************\n')
        self.assertEqual(collection.unlocks, 0)
        self.assertNotIn('synthetic-vault-key', output + key_output)

    def test_unknown_path_fails_but_a_real_null_value_is_preserved(self):
        code, output, error = self.command('config', 'get', 'api.assistance.mode')
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertIn('Unknown configuration key: api.assistance.mode', error)
        code, output, error = self.command('config', 'get', 'assistance.mode')
        self.assertEqual((code, output, error), (0, 'assistance.mode: auto\n', ''))
        self.config.set('app.optional', None)
        code, output, error = self.command('config', 'get', 'app.optional')
        self.assertEqual((code, output, error), (0, 'app.optional: None\n', ''))

    def test_environment_cannot_turn_a_typo_into_a_valid_configuration_path(self):
        with patch.dict(os.environ, {'LINUX_AI_API_ASSISTANCE_MODE': 'synthetic-invalid-path-value'}):
            code, output, error = self.command('config', 'get', 'api.assistance.mode')
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertIn('Unknown configuration key', error)
        self.assertNotIn('synthetic-invalid-path-value', error)

    def test_invalid_storage_is_reported_and_remote_model_matches_client(self):
        self.config.set('api.providers.mistral.key_storage', 'invalid')
        with patch.dict(os.environ, {'LINUX_AI_API_PROVIDERS_MISTRAL_MODEL': 'synthetic-model-choice'}):
            _, output, _ = self.command('providers')
            client = AIClient.__new__(AIClient)
            client.config = self.config
            actual_model = client._get_api_config('mistral')['model']
        line = self.provider_line(output, 'mistral')
        self.assertIn('Key: ✗ (invalid credential storage)', line)
        self.assertIn('Model: ' + actual_model, line)
        self.assertNotIn('synthetic-model-choice', line)

    def test_provider_map_model_override_matches_remote_chat_and_malformed_entries_are_safe(self):
        client = AIClient.__new__(AIClient)
        client.config = self.config
        models = {'mistral': {'model': 'synthetic-map-model'}, 'google_ai_studio': 'invalid-entry'}
        with patch.dict(os.environ, {'LINUX_AI_API_PROVIDERS': json.dumps(models)}):
            _, output, _ = self.command('providers')
            actual_model = client._get_api_config('mistral')['model']
        self.assertIn('Model: ' + actual_model, self.provider_line(output, 'mistral'))
        self.assertIn('Model: N/A', self.provider_line(output, 'google_ai_studio'))
        self.config.config['api']['providers'] = []
        code, output, error = self.command('providers')
        self.assertEqual((code, error), (0, ''))
        self.assertIn('Model: N/A', self.provider_line(output, 'mistral'))

    def test_local_model_leaf_override_matches_local_chat_settings(self):
        client = AIClient.__new__(AIClient)
        client.config = self.config
        with patch.dict(os.environ, {'LINUX_AI_API_PROVIDERS_LOCAL_LLM_MODEL': 'synthetic-local-model'}):
            _, output, _ = self.command('providers')
            actual_model = client._local_settings()['model']
        self.assertIn('Model: ' + actual_model, self.provider_line(output, 'local_llm'))

    def test_getting_parent_sections_never_prints_nested_api_keys(self):
        self.config.set_api_key('google_ai_studio', 'synthetic-stored-google')
        self.config.set_api_key('mistral', 'synthetic-stored-mistral')
        for section in ('api', 'api.providers', 'api.providers.google_ai_studio'):
            with self.subTest(section=section):
                code, output, error = self.command('config', 'get', section)
                self.assertEqual((code, error), (0, ''))
                self.assertIn('************', output)
                self.assertNotIn('synthetic-stored-google', output)
                self.assertNotIn('synthetic-stored-mistral', output)
        self.assertEqual(self.config.get_api_key('google_ai_studio'), 'synthetic-stored-google')

    def test_nested_environment_sections_are_also_redacted_without_mutation(self):
        override = '{"items": [{"API_KEY": "synthetic-nested-secret"}], "mode": "auto"}'
        with patch.dict(os.environ, {'LINUX_AI_ASSISTANCE': override}):
            code, output, error = self.command('config', 'get', 'assistance')
            self.assertEqual((code, error), (0, ''))
            self.assertNotIn('synthetic-nested-secret', output)
            self.assertIn('************', output)
            self.assertIn("'mode': 'auto'", output)
        self.assertEqual(self.config.get('assistance'), {'mode': 'auto'})

    def test_config_list_redacts_keys_inside_lists(self):
        self.config.set('app.extra', [{'API_KEY': 'synthetic-listed-secret', 'enabled': True}])
        code, output, error = self.command('config', 'list')
        self.assertEqual((code, error), (0, ''))
        self.assertNotIn('synthetic-listed-secret', output)
        self.assertIn("'API_KEY': '************'", output)
        self.assertIn("'enabled': True", output)


if __name__ == '__main__':
    unittest.main()
