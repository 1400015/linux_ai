"""GTK image review proves that consent and preview precede any request."""

import io
import unittest
from unittest.mock import patch

from PIL import Image

from src.image_attachments import ImageAttachmentError, prepare_image_bytes
from src.i18n import set_language


class ImageReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable; run with xvfb-run')
            from src import image_dialog
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.Gtk = Gtk
        cls.image_dialog = image_dialog

    def setUp(self):
        set_language('en')
        output = io.BytesIO()
        with Image.new('RGB', (8, 4), 'red') as image:
            image.save(output, format='PNG')
        self.attachment = prepare_image_bytes(output.getvalue(), '/private/reviewed.png')

    def test_confirmation_includes_preview_destination_and_next_request_only(self):
        dialogs = []
        original_dialog = self.Gtk.Dialog
        def created(*args, **kwargs):
            dialog = original_dialog(*args, **kwargs)
            dialogs.append(dialog)
            return dialog
        inspected = []
        def inspect_dialog():
            children = dialogs[-1].get_content_area().get_children()
            labels = '\n'.join(child.get_text() for child in children if isinstance(child, self.Gtk.Label))
            inspected.append(labels)
            self.assertTrue(any(isinstance(child, self.Gtk.Image) for child in children))
            return self.Gtk.ResponseType.OK
        with patch('src.image_dialog.Gtk.Dialog', side_effect=created), \
                patch.object(original_dialog, 'run', side_effect=inspect_dialog):
            approved = self.image_dialog.review_image(None, self.attachment, 'openrouter', 'openai/gpt-4o-mini')
        self.assertTrue(approved)
        self.assertIn('Destination: OpenRouter', inspected[0])
        self.assertIn('Model: openai/gpt-4o-mini', inspected[0])
        self.assertIn('Only the next request', inspected[0])
        self.assertIn('not saved in conversation history', inspected[0])
        self.assertIn('visible secrets have not', inspected[0])
        self.assertNotIn('/private', inspected[0])

    def test_cancel_does_not_attach_and_unsupported_model_has_no_review(self):
        with patch.object(self.Gtk.Dialog, 'run', return_value=self.Gtk.ResponseType.CANCEL):
            self.assertFalse(self.image_dialog.review_image(None, self.attachment, 'openrouter', 'openai/gpt-4o-mini'))
        with patch('src.image_dialog.Gtk.Dialog') as dialog:
            with self.assertRaises(ImageAttachmentError):
                self.image_dialog.review_image(None, self.attachment, 'openrouter', 'text-only-unknown')
            dialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()
