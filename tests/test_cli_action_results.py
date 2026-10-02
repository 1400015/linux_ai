"""CLI outcomes reach the shell and history without running real commands."""

import io
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.ai_client import AIProviderError
from src.cli import CLIApp
from src.i18n import set_language
from src.offline_assistant import Command, Reply


class TestCliActionResults(unittest.TestCase):
    def setUp(self):
        set_language("en")

    def tearDown(self):
        set_language("en")

    def app(self, reply, mode="offline"):
        app = CLIApp.__new__(CLIApp)
        app.expert_mode = False
        app.conversation_history = []
        app._pending_exchanges = []
        app._prepare_conversation = Mock()
        app._build_request_messages = Mock(return_value=[
            {"role": "user", "content": "configure device"},
        ])
        app._save_history = Mock()
        app.offline = SimpleNamespace(
            handle=Mock(return_value=reply), propose=Mock(return_value=reply),
        )
        app.ai_client = SimpleNamespace(
            provider_ready=Mock(return_value=mode != "offline"),
            chat=Mock(return_value="Model answer"),
            stream_chat=Mock(return_value=iter(["Model ", "answer"])),
        )
        args = SimpleNamespace(
            message=["configure", "device"], provider=None, model=None,
            expert=False, stream=mode == "stream", no_history=False,
        )
        return app, args

    @staticmethod
    def completed(argv, code=0, output="", error=""):
        return subprocess.CompletedProcess(argv, code, output, error)

    def test_confirmed_command_failure_returns_one_in_every_answer_mode(self):
        command = Command(["apt-get", "install", "-y", "htop"], privileged=True)
        reply = Reply("Catalog answer", [command])
        for mode in ("offline", "online", "stream"):
            with self.subTest(mode=mode):
                app, args = self.app(reply, mode)
                with patch("sys.stdin.isatty", return_value=True), \
                        patch("builtins.input", return_value="yes"), \
                        patch("sys.stdout", io.StringIO()), \
                        patch("subprocess.run", return_value=self.completed(
                            command.argv, 7, error="Permission denied",
                        )) as run:
                    self.assertEqual(app.handle_chat(args), 1)
                self.assertEqual(run.call_args.args[0], ["pkexec", *command.argv])
                response = app.conversation_history[-1]["content"]
                self.assertIn("Permission denied", response)
                self.assertIn("$ apt-get install -y htop", response)
                self.assertIn("Catalog answer" if mode == "offline" else "Model answer", response)
                app._save_history.assert_called_once()

    def test_success_and_cancellation_keep_zero_and_distinct_outcomes(self):
        command = Command(["true"])
        app, _args = self.app(Reply("Catalog answer", [command]))
        with patch("builtins.input", return_value="yes"), \
                patch("sys.stdout", io.StringIO()), \
                patch("subprocess.run", return_value=self.completed(["true"])):
            result = app._confirm_and_run([command])
        self.assertEqual(result.status, "success")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Done.", result)
        for mode in ("offline", "online", "stream"):
            with self.subTest(mode=mode):
                app, args = self.app(Reply("Catalog answer", [command]), mode)
                with patch("sys.stdin.isatty", return_value=True), \
                        patch("builtins.input", return_value="no"), \
                        patch("sys.stdout", io.StringIO()), \
                        patch("subprocess.run") as run:
                    self.assertEqual(app.handle_chat(args), 0)
                run.assert_not_called()
        with patch("builtins.input", return_value="no"), \
                patch("sys.stdout", io.StringIO()):
            result = app._confirm_and_run([command])
        self.assertEqual(result, "")
        self.assertEqual(result.status, "cancelled")

    def test_pipes_only_show_proposals_and_return_zero(self):
        offers = [Reply("Catalog answer", [Command(["true"])])]
        offers.extend(Reply("Catalog answer", interaction=kind)
                      for kind in ("wifi", "printer", "scanner"))
        for reply in offers:
            for mode in ("offline", "online", "stream"):
                with self.subTest(interaction=reply.interaction, mode=mode):
                    app, args = self.app(reply, mode)
                    with patch("sys.stdin.isatty", return_value=False), \
                            patch("builtins.input", side_effect=AssertionError("asked")), \
                            patch("sys.stdout", io.StringIO()) as output, \
                            patch("subprocess.run", side_effect=AssertionError("executed")):
                        self.assertEqual(app.handle_chat(args), 0)
                    self.assertIn("interactive terminal", output.getvalue())

    def test_multiple_commands_stop_at_the_first_failure(self):
        commands = [Command(["first"]), Command(["second"]), Command(["third"])]
        app, _args = self.app(Reply("Catalog answer", commands))
        with patch("builtins.input", return_value="yes"), \
                patch("sys.stdout", io.StringIO()), \
                patch("subprocess.run", side_effect=[
                    self.completed(["first"], output="first done"),
                    self.completed(["second"], 2, error="second failed"),
                ]) as run:
            result = app._confirm_and_run(commands)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(run.call_count, 2)
        self.assertIn("first done", result)
        self.assertIn("second failed", result)
        self.assertNotIn("third", result)

    def test_provider_fallback_preserves_offline_action_failure(self):
        reply = Reply("Catalog answer", [Command(["true"])])
        for mode in ("online", "stream"):
            with self.subTest(mode=mode):
                app, args = self.app(reply, mode)
                if mode == "stream":
                    app.ai_client.stream_chat.side_effect = AIProviderError("Unavailable")
                else:
                    app.ai_client.chat.return_value = None
                with patch("sys.stdin.isatty", return_value=True), \
                        patch("builtins.input", return_value="yes"), \
                        patch("sys.stdout", io.StringIO()), \
                        patch("subprocess.run", return_value=self.completed(
                            ["true"], 1, error="Action failed",
                        )):
                    self.assertEqual(app.handle_chat(args), 1)
                self.assertIn("Action failed", app.conversation_history[-1]["content"])
                app.offline.propose.assert_not_called()

    def test_wifi_connection_failure_returns_one_and_redacts_password(self):
        app, args = self.app(Reply("Choose Wi-Fi", interaction="wifi"))
        secret = "private-wifi-password"

        def subprocess_result(argv, **_kwargs):
            if argv[-1] == "rescan":
                return self.completed(argv)
            if argv[-1] == "list":
                return self.completed(argv, output="no:Cafe:70:WPA2")
            return self.completed(argv, 10, error="Invalid password: " + secret)

        with patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value="1"), \
                patch("src.cli.getpass.getpass", return_value=secret), \
                patch("sys.stdout", io.StringIO()) as output, \
                patch("subprocess.run", side_effect=subprocess_result) as run:
            self.assertEqual(app.handle_chat(args), 1)
        self.assertEqual(run.call_args.args[0], [
            "nmcli", "device", "wifi", "connect", "Cafe", "password", secret,
        ])
        response = app.conversation_history[-1]["content"]
        self.assertIn("Could not connect to Cafe", response)
        self.assertNotIn(secret, response)
        self.assertNotIn(secret, output.getvalue())

    def test_printer_command_failure_and_invalid_queue_return_one(self):
        for queue, expected_runs in (("office", 2), ("bad queue", 1)):
            with self.subTest(queue=queue):
                app, args = self.app(Reply("Choose printer", interaction="printer"))
                with patch("sys.stdin.isatty", return_value=True), \
                        patch("builtins.input", side_effect=["1", queue, "yes"]), \
                        patch("sys.stdout", io.StringIO()), \
                        patch("subprocess.run", side_effect=[
                            self.completed(["lpinfo"], output="network ipp://192.0.2.1/print"),
                            self.completed(["lpadmin"], 1, error="CUPS refused the queue"),
                        ]) as run:
                    self.assertEqual(app.handle_chat(args), 1)
                self.assertEqual(run.call_count, expected_runs)
                response = app.conversation_history[-1]["content"]
                self.assertIn("CUPS refused" if queue == "office" else "Invalid printer", response)
                if queue == "office":
                    self.assertEqual(run.call_args.args[0][:4], ["pkexec", "lpadmin", "-p", "office"])

    def test_scanner_support_install_failure_returns_one(self):
        command = Command(
            ["apt-get", "install", "-y", "sane-utils", "sane-airscan"],
            privileged=True,
        )
        app, args = self.app(Reply("Choose scanner", [command], interaction="scanner"))
        with patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value="yes"), \
                patch("sys.stdout", io.StringIO()), \
                patch("subprocess.run", side_effect=[
                    FileNotFoundError("scanimage"),
                    self.completed(command.argv, 1, error="Install failed"),
                ]) as run:
            self.assertEqual(app.handle_chat(args), 1)
        self.assertEqual(run.call_args.args[0], ["pkexec", *command.argv])
        self.assertIn("Install failed", app.conversation_history[-1]["content"])

    def test_discovery_error_is_a_failure_but_empty_discovery_is_not(self):
        for kind in ("wifi", "printer", "scanner"):
            for code in (0, 2):
                with self.subTest(kind=kind, code=code):
                    app, args = self.app(Reply("Choose device", interaction=kind))
                    with patch("sys.stdin.isatty", return_value=True), \
                            patch("builtins.input", side_effect=AssertionError("asked")), \
                            patch("sys.stdout", io.StringIO()), \
                            patch("subprocess.run", return_value=self.completed(
                                [], code, error="Discovery failed" if code else "",
                            )):
                        self.assertEqual(app.handle_chat(args), 1 if code else 0)
                    if code:
                        self.assertIn("Discovery failed", app.conversation_history[-1]["content"])

    def test_no_history_does_not_hide_the_action_failure(self):
        app, args = self.app(Reply("Catalog answer", [Command(["true"])]))
        args.no_history = True
        with patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value="yes"), \
                patch("sys.stdout", io.StringIO()), \
                patch("subprocess.run", return_value=self.completed(["true"], 1)):
            self.assertEqual(app.handle_chat(args), 1)
        self.assertEqual(app.conversation_history, [])
        app._save_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
