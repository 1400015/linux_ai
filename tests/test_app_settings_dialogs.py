"""The settings dialog is built outside the window; the window drives it.

`build_settings_dialog()` owns the GTK layout (the notebook pages, the theme
list, the language and theme combos) and `MainWindow` owns the behaviour.
Both halves were untested — including the page `on_themes_clicked` jumps to,
which resolved to *Appearance* after an Assistance page was inserted.
"""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False

if GTK_AVAILABLE:
    from src.app_settings_dialogs import build_settings_dialog, populate_themes_list
    from src.i18n import SUPPORTED_LANGUAGES, _, get_language, set_language


def tab_titles(notebook):
    """Tab labels with the emphasis markup `_add_settings_page` adds removed."""
    titles = []
    for index in range(notebook.get_n_pages()):
        text = notebook.get_tab_label_text(notebook.get_nth_page(index)) or ""
        titles.append(text.replace("<b>", "").replace("</b>", ""))
    return titles


def combo_ids(combo):
    """Gtk.ComboBoxText models are (text, id): the id is the second column."""
    return [row[1] for row in combo.get_model()]


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class PopulateThemesListTests(unittest.TestCase):
    def setUp(self):
        self.original_language = get_language()
        self.addCleanup(set_language, self.original_language)
        set_language('en')
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

    @staticmethod
    def config(available, info=None):
        info = info or {}
        return SimpleNamespace(
            get_available_themes=lambda: list(available),
            get_theme_info=lambda name: info.get(name),
        )

    def test_rows_carry_the_theme_id_and_the_human_label(self):
        populate_themes_list(self.listbox, self.config(
            ['dark', 'custom'],
            {'dark': {'name': 'Dark', 'description': 'Default dark'},
             'custom': {'name': 'Custom', 'description': 'Mine'}}))
        rows = self.listbox.get_children()
        self.assertEqual([row._theme_id for row in rows], ['dark', 'custom'])
        texts = [[child.get_text() for child in row.get_child().get_children()]
                 for row in rows]
        self.assertEqual(texts, [['Dark', 'Default dark'], ['Custom', 'Mine']])

    def test_a_theme_without_metadata_still_shows_its_identifier(self):
        populate_themes_list(self.listbox, self.config(['ghost'], {'ghost': None}))
        row = self.listbox.get_children()[0]
        labels = row.get_child().get_children()
        self.assertEqual(row._theme_id, 'ghost')
        self.assertEqual(labels[0].get_text(), 'ghost')
        self.assertEqual(labels[1].get_text(), '')

    def test_repainting_the_list_never_duplicates_rows(self):
        config = self.config(['dark', 'light'])
        populate_themes_list(self.listbox, config)
        populate_themes_list(self.listbox, config)
        self.assertEqual([row._theme_id for row in self.listbox.get_children()],
                         ['dark', 'light'])


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class BuildSettingsDialogTests(unittest.TestCase):
    def setUp(self):
        from src.ai_client import AIClient
        from src.config_manager import ConfigManager
        from src.main_window import MainWindow
        from src.system_utils import SystemUtils

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        cache = self.root / 'cache'
        cache.mkdir()
        environment = patch.dict(os.environ, {
            'DISPLAY': os.environ.get('DISPLAY', ''), 'XDG_CACHE_HOME': str(cache),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        home = patch('pathlib.Path.home', return_value=self.root)
        home.start()
        self.addCleanup(home.stop)

        self.original_language = get_language()
        self.addCleanup(set_language, self.original_language)
        set_language('en')

        self.config = ConfigManager(str(self.root / 'config.json'))
        self.addCleanup(self.config.flush)
        self.client = AIClient(self.config)
        self.addCleanup(self.client.session.close)
        self.addCleanup(self.client.flush_usage)
        self.window = MainWindow(SimpleNamespace(tray_icon=None), self.config, self.client,
                                 SystemUtils(self.config))
        self.addCleanup(self.window.destroy)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, self.window.get_screen(),
                        self.window._style_provider)
        self.addCleanup(self.window.close_history_writer)

    def build(self):
        state = build_settings_dialog(self.window)
        self.addCleanup(state.dialog.destroy)
        return state

    def test_every_page_is_present_in_order(self):
        state = self.build()
        self.assertEqual(tab_titles(state.notebook),
                         [_('API'), _('Assistance'), _('Appearance'),
                          _('Themes'), _('Features')])

    def test_the_state_hands_the_window_the_widgets_it_drives(self):
        state = self.build()
        for name in ('dialog', 'notebook', 'provider_combo', 'credential_settings',
                     'assistance_settings', 'model_settings', 'theme_combo',
                     'lang_combo', 'themes_btn', 'themes_listbox', 'opacity_scale'):
            self.assertIsNotNone(getattr(state, name), name)
        # These two are what MainWindow keeps after _show_config_dialog builds it.
        self.window._settings_notebook = state.notebook
        self.window.themes_listbox = state.themes_listbox
        self.addCleanup(setattr, self.window, '_settings_notebook', None)
        self.addCleanup(setattr, self.window, 'themes_listbox', None)
        self.assertIs(self.window.themes_listbox, state.themes_listbox)

    def test_combos_start_from_the_configuration(self):
        state = self.build()
        self.assertEqual(state.theme_combo.get_active_id(),
                         self.config.get('app.theme', 'dark'))
        self.assertEqual(combo_ids(state.theme_combo),
                         self.config.get_available_themes())
        self.assertEqual(state.lang_combo.get_active_id(), get_language())
        self.assertEqual(set(combo_ids(state.lang_combo)), set(SUPPORTED_LANGUAGES))
        self.assertEqual(state.provider_combo.get_active_id(),
                         self.config.get('api.default_provider', 'openrouter'))

    def test_the_theme_list_starts_populated_from_disk(self):
        state = self.build()
        self.assertEqual([row._theme_id for row in state.themes_listbox.get_children()],
                         self.config.get_available_themes())

    def test_manage_themes_lands_on_the_themes_page(self):
        # The handler used a hardcoded index, which pointed at Appearance once
        # the Assistance page was inserted.
        state = self.build()
        self.window._settings_notebook = state.notebook
        self.addCleanup(setattr, self.window, '_settings_notebook', None)
        state.notebook.set_current_page(0)
        self.window.on_themes_clicked(None)
        current = state.notebook.get_current_page()
        self.assertEqual(tab_titles(state.notebook)[current], _('Themes'))


if __name__ == '__main__':
    unittest.main()
