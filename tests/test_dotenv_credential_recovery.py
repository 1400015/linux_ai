"""Recover installer placeholders without changing inherited credentials."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.ai_client import AIClient
from src.config_manager import ConfigManager, _DOTENV_MAX_BYTES


GOOGLE = 'LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY'
MISTRAL = 'LINUX_AI_API_PROVIDERS_MISTRAL_API_KEY'


class TestDotenvCredentialRecovery(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.dotenv = self.root / '.env'
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.config = None

    def tearDown(self):
        if self.config is not None:
            self.config.flush()
        self.environment.stop()
        self.directory.cleanup()

    def manager(self, contents):
        self.dotenv.write_text(contents, encoding='utf-8')
        self.config = ConfigManager(str(self.root / 'config.json'))
        return self.config

    def assert_refused(self, provider='google_ai_studio'):
        original = self.dotenv.read_bytes()
        environment = dict(os.environ)
        self.assertFalse(self.config.can_remove_empty_api_key_override(provider))
        with self.assertRaisesRegex(ValueError, 'could not be removed safely'):
            self.config.remove_empty_api_key_override(provider)
        self.assertEqual(self.dotenv.read_bytes(), original)
        self.assertEqual(dict(os.environ), environment)

    def test_copied_installation_template_does_not_disable_saved_online_keys(self):
        template = Path(__file__).parents[1] / 'config' / '.env.example'
        config = self.manager(template.read_text())
        client = object.__new__(AIClient)
        client.config = config
        for provider in ('google_ai_studio', 'mistral'):
            config.set_api_key(provider, 'synthetic-stored-key')
            config.set('api.default_provider', provider)
            self.assertIsNone(config.get_api_key_env_override(provider))
            self.assertEqual(client.provider_status().state, 'configured')

    def test_explicit_removal_preserves_other_records_and_uses_stored_key(self):
        before = '# retained comment\r\nOTHER_TOKEN="synthetic\nmultiline"\r\n'
        assignment = f'export {GOOGLE}=\r\n'
        after = "OTHER_SETTING='retained value'\r\n"
        # Write bytes to exercise verbatim CRLF preservation.
        self.dotenv.write_bytes((before + assignment + after).encode())
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.set_api_key('google_ai_studio', 'synthetic-stored')
        self.assertEqual(self.config.get_api_key('google_ai_studio'), '')
        self.assertTrue(self.config.can_remove_empty_api_key_override('google_ai_studio'))
        self.config.remove_empty_api_key_override('google_ai_studio')
        self.assertEqual(self.dotenv.read_bytes(), (before + after).encode())
        self.assertEqual(self.dotenv.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(GOOGLE, os.environ)
        self.assertEqual(self.config.get_api_key('google_ai_studio'), 'synthetic-stored')
        self.assertEqual(os.environ['OTHER_TOKEN'], 'synthetic\nmultiline')
        self.assertFalse(self.config.can_remove_empty_api_key_override('google_ai_studio'))

    def test_sequential_google_and_mistral_removals_keep_source_provenance(self):
        config = self.manager(f'{GOOGLE}=\n{MISTRAL}=\nRETAINED=value\n')
        for provider in ('google_ai_studio', 'mistral'):
            self.assertTrue(config.can_remove_empty_api_key_override(provider))
            config.remove_empty_api_key_override(provider)
        self.assertEqual(self.dotenv.read_text(), 'RETAINED=value\n')
        self.assertNotIn(GOOGLE, os.environ)
        self.assertNotIn(MISTRAL, os.environ)

    def test_inherited_empty_value_is_never_removed_even_with_matching_dotenv_line(self):
        os.environ[GOOGLE] = ''
        self.manager(f'{GOOGLE}=\n')
        self.assert_refused()

    def test_nonempty_value_is_never_removed(self):
        self.manager(f'{GOOGLE}=synthetic-environment-key\n')
        self.assert_refused()

    def test_modified_process_override_is_not_removed(self):
        self.manager(f'{GOOGLE}=\n')
        os.environ[GOOGLE] = 'synthetic-replacement'
        self.assert_refused()

    def test_changed_dotenv_is_not_rewritten(self):
        self.manager(f'{GOOGLE}=\n')
        self.dotenv.write_text(f'{GOOGLE}=\nNEW_SETTING=retained\n')
        self.assert_refused()

    def test_duplicate_or_interpolated_and_invalid_records_are_refused(self):
        for contents in (f'{GOOGLE}=\n{GOOGLE}=\n', f'{GOOGLE}=${{ABSENT}}\n',
                         f'{GOOGLE}=\nINVALID="unclosed\n', f'{GOOGLE}\n'):
            with self.subTest(contents=contents), patch.dict(os.environ, {}, clear=True):
                self.manager(contents)
                self.assert_refused()

    def test_oversized_dotenv_is_refused_without_mutation(self):
        self.manager(f'{GOOGLE}=\n#' + 'x' * _DOTENV_MAX_BYTES)
        self.assert_refused()

    def test_symlink_target_is_refused(self):
        self.manager(f'{GOOGLE}=\n')
        original = self.root / 'original.env'
        self.dotenv.rename(original)
        self.dotenv.symlink_to(original)
        self.assert_refused()

    def test_symlink_parent_is_refused(self):
        self.manager(f'{GOOGLE}=\n')
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.config._dotenv_path = alias / '.env'
        self.assert_refused()

    def test_foreign_owned_or_writable_file_is_refused(self):
        self.manager(f'{GOOGLE}=\n')
        self.dotenv.chmod(0o666)
        self.assert_refused()
        self.dotenv.chmod(0o600)
        with patch('src.config_manager.os.getuid', return_value=os.getuid() + 1):
            self.assert_refused()

    def test_failed_sync_or_replace_preserves_file_process_and_stored_key(self):
        config = self.manager(f'{GOOGLE}=\nRETAINED=synthetic-key\n')
        config.set_api_key('google_ai_studio', 'synthetic-stored')
        config.flush()
        original = self.dotenv.read_bytes()
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation), patch(
                    'src.config_manager.os.' + operation, side_effect=OSError('synthetic-error')):
                with self.assertRaises(ValueError) as error:
                    config.remove_empty_api_key_override('google_ai_studio')
                self.assertNotIn('synthetic-error', str(error.exception))
            self.assertEqual(self.dotenv.read_bytes(), original)
            self.assertEqual(os.environ[GOOGLE], '')
            self.assertEqual(config.get_stored_api_key('google_ai_studio'), 'synthetic-stored')
            self.assertEqual(list(self.root.glob('.env.tmp-*')), [])

    def test_fifo_replacement_is_rejected_without_blocking(self):
        self.manager(f'{GOOGLE}=\n')
        self.dotenv.unlink()
        os.mkfifo(self.dotenv)
        self.assertFalse(self.config.can_remove_empty_api_key_override('google_ai_studio'))
        with self.assertRaises(ValueError):
            self.config.remove_empty_api_key_override('google_ai_studio')
        self.assertEqual(os.environ[GOOGLE], '')
