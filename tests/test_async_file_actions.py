"""GTK stays responsive without weakening file consent or recovery outcomes."""

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src import file_actions
from src.change_journal import ChangeJournal, file_digest
from src.i18n import set_language

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    from src.change_dialog import ChangeDialog
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK is unavailable')
class AsyncFileFixtures(unittest.TestCase):
    def setUp(self):
        set_language('en')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / 'target'
        self.target.write_text('old\n', encoding='utf-8')
        self.journal = ChangeJournal(self.root / 'changes.json')
        self.window = Gtk.Window()
        self.addCleanup(self.window.destroy)
        self.window.change_journal = self.journal
        self.window.history_store = SimpleNamespace(active_session_id='original')
        self.window.config = SimpleNamespace(get=lambda key, default=None: [str(self.root)])
        self.window._record_file_action_result = Mock()
        privilege = patch.object(file_actions, 'is_privileged_path', return_value=False)
        privilege.start()
        self.addCleanup(privilege.stop)
        self.callbacks = []
        self.progress = []
        self.main_thread = threading.get_ident()
        self.current = True

    def pump_until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if predicate():
                return
            time.sleep(0.002)
        self.fail('The asynchronous file operation did not finish')

    def notify(self, status, message):
        (self.progress if status == 'writing' else self.callbacks).append(
            (status, message, threading.get_ident()))

    def offer(self, reply=None):
        return file_actions.offer_file_blocks_async(
            self.window, reply or '```{}\nnew\n```'.format(self.target), self.notify,
            [str(self.root)], self.journal, 'original', lambda: self.current)

    def pause(self, actual):
        entered, release = threading.Event(), threading.Event()
        done = threading.Event()

        def release_and_wait():
            # A failed assertion must not remove the fixture underneath a
            # still-approved worker or leave a delayed review dialog behind.
            self.window.destroy()
            release.set()
            if entered.is_set():
                self.assertTrue(done.wait(4), 'File worker survived fixture cleanup')
        self.addCleanup(release_and_wait)

        def blocked(*args, **kwargs):
            entered.set()
            try:
                if not release.wait(4):
                    raise TimeoutError('Test did not release the file operation')
                return actual(*args, **kwargs)
            finally:
                done.set()

        return entered, release, blocked

    def assert_gtk_timer_runs(self):
        fired = []
        GLib.timeout_add(5, lambda: fired.append(threading.get_ident()) and False)
        self.pump_until(lambda: bool(fired))
        self.assertEqual(fired, [self.main_thread])

    def assert_no_write(self):
        self.assertEqual(self.target.read_text(), 'old\n')
        self.assertEqual(self.journal.list_changes(), [])


