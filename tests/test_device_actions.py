"""Closed Wi-Fi, printer and scanner actions. No GTK and no real devices."""

import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.cli import CLIApp
from src.device_actions import (
    PrinterDevice, ScannerDevice, WifiNetwork, choose_numbered, confirmed,
    parse_printers, parse_scanners, parse_wifi_list, printer_add_argv,
    redact, split_offer, support_package, wifi_connect_argv,
)
from src.i18n import set_language
from src.offline_assistant import Command
from tests.test_offline import assistant


def _which(*names):
    def which(name):
        return f"/usr/bin/{name}" if name in names else None
    return which


class TestDeviceParsing(unittest.TestCase):
    def test_wifi_list_unescapes_dedupes_and_drops_empty_names(self):
        text = "\n".join([
            r"no:Cafe\:Net:70:WPA2",
            r"yes:Cafe\:Net:40:WPA2",
            "no::10:--",
            "no:Home:90:WPA2",
            "broken-line",
        ])
        networks = parse_wifi_list(text)
        self.assertEqual([item.ssid for item in networks], ["Home", "Cafe:Net"])
        cafe = networks[1]
        self.assertEqual(cafe.signal, 70)
        self.assertTrue(cafe.active)
        self.assertEqual(cafe.security, "WPA2")

    def test_wifi_and_printer_builders_reject_unsafe_values(self):
        self.assertEqual(
            wifi_connect_argv("Cafe Net", "p;rm"),
            ["nmcli", "device", "wifi", "connect", "Cafe Net", "password", "p;rm"],
        )
        with self.assertRaises(ValueError):
            wifi_connect_argv("bad\nname", "secret")
        with self.assertRaises(ValueError):
            wifi_connect_argv("Home", "secret\nline")
        printers = parse_printers("\n".join([
            "network ipp://192.0.2.10/ipp/print",
            "direct usb://hp/laser",
            "network file:///etc/passwd",
            "network ipp://192.0.2.10/My Printer",
        ]))
        self.assertEqual([item.uri for item in printers], [
            "ipp://192.0.2.10/ipp/print",
            "usb://hp/laser",
        ])
        self.assertTrue(printers[0].driverless)
        self.assertFalse(printers[1].driverless)
        argv = printer_add_argv("printer-abc123", printers[0].uri)
        self.assertEqual(argv[:4], ["lpadmin", "-p", "printer-abc123", "-E"])
        self.assertEqual(argv[-2:], ["-m", "everywhere"])
        with self.assertRaises(ValueError):
            printer_add_argv("printer-abc123", "usb://hp/laser")
        with self.assertRaises(ValueError):
            printer_add_argv("1bad", printers[0].uri)

    def test_scanner_line_and_redaction(self):
        devices = parse_scanners("device `airscan:e0:HP' is a HP ScanJet\nnoise")
        self.assertEqual(devices, [ScannerDevice("airscan:e0:HP", "HP ScanJet")])
        self.assertEqual(redact("failed for secret value", "secret"), "failed for ******** value")
        self.assertEqual(support_package("wifi", "xbps"), "NetworkManager")
        self.assertEqual(support_package("printer", "apt"), "cups")
        self.assertEqual(support_package("scanner", "pacman"), "sane-airscan")

    def test_numbered_choice_and_confirmation(self):
        answers = iter(["0", "2", "x", ""])
        prompts = []

        def read_line(prompt):
            prompts.append(prompt)
            return next(answers)

        self.assertIsNone(choose_numbered(["a", "b"], read_line, lambda text: None, lambda item: item))
        self.assertEqual(choose_numbered(["a", "b"], read_line, lambda text: None, lambda item: item), 1)
        self.assertIsNone(choose_numbered(["a", "b"], read_line, lambda text: None, lambda item: item))
        self.assertIsNone(choose_numbered(["a", "b"], read_line, lambda text: None, lambda item: item))
        self.assertEqual(prompts[0], "Number (Enter cancels): ")
        self.assertTrue(confirmed(" S "))
        self.assertTrue(confirmed("yes"))
        self.assertFalse(confirmed("n"))
        self.assertFalse(confirmed(""))

    def test_split_offer_ignores_stand_ins(self):
        self.assertEqual(split_offer(None), ("", []))
        self.assertEqual(split_offer(SimpleNamespace(interaction="wifi", commands=[])), ("wifi", []))
        self.assertEqual(split_offer(SimpleNamespace(interaction=Mock(), commands=Mock())), ("", []))
        self.assertEqual(split_offer(SimpleNamespace(commands=["not-a-real-check"])), ("", ["not-a-real-check"]))


