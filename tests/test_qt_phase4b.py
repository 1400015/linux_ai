"""Phase 4b: Qt chat widget on the shared offline backend."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.qt_chat import MAX_LOG_CHARS, append_is_bounded, offline_reply_text


def offline(text="Resposta", fail=False):
    assistant = Mock()
    if fail:
        assistant.handle = Mock(side_effect=RuntimeError("boom"))
    else:
        assistant.handle = Mock(return_value=SimpleNamespace(text=text, commands=[]))
    return assistant


class TestOfflineReplyPath(unittest.TestCase):
    def test_message_routes_through_offline_assistant(self):
        result = offline_reply_text(offline("Guia"), "guia disk-space", "pt")
        self.assertEqual(result, "Guia")
        assistant = offline()
        offline_reply_text(assistant, "olá", "pt")
        assistant.handle.assert_called_once_with("olá", "pt")

    def test_empty_message_returns_none_without_calling_backend(self):
        assistant = offline()
        self.assertIsNone(offline_reply_text(assistant, ""))
        self.assertIsNone(offline_reply_text(assistant, "   "))
        assistant.handle.assert_not_called()

    def test_backend_failure_returns_none(self):
        self.assertIsNone(offline_reply_text(offline(fail=True), "qualquer"))

    def test_none_reply_returns_none(self):
        assistant = Mock()
        assistant.handle = Mock(return_value=None)
        self.assertIsNone(offline_reply_text(assistant, "msg"))


class TestLogBound(unittest.TestCase):
    def test_bound_allows_within_limit(self):
        self.assertTrue(append_is_bounded(0, "x" * 100))

    def test_bound_rejects_past_limit(self):
        self.assertFalse(append_is_bounded(MAX_LOG_CHARS, "x"))
        self.assertFalse(append_is_bounded(MAX_LOG_CHARS - 1, "xx"))


class TestQtChatModuleContract(unittest.TestCase):
    def test_module_imports_without_pyside6(self):
        import src.qt_chat as qt_chat
        self.assertIn(qt_chat.QT_AVAILABLE, (True, False))

    def test_widget_raises_without_pyside6(self):
        import src.qt_chat as qt_chat
        if qt_chat.QT_AVAILABLE:
            self.skipTest("PySide6 installed in this environment")
        with self.assertRaises(RuntimeError):
            qt_chat.QtChatWidget(SimpleNamespace(get=Mock(return_value="")), offline())


class TestQtChatBehaviour(unittest.TestCase):
    def setUp(self):
        import src.qt_chat as qt_chat
        if not qt_chat.QT_AVAILABLE:
            self.skipTest("PySide6 unavailable in this environment")
        from PySide6 import QtWidgets
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.widget = qt_chat.QtChatWidget(SimpleNamespace(get=Mock(return_value="")), offline("Resposta"))

    def test_send_routes_through_offline_assistant(self):
        self.widget.input.setText("guia disk-space")
        self.widget._on_send()
        self.assertIn("Resposta", self.widget.log.toPlainText())

    def test_empty_input_is_ignored(self):
        self.widget._on_send()
        self.widget.offline.handle.assert_not_called()

    def test_log_is_bounded(self):
        for _ in range(20):
            self.widget.append_message("AI", "x" * 20000)
        self.assertLessEqual(len(self.widget.log.toPlainText()), MAX_LOG_CHARS)


class TestShellHostsChat(unittest.TestCase):
    def test_shell_builds_chat(self):
        import src.qt_app as qt_app
        if not qt_app.available():
            self.skipTest("PySide6 unavailable in this environment")
        from PySide6 import QtWidgets
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        shell = qt_app.QtShell(SimpleNamespace(get=Mock(return_value="")), "windows")
        if shell.chat is not None:
            self.assertIsNotNone(shell.chat.offline)
        shell.close()


if __name__ == "__main__":
    unittest.main()