class TestAsyncFileOffers(AsyncFileFixtures):
    def test_blocked_preview_keeps_gtk_live_and_requires_consent(self):
        entered, release, preview = self.pause(file_actions.preview_diff)
        with patch.object(file_actions, 'preview_diff', side_effect=preview), \
                patch.object(file_actions, '_show_write_confirmation', return_value=Gtk.ResponseType.CANCEL) as confirm:
            controller = self.offer()
            self.pump_until(entered.is_set)
            self.assertFalse(controller.active)
            self.assertEqual(self.progress, [])
            self.assert_gtk_timer_runs()
            confirm.assert_not_called()
            self.assert_no_write()
            release.set()
            self.pump_until(lambda: controller.finished)
        self.assert_no_write()

    def test_approved_write_is_recorded_for_the_original_conversation(self):
        def approve(parent, path, content, is_new):
            self.assertEqual(threading.get_ident(), self.main_thread)
            self.assertEqual(path, str(self.target))
            self.assertIn('-old', content)
            self.assertIn('+new', content)
            self.assertFalse(is_new)
            self.assert_no_write()
            return Gtk.ResponseType.OK

        with patch.object(file_actions, '_show_write_confirmation', side_effect=approve):
            controller = self.offer()
            self.pump_until(lambda: controller.finished)
        self.assertEqual(self.target.read_text(), 'new\n')
        self.assertEqual(self.journal.list_changes()[0]['session_id'], 'original')
        self.assertEqual(self.callbacks, [('written', 'File written: ' + str(self.target), self.main_thread)])

    def test_file_changed_after_preview_is_not_overwritten(self):
        def approve(*args):
            self.target.write_text('changed independently\n')
            return Gtk.ResponseType.OK

        with patch.object(file_actions, '_show_write_confirmation', side_effect=approve):
            controller = self.offer()
            self.pump_until(lambda: controller.finished)
        self.assertEqual(self.target.read_text(), 'changed independently\n')
        self.assertEqual(self.journal.list_changes(), [])
        self.assertEqual(self.callbacks[0][0], 'error')
        self.assertIn('changed', self.callbacks[0][1].lower())

    def test_policy_change_after_preview_refuses_the_write(self):
        def approve(*args):
            policy.return_value = False
            return Gtk.ResponseType.OK

        with patch.object(file_actions, 'is_allowed_path', return_value=True) as policy, \
                patch.object(file_actions, '_show_write_confirmation', side_effect=approve):
            controller = self.offer()
            self.pump_until(lambda: controller.finished)
        self.assert_no_write()
        self.assertEqual(self.callbacks[0][0], 'error')

    def test_parent_replaced_after_preview_refuses_the_same_content_target(self):
        destination = self.root / 'destination'
        destination.mkdir()
        self.target = destination / 'target'
        self.target.write_text('old\n')
        retired = self.root / 'retired-destination'

        def approve(*args):
            destination.rename(retired)
            destination.mkdir()
            self.target.write_text('old\n')
            return Gtk.ResponseType.OK

        with patch.object(file_actions, '_show_write_confirmation', side_effect=approve):
            controller = self.offer()
            self.pump_until(lambda: controller.finished)
        self.assertEqual(self.target.read_text(), 'old\n')
        self.assertEqual((retired / 'target').read_text(), 'old\n')
        self.assertEqual(self.callbacks[0][0], 'error')
        self.assertNotIn('applied', [item['status'] for item in self.journal.list_changes()])

    def test_session_change_while_preparing_suppresses_confirmation_and_write(self):
        entered, release, preview = self.pause(file_actions.preview_diff)
        with patch.object(file_actions, 'preview_diff', side_effect=preview), \
                patch.object(file_actions, '_show_write_confirmation', return_value=Gtk.ResponseType.OK) as confirm:
            controller = self.offer()
            self.pump_until(entered.is_set)
            self.current = False
            release.set()
            self.pump_until(lambda: controller.finished)
        confirm.assert_not_called()
        self.assert_no_write()

    def test_session_change_during_confirmation_stops_this_and_later_offers(self):
        second = self.root / 'second'

        def approve(*args):
            self.current = False
            return Gtk.ResponseType.OK

        reply = '```{}\nnew\n```\n```{}\nother\n```'.format(self.target, second)
        with patch.object(file_actions, '_show_write_confirmation', side_effect=approve) as confirm:
            controller = self.offer(reply)
            self.pump_until(lambda: controller.finished)
        confirm.assert_called_once()
        self.assert_no_write()
        self.assertFalse(second.exists())

    def test_cancelled_confirmation_stops_later_file_offers(self):
        second = self.root / 'second'
        reply = '```{}\nnew\n```\n```{}\nother\n```'.format(self.target, second)
        with patch.object(file_actions, '_show_write_confirmation', return_value=Gtk.ResponseType.CANCEL) as confirm:
            controller = self.offer(reply)
            self.pump_until(lambda: controller.finished)
        confirm.assert_called_once()
        self.assert_no_write()
        self.assertFalse(second.exists())

    def test_started_write_keeps_gtk_live_and_reports_its_outcome_after_cancel(self):
        entered, release, apply = self.pause(self.journal.apply)
        with patch.object(self.journal, 'apply', side_effect=apply), \
                patch.object(file_actions, '_show_write_confirmation', return_value=Gtk.ResponseType.OK):
            controller = self.offer()
            self.pump_until(entered.is_set)
            self.assertTrue(controller.active)
            self.assertEqual(self.progress, [('writing', 'Writing approved file…', self.main_thread)])
            self.assert_gtk_timer_runs()
            self.assertEqual(self.target.read_text(), 'old\n')
            self.current = False
            self.window.history_store.active_session_id = 'different'
            release.set()
            self.pump_until(lambda: controller.finished)
        self.assertEqual(self.target.read_text(), 'new\n')
        self.assertEqual(self.journal.list_changes()[0]['session_id'], 'original')
        self.assertEqual(self.callbacks, [('written', 'File written: ' + str(self.target), self.main_thread)])


