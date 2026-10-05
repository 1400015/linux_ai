"""A stale settings request releases its HTTP worker through cancellation."""

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.ai_client import AIClient
from src.config_manager import ConfigManager

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


class CancellableClient(AIClient):
    def __init__(self):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.cancel_event = None

    def list_remote_models(self, provider, cancel_event=None):
        self.cancel_event = cancel_event
        self.started.set()
        if cancel_event is not None:
            cancel_event.wait(2)
        self.finished.set()
        return ['stale-model']


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class TestRemoteModelCancellation(unittest.TestCase):
    def setUp(self):
        from src.remote_model_settings import RemoteModelSettings
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.config = ConfigManager(str(Path(self.directory.name) / 'config.json'))
        self.combo = Gtk.ComboBoxText()
        for provider in ('groq', 'anthropic'):
            self.combo.append(provider, provider)
        self.combo.set_active_id('groq')
        self.mode = Gtk.ComboBoxText()
        for mode in ('remote', 'offline', 'local'):
            self.mode.append(mode, mode)
        self.mode.set_active_id('remote')
        self.client = CancellableClient()
        self.widget = RemoteModelSettings(self.config, self.client, self.combo, self.mode)

    def tearDown(self):
        self.widget.destroy()
        self.client.finished.wait(2)
        self.combo.destroy()
        self.mode.destroy()
        self.config.flush()
        self.environment.stop()
        self.directory.cleanup()

    def begin(self):
        self.widget.button.emit('clicked')
        self.assertTrue(self.client.started.wait(1))
        self.assertIsNotNone(self.client.cancel_event)
        self.assertFalse(self.client.cancel_event.is_set())

    def assert_cancelled_and_stale(self):
        self.assertTrue(self.client.cancel_event.is_set())
        self.assertTrue(self.client.finished.wait(1))
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.001)
        self.assertNotEqual(self.widget.model.get_child().get_text(), 'stale-model')

    def test_provider_change_cancels_pending_listing(self):
        self.begin()
        self.combo.set_active_id('anthropic')
        self.assert_cancelled_and_stale()

    def test_draft_edit_cancels_without_overwriting_manual_model(self):
        self.begin()
        self.widget.model.get_child().set_text('manual-model')
        self.assert_cancelled_and_stale()
        self.assertEqual(self.widget.model.get_child().get_text(), 'manual-model')

    def test_mode_change_cancels_and_disables_remote_button(self):
        self.begin()
        self.mode.set_active_id('offline')
        self.assert_cancelled_and_stale()
        self.assertFalse(self.widget.button.get_sensitive())

    def test_destroy_cancels_and_ignores_late_result(self):
        self.begin()
        self.widget.destroy()
        self.assertTrue(self.client.cancel_event.is_set())
        self.assertTrue(self.client.finished.wait(1))
        self.assertFalse(self.widget._result(self.widget._generation, ['stale-model'], False))


if __name__ == '__main__':
    unittest.main()
