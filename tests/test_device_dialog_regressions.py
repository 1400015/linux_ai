"""GTK confirmation regressions; device discovery and execution are simulated."""

import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.device_actions import PrinterDevice, WifiNetwork, printer_add_argv, queue_name_for
from src.i18n import set_language
from src.offline_assistant import Command


class TestDeviceDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest("GTK display unavailable; run with xvfb-run")
            from src import device_dialogs
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest("GTK3 unavailable: " + str(error))
        cls.Gtk = Gtk
        cls.dialogs = device_dialogs

    def setUp(self):
        set_language("en")
        self.opened_dialogs = []

    def tearDown(self):
        for dialog in self.opened_dialogs:
            dialog.destroy()
        set_language("en")

    def widget(self, dialog, widget_type):
        pending = [dialog.get_content_area()]
        while pending:
            child = pending.pop()
            if isinstance(child, widget_type):
                return child
            if isinstance(child, self.Gtk.Container):
                pending.extend(child.get_children())
        self.fail("Dialog widget not found: " + widget_type.__name__)

    def simulate_dialog(self, interaction, response=None):
        original = self.dialogs._dialog
        if response is None:
            response = self.Gtk.ResponseType.OK

        def create(*args):
            dialog, area = original(*args)
            self.opened_dialogs.append(dialog)

            def run():
                interaction(dialog)
                return response

            dialog.run = run
            return dialog, area

        return patch.object(self.dialogs, "_dialog", side_effect=create)

    def immediate_worker(self):
        return patch.object(
            self.dialogs.threading, "Thread",
            side_effect=lambda target, daemon: SimpleNamespace(start=target),
        )

    def printer_preview(self, dialog):
        return next(
            child.get_text() for child in dialog.get_content_area().get_children()
            if isinstance(child, self.Gtk.Label) and child.get_text().startswith("$ ")
        )

    def test_edited_queue_preview_matches_the_executed_command(self):
        device = PrinterDevice("ipp://192.0.2.10/ipp/print", True)
        approved = []

        def approve(dialog):
            queue = self.widget(dialog, self.Gtk.Entry)
            queue.set_text("existing-production-queue")
            approved.append(self.printer_preview(dialog))
            self.assertEqual(
                approved[0], "$ " + shlex.join(printer_add_argv(queue.get_text(), device.uri))
            )
            self.assertTrue(dialog.get_widget_for_response(self.Gtk.ResponseType.OK).get_sensitive())

        with self.simulate_dialog(approve), self.immediate_worker(), patch.object(
            self.dialogs.OfflineAssistant, "run_privileged", return_value=(True, "")
        ) as run:
            self.dialogs._present_printers(None, [device], "", [], Mock(), lambda: True)
        command = run.call_args.args[0]
        self.assertEqual("$ " + command.display(), approved[0])
        self.assertEqual(command.argv[2], "existing-production-queue")

    def test_switching_printer_updates_queue_and_final_approved_device(self):
        devices = [
            PrinterDevice("ipp://192.0.2.10/ipp/print", True),
            PrinterDevice("ipps://192.0.2.20/ipp/print", True),
        ]
        approved = []

        def approve(dialog):
            queue = self.widget(dialog, self.Gtk.Entry)
            queue.set_text("first-queue")
            listbox = self.widget(dialog, self.Gtk.ListBox)
            listbox.select_row(listbox.get_row_at_index(1))
            self.assertEqual(queue.get_text(), queue_name_for(devices[1].uri))
            queue.set_text("second-queue")
            approved.append(self.printer_preview(dialog))

        with self.simulate_dialog(approve), self.immediate_worker(), patch.object(
            self.dialogs.OfflineAssistant, "run_privileged", return_value=(True, "")
        ) as run:
            self.dialogs._present_printers(None, devices, "", [], Mock(), lambda: True)
        self.assertEqual(run.call_args.args[0].argv, printer_add_argv("second-queue", devices[1].uri))
        self.assertEqual("$ " + run.call_args.args[0].display(), approved[0])

    def test_invalid_queue_disables_confirmation_and_never_executes(self):
        device = PrinterDevice("ipp://192.0.2.10/ipp/print", True)

        def approve(dialog):
            self.widget(dialog, self.Gtk.Entry).set_text("invalid queue;name")
            self.assertFalse(dialog.get_widget_for_response(self.Gtk.ResponseType.OK).get_sensitive())
            labels = [child.get_text() for child in dialog.get_content_area().get_children()
                      if isinstance(child, self.Gtk.Label)]
            self.assertFalse(any(text.startswith("$ ") for text in labels))

        with self.simulate_dialog(approve), self.immediate_worker(), patch.object(
            self.dialogs.OfflineAssistant, "run_privileged"
        ) as run:
            self.dialogs._present_printers(None, [device], "", [], Mock(), lambda: True)
        run.assert_not_called()

    def test_switching_secured_ssid_clears_password_before_new_connection(self):
        networks = [WifiNetwork("PrivateNet", 80, "WPA2", False),
                    WifiNetwork("OtherPrivateNet", 90, "WPA2", False)]

        def approve(dialog):
            password = self.widget(dialog, self.Gtk.Entry)
            password.set_text("first-secret")
            listbox = self.widget(dialog, self.Gtk.ListBox)
            listbox.select_row(listbox.get_row_at_index(1))
            self.assertEqual(password.get_text(), "")
            self.assertTrue(password.get_sensitive())
            password.set_text("second-secret")

        with self.simulate_dialog(approve), self.immediate_worker(), patch.object(
            self.dialogs, "connect_wifi", return_value=(True, "")
        ) as connect:
            self.dialogs._present_wifi(None, networks, "", Mock(), lambda: True)
        connect.assert_called_once_with("OtherPrivateNet", "second-secret")

    def test_open_network_discards_password_even_if_programmatically_set(self):
        networks = [WifiNetwork("PrivateNet", 80, "WPA2", False),
                    WifiNetwork("OpenNet", 90, "--", False)]

        def approve(dialog):
            password = self.widget(dialog, self.Gtk.Entry)
            password.set_text("first-secret")
            listbox = self.widget(dialog, self.Gtk.ListBox)
            listbox.select_row(listbox.get_row_at_index(1))
            self.assertEqual(password.get_text(), "")
            self.assertFalse(password.get_sensitive())
            password.set_text("a-stale-secret")

        with self.simulate_dialog(approve), self.immediate_worker(), patch.object(
            self.dialogs, "connect_wifi", return_value=(True, "")
        ) as connect:
            self.dialogs._present_wifi(None, networks, "", Mock(), lambda: True)
        connect.assert_called_once_with("OpenNet", None)

    def test_cancelling_wifi_never_connects(self):
        network = WifiNetwork("PrivateNet", 80, "WPA2", False)

        def cancel(dialog):
            self.widget(dialog, self.Gtk.Entry).set_text("first-secret")

        with self.simulate_dialog(cancel, self.Gtk.ResponseType.CANCEL), self.immediate_worker(), patch.object(
            self.dialogs, "connect_wifi", return_value=(True, "")
        ) as connect:
            self.dialogs._present_wifi(None, [network], "", Mock(), lambda: True)
        connect.assert_not_called()

    def test_install_confirmation_and_failure_name_all_packages(self):
        command = Command(["apt-get", "install", "-y", "sane-utils", "sane-airscan"], privileged=True)
        reports = []

        def approve(dialog):
            self.assertEqual(dialog.get_title(), "Install sane-utils, sane-airscan")
            self.assertEqual(self.printer_preview(dialog), "$ " + command.display())

        with self.simulate_dialog(approve), self.immediate_worker(), patch.object(
            self.dialogs.OfflineAssistant, "run_command", return_value=(False, "")
        ) as run:
            self.dialogs._confirm_install(None, [command], reports.append, lambda: True)
        run.assert_called_once_with(command)
        self.assertIn("Could not install sane-utils, sane-airscan.", reports[0])


if __name__ == "__main__":
    unittest.main()
