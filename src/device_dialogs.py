"""Choosers for Wi-Fi, printers and scanners.

Discovery runs off the GTK main thread. The dialog is the confirmation:
nothing is changed before the action button. A Wi-Fi password is cleared
from the entry and is not included in the text reported back to the chat.
"""

import logging
import shlex
import threading

from gi.repository import GLib, Gtk

from .device_actions import (
    collect_printers,
    collect_scanners,
    collect_wifi,
    connect_wifi,
    printer_add_argv,
    queue_name_for,
    wifi_needs_password,
)
from .i18n import _
from .offline_assistant import OfflineAssistant, PKG_MANAGERS

logger = logging.getLogger(__name__)


def start(parent, kind, commands, report, still_current):
    """Collect devices, then open the chooser on the main thread."""

    def work():
        if not still_current():
            return
        if kind == "wifi":
            items, error = collect_wifi()
        elif kind == "printer":
            items, error = collect_printers()
        elif kind == "scanner":
            items, error = collect_scanners()
        else:
            return
        GLib.idle_add(_present, parent, kind, items, error, commands, report, still_current)

    threading.Thread(target=work, daemon=True).start()


def _present(parent, kind, items, error, commands, report, still_current):
    if not still_current():
        return False
    if kind == "wifi":
        _present_wifi(parent, items, error, report, still_current)
    elif kind == "printer":
        _present_printers(parent, items, error, commands, report, still_current)
    elif kind == "scanner":
        _present_scanners(parent, items, error, commands, report, still_current)
    return False


def _scrolled_list(rows):
    scroll = Gtk.ScrolledWindow()
    scroll.set_min_content_height(180)
    scroll.set_min_content_width(460)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
    for label, payload in rows:
        row = Gtk.ListBoxRow()
        row.payload = payload
        text = Gtk.Label(label=label)
        text.set_halign(Gtk.Align.START)
        text.set_margin_start(8)
        text.set_margin_end(8)
        text.set_margin_top(4)
        text.set_margin_bottom(4)
        row.add(text)
        listbox.add(row)
    if rows:
        listbox.select_row(listbox.get_row_at_index(0))
    scroll.add(listbox)
    return scroll, listbox