class TestConfirmedOffers(unittest.TestCase):
    def test_propose_matches_handle_and_keeps_the_diagnostic(self):
        bot = assistant()
        bot._diagnostic = {"id": "network-interface", "step": 0}
        proposed = bot.propose("install htop")
        self.assertEqual(bot.diagnostic_state(), {"id": "network-interface", "step": 0})
        self.assertEqual(proposed.commands[0].argv, assistant().handle("install htop").commands[0].argv)
        self.assertEqual(proposed.commands[0].argv, ["apt-get", "install", "-y", "htop"])

    def test_leading_article_is_not_a_package_name(self):
        reply = assistant().propose("instalar o htop", "pt")
        self.assertEqual(reply.commands[0].argv, ["apt-get", "install", "-y", "htop"])
        self.assertEqual(assistant().handle("install the htop").commands[0].argv[-1], "htop")
        self.assertEqual(assistant().handle("install a package").commands, [])
        self.assertEqual(assistant().handle("install nano on Ubuntu").commands, [])
        mixed = assistant().propose("instala htop e configura o wifi", "pt")
        self.assertEqual(mixed.commands, [])
        self.assertEqual(mixed.interaction, "")

    def test_problems_and_refusals_do_not_become_actions(self):
        for text in ("erro ao instalar nano", "não quero wifi", "wifi não funciona", "não instales nano"):
            with self.subTest(text=text):
                proposed = assistant().propose(text, "pt")
                self.assertEqual(proposed.commands, [])
                self.assertEqual(proposed.interaction, "")
                self.assertEqual(proposed.text, "")
                handled = assistant().handle(text, "pt")
                self.assertEqual(handled.commands, [])
                self.assertEqual(handled.interaction, "")

    def test_missing_tools_offer_the_known_package(self):
        wifi = assistant().handle("mostra as redes wifi", "pt")
        self.assertEqual(wifi.interaction, "")
        self.assertEqual(wifi.commands[0].argv, ["apt-get", "install", "-y", "network-manager"])
        void = assistant(distro_id="void", pretty="Void", id_like="").handle("wifi")
        self.assertEqual(void.commands[0].argv, ["xbps-install", "-Sy", "NetworkManager"])
        printer = assistant().handle("configurar a impressora", "pt")
        self.assertEqual(printer.commands[0].argv[-1], "cups")
        scanner = assistant().handle("configurar o scanner", "pt")
        self.assertEqual(scanner.interaction, "")
        self.assertEqual(scanner.commands[0].argv[-1], "sane-airscan")

    def test_present_tools_open_a_chooser_instead_of_installing(self):
        wifi = assistant(which=_which("nmcli")).handle("redes sem fios")
        self.assertEqual(wifi.interaction, "wifi")
        self.assertEqual(wifi.commands, [])
        printer = assistant(which=_which("lpinfo")).handle("printer")
        self.assertEqual(printer.interaction, "printer")
        self.assertEqual(printer.commands, [])
        scanner = assistant(which=_which("scanimage")).handle("scanner")
        self.assertEqual(scanner.interaction, "scanner")
        self.assertEqual(scanner.commands[0].argv[-1], "sane-airscan")


