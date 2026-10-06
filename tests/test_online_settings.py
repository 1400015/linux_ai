"""The online settings workflow uses drafts without losing key precedence."""

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.ai_client import AIClient
from src.config_manager import ConfigManager
from src.i18n import _
from src.remote_models import ModelDiscoveryError

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class TestOnlineSettings(unittest.TestCase):
    def setUp(self):
        from src.credential_settings import APIKeySettings
        from src.remote_model_settings import RemoteModelSettings
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.config.set('api.default_provider', 'google_ai_studio')
        self.config.flush()
        self.before = Path(self.config.config_path).read_bytes()
        self.provider = Gtk.ComboBoxText()
        for name in ('google_ai_studio', 'mistral'):
            self.provider.append(name, name)
        self.provider.set_active_id('google_ai_studio')
        self.keys = APIKeySettings(self.config, self.provider)
        self.client = object.__new__(AIClient)
        self.client.config = self.config
        self.models = RemoteModelSettings(self.config, self.client, self.provider,
                                          credential_settings=self.keys)

    def tearDown(self):
        self.models.destroy()
        self.keys.destroy()
        self.provider.destroy()
        self.config.flush()
        self.environment.stop()
        self.directory.cleanup()

    def drain_until(self, predicate):
        deadline = time.monotonic() + 2
        context = GLib.MainContext.default()
        while not predicate() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(.001)
        self.assertTrue(predicate(), 'model-list callback did not finish')

    def list_with(self, callback):
        with patch('src.remote_models.discover_remote_models', side_effect=callback) as discovery:
            self.models.button.emit('clicked')
            self.drain_until(lambda: self.models.button.get_sensitive())
        return discovery

    def test_new_key_lists_models_without_saving_then_selected_model_survives_reload(self):
        self.keys.entry.set_text('synthetic-google-draft')

        def discover(provider, settings, key, **options):
            self.assertEqual(provider, 'google_ai_studio')
            self.assertEqual(key, 'synthetic-google-draft')
            return ['first-chat-model', 'second-chat-model']

        self.list_with(discover)
        self.assertEqual(len(self.models.model.get_model()), 2)
        self.assertFalse(self.client.provider_ready())
        self.assertEqual(Path(self.config.config_path).read_bytes(), self.before)
        self.models.model.set_active(1)
        self.models.save()
        self.assertTrue(self.keys.save())
        self.config.flush()
        reloaded = ConfigManager(self.config.config_path)
        self.addCleanup(reloaded.flush)
        self.assertEqual(reloaded.get('api.providers.google_ai_studio.model'), 'second-chat-model')
        self.assertTrue(self.client.provider_ready())

    def test_mistral_selection_uses_its_own_draft_and_preserves_google_draft(self):
        self.keys.entry.set_text('synthetic-google-draft')
        self.provider.set_active_id('mistral')
        self.keys.entry.set_text('synthetic-mistral-draft')
        discovery = self.list_with(lambda *args, **kwargs: ['mistral-chat-model'])
        self.assertEqual(discovery.call_args.args[0], 'mistral')
        self.assertEqual(discovery.call_args.args[2], 'synthetic-mistral-draft')
        self.provider.set_active_id('google_ai_studio')
        self.assertEqual(self.keys.entry.get_text(), 'synthetic-google-draft')
        self.assertEqual(Path(self.config.config_path).read_bytes(), self.before)

    def test_environment_key_wins_over_draft_including_intentionally_empty_key(self):
        self.keys.entry.set_text('synthetic-draft')
        name = 'LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY'
        for value in ('synthetic-environment', ''):
            with self.subTest(value_present=bool(value)), patch.dict(os.environ, {name: value}):
                discovery = self.list_with(lambda *args, **kwargs: ['chat-model'])
                self.assertEqual(discovery.call_args.args[2], value)
        self.assertEqual(self.config.get_stored_api_key('google_ai_studio'), '')

    def test_edited_key_cancels_listing_and_ignores_old_models(self):
        self.keys.entry.set_text('synthetic-first')
        entered = threading.Event()
        cancelled = threading.Event()
        finished = threading.Event()
        result_seen = threading.Event()
        worker_errors = []

        def discover(provider, settings, key, **options):
            entered.set()
            try:
                if options['cancel_event'].wait(2):
                    cancelled.set()
                else:
                    worker_errors.append('listing was not cancelled')
                return ['stale-model']
            finally:
                finished.set()

        original_result = self.models._result

        def receive_result(*args):
            try:
                return original_result(*args)
            finally:
                result_seen.set()

        with patch('src.remote_models.discover_remote_models', side_effect=discover), \
                patch.object(self.models, '_result', side_effect=receive_result):
            self.models.button.emit('clicked')
            self.assertTrue(entered.wait(2))
            self.keys.entry.set_text('synthetic-second')
            self.assertTrue(cancelled.wait(2))
            self.assertTrue(finished.wait(2))
            self.drain_until(result_seen.is_set)
        # Drain the stale idle result and check that it cannot populate the list.
        self.assertTrue(self.models.button.get_sensitive())
        self.assertFalse(worker_errors, worker_errors)
        self.assertEqual(len(self.models.model.get_model()), 0)
        self.assertEqual(self.models.status.get_text(), _('Connection not tested'))

    def test_safe_provider_error_is_visible_but_transport_details_are_hidden(self):
        self.keys.entry.set_text('synthetic-key')
        message = 'The provider rejected the API key (HTTP 401).'
        self.list_with(lambda *args, **kwargs: (_ for _ in ()).throw(ModelDiscoveryError(message)))
        self.assertEqual(self.models.status.get_text(), _(message))
        self.list_with(lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('synthetic-secret-response')))
        self.assertNotIn('synthetic-secret-response', self.models.status.get_text())
        self.assertEqual(self.models.status.get_text(), _('Model listing failed. Check the key, mode and endpoint.'))

    def test_offline_policy_still_blocks_remote_discovery_with_draft_key(self):
        self.keys.entry.set_text('synthetic-key')
        for mode in ('offline', 'local'):
            self.config.set_assistance_mode(mode)
            self.models._mode_changed(None)
            with patch('src.remote_models.discover_remote_models') as discovery:
                self.models._list(self.models.button)
                discovery.assert_not_called()


if __name__ == '__main__':
    unittest.main()