def _dialog(parent, title, action):
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    if action:
        dialog.add_button(action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    area = dialog.get_content_area()
    area.set_border_width(8)
    area.set_spacing(6)
    return dialog, area


def _selected(listbox):
    row = listbox.get_selected_row()
    if row is None:
        return None
    return getattr(row, "payload", None)


def _present_wifi(parent, networks, error, report, still_current):
    if not networks:
        report(error or _("No Wi-Fi networks found."))
        return
    dialog, area = _dialog(parent, _("Available Wi-Fi networks"), _("Connect"))
    note = Gtk.Label(label=_(
        "The password is sent only to NetworkManager and is not written in the chat."
    ))
    note.set_line_wrap(True)
    note.set_halign(Gtk.Align.START)
    area.pack_start(note, False, False, 0)
    rows = []
    for network in networks:
        security = network.security or "--"
        label = f"{network.ssid}   {network.signal}%   {security}"
        if network.active:
            label += "   (" + _("In use") + ")"
        rows.append((label, network))
    _scroll, listbox = _scrolled_list(rows)
    area.pack_start(_scroll, True, True, 0)
    password = Gtk.Entry()
    password.set_visibility(False)
    password.set_placeholder_text(_("Password"))
    area.pack_start(password, False, False, 0)
    selected_ssid = None

    def refresh(*_args):
        nonlocal selected_ssid
        network = _selected(listbox)
        ssid = network.ssid if network else None
        needs_password = bool(network and wifi_needs_password(network))
        if ssid != selected_ssid or not needs_password:
            password.set_text("")
        selected_ssid = ssid
        password.set_sensitive(needs_password)

    listbox.connect("row-selected", refresh)
    refresh()
    dialog.show_all()
    response = dialog.run()
    network = _selected(listbox)
    secret = password.get_text() if network and wifi_needs_password(network) else ""
    password.set_text("")
    dialog.destroy()
    if response != Gtk.ResponseType.OK or network is None or not still_current():
        return
    if wifi_needs_password(network) and not secret:
        report(_("Could not connect to {ssid}.").format(ssid=network.ssid))
        return

    held = [secret]
    secret = ""

    def work():
        try:
            ok, output = connect_wifi(network.ssid, held[0] or None)
        finally:
            held[0] = ""
        if ok:
            text = _("Connected to {ssid}.").format(ssid=network.ssid)
        else:
            detail = output or _("Could not connect to {ssid}.").format(ssid=network.ssid)
            text = _("Could not connect to {ssid}.").format(ssid=network.ssid)
            if detail and detail != text:
                text = text + "\n" + detail
        report(text)

    threading.Thread(target=work, daemon=True).start()


def _present_printers(parent, devices, error, commands, report, still_current):
    if not devices:
        if commands:
            _confirm_install(parent, commands, report, still_current)
            return
        report(error or _("No printers found."))
        return
    dialog, area = _dialog(parent, _("Add printer"), _("Add printer"))
    rows = []
    for device in devices:
        mark = "" if device.driverless else "   (" + _(
            "This device needs a driver. I will not add it automatically."
        ) + ")"
        rows.append((device.uri + mark, device))
    _scroll, listbox = _scrolled_list(rows)
    area.pack_start(_scroll, True, True, 0)
    queue = Gtk.Entry()
    area.pack_start(Gtk.Label(label=_("Queue name"), halign=Gtk.Align.START), False, False, 0)
    area.pack_start(queue, False, False, 0)
    preview = Gtk.Label(label="", selectable=True, halign=Gtk.Align.START)
    preview.set_line_wrap(True)
    area.pack_start(preview, False, False, 0)
    preview_argv = None

    def refresh_preview(*_args):
        nonlocal preview_argv
        preview_argv = None
        device = _selected(listbox)
        if device is None:
            preview.set_text("")
            dialog.set_response_sensitive(Gtk.ResponseType.OK, False)
            return
        try:
            argv = printer_add_argv(queue.get_text().strip(), device.uri)
            preview.set_text("$ " + shlex.join(argv))
            preview_argv = argv
        except ValueError as exc:
            message = _(
                "This device needs a driver. I will not add it automatically."
            ) if not device.driverless else str(exc)
            preview.set_text(message)
        dialog.set_response_sensitive(Gtk.ResponseType.OK, preview_argv is not None)

    def refresh_selection(*_args):
        device = _selected(listbox)
        if device is not None:
            queue.set_text(queue_name_for(device.uri))
        refresh_preview()

    queue.connect("changed", refresh_preview)
    listbox.connect("row-selected", refresh_selection)
    refresh_selection()
    dialog.show_all()
    response = dialog.run()
    device = _selected(listbox)
    # Execute the validated argv shown when the user approved the dialog.
    argv = list(preview_argv) if preview_argv is not None else None
    dialog.destroy()
    if (response != Gtk.ResponseType.OK or device is None or argv is None
            or not still_current()):
        return
    name = argv[2]

    def work():
        from .offline_assistant import Command
        ok, output = OfflineAssistant.run_privileged(
            Command(argv=argv, privileged=True, description=f"Add printer {name}"),
            timeout=60,
        )
        if ok:
            text = _("Printer {name} added.").format(name=name)
        else:
            text = _("Could not add printer {name}.").format(name=name)
            if output:
                text = text + "\n" + output
        report(text)

    threading.Thread(target=work, daemon=True).start()


def _present_scanners(parent, devices, error, commands, report, still_current):
    if not devices:
        if commands:
            _confirm_install(parent, commands, report, still_current)
            return
        report(error or _("No scanners found."))
        return
    lines = "\n".join(f"{item.device} — {item.description}" for item in devices)
    report(lines)


def _confirm_install(parent, commands, report, still_current):
    """Reuse the visible argv as the confirmation for a support package."""
    if not still_current():
        return

    def package_names(command):
        for manager in PKG_MANAGERS.values():
            prefix = manager["install"][:-1]
            if command.argv[:len(prefix)] == prefix:
                return ", ".join(command.argv[len(prefix):])
        return command.argv[-1]

    package = ", ".join(package_names(command) for command in commands)
    title = _("Install {pkg}").format(pkg=package)
    dialog, area = _dialog(parent, title, title)
    for command in commands:
        label = Gtk.Label(label="$ " + command.display(), selectable=True, halign=Gtk.Align.START)
        label.set_line_wrap(True)
        area.pack_start(label, False, False, 0)
    dialog.show_all()
    response = dialog.run()
    dialog.destroy()
    if response != Gtk.ResponseType.OK or not still_current():
        return

    def work():
        chunks = []
        for command in commands:
            ok, output = OfflineAssistant.run_command(command)
            chunks.append("$ " + command.display())
            if output:
                detail = output
            elif ok:
                detail = _("Done.")
            else:
                detail = _("Could not install {pkg}.").format(pkg=package_names(command))
            chunks.append(detail)
            if not ok:
                break
        report("\n".join(chunks))

    threading.Thread(target=work, daemon=True).start()
