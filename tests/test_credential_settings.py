"""GTK credential edits never persist an effective environment key implicitly."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False

from src.config_manager import ConfigManager


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class TestCredentialSettings(unittest.TestCase):
    def setUp(self):
        from src.credential_settings import APIKeySettings
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.config.set_api_key('openrouter', 'stored-synthetic')
        self.combo = Gtk.ComboBoxText()
        for name in ('openrouter', 'anthropic'):
            self.combo.append(name, name)
        self.combo.set_active_id('openrouter')
        self.widget = APIKeySettings(self.config, self.combo)

    def tearDown(self):
        self.widget.destroy()
        self.combo.destroy()
        self.config.flush()
        self.environment.stop()
        self.directory.cleanup()

    def test_environment_override_is_not_preloaded_or_written_on_unchanged_ok(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-synthetic'}):
            self.widget._provider_changed(self.combo)
            self.assertEqual(self.widget.entry.get_text(), 'stored-synthetic')
            with patch.object(self.config, 'set_api_key') as setter:
                self.assertTrue(self.widget.save())
                setter.assert_not_called()
            self.assertNotIn('environment-synthetic', self.widget.warning.get_text())
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')

    def test_explicit_copy_persists_environment_key(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-synthetic'}):
            self.widget._copy(self.widget.copy_button)
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'environment-synthetic')

    def test_provider_switch_preserves_only_user_edited_drafts_for_ok(self):
        self.widget.entry.set_text('edited-openrouter')
        self.combo.set_active_id('anthropic')
        self.widget.entry.set_text('edited-anthropic')
        self.assertTrue(self.widget.save())
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'edited-openrouter')
        self.assertEqual(self.config.get_stored_api_key('anthropic'), 'edited-anthropic')

    def test_reload_discards_only_current_draft(self):
        self.widget.entry.set_text('edited')
        self.widget._reload(None)
        self.assertEqual(self.widget.entry.get_text(), 'stored-synthetic')
        with patch.object(self.config, 'set_api_key') as setter:
            self.assertTrue(self.widget.save())
            setter.assert_not_called()

    def test_failed_key_write_remains_unsaved_and_never_discloses_exception(self):
        self.widget.entry.set_text('edited')
        with patch.object(self.config, 'set_api_key', side_effect=RuntimeError('secret-exception')):
            self.assertFalse(self.widget.save())
        self.assertEqual(self.widget._drafts, {'openrouter': 'edited'})
        self.assertNotIn('secret-exception', self.widget.status.get_text())

    def test_migration_requires_separate_action_and_no_edited_draft(self):
        self.widget.storage.set_active_id('secret-service')
        with patch.object(self.config, 'set_api_key_storage') as migration:
            self.assertTrue(self.widget.save())
            migration.assert_not_called()
            self.widget._migrate(None)
            migration.assert_called_once_with('openrouter', 'secret-service')
        self.widget.entry.set_text('edited')
        with patch.object(self.config, 'set_api_key_storage') as migration:
            self.widget._migrate(None)
            migration.assert_not_called()

    def test_vault_locked_at_open_does_not_request_unlock(self):
        with patch.object(self.config, 'get_stored_api_key', side_effect=RuntimeError('synthetic')), \
                patch.object(self.config, 'unlock_api_key_store') as unlock:
            self.widget._provider_changed(self.combo)
            self.assertEqual(self.widget.entry.get_text(), '')
            unlock.assert_not_called()
            self.assertTrue(self.widget.save())

    def test_empty_override_is_explicit_and_cannot_be_copied_over_stored_key(self):
        variable = 'LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY'
        with patch.dict(os.environ, {variable: ''}):
            self.widget._provider_changed(self.combo)
            self.assertIn(variable, self.widget.warning.get_text())
            self.assertIn('empty', self.widget.warning.get_text())
            self.assertFalse(self.widget.copy_button.get_sensitive())
            self.assertFalse(self.widget.remove_override_button.get_sensitive())
            self.widget._copy(None)
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')

    def test_explicit_placeholder_removal_enables_saved_key_and_emits_probe_change(self):
        variable = 'LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY'
        dotenv = Path(self.directory.name) / '.env'
        dotenv.write_text(f'{variable}=\n')
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.config.set_api_key('openrouter', 'stored-synthetic')
        self.widget.config = self.config
        self.widget._provider_changed(self.combo)
        self.assertTrue(self.widget.remove_override_button.get_sensitive())
        changes = []
        self.widget.connect('probe-key-changed', lambda widget: changes.append(True))
        self.widget.remove_override_button.emit('clicked')
        self.assertEqual(self.widget.warning.get_text(), '')
        self.assertFalse(self.widget.remove_override_button.get_visible())
        self.assertEqual(self.widget.effective_key_for_probe('openrouter'), 'stored-synthetic')
        self.assertNotIn(variable, os.environ)
        self.assertTrue(changes)

    def test_probe_key_uses_unsaved_draft_but_environment_still_takes_priority(self):
        self.assertEqual(self.widget.effective_key_for_probe('openrouter'), 'stored-synthetic')
        self.widget.entry.set_text('edited-synthetic')
        self.assertEqual(self.widget.effective_key_for_probe('openrouter'), 'edited-synthetic')
        self.assertEqual(self.config.get_stored_api_key('openrouter'), 'stored-synthetic')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-synthetic'}):
            self.assertEqual(self.widget.effective_key_for_probe('openrouter'), 'environment-synthetic')
        with patch.dict(os.environ, {'LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY': ''}):
            self.assertEqual(self.widget.effective_key_for_probe('openrouter'), '')
        self.widget.entry.set_text('')
        self.assertEqual(self.widget.effective_key_for_probe('openrouter'), '')

    def test_credential_edits_provider_switch_and_reload_emit_no_secret_signal_arguments(self):
        changed = Mock()
        self.widget.connect('probe-key-changed', changed)
        self.widget.entry.set_text('edited-synthetic')
        self.widget._reload(None)
        self.combo.set_active_id('anthropic')
        self.assertGreaterEqual(changed.call_count, 3)
        for arguments, keywords in changed.call_args_list:
            self.assertEqual(arguments, (self.widget,))
            self.assertEqual(keywords, {})


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class TestRemoteModelSettings(unittest.TestCase):
    def setUp(self):
        from src.remote_model_settings import RemoteModelSettings
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.combo = Gtk.ComboBoxText()
        for name in ('groq', 'anthropic'):
            self.combo.append(name, name)
        self.combo.set_active_id('groq')
        self.client = Mock()
        self.widget = RemoteModelSettings(self.config, self.client, self.combo)

    def tearDown(self):
        self.widget.destroy()
        self.combo.destroy()
        self.config.flush()
        self.environment.stop()
        self.directory.cleanup()

    def test_opening_settings_and_saving_unchanged_has_no_network_or_model_write(self):
        self.client.list_remote_models.assert_not_called()
        with patch.object(self.config, 'set') as setter:
            self.widget.save()
            setter.assert_not_called()

    def test_listing_keeps_manual_model_and_does_not_save(self):
        self.widget.model.get_child().set_text('custom-model')
        with patch.object(self.config, 'set') as setter:
            self.widget._result(self.widget._generation, ['one', 'two'], False)
            self.assertEqual(self.widget.model.get_child().get_text(), 'custom-model')
            setter.assert_not_called()
        self.widget.save()
        self.assertEqual(self.config.get('api.providers.groq.model'), 'custom-model')

    def test_button_starts_discovery_only_on_explicit_click(self):
        import threading
        import time
        from gi.repository import GLib
        requested = threading.Event()
        def listing(provider):
            self.assertEqual(provider, 'groq')
            requested.set()
            return ['test-model']
        self.client.list_remote_models.side_effect = listing
        self.client.list_remote_models.assert_not_called()
        self.widget.button.emit('clicked')
        self.assertTrue(requested.wait(2))
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 2
        while not self.widget.button.get_sensitive() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.001)
        self.client.list_remote_models.assert_called_once_with('groq')
        self.assertTrue(self.widget.button.get_sensitive())

    def test_stale_results_and_destroyed_widget_are_ignored(self):
        generation = self.widget._generation
        self.combo.set_active_id('anthropic')
        current = self.widget.model.get_child().get_text()
        self.widget._result(generation, ['stale'], False)
        self.assertEqual(self.widget.model.get_child().get_text(), current)
        self.widget.destroy()
        self.assertFalse(self.widget._result(self.widget._generation, ['late'], False))

    def test_offline_or_local_modes_disable_remote_button(self):
        for mode in ('offline', 'local'):
            self.config.set_assistance_mode(mode)
            self.widget._mode_changed(None)
            self.assertFalse(self.widget.button.get_sensitive())
            self.widget._list(self.widget.button)
        self.client.list_remote_models.assert_not_called()
