"""A model list is requested explicitly and never changes the saved model."""

import threading
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from .i18n import _
from .remote_models import _model_name


class RemoteModelSettings(Gtk.Box):
    def __init__(self, config, client, provider_combo, mode_combo=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config, self.client = config, client
        self.provider_combo, self.mode_combo = provider_combo, mode_combo
        self._provider, self._loading = None, False
        self._drafts = {}
        self._alive, self._generation = True, 0
        self.pack_start(Gtk.Label(label=_('Remote model'), xalign=0), False, False, 0)
        self.model = Gtk.ComboBoxText.new_with_entry()
        self.pack_start(self.model, False, False, 0)
        self.button = Gtk.Button(label=_('List models'))
        self.button.connect('clicked', self._list)
        self.pack_start(self.button, False, False, 0)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.pack_start(self.status, False, False, 0)
        self.model.get_child().connect('changed', self._edited)
        provider_combo.connect('changed', self._selected)
        if mode_combo:
            mode_combo.connect('changed', self._mode_changed)
        self.connect('destroy', self._destroyed)
        self._selected(provider_combo)

    def _destroyed(self, widget):
        self._alive = False
        self._generation += 1

    def _allowed(self):
        draft_mode = self.mode_combo.get_active_id() if self.mode_combo else self.config.get_assistance_mode()
        return (self._provider != 'local_llm' and draft_mode not in ('offline', 'local')
                and self.config.get_assistance_mode() not in ('offline', 'local'))

    def _mode_changed(self, widget):
        self._generation += 1
        self.button.set_sensitive(self._allowed())

    def _selected(self, combo):
        self._generation += 1
        self._provider = combo.get_active_id()
        self._loading = True
        self.model.remove_all()
        current = self.config.get('api.providers.{}.model'.format(self._provider), '')
        self.model.get_child().set_text(self._drafts.get(self._provider, current))
        self._loading = False
        self.status.set_text(_('Connection not tested'))
        self.button.set_sensitive(self._allowed())
        self.set_visible(self._provider != 'local_llm')

    def _edited(self, entry):
        if not self._loading and self._provider:
            self._drafts[self._provider] = entry.get_text()
            self._generation += 1
            self.button.set_sensitive(self._allowed())

    def _list(self, button):
        if not self._allowed():
            return
        self._generation += 1
        generation, provider = self._generation, self._provider
        button.set_sensitive(False)
        self.status.set_text(_('Listing models…'))

        def worker():
            try:
                models = self.client.list_remote_models(provider)
                GLib.idle_add(self._result, generation, models, False)
            except Exception:
                GLib.idle_add(self._result, generation, [], True)
        threading.Thread(target=worker, daemon=True).start()

    def _result(self, generation, models, error):
        if not self._alive or generation != self._generation:
            return False
        self.button.set_sensitive(self._allowed())
        if error:
            self.status.set_text(_('Model listing failed. Check the key, mode and endpoint.'))
            return False
        self._loading = True
        current = self.model.get_child().get_text()
        self.model.remove_all()
        for model in models:
            self.model.append_text(model)
        self.model.get_child().set_text(current)
        self._loading = False
        self.status.set_text(_('Models listed. Select a model and save to apply it.'))
        return False

    def save(self):
        if any(not _model_name(model.strip()) for model in self._drafts.values()):
            raise ValueError(_('Enter a valid model identifier.'))
        for provider, model in self._drafts.items():
            self.config.set('api.providers.{}.model'.format(provider), model.strip())
