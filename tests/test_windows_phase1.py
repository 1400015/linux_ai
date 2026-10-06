"""Phase 1 platform detection and Windows/PowerShell knowledge modules."""
import unittest

from src.knowledge_loader import available_bundled_modules, compose_modules
from src.local_knowledge import PROCEDURE_BY_ID
from src.platform import LINUX, WSL, WINDOWS, detect_platform, effective_platform, wsl_distro_name


class TestPlatformDetection(unittest.TestCase):
    def test_windows_detection_ignores_environment(self):
        self.assertEqual(
            detect_platform(environ={"WSL_DISTRO_NAME": "Ubuntu"}, system_platform="win32"),
            WINDOWS,
        )

    def test_wsl_distro_name_variable(self):
        self.assertEqual(
            detect_platform(environ={"WSL_DISTRO_NAME": "Debian"}, system_platform="linux"),
            WSL,
        )
        self.assertEqual(wsl_distro_name({"WSL_DISTRO_NAME": "Kali"}), "Kali")
        self.assertEqual(wsl_distro_name({}), "")

    def test_wsl_proc_version_fallback(self):
        self.assertEqual(
            detect_platform(
                environ={},
                read_text=lambda path: "Linux version 6.6 (Microsoft@WSL2)",
                system_platform="linux",
            ),
            WSL,
        )

    def test_linux_detection(self):
        self.assertEqual(
            detect_platform(
                environ={},
                read_text=lambda path: "Linux version 6.6",
                system_platform="linux",
            ),
            LINUX,
        )

    def test_effective_platform_validates_explicit_value(self):
        self.assertEqual(effective_platform(WINDOWS), WINDOWS)
        self.assertEqual(effective_platform("invalid"), LINUX)
        self.assertEqual(
            effective_platform(None, environ={"WSL_DISTRO_NAME": "Ubuntu"}, system_platform="linux"),
            WSL,
        )


def context(distro_id="ubuntu", identifiers=("apt", "systemd", "pipewire", "nmcli")):
    from types import SimpleNamespace

    components = ("package_manager", "service_manager", "audio", "network")
    values = (identifiers + ("unknown",) * len(components))[: len(components)]
    modules = [SimpleNamespace(identifier=value) for value in values]
    return SimpleNamespace(
        distro_id=distro_id,
        id_like=(),
        **dict(zip(components, modules)),
    )


class TestWindowsKnowledgeModules(unittest.TestCase):
    def test_bundled_modules_include_windows_knowledge(self):
        ids = {module["id"] for module in available_bundled_modules()}
        self.assertIn("windows-core", ids)
        self.assertIn("shell-powershell", ids)

    def test_windows_modules_compose_on_windows_only(self):
        linux_modules = {module["id"] for module in compose_modules(context(), platform=LINUX)}
        windows_modules = {module["id"] for module in compose_modules(context(), platform=WINDOWS)}
        self.assertNotIn("windows-core", linux_modules)
        self.assertNotIn("shell-powershell", linux_modules)
        self.assertIn("windows-core", windows_modules)
        self.assertIn("shell-powershell", windows_modules)

    def test_windows_modules_do_not_compose_for_linux_by_detection(self):
        modules = {module["id"] for module in compose_modules(context())}
        self.assertNotIn("windows-core", modules)

    def test_windows_procedures_are_searchable(self):
        identifiers = {"windows-service-fails", "windows-disk-space", "windows-network-adapter",
                       "windows-update-issue", "windows-event-log-errors",
                       "powershell-execution-policy", "powershell-profile-not-loading",
                       "powershell-module-missing", "powershell-alias-conflict",
                       "powershell-error-reading"}
        for identifier in identifiers:
            with self.subTest(procedure=identifier):
                procedure = PROCEDURE_BY_ID.get(identifier)
                self.assertIsNotNone(procedure)
                self.assertEqual(len(procedure.title), 2)
                self.assertTrue(procedure.sources)
                self.assertEqual(procedure.effects, "read_only_guidance")

    def test_windows_modules_are_read_only(self):
        for module in available_bundled_modules():
            if module["kind"] != "platform":
                continue
            with self.subTest(module=module["id"]):
                self.assertEqual(module["action_ids"], [])
                for procedure in module["procedures"]:
                    self.assertEqual(procedure["effects"], "read_only_guidance")
                    for step in procedure["steps"]:
                        self.assertEqual(step["probe_key"], "")
                        self.assertEqual(step["command"], "")

    def test_module_sources_are_https(self):
        for module in available_bundled_modules():
            if module["id"] not in {"windows-core", "shell-powershell"}:
                continue
            with self.subTest(module=module["id"]):
                for source in module["provenance"]["sources"]:
                    self.assertTrue(source.startswith("https://"))
                for procedure in module["procedures"]:
                    for source in procedure["sources"]:
                        self.assertTrue(source.startswith("https://"))

    def test_powershell_module_requires_windows_core(self):
        modules = {module["id"]: module for module in available_bundled_modules()}
        self.assertIn("windows-core", modules["shell-powershell"]["requires"])


if __name__ == "__main__":
    unittest.main()
