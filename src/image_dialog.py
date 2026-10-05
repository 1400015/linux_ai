"""Explicit local preview before attaching an image to the next AI request."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gtk

from .i18n import _
from .image_attachments import prepare_image, supports_image_input, validate_attachment, ImageAttachmentError


def pick_image(parent):
    chooser = Gtk.FileChooserDialog(title=_("Choose an image"), transient_for=parent,
                                   action=Gtk.FileChooserAction.OPEN,
                                   buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                            Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
    image_filter = Gtk.FileFilter()
    image_filter.set_name(_("PNG, JPEG and WebP images"))
    for mime in ("image/png", "image/jpeg", "image/webp"):
        image_filter.add_mime_type(mime)
    chooser.add_filter(image_filter)
    try:
        if chooser.run() != Gtk.ResponseType.OK:
            return None
        path = chooser.get_filename()
    finally:
        chooser.destroy()
    return prepare_image(path)


def review_image(parent, attachment, provider, model):
    """Confirm the exact snapshot and destination; nothing is sent here."""
    validate_attachment(attachment)
    if not supports_image_input(provider, model):
        raise ImageAttachmentError("Choose a supported OpenRouter image model before reviewing an image")
    dialog = Gtk.Dialog(title=_("Review image for AI"), transient_for=parent, modal=True,
                        buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                 _("Attach to next request"), Gtk.ResponseType.OK))
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.set_default_size(640, 550)
    box = dialog.get_content_area()
    box.set_border_width(12)
    box.set_spacing(10)
    detail = Gtk.Label(xalign=0, wrap=True)
    detail.set_text("{} — {} × {}\n{}: OpenRouter\n{}: {}".format(
        attachment.filename, attachment.width, attachment.height,
        _("Destination"), _("Model"), model))
    box.pack_start(detail, False, False, 0)
    loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
    loader.write(attachment.data)
    loader.close()
    pixbuf = loader.get_pixbuf()
    ratio = min(1.0, 600 / pixbuf.get_width(), 380 / pixbuf.get_height())
    if ratio < 1:
        pixbuf = pixbuf.scale_simple(max(1, int(pixbuf.get_width() * ratio)),
                                    max(1, int(pixbuf.get_height() * ratio)),
                                    GdkPixbuf.InterpType.BILINEAR)
    box.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), True, True, 0)
    notice = Gtk.Label(xalign=0, wrap=True)
    notice.set_text(_("Only the next request will include this image. OpenRouter and the selected model's "
                      "provider will receive its pixels. Check for private information before attaching. "
                      "Metadata has been removed; visible secrets have not. The image is not saved in conversation history."))
    box.pack_start(notice, False, False, 0)
    try:
        dialog.show_all()
        return dialog.run() == Gtk.ResponseType.OK
    finally:
        dialog.destroy()
