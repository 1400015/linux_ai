"""Review the exact reference excerpts before sending them to a model."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .i18n import _
from .document_context import display_document_context


def review_document_context(parent, context, provider):
    dialog = Gtk.Dialog(title=_("Review document excerpts"), transient_for=parent,
                        modal=True, buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                             _("Use these excerpts"), Gtk.ResponseType.OK))
    dialog.set_default_size(680, 480)
    area = dialog.get_content_area()
    area.set_border_width(12)
    area.set_spacing(10)
    label = Gtk.Label(label=_("These excerpts will be sent with your next question to {provider}.")
                      .format(provider=provider), wrap=True, xalign=0)
    area.pack_start(label, False, False, 0)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
    view.get_buffer().set_text(display_document_context(context))
    scroll.add(view)
    area.pack_start(scroll, True, True, 0)
    dialog.show_all()
    try:
        return dialog.run() == Gtk.ResponseType.OK
    finally:
        dialog.destroy()
