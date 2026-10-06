"""Complete online-settings and small-screen workflows with synthetic keys."""

import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.ai_client import AIClient
from src.config_manager import ConfigManager
from src.i18n import get_language, set_language

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class TestOnlineSettingsDialog(unittest.TestCase):
    def setUp(self):
        from src.main_window import MainWindow
        from src.system_utils import SystemUtils

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        cache = self.root / 'cache'
        cache.mkdir()
        self.environment = patch.dict(os.environ, {
            'DISPLAY': os.environ.get('DISPLAY', ''), 'XDG_CACHE_HOME': str(cache),
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.home = patch('pathlib.Path.home', return_value=self.root)
        self.home.start()
        self.addCleanup(self.home.stop)
        original_language = get_language()
        self.addCleanup(set_language, original_language)
        self.dotenv = self.root / '.env'
        self.dotenv.write_text('LINUX_AI_API_PROVIDERS_GOOGLE_AI_STUDIO_API_KEY=\n')
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.addCleanup(self.config.flush)
        self.config.set('app.language', 'pt')
        set_language('pt')
        self.client = AIClient(self.config)
        self.addCleanup(self.client.session.close)
        self.addCleanup(self.client.flush_usage)
        self.window = MainWindow(SimpleNamespace(tray_icon=None), self.config, self.client,
                                 SystemUtils(self.config))
        self.addCleanup(self.window.destroy)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, self.window.get_screen(),
                        self.window._style_provider)
        self.addCleanup(self.window.close_history_writer)
        self.window.show_all()
        self.settle()

    def settle(self):
        loop = GLib.MainLoop()
        GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
        loop.run()

    def drain_until(self, predicate):
        deadline = time.monotonic() + 2
        context = GLib.MainContext.default()
        while not predicate() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(.001)
        self.assertTrue(predicate(), 'the model-list callback did not finish')

    def api_widgets(self):
        from src.credential_settings import APIKeySettings
        from src.remote_model_settings import RemoteModelSettings

        scroll = self.window._settings_notebook.get_nth_page(0)
        descendants = []
        pending = [scroll]
        while pending:
            widget = pending.pop()
            descendants.append(widget)
            if isinstance(widget, Gtk.Container):
                pending.extend(widget.get_children())
        keys = next(widget for widget in descendants if isinstance(widget, APIKeySettings))
        models = next(widget for widget in descendants if isinstance(widget, RemoteModelSettings))
        return scroll, keys, models

    def show_dialog(self, exercise):
        def intercept(dialog):
            self.assertNotIsInstance(dialog, Gtk.MessageDialog, 'unexpected validation error')
            dialog.present()
            self.settle()
            return exercise(dialog)

        with patch.object(Gtk.Dialog, 'run', new=intercept):
            self.window._show_config_dialog()

    def test_google_override_recovery_listing_and_ok_persist_the_selected_configuration(self):
        self.config.flush()
        before = Path(self.config.config_path).read_bytes()
        observed = []

        def discover(provider, settings, key, **options):
            observed.append((provider, key))
            return ['first-fixture', 'second-fixture']

        def exercise(dialog):
            _scroll, keys, models = self.api_widgets()
            models.provider_combo.set_active_id('google_ai_studio')
            keys.entry.set_text('synthetic-google-draft')
            self.assertTrue(keys.remove_override_button.get_visible())
            self.assertTrue(keys.remove_override_button.get_sensitive())
            keys.remove_override_button.emit('clicked')
            self.assertIsNone(self.config.get_api_key_env_override('google_ai_studio'))
            self.assertFalse(keys.remove_override_button.get_visible())
            models.button.emit('clicked')
            self.drain_until(models.button.get_sensitive)
            self.assertEqual(observed, [('google_ai_studio', 'synthetic-google-draft')])
            self.assertEqual(Path(self.config.config_path).read_bytes(), before)
            self.assertEqual(self.config.get_stored_api_key('google_ai_studio'), '')
            models.model.set_active(1)
            return Gtk.ResponseType.OK

        with patch('src.remote_models.discover_remote_models', side_effect=discover):
            self.show_dialog(exercise)
        self.config.flush()
        reloaded = ConfigManager(self.config.config_path)
        self.addCleanup(reloaded.flush)
        self.assertEqual(reloaded.get('api.default_provider'), 'google_ai_studio')
        self.assertEqual(reloaded.get('api.providers.google_ai_studio.model'), 'second-fixture')
        self.assertEqual(reloaded.get_stored_api_key('google_ai_studio'), 'synthetic-google-draft')
        self.assertEqual(self.client.provider_status().state, 'configured')
        self.assertTrue(self.client.provider_ready())

    def test_long_portuguese_warning_keeps_confirmation_visible_and_model_controls_reachable(self):
        def exercise(dialog):
            scroll, keys, models = self.api_widgets()
            models.provider_combo.set_active_id('google_ai_studio')
            self.settle()
            self.assertTrue(keys.warning.get_text())
            self.assertTrue(keys.remove_override_button.get_visible())
            self.assertLessEqual(dialog.get_allocated_height(), 600)
            self.assertLessEqual(dialog.get_allocated_width(), 800)
            ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)
            _x, y = ok.translate_coordinates(dialog, 0, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y + ok.get_allocated_height(), dialog.get_allocated_height())

            adjustment = scroll.get_vadjustment()
            self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
            viewport = scroll.get_child()
            self.assertGreaterEqual(viewport.get_allocated_height(), 200,
                                    'the settings page must use the available dialog height')
            for control in (models.model, models.button):
                with self.subTest(control=type(control).__name__):
                    _x, current_y = control.translate_coordinates(viewport, 0, 0)
                    content_y = current_y + adjustment.get_value()
                    adjustment.clamp_page(content_y, content_y + control.get_allocated_height())
                    self.settle()
                    self.assertTrue(control.get_mapped())
                    _x, y = control.translate_coordinates(viewport, 0, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(y + control.get_allocated_height(),
                                         viewport.get_allocated_height())
            return Gtk.ResponseType.CANCEL

        self.show_dialog(exercise)


if __name__ == '__main__':
    unittest.main()
