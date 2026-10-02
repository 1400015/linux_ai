"""Device intent safety and explicit scanner tool dependencies; no real devices."""

import unittest
from unittest.mock import patch

from src.device_actions import scanner_support_packages
from src.offline_assistant import OfflineAssistant


def assistant(distro_id="ubuntu", tools=()):
    return OfflineAssistant(
        os_release={"ID": distro_id, "PRETTY_NAME": distro_id},
        which=lambda name: "/usr/bin/" + name if name in tools else None,
        is_systemd_running=False,
    )


class TestHypotheticalDeviceRequests(unittest.TestCase):
    def test_hypothetical_requests_never_offer_setup_or_installation(self):
        requests = (
            ("pt", "se eu configurar o wifi"),
            ("pt", "o que acontece se configurar as redes sem fios?"),
            ("pt", "talvez configurar a impressora"),
            ("pt", "se eu configurar o scanner"),
            ("en", "what happens if I configure wifi?"),
            ("en", "maybe configure the printer"),
            ("en", "if I configure the scanner"),
            ("en", "whether to configure wifi"),
        )
        for lang, message in requests:
            for tools in ((), ("nmcli", "lpinfo", "scanimage")):
                for method in ("handle", "propose"):
                    with self.subTest(lang=lang, message=message, tools=tools, method=method):
                        bot = assistant(tools=tools)
                        state = {"id": "network-interface", "step": 0}
                        self.assertTrue(bot.restore_diagnostic(state))
                        with patch("subprocess.run", side_effect=AssertionError("Executed a command")):
                            reply = getattr(bot, method)(message, lang)
                        self.assertEqual(reply.commands, [])
                        self.assertEqual(reply.interaction, "")
                        self.assertEqual(bot.diagnostic_state(), state)

    def test_device_refusals_still_produce_no_action(self):
        requests = (
            ("pt", "não quero configurar o wifi"),
            ("pt", "não configurar a impressora"),
            ("pt", "nunca configurar o scanner"),
            ("en", "do not configure wifi"),
            ("en", "don't configure the printer"),
            ("en", "never configure the scanner"),
        )
        for lang, message in requests:
            for tools in ((), ("nmcli", "lpinfo", "scanimage")):
                for method in ("handle", "propose"):
                    with self.subTest(message=message, tools=tools, method=method):
                        reply = getattr(assistant(tools=tools), method)(message, lang)
                        self.assertEqual(reply.commands, [])
                        self.assertEqual(reply.interaction, "")

    def test_direct_requests_still_reach_the_device_chooser(self):
        for lang, message, expected in (
            ("pt", "configurar as redes sem fios", "wifi"),
            ("pt", "configurar a impressora", "printer"),
            ("pt", "configurar o scanner", "scanner"),
            ("en", "configure wifi", "wifi"),
            ("en", "configure the printer", "printer"),
            ("en", "configure the scanner", "scanner"),
        ):
            for method in ("handle", "propose"):
                with self.subTest(lang=lang, message=message, method=method):
                    reply = getattr(assistant(tools=("nmcli", "lpinfo", "scanimage")), method)(message, lang)
                    self.assertEqual(reply.interaction, expected)

    def test_wifi_failure_remains_a_diagnostic_without_a_setup_action(self):
        bot = assistant(tools=("nmcli",))
        reply = bot.handle("wifi não funciona", "pt")
        self.assertEqual(reply.commands, [])
        self.assertEqual(reply.interaction, "")
        self.assertIsNotNone(bot.diagnostic_state())


class TestScannerToolDependencies(unittest.TestCase):
    # Exact command transactions, including the package owning scanimage.
    CASES = (
        ("debian", "apt", ["apt-get", "install", "-y"], "sane-utils"),
        ("ubuntu", "apt", ["apt-get", "install", "-y"], "sane-utils"),
        ("void", "xbps", ["xbps-install", "-Sy"], "sane"),
        ("fedora", "dnf", ["dnf", "install", "-y"], "sane-backends"),
        ("arch", "pacman", ["pacman", "-S", "--noconfirm"], "sane"),
        ("opensuse-tumbleweed", "zypper", ["zypper", "install", "-y"], "sane-backends"),
        ("alpine", "apk", ["apk", "add"], "sane-utils"),
    )

    def test_missing_scanimage_offers_frontend_and_backend_in_one_confirmation(self):
        for distro, manager, prefix, frontend in self.CASES:
            for lang, message in (("pt", "configurar o scanner"), ("en", "configure the scanner")):
                for method in ("handle", "propose"):
                    with self.subTest(distro=distro, lang=lang, method=method):
                        bot = assistant(distro)
                        self.assertEqual(bot.distro.pkg_manager, manager)
                        with patch("subprocess.run", side_effect=AssertionError("Executed an install")):
                            reply = getattr(bot, method)(message, lang)
                        self.assertEqual(reply.interaction, "")
                        self.assertEqual(len(reply.commands), 1)
                        self.assertTrue(reply.commands[0].privileged)
                        self.assertEqual(reply.commands[0].argv, prefix + [frontend, "sane-airscan"])
                        self.assertIn(frontend, reply.text)
                        self.assertIn("sane-airscan", reply.text)

    def test_existing_scanimage_keeps_discovery_before_backend_installation(self):
        for distro, manager, prefix, _frontend in self.CASES:
            with self.subTest(distro=distro):
                bot = assistant(distro, tools=("scanimage",))
                reply = bot.handle("scanner")
                self.assertEqual(reply.interaction, "scanner")
                self.assertEqual(len(reply.commands), 1)
                self.assertEqual(reply.commands[0].argv, prefix + ["sane-airscan"])
                self.assertEqual(scanner_support_packages(manager, needs_scanimage=False), ("sane-airscan",))

    def test_unknown_package_manager_never_guesses_install_commands(self):
        self.assertEqual(scanner_support_packages("unknown"), ())
        self.assertEqual(scanner_support_packages("unknown", needs_scanimage=False), ())
        reply = assistant("unknown-distro").handle("scanner")
        self.assertEqual(reply.commands, [])
        self.assertEqual(reply.interaction, "")


if __name__ == "__main__":
    unittest.main()
