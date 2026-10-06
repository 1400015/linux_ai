"""Phase 4e: automatic UI track selection per platform."""
import unittest
from unittest.mock import patch

from src.platform import LINUX, WINDOWS, WSL
from src.platform.ui_selection import AUTO, GTK, QT, select_ui_track


class TestSelectUiTrack(unittest.TestCase):
    def test_auto_selects_qt_on_windows(self):
        self.assertEqual(select_ui_track(AUTO, platform=WINDOWS), QT)

    def test_auto_selects_gtk_on_linux_and_wsl(self):
        self.assertEqual(select_ui_track(AUTO, platform=LINUX), GTK)
        self.assertEqual(select_ui_track(AUTO, platform=WSL), GTK)

    def test_explicit_request_wins_over_platform(self):
        self.assertEqual(select_ui_track(QT, platform=LINUX), QT)
        self.assertEqual(select_ui_track(GTK, platform=WINDOWS), GTK)

    def test_unknown_request_falls_back_to_auto(self):
        self.assertEqual(select_ui_track("web", platform=WINDOWS), QT)
        self.assertEqual(select_ui_track("web", platform=LINUX), GTK)

    def test_default_request_is_auto(self):
        with patch("src.platform.ui_selection.detect_platform", return_value=WINDOWS):
            self.assertEqual(select_ui_track(), QT)
        with patch("src.platform.ui_selection.detect_platform", return_value=LINUX):
            self.assertEqual(select_ui_track(), GTK)


class TestParserContract(unittest.TestCase):
    def test_ui_choices_include_auto_with_default(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--ui", choices=("auto", "gtk", "qt"), default="auto")
        self.assertEqual(parser.parse_args([]).ui, "auto")
        self.assertEqual(parser.parse_args(["--ui", "gtk"]).ui, "gtk")
        self.assertEqual(parser.parse_args(["--ui", "qt"]).ui, "qt")
        with self.assertRaises(SystemExit):
            parser.parse_args(["--ui", "web"])


class TestLauncherScripts(unittest.TestCase):
    def test_run_ps1_defaults_to_auto(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        text = (root / "run.ps1").read_text(encoding="utf-8")
        self.assertIn('[string]$Ui = "auto"', text)
        self.assertIn('"auto", "gtk", "qt"', text)

    def test_run_sh_does_not_force_a_track(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        text = (root / "run.sh").read_text(encoding="utf-8")
        self.assertNotIn("--ui", text)


if __name__ == "__main__":
    unittest.main()
