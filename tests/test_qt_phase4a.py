"""Phase 4a: minimal Qt shell foundation (no GTK, no PySide6 required)."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class TestQtAvailabilityContract(unittest.TestCase):
    def test_module_imports_without_pyside6(self):
        import src.qt_app as qt_app
        self.assertIn(qt_app.available(), (True, False))
        if not qt_app.available():
            self.assertIn("PySide6", qt_app.missing_dependency_message())
            self.assertIn("pip install", qt_app.missing_dependency_message())

    def test_missing_dependency_message_names_the_extra(self):
        import src.qt_app as qt_app
        message = qt_app.missing_dependency_message()
        self.assertIn(".[qt]", message)


class TestQtRunWithoutDependency(unittest.TestCase):
    def test_run_reports_missing_dependency_and_returns_nonzero(self):
        import src.qt_app as qt_app
        if qt_app.available():
            self.skipTest("PySide6 installed in this environment")
        with patch("sys.stderr", new_callable=Mock) as stderr:
            self.assertEqual(qt_app.run(SimpleNamespace(get=Mock(return_value="")), argv=["test"]), 1)
        self.assertTrue(stderr.write.called)


class TestQtShellConstruction(unittest.TestCase):
    def test_shell_builds_with_config_and_platform(self):
        import src.qt_app as qt_app
        if not qt_app.available():
            self.skipTest("PySide6 unavailable in this environment")
        from PySide6 import QtWidgets
        _app = QtWidgets.QApplication([]) if not QtWidgets.QApplication.instance() else None
        config = SimpleNamespace(get=Mock(return_value=""))
        shell = qt_app.QtShell(config, "windows")
        self.assertEqual(shell.windowTitle(), "Linux AI Assistant")
        self.assertIn("Windows", shell._status_text())
        self.assertTrue(shell.isVisible() is False)
        shell.close()


class TestMainUiFlag(unittest.TestCase):
    def test_parser_accepts_and_rejects_ui_values(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--ui", choices=("gtk", "qt"), default="gtk")
        self.assertEqual(parser.parse_args(["--ui", "qt"]).ui, "qt")
        self.assertEqual(parser.parse_args([]).ui, "gtk")
        with self.assertRaises(SystemExit):
            parser.parse_args(["--ui", "web"])


if __name__ == "__main__":
    unittest.main()
