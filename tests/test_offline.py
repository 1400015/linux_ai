"""Tests for the offline assistant (no GTK, no network)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.offline_assistant import OfflineAssistant, detect_distro
from src.ai_client import AIClient
from src.config_manager import ConfigManager


class FakeSystemUtils:
    """Minimal SystemUtils stand-in returning canned command output."""

    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.calls = []

    def execute_command(self, command, timeout=10):
        self.calls.append(command)
        return self.outputs.get(command.split()[0], (False, "not allowed"))


def assistant(distro_id="ubuntu", pretty="Ubuntu 24.04", id_like="debian", su=None,
              which=lambda name: None, is_systemd_running=False):
    """Fixture determinística: sem sondas injectadas, um runner Linux com
    systemd a correr detetava "void" como systemd (o CI antigo nunca chegou
    a correr estes testes — morria no import)."""
    return OfflineAssistant(
        su,
        os_release={
            "ID": distro_id,
            "PRETTY_NAME": pretty,
            "ID_LIKE": id_like,
        },
        which=which,
        is_systemd_running=is_systemd_running,
    )


class TestDetectDistro(unittest.TestCase):
    def test_void_uses_xbps_and_runit(self):
        d = detect_distro({"ID": "void", "PRETTY_NAME": "Void"},
                          which=lambda name: None, is_systemd_running=False)
        self.assertEqual(d.pkg_manager, "xbps")
        self.assertEqual(d.service_manager, "runit")

    def test_debian_family_uses_apt_and_systemd(self):
        d = detect_distro({"ID": "ubuntu", "ID_LIKE": "debian"},
                          which=lambda name: None, is_systemd_running=False)
        self.assertEqual(d.pkg_manager, "apt")
        self.assertEqual(d.service_manager, "systemd")

    def test_arch_uses_pacman(self):
        d = detect_distro({"ID": "arch"}, which=lambda name: None,
                          is_systemd_running=False)
        self.assertEqual(d.pkg_manager, "pacman")

    def test_alpine_uses_apk_and_openrc(self):
        d = detect_distro({"ID": "alpine"}, which=lambda name: None,
                          is_systemd_running=False)
        self.assertEqual(d.pkg_manager, "apk")
        self.assertEqual(d.service_manager, "openrc")

    def test_systemd_running_wins_over_installed_sv(self):
        d = detect_distro(
            {"ID": "void"},
            which=lambda name: "/usr/bin/sv" if name == "sv" else None,
            is_systemd_running=True,
        )
        self.assertEqual(d.service_manager, "systemd")

    def test_id_like_is_used_as_fallback(self):
        d = detect_distro({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"},
                          which=lambda name: None, is_systemd_running=False)
        # linuxmint is mapped directly; unknown ids fall back through ID_LIKE.
        self.assertIn(d.pkg_manager, ("apt", "xbps"))


class TestOfflineIntents(unittest.TestCase):
    def test_update_offers_a_privileged_command(self):
        reply = assistant().handle("como atualizo o sistema?", "pt")
        self.assertIn("apt-get", reply.text)
        self.assertTrue(reply.commands)
        self.assertTrue(all(c.privileged for c in reply.commands))

    def test_install_named_package(self):
        reply = assistant().handle("install htop", "en")
        self.assertEqual(reply.commands[0].argv[-1], "htop")

    def test_remove_named_package(self):
        reply = assistant().handle("remove nano", "en")
        self.assertIn("nano", reply.commands[0].argv)

    def test_service_enable_substitutes_placeholder(self):
        reply = assistant(distro_id="void", pretty="Void", id_like="") \
            .handle("ativar serviço chronyd", "pt")
        argv = reply.commands[0].argv
        self.assertIn("/etc/sv/chronyd", argv)
        self.assertNotIn("{svc}", argv)

    def test_set_timezone(self):
        reply = assistant().handle("set timezone to Europe/Lisbon", "en")
        self.assertEqual(
            reply.commands[0].argv,
            ["timedatectl", "set-timezone", "Europe/Lisbon"],
        )

    def test_set_hostname(self):
        reply = assistant().handle("set hostname to laptop", "en")
        self.assertEqual(
            reply.commands[0].argv,
            ["hostnamectl", "set-hostname", "laptop"],
        )

    def test_shell_metacharacters_never_reach_a_command(self):
        reply = assistant().handle("install pkg; rm -rf ~", "en")
        for command in reply.commands:
            for part in command.argv:
                self.assertNotIn(";", part)
                self.assertNotIn("rm", part)

    def test_stopword_is_not_treated_as_a_package(self):
        reply = assistant().handle("install a package", "en")
        self.assertEqual(reply.commands, [])

    def test_unknown_question_falls_back_to_help(self):
        reply = assistant().handle("conta-me uma historia", "pt")
        self.assertIn("modo offline", reply.text.lower())

    def test_unknown_language_falls_back_to_english(self):
        reply = assistant().handle("install htop", "xx")
        self.assertIn("install", reply.text.lower())

    def test_disk_uses_diagnostic_output(self):
        su = FakeSystemUtils({"df": (True, "/dev/sda1 100G 40G")})
        reply = assistant(su=su).handle("quanto espaço em disco?", "pt")
        self.assertIn("/dev/sda1", reply.text)
        self.assertIn("df -h", su.calls)


class TestProviderReady(unittest.TestCase):
    """AIClient.provider_ready drives the automatic offline fallback."""

    def _make(self, d):
        config = ConfigManager(str(Path(d) / "config.json"))
        # Patch Path.home so usage.json lands in the temp dir and clearing the
        # environment (which drops HOME) does not break AIClient.
        with patch("src.ai_client.Path.home", return_value=Path(d)):
            return config, AIClient(config)

    def test_no_key_means_not_ready(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {}, clear=True):
            config, client = self._make(d)
            try:
                self.assertFalse(client.provider_ready())
            finally:
                client.session.close()

    def test_key_means_ready(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {}, clear=True):
            config, client = self._make(d)
            config.set_api_key("openrouter", "k")
            try:
                self.assertTrue(client.provider_ready())
            finally:
                client.session.close()

    def test_local_llm_needs_no_key(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {}, clear=True):
            config, client = self._make(d)
            config.set("api.default_provider", "local_llm")
            try:
                self.assertTrue(client.provider_ready())
            finally:
                client.session.close()


if __name__ == "__main__":
    unittest.main()
