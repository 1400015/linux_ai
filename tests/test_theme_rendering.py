"""Render GTK widgets: computed colors alone cannot catch theme gradients."""

import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest


class TestThemeRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('Gdk', '3.0')
            from gi.repository import Gdk, Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
            from src.chat_view import ChatView
            from src.main_window import MainWindow
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.Gtk, cls.Gdk, cls.ChatView, cls.MainWindow = Gtk, Gdk, ChatView, MainWindow

    def setUp(self):
        self.settings = self.Gtk.Settings.get_default()
        names = ('gtk-theme-name', 'gtk-application-prefer-dark-theme', 'gtk-enable-animations')
        self.saved_settings = {name: self.settings.get_property(name) for name in names}
        self.settings.set_property('gtk-theme-name', 'Adwaita')
        self.settings.set_property('gtk-enable-animations', False)
        self.windows = []

    def tearDown(self):
        for window in reversed(self.windows):
            provider = getattr(window, '_style_provider', None)
            if provider is not None:
                self.Gtk.StyleContext.remove_provider_for_screen(window.get_screen(), provider)
            window.destroy()
        for name, value in self.saved_settings.items():
            self.settings.set_property(name, value)

    def settle(self):
        deadline = time.monotonic() + 0.08
        while time.monotonic() < deadline:
            while self.Gtk.events_pending():
                self.Gtk.main_iteration_do(False)
            time.sleep(0.002)

    def themed_window(self, name):
        theme = json.loads((Path(__file__).resolve().parents[1] / 'themes' / (name + '.json')).read_text())
        window = self.Gtk.Window()
        self.windows.append(window)
        window.set_default_size(400, 260)
        window.config = SimpleNamespace(
            get=lambda key, fallback=None: name if key == 'app.theme' else fallback,
            get_theme_colors=lambda: theme['colors'],
            get_theme_info=lambda selected: theme,
        )
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=8)
        box.set_name('main-box')
        window.add(box)
        window.button = self.Gtk.Button.new_from_icon_name('open-menu-symbolic', self.Gtk.IconSize.MENU)
        box.pack_start(window.button, False, False, 0)
        window.chat_view = self.ChatView()
        box.pack_start(window.chat_view.scrolled, True, True, 0)
        window.entry = self.Gtk.Entry()
        window.entry.set_text('Readable message')
        box.pack_start(window.entry, False, False, 0)
        self.MainWindow._setup_style(window)
        window.show_all()
        self.settle()
        return window, theme

    def pixel(self, window, widget, x, y):
        point = widget.translate_coordinates(window, x, y)
        pixbuf = self.Gdk.pixbuf_get_from_window(window.get_window(), point[0], point[1], 1, 1)
        self.assertIsNotNone(pixbuf)
        return tuple(pixbuf.get_pixels()[:3])

    def assert_pixel_color(self, actual, expected):
        rgb = tuple(int(expected[offset:offset + 2], 16) for offset in (1, 3, 5))
        for channel, target in zip(actual, rgb):
            self.assertLessEqual(abs(channel - target), 2, (actual, expected))

    def test_chat_and_buttons_render_theme_backgrounds_under_light_and_dark_gtk(self):
        for native_dark in (False, True):
            self.settings.set_property('gtk-application-prefer-dark-theme', native_dark)
            for theme_name in ('dark', 'light'):
                with self.subTest(native_dark=native_dark, application_theme=theme_name):
                    window, theme = self.themed_window(theme_name)
                    view = window.chat_view.textview
                    self.assert_pixel_color(
                        self.pixel(window, view, view.get_allocated_width() // 2,
                                   view.get_allocated_height() // 2), theme['colors']['tertiary'])
                    self.assert_pixel_color(self.pixel(window, window.button, 10, 10), theme['colors']['accent'])
                    window.hide()

    def test_dialog_controls_keep_their_native_theme(self):
        for native_dark in (False, True):
            with self.subTest(native_dark=native_dark):
                self.settings.set_property('gtk-application-prefer-dark-theme', native_dark)
                dialog = self.Gtk.Dialog()
                self.windows.append(dialog)
                button = dialog.add_button('Readable dialog button', self.Gtk.ResponseType.CLOSE)
                entry = self.Gtk.Entry()
                entry.set_text('Readable dialog entry')
                dialog.get_content_area().add(entry)
                dialog.show_all()
                self.settle()
                before = [widget.get_style_context().get_color(self.Gtk.StateFlags.NORMAL).to_string()
                          for widget in (button, entry)]
                background = self.pixel(dialog, button, 10, 10)
                self.themed_window('dark')
                self.settle()
                self.assertEqual(before, [widget.get_style_context().get_color(self.Gtk.StateFlags.NORMAL).to_string()
                                          for widget in (button, entry)])
                self.assertEqual(background, self.pixel(dialog, button, 10, 10))
                dialog.hide()

    def test_text_tags_follow_light_theme_and_invalid_colors_keep_readable_fallback(self):
        window, theme = self.themed_window('light')
        table = window.chat_view.buffer.get_tag_table()
        for tag_name, color_name in (('user-message', 'user_message'), ('ai-message', 'ai_message'),
                                     ('system-message', 'system_message')):
            actual = table.lookup(tag_name).get_property('foreground-rgba')
            expected = self.Gdk.RGBA()
            expected.parse(theme['syntax_highlighting'][color_name])
            self.assertEqual(actual.to_string(), expected.to_string())
        theme['syntax_highlighting'] = {'user_message': 'invalid; CSS', 'ai_message': 'invalid'}
        self.MainWindow._setup_style(window)
        expected = self.Gdk.RGBA()
        expected.parse(theme['colors']['text'])
        for tag_name in ('user-message', 'ai-message', 'system-message'):
            self.assertEqual(table.lookup(tag_name).get_property('foreground-rgba').to_string(), expected.to_string())

    def test_symbolic_icons_remain_readable_on_pale_and_dark_accents(self):
        window, theme = self.themed_window('dark')
        for accent, foreground in (('#ffeecc', '#000000'), ('#000080', '#ffffff')):
            with self.subTest(accent=accent):
                theme['colors']['accent'] = accent
                self.MainWindow._setup_style(window)
                self.settle()
                expected = self.Gdk.RGBA()
                expected.parse(foreground)
                actual = window.button.get_image().get_style_context().get_color(self.Gtk.StateFlags.NORMAL)
                self.assertEqual(actual.to_string(), expected.to_string())


if __name__ == '__main__':
    unittest.main()
