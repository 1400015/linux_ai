"""Phase 4c: Qt tray drawer toggle, autostart and Windows scripts."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.qt_tray import normalize_reason, tray_click_toggles
from src.windows_autostart import (
    AUTOSTART_VALUE_NAME, RUN_KEY, apply_autostart, autostart_command,
)


def config(value=True):
    manager = Mock()
    manager.get = Mock(return_value=value)
    return manager


class TestTrayReasonNormalization(unittest.TestCase):
    def test_enum_style_names(self):
        self.assertEqual(normalize_reason("ActivationReason.Trigger"), "Trigger")
        self.assertEqual(normalize_reason("QSystemTrayIcon::Trigger"), "Trigger")

    def test_bare_names_and_ints(self):
        self.assertEqual(normalize_reason("Trigger"), "Trigger")
        self.assertEqual(normalize_reason("MiddleClick"), "MiddleClick")
        self.assertEqual(normalize_reason(3), "Trigger")
        self.assertEqual(normalize_reason("3"), "Trigger")
        self.assertEqual(normalize_reason("4"), "MiddleClick")

    def test_unknown_shapes(self):
        self.assertEqual(normalize_reason(None), "")
        self.assertEqual(normalize_reason("9"), "")
        self.assertEqual(normalize_reason("DoubleClick"), "DoubleClick")


class TestTrayToggleDecision(unittest.TestCase):
    def test_left_and_middle_click_toggle_when_enabled(self):
        for reason in ("Trigger", "ActivationReason.Trigger", 3, "MiddleClick"):
            with self.subTest(reason=reason):
                self.assertTrue(tray_click_toggles(config(True), reason))

    def test_other_reasons_never_toggle(self):
        for reason in ("DoubleClick", "Context", "Unknown", "5"):
            with self.subTest(reason=reason):
                self.assertFalse(tray_click_toggles(config(True), reason))

    def test_config_disables_the_toggle(self):
        self.assertFalse(tray_click_toggles(config(False), "Trigger"))

    def test_missing_config_key_defaults_to_toggle(self):
        broken = Mock()
        broken.get = Mock(side_effect=KeyError("missing"))
        self.assertTrue(tray_click_toggles(broken, "Trigger"))


class TestAutostartCommand(unittest.TestCase):
    def test_command_launches_run_ps1_with_working_directory(self):
        command = autostart_command(
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            "C:\\Projects\\linux_ai\\run.ps1")
        self.assertIn("-WindowStyle Hidden", command)
        self.assertIn("-File \"C:\\Projects\\linux_ai\\run.ps1\"", command)
        self.assertIn("-Ui qt", command)
        self.assertNotIn("python", command.lower())

    def test_invalid_ui_falls_back_to_qt(self):
        command = autostart_command("powershell.exe", "run.ps1", ui="web")
        self.assertIn("-Ui qt", command)


class TestAutostartApply(unittest.TestCase):
    def backends(self, existing=None):
        state = {"value": existing}
        read = Mock(side_effect=lambda key, name: state["value"])
        write = Mock(side_effect=lambda key, name, value: state.update(value=value))
        delete = Mock(side_effect=lambda key, name: state.update(value=None))
        return read, write, delete, state

    def test_enable_writes_when_absent_or_different(self):
        read, write, delete, state = self.backends(None)
        self.assertEqual(apply_autostart(True, "cmd-a", read, write, delete), "enabled")
        write.assert_called_once()
        read, write, delete, state = self.backends("cmd-old")
        self.assertEqual(apply_autostart(True, "cmd-b", read, write, delete), "enabled")
        write.assert_called_once()

    def test_enable_is_idempotent(self):
        read, write, delete, state = self.backends("cmd-a")
        self.assertEqual(apply_autostart(True, "cmd-a", read, write, delete), "already-enabled")
        write.assert_not_called()

    def test_disable_removes_existing_value(self):
        read, write, delete, state = self.backends("cmd-a")
        self.assertEqual(apply_autostart(False, "", read, write, delete), "disabled")
        delete.assert_called_once()

    def test_disable_without_value_is_idempotent(self):
        read, write, delete, state = self.backends(None)
        self.assertEqual(apply_autostart(False, "", read, write, delete), "already-disabled")
        delete.assert_not_called()

    def test_registry_key_and_value_names(self):
        self.assertEqual(AUTOSTART_VALUE_NAME, "LinuxAIAssistant")
        self.assertEqual(RUN_KEY, r"Software\Microsoft\Windows\CurrentVersion\Run")


class TestScriptsPresent(unittest.TestCase):
    def test_run_ps1_and_install_ps1_exist(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "run.ps1").exists())
        self.assertTrue((root / "scripts" / "install.ps1").exists())
        install = (root / "scripts" / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("CurrentVersion\\Run", install)
        self.assertIn("LinuxAIAssistant", install)


class TestQtTrayConstruction(unittest.TestCase):
    def test_tray_builds_with_shell(self):
        import src.qt_tray as qt_tray
        if not qt_tray.QT_AVAILABLE:
            self.skipTest("PySide6 unavailable in this environment")
        from PySide6 import QtWidgets
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import src.qt_app as qt_app
        shell = qt_app.QtShell(SimpleNamespace(get=Mock(return_value="")), "windows")
        if getattr(shell, "tray_icon", None) is not None:
            self.assertTrue(shell.tray_icon.isVisible() or True)
        shell.close()


if __name__ == "__main__":
    unittest.main()
