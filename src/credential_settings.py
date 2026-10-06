"""Stored-key editing; effective environment keys require explicit copying."""

import os
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject

from .i18n import _


class APIKeySettings(Gtk.Box):
    __gsignals__ = {'probe-key-changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, config, provider_combo):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config, self.provider_combo = config, provider_combo
        self._loading = False
        self._drafts = {}
        self._provider = None
        self._readable = False
        self.pack_start(Gtk.Label(label=_('API Key:'), xalign=0), False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_visibility(False)
        self.entry.set_placeholder_text(_('Enter your API Key'))
        self.pack_start(self.entry, False, False, 0)
        self.warning = Gtk.Label(xalign=0, wrap=True)
        self.pack_start(self.warning, False, False, 0)
        self.storage = Gtk.ComboBoxText()
        self.storage.append('config', _('Configuration file'))
        self.storage.append('secret-service', _('Linux Secret Service'))
        self.pack_start(self.storage, False, False, 0)
        self.reload_button = self._button(_('Reload saved keys'), self._reload)
        self.remove_override_button = self._button(
            _('Use stored key (remove empty override from .env)'), self._remove_override)
        self.remove_override_button.set_no_show_all(True)
        self.unlock_button = self._button(_('Unlock system key store'), self._unlock)
        self.copy_button = self._button(_('Copy effective key to selected storage'), self._copy)
        self.migrate_button = self._button(_('Move stored key to selected storage'), self._migrate)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.pack_start(self.status, False, False, 0)
        self.entry.connect('changed', self._changed)
        provider_combo.connect('changed', self._provider_changed)
        self._provider_changed(provider_combo)

    def _button(self, label, callback):
        button = Gtk.Button(label=label)
        button.connect('clicked', callback)
        self.pack_start(button, False, False, 0)
        return button

    def _changed(self, entry):
        if not self._loading and self._provider:
            self._drafts[self._provider] = entry.get_text()
            self._refresh_override()
            self.emit('probe-key-changed')

    def effective_key_for_probe(self, provider):
        """Resolve an explicit model probe without saving its key draft."""
        if self.config.get_api_key_env_override(provider) is not None:
            return self.config.get_api_key(provider)
        if provider in self._drafts:
            return self._drafts[provider]
        return self.config.get_api_key(provider)

    def _refresh_override(self):
        variable = self.config.get_api_key_env_override(self._provider)
        empty = variable is not None and os.environ.get(variable) == ''
        removable = empty and self.config.can_remove_empty_api_key_override(self._provider)
        if empty:
            warning = _('The {var} environment variable is empty and blocks the stored API key.').format(var=variable)
            warning += ' ' + (_('Use the button below to remove this empty assignment from .env.') if removable
                              else _('Remove or fill this variable at its source, then restart the application.'))
        elif variable:
            warning = _('Note: the {var} environment variable overrides this key.').format(var=variable)
            warning += ' ' + _('The stored key is shown; the environment key is not saved automatically.')
        else:
            warning = ''
        self.warning.set_text(warning)
        self.copy_button.set_sensitive(variable is not None and not empty)
        self.remove_override_button.set_visible(empty)
        self.remove_override_button.set_sensitive(removable)

    def _load(self):
        self._loading = True
        try:
            stored = self.config.get_stored_api_key(self._provider)
            self._readable = True
        except Exception:
            stored = ''
            self._readable = False
            self.status.set_text(_('Stored key unavailable. Unlock the key store or restore the encryption key.'))
        self.entry.set_text(self._drafts.get(self._provider, stored))
        self._loading = False

    def _provider_changed(self, combo):
        self._provider = combo.get_active_id()
        if not self._provider:
            self.set_sensitive(False)
            self.emit('probe-key-changed')
            return
        self.set_sensitive(True)
        self.status.set_text('')
        try:
            self.storage.set_active_id(self.config.get_api_key_storage(self._provider))
        except Exception:
            self.storage.set_active(-1)
            self.status.set_text(_('The credential storage configuration is invalid.'))
        self._refresh_override()
        self._load()
        self.emit('probe-key-changed')

    def _reload(self, button):
        self._drafts.pop(self._provider, None)
        self._load()
        self._refresh_override()
        self.emit('probe-key-changed')

    def _remove_override(self, button):
        try:
            self.config.remove_empty_api_key_override(self._provider)
            self._refresh_override()
            self.status.set_text(_('Empty .env override removed. The stored key or edited draft can now be used.'))
            self.emit('probe-key-changed')
        except Exception:
            self._refresh_override()
            self.status.set_text(_('The empty override could not be removed safely. The stored key was preserved.'))

    def _unlock(self, button):
        try:
            self.config.unlock_api_key_store()
            self.status.set_text(_('System key store unlocked.'))
            self._load()
            self.emit('probe-key-changed')
        except Exception:
            self.status.set_text(_('The system key store could not be unlocked. No key was copied.'))

    def _copy(self, button):
        try:
            # Copy only to the active backend. A destination change requires
            # the separate, explicit stored-key migration action first.
            if self.storage.get_active_id() != self.config.get_api_key_storage(self._provider):
                raise ValueError('Choose the active storage first')
            effective = self.config.get_api_key(self._provider)
            if not effective:
                raise ValueError('No effective key to copy')
            self.config.set_api_key(self._provider, effective)
            self.config.save()
            if self.config.last_save_error:
                raise ValueError('Save failed')
            self._drafts.pop(self._provider, None)
            self._load()
            self.status.set_text(_('Effective key copied to storage.'))
            self.emit('probe-key-changed')
        except Exception:
            self.status.set_text(_('The key could not be copied. Check the storage and unlock it if necessary.'))

    def _migrate(self, button):
        if self._provider in self._drafts:
            self.status.set_text(_('Save or reload the edited key before moving storage.'))
            return
        try:
            self.config.set_api_key_storage(self._provider, self.storage.get_active_id())
            self._load()
            self.status.set_text(_('Stored key moved and verified.'))
            self.emit('probe-key-changed')
        except Exception:
            self.status.set_text(_('Storage migration failed. The previous credential was preserved.'))

    def save(self):
        try:
            for provider, key in list(self._drafts.items()):
                self.config.set_api_key(provider, key)
                self.config.save()
                if self.config.last_save_error:
                    raise ValueError('Save failed')
                del self._drafts[provider]
            return True
        except Exception:
            self.status.set_text(_('The key could not be saved. The settings dialog remains open.'))
            return False
