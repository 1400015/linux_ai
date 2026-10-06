"""Phase 3: WSL bridge, PowerShell allowlist and WSL/Kali knowledge modules."""
import unittest
from types import SimpleNamespace

from src.knowledge_loader import available_bundled_modules, compose_modules
from src.local_knowledge import PROCEDURE_BY_ID
from src.platform.shell_pwsh import validate_pwsh_arguments as validate_pwsh
from src.platform.wsl_bridge import detect_wsl_distros, parse_wsl_list, probe_argv


def run_fixture(stdout, returncode=0):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout=stdout)
    return run


class TestWslListParsing(unittest.TestCase):
    def test_parses_utf16_output(self):
        output = "\ufeff  NAME            STATE           VERSION\n* Ubuntu-22.04    Running         2\n  Debian         Stopped         2\n".encode("utf-16")
        records = parse_wsl_list(output)
        self.assertEqual([record["name"] for record in records], ["Ubuntu-22.04", "Debian"])
        self.assertEqual(records[0]["state"], "Running")
        self.assertEqual(records[0]["version"], "2")

    def test_rejects_malformed_lines(self):
        output = "NAME STATE VERSION\n* ok Running 2\nbad!name Running 2\n"
        records = parse_wsl_list(output)
        self.assertEqual([record["name"] for record in records], ["ok"])

    def test_empty_output_yields_no_records(self):
        self.assertEqual(parse_wsl_list(""), ())
        self.assertEqual(parse_wsl_list(b""), ())


class TestWslDetection(unittest.TestCase):
    def test_missing_wsl_yields_empty(self):
        def failing_run(argv, **kwargs):
            raise FileNotFoundError("wsl.exe")
        self.assertEqual(detect_wsl_distros(failing_run), ())

    def test_nonzero_exit_yields_empty(self):
        self.assertEqual(detect_wsl_distros(run_fixture("err", returncode=1)), ())

    def test_valid_listing(self):
        output = "  NAME      STATE     VERSION\n* Ubuntu    Running   2\n".encode("utf-16")
        records = detect_wsl_distros(run_fixture(output))
        self.assertEqual(records[0]["name"], "Ubuntu")


class TestWslProbeArgv(unittest.TestCase):
    def test_valid_inner_probe_is_wrapped(self):
        argv = probe_argv("Ubuntu", ["ip", "-brief", "link"])
        self.assertEqual(argv, ("wsl.exe", "--distribution", "Ubuntu", "--exec", "ip", "-brief", "link"))

    def test_unsafe_inner_command_is_rejected(self):
        self.assertIsNone(probe_argv("Ubuntu", ["bash", "-c", "id"]))
        self.assertIsNone(probe_argv("Ubuntu", ["sudo", "id"]))

    def test_hostile_distro_name_is_rejected(self):
        self.assertIsNone(probe_argv("--exec", ["ip", "link"]))

    def test_empty_inner_command_is_rejected(self):
        self.assertIsNone(probe_argv("Ubuntu", []))


class TestPowerShellPolicy(unittest.TestCase):
    def test_read_only_cmdlets_are_allowed(self):
        self.assertTrue(validate_pwsh(["Get-NetAdapter"]))
        self.assertTrue(validate_pwsh(["Get-Service", "-Name", "wuauserv"]))
        self.assertTrue(validate_pwsh(["Get-WinEvent", "-LogName", "System", "-MaxEvents", "50"]))
        self.assertTrue(validate_pwsh(["Get-ExecutionPolicy", "-List"]))
        self.assertTrue(validate_pwsh(["Get-ChildItem", "-Recurse"]))

    def test_mutation_and_engine_cmdlets_are_rejected(self):
        for argv in (["Remove-Item", "-Path", "x"], ["Invoke-Expression", "x"],
                     ["Set-Content", "x"], ["Start-Process", "x"],
                     ["New-NetFirewallRule", "-DisplayName", "x"],
                     ["Invoke-WebRequest", "https://example.com"],
                     ["powershell"], ["pwsh"], ["wsl"], ["winget"]):
            with self.subTest(argv=argv):
                self.assertFalse(validate_pwsh(argv))

    def test_injection_tokens_are_rejected_anywhere(self):
        for argv in (["Get-Service", "-Name", "a;Remove-Item", "x"],
                     ["Get-Service", "-Name", "a|b"],
                     ["Get-Service", "-Name", "$(whoami)"],
                     ["Get-Service", "-Name", "`whoami"],
                     ["Get-Content", "-Path", "C:\\x;rm", "y"]):
            with self.subTest(argv=argv):
                self.assertFalse(validate_pwsh(argv))

    def test_unknown_and_unsafe_parameters_are_rejected(self):
        self.assertFalse(validate_pwsh(["Get-Service", "-ComputerName", "host"]))
        self.assertFalse(validate_pwsh(["Get-Service", "-Name"]))
        self.assertFalse(validate_pwsh(["Not-A-Cmdlet"]))
        self.assertFalse(validate_pwsh([]))

    def test_argv_size_is_bounded(self):
        self.assertFalse(validate_pwsh(["Get-Service"] + ["-Name"] * 40))


class TestPhase3KnowledgeModules(unittest.TestCase):
    def test_bundled_modules_include_wsl_and_kali(self):
        ids = {module["id"] for module in available_bundled_modules()}
        self.assertIn("wsl-core", ids)
        self.assertIn("distro-kali", ids)

    def test_kali_module_composes_for_kali(self):
        context = SimpleNamespace(
            distro_id="kali",
            id_like=("debian",),
            package_manager=SimpleNamespace(identifier="apt"),
            service_manager=SimpleNamespace(identifier="systemd"),
            audio=SimpleNamespace(identifier="pipewire"),
            network=SimpleNamespace(identifier="nmcli"),
        )
        ids = {module["id"] for module in compose_modules(context)}
        self.assertIn("distro-kali", ids)
        self.assertIn("packages-apt", ids)

    def test_wsl_module_requires_windows_core(self):
        modules = {module["id"]: module for module in available_bundled_modules()}
        self.assertIn("windows-core", modules["wsl-core"]["requires"])

    def test_wsl_procedures_exist(self):
        for identifier in ("wsl-distro-not-starting", "wsl-network-unreachable",
                           "wsl-systemd-not-running", "wsl-file-permissions"):
            with self.subTest(procedure=identifier):
                procedure = PROCEDURE_BY_ID.get(identifier)
                self.assertIsNotNone(procedure)
                self.assertEqual(procedure.effects, "read_only_guidance")
                self.assertTrue(all(step.probe_key == "" for step in procedure.steps))

    def test_kali_procedures_exist(self):
        for identifier in ("kali-apt-rolling-errors", "kali-tool-missing"):
            with self.subTest(procedure=identifier):
                procedure = PROCEDURE_BY_ID.get(identifier)
                self.assertIsNotNone(procedure)
                self.assertEqual(procedure.effects, "read_only_guidance")

    def test_kali_procedures_apply_to_kali_distro(self):
        distro = SimpleNamespace(distro_id="kali", id_like=("debian",), version_id="",
                                 service_manager="systemd")
        for identifier in ("kali-apt-rolling-errors", "kali-tool-missing"):
            procedure = PROCEDURE_BY_ID[identifier]
            with self.subTest(procedure=identifier):
                self.assertTrue(procedure.applies_to(distro))


if __name__ == "__main__":
    unittest.main()
