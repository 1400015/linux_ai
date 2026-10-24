"""The document-context review is the last gate before excerpts leave the box.

`review_document_context()` was only ever reached from the GUI; these tests
drive the real dialog and pin the two things that matter: the user sees the
exact text that will be sent, and declining it does not send anything.
"""

import unittest
from unittest.mock import patch

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False

if GTK_AVAILABLE:
    from src.document_review import review_document_context
    from src.i18n import get_language, set_language


def collect(widget, kind):
    """Depth-first list of `kind` widgets inside `widget`."""
    found = []

    def walk(child):
        if isinstance(child, kind):
            found.append(child)
        if isinstance(child, Gtk.Container):
            child.forall(walk)

    walk(widget)
    return found


@unittest.skipUnless(GTK_AVAILABLE, 'GTK display unavailable')
class ReviewDocumentContextTests(unittest.TestCase):
    def setUp(self):
        self.original_language = get_language()
        self.addCleanup(set_language, self.original_language)
        set_language('en')

    def review(self, context, provider, response):
        """Run the dialog with `response` and return the strings it displayed."""
        shown = {}

        def fake_run(dialog, *_args, **_kwargs):
            labels = collect(dialog, Gtk.Label)
            views = collect(dialog, Gtk.TextView)
            shown['label'] = labels[0].get_text() if labels else ''
            buffer = views[0].get_buffer()
            shown['text'] = buffer.get_text(buffer.get_start_iter(),
                                            buffer.get_end_iter(), False)
            return response

        with patch.object(Gtk.Dialog, 'run', autospec=True, side_effect=fake_run):
            accepted = review_document_context(None, context, provider)
        shown['accepted'] = accepted
        return shown

    def test_accepting_shows_the_exact_excerpts_and_the_destination(self):
        context = ("--- documents/guide.md p.3 ---\nThe daemon listens on port 5432.\n"
                   "--- notes.txt p.1 ---\nRestart with systemctl restart app.")
        shown = self.review(context, 'openrouter', Gtk.ResponseType.OK)
        self.assertTrue(shown['accepted'])
        self.assertIn('openrouter', shown['label'])
        self.assertIn('excerpts will be sent', shown['label'])
        self.assertEqual(shown['text'], context)

    def test_declining_returns_false_without_changing_the_message(self):
        shown = self.review('--- notes.txt p.1 ---\nsecret excerpt',
                            'google_ai_studio', Gtk.ResponseType.CANCEL)
        self.assertFalse(shown['accepted'])
        # The text was still displayed, so the user declined an informed choice.
        self.assertIn('secret excerpt', shown['text'])

    def test_the_dialog_is_destroyed_after_the_decision(self):
        with patch.object(Gtk.Dialog, 'run', autospec=True,
                          return_value=Gtk.ResponseType.OK), \
                patch.object(Gtk.Dialog, 'destroy', autospec=True) as destroy:
            review_document_context(None, 'context', 'openrouter')
        destroy.assert_called_once()

    def test_a_failing_dialog_still_releases_the_widget(self):
        # The `finally` is what stops a run() failure from leaking a modal.
        with patch.object(Gtk.Dialog, 'run', autospec=True,
                          side_effect=RuntimeError('nested loop failed')), \
                patch.object(Gtk.Dialog, 'destroy', autospec=True) as destroy:
            with self.assertRaises(RuntimeError):
                review_document_context(None, 'context', 'openrouter')
        destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
