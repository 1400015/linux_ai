"""Custom color validation must agree with the real GTK3 CSS parser."""

from types import SimpleNamespace
import unittest


class TestGtkCssColors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('Gdk', '3.0')
            from gi.repository import Gdk, Gtk
            from src.main_window import MainWindow, contrasting_text_color, safe_color
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        if not Gtk.init_check()[0]:
            raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
        cls.Gdk, cls.Gtk, cls.MainWindow = Gdk, Gtk, MainWindow
        cls.safe_color = staticmethod(safe_color)
        cls.contrasting_text_color = staticmethod(contrasting_text_color)

    def parse_css_color(self, value):
        provider = self.Gtk.CssProvider()
        provider.load_from_data(('button { background-color: ' + value + '; }').encode())
        color = self.Gdk.RGBA()
        self.assertTrue(color.parse(value))
        return color

    def test_supported_hex_colors_reach_gtk_and_keep_readable_foreground(self):
        for color, foreground in (
            ('#fff', '#000000'), ('#000', '#ffffff'),
            ('#FFEECC', '#000000'), ('#000080', '#ffffff'),
        ):
            with self.subTest(color=color):
                validated = self.safe_color(' ' + color + ' ')
                self.assertEqual(validated, color)
                self.parse_css_color(validated)
                self.assertEqual(self.contrasting_text_color(color), foreground)

    def test_rgba_hex_colors_use_a_gtk_supported_fallback(self):
        for color in ('#fff8', '#000f', '#ffeeccff', '#000080ff'):
            with self.subTest(color=color):
                validated = self.safe_color(color, '#123456')
                self.assertEqual(validated, '#123456')
                self.parse_css_color(validated)
                self.assertEqual(self.contrasting_text_color(color),
                                 self.contrasting_text_color('#4CAF50'))

    def test_rgba_hex_in_custom_theme_does_not_abort_theme_application(self):
        for color in ('#abcd', '#000080ff'):
            with self.subTest(color=color):
                window = self.Gtk.Window()
                colors = dict.fromkeys(('background', 'text', 'accent', 'secondary', 'tertiary'), color)
                window.config = SimpleNamespace(
                    get=lambda key, fallback=None: 'custom' if key == 'app.theme' else fallback,
                    get_theme_colors=lambda: colors,
                    get_theme_info=lambda name: {},
                )
                box = self.Gtk.Box()
                box.set_name('main-box')
                window.add(box)
                try:
                    self.MainWindow._setup_style(window)
                    self.assertIsInstance(window._style_provider, self.Gtk.CssProvider)
                finally:
                    provider = getattr(window, '_style_provider', None)
                    if provider is not None:
                        self.Gtk.StyleContext.remove_provider_for_screen(window.get_screen(), provider)
                    window.destroy()


if __name__ == '__main__':
    unittest.main()