class TestCliConfirmation(unittest.TestCase):
    def setUp(self):
        set_language("en")
        self.app = CLIApp.__new__(CLIApp)

    def tearDown(self):
        set_language("en")

    def test_non_tty_prints_the_command_and_does_not_run_or_scan(self):
        reply = assistant().propose("install htop")
        with patch("sys.stdin.isatty", return_value=False), \
                patch("sys.stdout", new=io.StringIO()) as output, \
                patch("src.cli.collect_wifi", side_effect=AssertionError("scanned")), \
                patch("src.cli.offline_assistant.OfflineAssistant.run_command",
                      side_effect=AssertionError("ran")):
            self.assertEqual(self.app._offer_action(reply), "")
        text = output.getvalue()
        self.assertIn("apt-get install -y htop", text)
        self.assertIn("interactive terminal", text)

        wifi = SimpleNamespace(text="list", commands=[], interaction="wifi")
        with patch("sys.stdin.isatty", return_value=False), \
                patch("sys.stdout", new=io.StringIO()) as output, \
                patch("src.cli.collect_wifi", side_effect=AssertionError("scanned")):
            self.assertEqual(self.app._offer_action(wifi), "")
        self.assertIn("interactive terminal", output.getvalue())

    def test_tty_runs_only_after_yes_and_stops_on_no(self):
        reply = assistant().propose("install htop")
        with patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value="n"), \
                patch("sys.stdout", new=io.StringIO()), \
                patch("src.cli.offline_assistant.OfflineAssistant.run_command") as run:
            self.assertEqual(self.app._offer_action(reply), "")
        run.assert_not_called()
        with patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", return_value="y"), \
                patch("sys.stdout", new=io.StringIO()) as output, \
                patch("src.cli.offline_assistant.OfflineAssistant.run_command",
                      return_value=(True, "installed")) as run:
            result = self.app._offer_action(reply)
        self.assertEqual(run.call_args.args[0].argv, ["apt-get", "install", "-y", "htop"])
        self.assertIn("installed", result)
        self.assertNotIn("secret", output.getvalue())

    def test_wifi_choice_never_prints_the_password(self):
        network = WifiNetwork("Cafe", 70, "WPA2", False)
        reply = SimpleNamespace(text="wifi", commands=[], interaction="wifi")
        answers = iter(["1"])
        with patch("sys.stdin.isatty", return_value=True), \
                patch("src.cli.collect_wifi", return_value=([network], "")), \
                patch("src.cli.connect_wifi", return_value=(True, "secret was here")) as connect, \
                patch("builtins.input", side_effect=lambda _prompt: next(answers)), \
                patch("src.cli.getpass.getpass", return_value="secret"), \
                patch("sys.stdout", new=io.StringIO()) as output:
            result = self.app._offer_action(reply)
        self.assertEqual(connect.call_args.args, ("Cafe", "secret"))
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn("secret", result)
        self.assertIn("Cafe", result)

    def test_open_network_does_not_ask_for_a_password(self):
        network = WifiNetwork("OpenNet", 50, "--", False)
        answers = iter(["1"])
        with patch("src.cli.collect_wifi", return_value=([network], "")), \
                patch("src.cli.connect_wifi", return_value=(True, "")) as connect, \
                patch("builtins.input", side_effect=lambda _prompt: next(answers)), \
                patch("src.cli.getpass.getpass", side_effect=AssertionError("asked")), \
                patch("sys.stdout", new=io.StringIO()):
            self.app._choose_wifi()
        self.assertEqual(connect.call_args.args, ("OpenNet", None))

    def test_usb_printer_is_not_added_and_a_found_scanner_is_not_installed(self):
        answers = iter(["1", ""])
        with patch("src.cli.collect_printers", return_value=([PrinterDevice("usb://hp/laser", False)], "")), \
                patch("builtins.input", side_effect=lambda _prompt: next(answers)), \
                patch("sys.stdout", new=io.StringIO()) as output, \
                patch("src.cli.offline_assistant.OfflineAssistant.run_command") as run:
            text = self.app._choose_printer([])
        run.assert_not_called()
        self.assertIn("driver", text.lower())
        self.assertIn("driver", output.getvalue().lower())

        command = Command(["apt-get", "install", "-y", "sane-airscan"], privileged=True)
        device = ScannerDevice("airscan:e0:HP", "HP ScanJet")
        with patch("src.cli.collect_scanners", return_value=([device], "")), \
                patch("sys.stdout", new=io.StringIO()) as output, \
                patch("src.cli.offline_assistant.OfflineAssistant.run_command") as run:
            text = self.app._choose_scanner([command])
        run.assert_not_called()
        self.assertIn("HP ScanJet", text)
        self.assertIn("HP ScanJet", output.getvalue())

    def test_model_offer_uses_the_user_sentence(self):
        self.app.offline = assistant()
        with patch("sys.stdin.isatty", return_value=False), \
                patch("sys.stdout", new=io.StringIO()) as output, \
                patch("src.cli.offline_assistant.OfflineAssistant.run_command",
                      side_effect=AssertionError("ran")):
            self.app._offer_model_action("install htop")
        self.assertIn("apt-get install -y htop", output.getvalue())
        with patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(self.app._offer_model_action("what is dns?"), "")


class TestGuiActionRouting(unittest.TestCase):
    def test_a_device_chooser_is_not_paired_with_the_generic_dialog(self):
        try:
            from src.main_window import MainWindow
        except (ImportError, ValueError) as exc:
            self.skipTest("GTK3 unavailable: " + str(exc))
        started = []
        window = SimpleNamespace(
            _active_request=1,
            _start_device_choice=lambda *args: started.append(("device", args)),
            _offer_offline_commands=lambda *args: started.append(("commands", args)) or False,
        )
        command = Command(["apt-get", "install", "-y", "sane-airscan"], privileged=True)
        result = MainWindow._offer_confirmed_action(
            window, "scanner", [command], 1, threading.Event(),
        )
        self.assertFalse(result)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][0], "device")
        started.clear()
        MainWindow._offer_confirmed_action(window, "", [command], 1, threading.Event())
        self.assertEqual(started[0][0], "commands")
        started.clear()
        MainWindow._offer_confirmed_action(window, "wifi", [], 2, threading.Event())
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