class TestAsyncFileRecovery(AsyncFileFixtures):
    def make_change(self):
        source = self.root / 'source'
        source.write_text('new\n')
        return self.journal.apply(str(source), str(self.target), file_digest(self.target),
                                  [str(self.root)], 'original')

    def make_dialog(self):
        dialog = ChangeDialog(self.window)
        self.addCleanup(dialog.destroy)
        self.pump_until(lambda: not dialog._busy)
        return dialog

    def test_locked_journal_list_does_not_block_gtk(self):
        self.make_change()
        entered, release, listing = self.pause(self.journal.list_changes)
        with patch.object(self.journal, 'list_changes', side_effect=listing):
            dialog = ChangeDialog(self.window)
            self.addCleanup(dialog.destroy)
            self.pump_until(entered.is_set)
            self.assert_gtk_timer_runs()
            self.assertFalse(dialog.restore_button.get_sensitive())
            release.set()
            self.pump_until(lambda: not dialog._busy)
        self.assertEqual(len(dialog.records), 1)

    def test_destroyed_dialog_drops_pending_recovery_confirmation(self):
        self.make_change()
        dialog = self.make_dialog()
        entered, release, preview = self.pause(self.journal.preview_restore)
        with patch.object(self.journal, 'preview_restore', side_effect=preview), \
                patch.object(dialog, '_show_recovery_confirmation', return_value=Gtk.ResponseType.OK) as confirm, \
                patch.object(self.journal, 'restore', wraps=self.journal.restore) as restore, \
                patch.object(dialog, '_reviewed', wraps=dialog._reviewed) as reviewed:
            dialog._restore(None)
            self.pump_until(entered.is_set)
            self.assert_gtk_timer_runs()
            dialog.destroy()
            release.set()
            self.pump_until(lambda: reviewed.called)
        confirm.assert_not_called()
        restore.assert_not_called()
        self.assertEqual(self.target.read_text(), 'new\n')
        self.window._record_file_action_result.assert_not_called()

    def test_started_restore_finishes_and_reports_after_dialog_destroy(self):
        self.make_change()
        dialog = self.make_dialog()
        entered, release, restore = self.pause(self.journal.restore)
        with patch.object(self.journal, 'restore', side_effect=restore), \
                patch.object(dialog, '_show_recovery_confirmation', return_value=Gtk.ResponseType.OK):
            dialog._restore(None)
            self.pump_until(entered.is_set)
            self.assertEqual(self.window._file_recovery_active, 1)
            self.assert_gtk_timer_runs()
            self.window.history_store.active_session_id = 'different'
            dialog.destroy()
            release.set()
            self.pump_until(lambda: bool(self.window._record_file_action_result.call_args_list))
        self.assertEqual(self.target.read_text(), 'old\n')
        self.assertEqual(self.window._file_recovery_active, 0)
        args = self.window._record_file_action_result.call_args[0]
        self.assertEqual(args[:2], ('original', 'restored'))
        self.assertIn('Recovery backup', args[2])
        self.assertEqual(self.journal.list_changes()[0]['status'], 'restored')


if __name__ == '__main__':
    unittest.main()
