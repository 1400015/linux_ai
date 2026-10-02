"""Assistance mode and local-model settings; probes never persist drafts."""

import threading
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from .i18n import _

MODE_LABELS = {
    'auto': 'Automatic (local guides if unavailable)',
    'offline': 'Local guides (no AI model)',
    'local': 'Local AI model',
    'remote': 'Remote AI provider',
}

STATUS_LABELS = {
    'offline': 'Local guides available', 'unconfigured': 'Provider is not configured',
    'configured': 'Configured; connection not tested', 'ready': 'Local model ready',
    'unreachable': 'Local server is unavailable', 'model_missing': 'Selected model is not installed',
    'blocked': 'Configuration is incompatible with this mode', 'error': 'Connection test failed',
}


class ProviderSettings(Gtk.Box):
    def __init__(self, config, client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_border_width(10)
        self.config, self.client = config, client
        self._generation = 0
        self._alive = True
        self.connect('destroy', self._destroyed)
        self.pack_start(Gtk.Label(label=_("Assistance mode"), xalign=0), False, False, 0)
        self.mode = Gtk.ComboBoxText()
        for value, label in MODE_LABELS.items():
            self.mode.append(value, _(label))
        self.mode.set_active_id(config.get_assistance_mode())
        self.pack_start(self.mode, False, False, 0)
        settings = config.get_local_model_settings()
        self.pack_start(Gtk.Label(label=_("Local server URL"), xalign=0), False, False, 0)
        self.url = Gtk.Entry(text=settings['base_url'])
        self.pack_start(self.url, False, False, 0)
        self.pack_start(Gtk.Label(label=_("Local model"), xalign=0), False, False, 0)
        self.model = Gtk.ComboBoxText.new_with_entry()
        self.model.get_child().set_text(settings['model'])
        self.pack_start(self.model, False, False, 0)
        self.backend = Gtk.ComboBoxText()
        self.backend.append('ollama', 'Ollama')
        self.backend.append('openai', 'OpenAI-compatible')
        self.backend.set_active_id(settings.get('backend', 'ollama'))
        self.pack_start(self.backend, False, False, 0)
        self.strict = Gtk.CheckButton(label=_("Strict local mode (loopback server and local models)"))
        self.strict.set_active(settings.get('strict_local', True))
        self.pack_start(self.strict, False, False, 0)
        note = Gtk.Label(label=_("Models must already be installed. A localhost address alone does not guarantee local inference."),
                         xalign=0, wrap=True)
        self.pack_start(note, False, False, 0)
        self.test = Gtk.Button(label=_("Test connection and list installed models"))
        self.test.connect('clicked', self._test)
        self.pack_start(self.test, False, False, 0)
        self.status = Gtk.Label(label=_("Connection not tested"), xalign=0, wrap=True)
        self.pack_start(self.status, False, False, 0)
        for entry in (self.url, self.model.get_child(), self.backend, self.strict):
            signal = 'toggled' if entry is self.strict else 'changed'
            entry.connect(signal, self._draft_changed)
        self.mode.connect('changed', self._draft_changed)

    def _destroyed(self, widget):
        self._alive = False
        self._generation += 1

    def _draft_changed(self, widget):
        self._generation += 1
        self.status.set_text(_("Connection not tested"))
        self.test.set_sensitive(True)

    def draft(self):
        return {'base_url': self.url.get_text().strip(),
                'model': self.model.get_child().get_text().strip(),
                'strict_local': self.strict.get_active(),
                'backend': self.backend.get_active_id() or 'ollama'}

    def _test(self, button):
        self._generation += 1
        generation = self._generation
        settings = self.draft()
        mode = self.mode.get_active_id() or 'auto'
        button.set_sensitive(False)
        self.status.set_text(_("Testing local connection…"))

        def worker():
            try:
                result = self.client.test_local_connection(timeout=3.0, settings=settings, mode=mode)
                GLib.idle_add(self._show_result, generation, result, None)
            except Exception as error:
                GLib.idle_add(self._show_result, generation, None, str(error))
        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, generation, result, error):
        if not self._alive or generation != self._generation:
            return False
        self.test.set_sensitive(True)
        if error:
            self.status.set_text(_("Connection test failed") + ': ' + error)
            return False
        current = self.model.get_child().get_text()
        self.model.remove_all()
        for model in result.models:
            self.model.append_text(model)
        self.model.get_child().set_text(current)
        self.status.set_text(_(STATUS_LABELS.get(result.state, result.state)) +
                             (': ' + result.detail if result.detail else ''))
        return False

    def save(self):
        settings = self.draft()
        # Validate values before writing any setting.
        self.config.set_assistance_settings(self.mode.get_active_id() or 'auto', **settings)
