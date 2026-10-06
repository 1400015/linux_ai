"""Phase 3 integration: platform probes flow through the real execution path."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.platform import WINDOWS, WSL
from src.platform.probes import probe_argv_for


class TestProbeArgvFor(unittest.TestCase):
    def test_windows_probes_are_wrapped_for_powershell(self):
        for key in ("links", "addresses", "routes", "disk", "memory"):
            with self.subTest(key=key):
                argv = probe_argv_for(key, platform=WINDOWS)
                self.assertIsNotNone(argv)
                self.assertEqual(argv[:2], ("powershell", "-Command"))
                self.assertTrue(argv[2].startswith("Get-"))

    def test_windows_probe_is_none_when_policy_rejects(self):
        def reject(argv):
            return False
        self.assertIsNone(probe_argv_for("links", platform=WINDOWS, validate_pwsh=reject))

    def test_wsl_probe_wraps_posix_inner_command(self):
        argv = probe_argv_for("disk", platform=WSL, wsl_distro="Ubuntu")
        self.assertEqual(argv, ("wsl.exe", "--distribution", "Ubuntu", "--exec", "df", "-h"))

    def test_wsl_probe_without_distro_is_none(self):
        self.assertIsNone(probe_argv_for("disk", platform=WSL, wsl_distro=None))

    def test_linux_keeps_none_and_native_path(self):
        self.assertIsNone(probe_argv_for("links", platform="linux"))
        self.assertIsNone(probe_argv_for("not-a-key", platform=WINDOWS))


class TestSystemUtilsPlatformPath(unittest.TestCase):
    def setUp(self):
        from src.system_utils import SystemUtils
        self.utils = SystemUtils(SimpleNamespace(get=Mock(return_value={"allowed_commands": ["df"]})))

    def test_validated_powershell_probe_runs_through_platform_path(self):
        utils = self.utils
        captured = {}
        def fake_run(argv, timeout, limit):
            captured["argv"] = argv
            return 0, "Name  InterfaceDescription", ""
        with patch("src.system_utils.run_bounded", side_effect=fake_run):
            ok, output = utils.execute_command(["powershell", "-Command", "Get-NetAdapter"], timeout=5)
        self.assertTrue(ok)
        self.assertIn("InterfaceDescription", output)
        self.assertEqual(captured["argv"][0], "powershell")

    def test_mutation_cmdlet_is_rejected(self):
        ok, output = self.utils.execute_command(["powershell", "-Command", "Remove-Item", "-Path", "x"])
        self.assertFalse(ok)
        self.assertIn("rejected", output)

    def test_injection_is_rejected(self):
        ok, output = self.utils.execute_command(["powershell", "-Command", "Get-Service", "-Name", "a;rm"])
        self.assertFalse(ok)

    def test_bare_powershell_without_command_is_rejected(self):
        ok, output = self.utils.execute_command(["powershell"])
        self.assertFalse(ok)

    def test_wsl_probe_with_validated_inner_command_runs(self):
        utils = self.utils
        def fake_run(argv, timeout, limit):
            return 0, "Filesystem Size Used", ""
        with patch("src.system_utils.run_bounded", side_effect=fake_run):
            ok, output = utils.execute_command(
                ["wsl.exe", "--distribution", "Ubuntu", "--exec", "df", "-h"], timeout=5)
        self.assertTrue(ok)

    def test_wsl_probe_with_unsafe_inner_command_is_rejected(self):
        ok, output = self.utils.execute_command(
            ["wsl.exe", "--distribution", "Ubuntu", "--exec", "bash", "-c", "id"])
        self.assertFalse(ok)

    def test_posix_allowlist_still_applies_to_normal_commands(self):
        ok, output = self.utils.execute_command(["ls"], timeout=5)
        self.assertFalse(ok)
        self.assertIn("not allowed", output.lower())


class TestOfflineAssistantPlatformProbe(unittest.TestCase):
    def setUp(self):
        from src.offline_assistant import OfflineAssistant
        self.OfflineAssistant = OfflineAssistant

    def assistant(self, platform="linux", wsl=""):
        utils = Mock()
        utils.execute_command = Mock(return_value=(True, "UP  eth0"))
        assistant = self.OfflineAssistant(
            utils,
            os_release={"ID": "ubuntu", "PRETTY_NAME": "Ubuntu", "ID_LIKE": "debian"},
            which=lambda name: None,
            is_systemd_running=False,
        )
        assistant._platform = platform
        assistant._wsl_distro = wsl
        return assistant, utils

    def test_windows_probe_uses_powershell_argv(self):
        assistant, utils = self.assistant(platform=WINDOWS)
        result = assistant._probe("links")
        self.assertEqual(utils.execute_command.call_args[0][0],
                         ["powershell", "-Command", "Get-NetAdapter"])
        self.assertEqual(result, "UP  eth0")

    def test_wsl_probe_wraps_posix_command(self):
        assistant, utils = self.assistant(platform=WSL, wsl="Ubuntu")
        assistant._probe("disk")
        self.assertEqual(utils.execute_command.call_args[0][0],
                         ["wsl.exe", "--distribution", "Ubuntu", "--exec", "df", "-h"])

    def test_windows_probe_without_utils_returns_none(self):
        assistant, _ = self.assistant(platform=WINDOWS)
        assistant.system_utils = None
        self.assertIsNone(assistant._probe("links"))

    def test_linux_probe_keeps_native_path(self):
        assistant, utils = self.assistant(platform="linux")
        assistant._distro = SimpleNamespace(
            available_tools=("ip", "df", "free"))
        assistant._probe("links")
        self.assertEqual(utils.execute_command.call_args[0][0], "ip link show")


if __name__ == "__main__":
    unittest.main()
