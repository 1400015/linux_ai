"""O sandbox dos comandos confirmados degrada sem sessão e nunca duplica efeitos.

Estes testes fixam as três garantias do wrapper: quando está ativo o prefixo
é exato (propriedades que isolam `/tmp` e limitam a unit), quando a sonda
falha os comandos correm intocados como antes, e uma falha de arranque do
próprio systemd-run — a única situação em que repetir é seguro — desactiva
o wrapper e repete uma única vez.
"""

import os
import unittest
from unittest.mock import Mock, patch

from src import command_sandbox
from src.offline_assistant import Command, OfflineAssistant


class CommandSandboxTests(unittest.TestCase):
    def setUp(self):
        command_sandbox.reset_for_tests()
        self.addCleanup(command_sandbox.reset_for_tests)
        self.env = patch.dict(os.environ, {"LINUX_AI_COMMAND_SANDBOX": "auto"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disabled_env_returns_the_same_argv_object(self):
        with patch.dict(os.environ, {"LINUX_AI_COMMAND_SANDBOX": "0"}):
            argv = ["apt-get", "install", "-y", "htop"]
            self.assertIs(command_sandbox.wrap(argv, 120), argv)

    def test_invalid_argv_is_left_for_run_bounded_to_reject(self):
        with patch.dict(os.environ, {"LINUX_AI_COMMAND_SANDBOX": "1"}):
            for bad in ([], "echo hi", [None]):
                self.assertEqual(command_sandbox.wrap(bad, 10), bad)

    def test_forced_mode_wraps_without_probing(self):
        with patch.dict(os.environ, {"LINUX_AI_COMMAND_SANDBOX": "1"}), \
                patch.object(command_sandbox, "_probe", side_effect=AssertionError("probed")):
            wrapped = command_sandbox.wrap(["nmcli", "dev"], 120)
        self.assertEqual(wrapped, [
            "systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet",
            "--same-dir", "-p", "PrivateTmp=yes", "-p", "RuntimeMaxSec=125", "--",
            "nmcli", "dev",
        ])

    def test_auto_mode_wraps_after_a_successful_probe(self):
        with patch.object(command_sandbox, "_probe", return_value=True):
            wrapped = command_sandbox.wrap(["uptime"], 20)
        self.assertEqual(wrapped[-1], "uptime")
        self.assertEqual(wrapped[0], "systemd-run")
        self.assertIn("RuntimeMaxSec=25", wrapped)
        # A sonda acontece uma vez; a segunda chamada reutiliza a cache.
        with patch.object(command_sandbox, "_probe", side_effect=AssertionError("re-probed")):
            command_sandbox.wrap(["uptime"], 20)

    def test_auto_mode_keeps_argv_untouched_when_the_probe_fails(self):
        with patch.object(command_sandbox, "_probe", return_value=False):
            argv = ["grep", "x", "/etc/hosts"]
            self.assertIs(command_sandbox.wrap(argv, 20), argv)

    def test_probe_requires_a_user_runtime_directory(self):
        command_sandbox.reset_for_tests()
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}), \
                patch("src.command_sandbox.shutil.which", return_value="/usr/bin/systemd-run"), \
                patch.object(command_sandbox, "run_bounded", side_effect=AssertionError("ran")):
            self.assertFalse(command_sandbox._probe(10))

    def test_demotion_stops_wrapping_for_the_rest_of_the_session(self):
        with patch.dict(os.environ, {"LINUX_AI_COMMAND_SANDBOX": "1"}):
            command_sandbox.demote()
            argv = ["uptime"]
            self.assertIs(command_sandbox.wrap(argv, 10), argv)

    def test_started_failed_only_matches_systemd_own_startup_errors(self):
        self.assertTrue(command_sandbox.started_failed(
            "Failed to start transient scope unit: Access denied"))
        self.assertTrue(command_sandbox.started_failed(
            "Failed to enqueue job: Unit generator not found"))
        # Stdout antes da mensagem, ou texto do próprio comando, não contam.
        self.assertFalse(command_sandbox.started_failed(
            "collected 3 items\nFailed to start transient something"))
        self.assertFalse(command_sandbox.started_failed(
            "failed to start the service, see journal"))


class RunCommandSandboxIntegration(unittest.TestCase):
    def setUp(self):
        command_sandbox.reset_for_tests()
        self.addCleanup(command_sandbox.reset_for_tests)
        self.env = patch.dict(os.environ, {"LINUX_AI_COMMAND_SANDBOX": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run_argv = Mock(return_value=(True, "ok"))
        patcher = patch("src.device_actions.run_argv", self.run_argv)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_non_privileged_command_runs_inside_the_sandbox(self):
        ok, output = OfflineAssistant.run_command(Command(["uptime"]))
        self.assertEqual((ok, output), (True, "ok"))
        executed = self.run_argv.call_args.args[0]
        self.assertEqual(executed[0], "systemd-run")
        self.assertEqual(executed[-1], "uptime")

    def test_privileged_command_never_reaches_the_sandbox(self):
        with patch("src.offline_assistant.run_bounded",
                   return_value=(0, "done", "")) as bounded:
            ok, output = OfflineAssistant.run_command(
                Command(["apt-get", "install", "-y", "htop"], privileged=True))
        self.assertTrue(ok)
        self.assertEqual(bounded.call_args.args[0], [
            "pkexec", "apt-get", "install", "-y", "htop",
        ])
        self.run_argv.assert_not_called()

    def test_startup_failure_demotes_and_retries_bare_once(self):
        self.run_argv.side_effect = [
            (False, "Failed to start transient scope unit: Access denied"),
            (True, "recovered"),
        ]
        ok, output = OfflineAssistant.run_command(Command(["uptime"]))
        self.assertEqual((ok, output), (True, "recovered"))
        self.assertEqual(self.run_argv.call_count, 2)
        self.assertEqual(self.run_argv.call_args_list[0].args[0][0], "systemd-run")
        self.assertEqual(self.run_argv.call_args_list[1].args[0], ["uptime"])
        # A sessão fica com o wrapper desactivado.
        argv = ["uptime"]
        self.assertIs(command_sandbox.wrap(argv, 10), argv)

    def test_command_failure_is_not_retried(self):
        self.run_argv.return_value = (False, "network is unreachable")
        ok, output = OfflineAssistant.run_command(Command(["apt-get", "update"]))
        self.assertEqual((ok, output), (False, "network is unreachable"))
        self.assertEqual(self.run_argv.call_count, 1)
        # Uma falha comum NÃO desactiva o wrapper.
        self.assertEqual(command_sandbox.wrap(["uptime"], 10)[0], "systemd-run")


if __name__ == "__main__":
    unittest.main()
