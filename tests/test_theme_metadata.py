"""Theme metadata must retain the message palette used by GTK text tags."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.config_manager import ConfigManager


class ThemeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.home_patch = patch('pathlib.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.config = ConfigManager(str(self.home / 'config.json'))
        self.addCleanup(self.config.flush)

    def test_light_theme_retains_dark_message_colours(self):
        info = self.config.get_theme_info('light')
        self.assertEqual(info['syntax_highlighting']['user_message'], '#333333')
        self.assertEqual(info['syntax_highlighting']['ai_message'], '#2e7d32')
        self.assertEqual(info['syntax_highlighting']['system_message'], '#666666')
        self.assertEqual(info['colors']['tertiary'], '#eeeeee')

    def test_custom_theme_retains_its_message_palette(self):
        directory = self.home / '.config' / 'linux_ai_assistant' / 'themes'
        directory.mkdir(parents=True)
        palette = {'user_message': '#112233', 'ai_message': '#223344', 'code': '#334455'}
        (directory / 'custom.json').write_text(
            json.dumps({'name': 'Custom', 'syntax_highlighting': palette}),
            encoding='utf-8',
        )
        self.assertEqual(self.config.get_theme_info('custom')['syntax_highlighting'], palette)

    def test_missing_or_malformed_palette_is_safe_for_callers(self):
        for value in (None, [], 'invalid', 42):
            with self.subTest(value=value), patch.object(
                self.config, '_load_theme', return_value={'name': 'Custom', 'syntax_highlighting': value},
            ):
                self.assertEqual(self.config.get_theme_info('custom')['syntax_highlighting'], {})
        with patch.object(self.config, '_load_theme', return_value={'name': 'Custom'}):
            self.assertEqual(self.config.get_theme_info('custom')['syntax_highlighting'], {})
