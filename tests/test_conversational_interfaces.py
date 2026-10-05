"""Local task results bypass providers and retain their owning conversation."""

import io
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.cli import CLIApp
from src.conversation_actions import ActionReply
from src.i18n import set_language


class TestConversationalCLI(unittest.TestCase):
    def setUp(self):
        set_language('en')

    def app(self, reply):
        app = CLIApp.__new__(CLIApp)
        app.actions = SimpleNamespace(handle=Mock(return_value=reply))
        app._store = Mock(return_value=SimpleNamespace(active_session_id='original'))
        app._prepare_conversation = Mock()
        app._message_from_args = Mock(return_value='install nano')
        app._record_exchange = Mock()
        app._save_history = Mock()
        app.ai_client = Mock()
        args = SimpleNamespace(expert=False, no_history=False, stdin=False, input=None)
        return app, args

    def test_local_result_bypasses_provider_and_records_actual_failure(self):
        app, args = self.app(ActionReply('Installation failed: permission denied', 'failed'))
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout', io.StringIO()):
            self.assertEqual(app.handle_chat(args), 1)
        app.ai_client.provider_ready.assert_not_called()
        app.ai_client.chat.assert_not_called()
        app._record_exchange.assert_called_once_with('install nano', 'Installation failed: permission denied')
        app._save_history.assert_called_once()
        self.assertTrue(app.actions.handle.call_args.kwargs['can_execute'])
        self.assertEqual(app.actions.handle.call_args.args[1], 'original')

    def test_piped_and_file_input_cannot_authorize_mutation(self):
        for terminal, stdin, filename in ((False, False, None), (True, True, None), (True, False, 'input.txt')):
            app, args = self.app(ActionReply('Proposal', 'proposal'))
            args.stdin, args.input = stdin, filename
            with patch('sys.stdin.isatty', return_value=terminal), patch('sys.stdout', io.StringIO()):
                self.assertEqual(app.handle_chat(args), 0)
            self.assertFalse(app.actions.handle.call_args.kwargs['can_execute'])

    def test_no_history_disables_choice_persistence_and_exchange(self):
        app, args = self.app(ActionReply('Available packages', 'needs_choice'))
        args.no_history = True
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(app.handle_chat(args), 0)
        self.assertFalse(app.actions.handle.call_args.kwargs['persist_choices'])
        app._record_exchange.assert_not_called()
        app._save_history.assert_not_called()

    def display(self):
        return SimpleNamespace(remaining_seconds=13.5, confirm=Mock(return_value=(True, 'Kept')),
                               revert=Mock(return_value=(True, 'Reverted')))

    def test_terminal_keeps_only_an_explicit_answer_before_deadline(self):
        change = self.display()
        app, args = self.app(ActionReply('Temporary mode', 'needs_keep', change=change))
        with patch('sys.stdin', io.StringIO('yes\n')), patch('sys.stdout', io.StringIO()), \
                patch('src.cli.select.select', return_value=([object()], [], [])) as wait:
            self.assertEqual(app.handle_chat(args), 0)
        self.assertEqual(wait.call_args.args[3], 13.5)
        change.confirm.assert_called_once()
        change.revert.assert_not_called()
        self.assertIn('Kept', app._record_exchange.call_args.args[1])

    def test_terminal_timeout_and_read_error_revert(self):
        for result in (None, OSError('lost terminal')):
            change = self.display()
            app, args = self.app(ActionReply('Temporary mode', 'needs_keep', change=change))
            with patch('sys.stdout', io.StringIO()), patch('src.cli.select.select',
                    side_effect=result, return_value=([], [], [])):
                self.assertEqual(app.handle_chat(args), 0)
            change.revert.assert_called_once()
            change.confirm.assert_not_called()

    def test_failed_restore_reaches_shell_and_history(self):
        change = self.display()
        change.revert.return_value = (False, 'Restore verification failed')
        app, args = self.app(ActionReply('Temporary mode', 'needs_keep', change=change))
        with patch('sys.stdout', io.StringIO()), patch('src.cli.select.select', return_value=([], [], [])):
            self.assertEqual(app.handle_chat(args), 1)
        self.assertIn('Restore verification failed', app._record_exchange.call_args.args[1])


class TestConversationalGTK(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable')
            from src import main_window
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest('GTK3 unavailable: ' + str(error))
        cls.module, cls.Gtk = main_window, Gtk

    def window(self):
        return SimpleNamespace(
            _active_request=3, _cancel_event=threading.Event(),
            offline=Mock(), ai_client=Mock(), actions=SimpleNamespace(handle=Mock()),
            chat_view=Mock(), _add_ai_message=Mock(), _remember=Mock(),
            _on_message_processed=Mock(), _confirm_display_change=Mock(),
            _finish_display_change=Mock(), _record_offline_result=Mock(),
            _add_loading_message=Mock(), _complete_action_reply=Mock(),
        )

    def test_local_action_bypasses_stream_and_offline_offer_parser(self):
        window = self.window()
        reply = ActionReply('Repository description includes ```file:/etc/example```')
        window.actions.handle.return_value = reply
        queued = []
        with patch.object(self.module.GLib, 'idle_add', side_effect=lambda *args: queued.append(args)):
            self.module.MainWindow._process_message(window, 'install nano', [], 3,
                                                   window._cancel_event, session_id='owning-session')
        window.ai_client.provider_ready.assert_not_called()
        window.ai_client.stream_chat.assert_not_called()
        window.offline.handle.assert_not_called()
        self.assertEqual(queued[-1], (window._complete_action_reply, 3, window._cancel_event, reply, 'owning-session'))
        window._active_request = 4
        self.assertFalse(window.actions.handle.call_args.kwargs['is_current']())

    def test_local_completion_records_once_without_model_file_actions(self):
        window = self.window()
        reply = ActionReply('Available modes', 'needs_choice')
        with patch.object(self.module.file_actions, 'offer_file_blocks_async') as files:
            self.module.MainWindow._complete_action_reply(window, 3, window._cancel_event, reply, 'owning-session')
        window._remember.assert_called_once_with('assistant', reply.text)
        window._add_ai_message.assert_called_once_with(reply.text, False)
        window._on_message_processed.assert_called_once_with(3)
        files.assert_not_called()

    def test_stale_completed_mutation_retains_original_owner_and_rolls_back(self):
        window = self.window()
        window._active_request = 4
        change = Mock()
        reply = ActionReply('Applied temporarily', change=change, executed=True)
        self.module.MainWindow._complete_action_reply(window, 3, window._cancel_event, reply, 'owning-session')
        window._record_offline_result.assert_called_once_with(3, reply.text, 'owning-session')
        window._finish_display_change.assert_called_once_with(change, False, 3, 'owning-session')
        window._add_ai_message.assert_not_called()

    def test_real_dialog_confirms_or_reverts_without_running_hardware(self):
        for response in (self.Gtk.ResponseType.OK, self.Gtk.ResponseType.CANCEL):
            window = self.Gtk.Window()
            window._active_request = 3
            window._finish_display_change = Mock()
            change = SimpleNamespace(status='pending', remaining_seconds=10.0)
            try:
                with patch.object(self.Gtk.Dialog, 'run', return_value=response):
                    self.module.MainWindow._confirm_display_change(window, change, 3, threading.Event(), 'owner')
                window._finish_display_change.assert_called_once_with(change, response == self.Gtk.ResponseType.OK, 3, 'owner')
            finally:
                window.destroy()

    def test_canceled_request_never_opens_keep_dialog(self):
        window = self.Gtk.Window()
        window._active_request = 4
        window._finish_display_change = Mock()
        try:
            with patch.object(self.Gtk.Dialog, 'run') as run:
                self.module.MainWindow._confirm_display_change(
                    window, SimpleNamespace(status='pending', remaining_seconds=10), 3, threading.Event(), 'owner')
            run.assert_not_called()
            self.assertFalse(window._finish_display_change.call_args.args[1])
        finally:
            window.destroy()

    def test_window_shutdown_revokes_requests_before_closing_actions_and_history(self):
        event = threading.Event()
        order = []
        window = SimpleNamespace(_cancel_event=event,
                                 actions=SimpleNamespace(close=lambda: order.append(('actions', event.is_set()))),
                                 history_store=SimpleNamespace(close=lambda timeout: order.append(('history', event.is_set())) or True))
        self.module.MainWindow.close_history_writer(window)
        self.assertEqual(order, [('actions', True), ('history', True)])

    def test_audit_tree_is_expandable_and_keeps_the_original_session(self):
        window = self.Gtk.Window()
        window.history_store = SimpleNamespace(active_session_id='owner')
        event = dict(operation_id='a' * 32, action_id='packages.install', resource='nano=1',
                     phase='verified', detail='<literal backend detail>')
        audit = SimpleNamespace(events=Mock(return_value=[event]))
        window.actions = SimpleNamespace(audit=audit)
        pending = []
        queued = []
        dialogs = []
        def run(dialog):
            dialogs.append(dialog)
            return self.Gtk.ResponseType.CLOSE
        try:
            with patch.object(self.module.threading, 'Thread', side_effect=lambda target, daemon: SimpleNamespace(start=lambda: pending.append(target))), \
                    patch.object(self.module.GLib, 'idle_add', side_effect=lambda callback, *args: queued.append((callback, args))), \
                    patch.object(self.Gtk.Dialog, 'run', run), patch.object(self.Gtk.Dialog, 'destroy'):
                self.module.MainWindow._show_action_audit(window)
                window.history_store.active_session_id = 'other'
                pending[0]()
                queued[0][0](*queued[0][1])
                dialog = dialogs[0]
                audit.events.assert_called_once_with('owner')
                view = dialog.get_content_area().get_children()[0].get_child()
                model = view.get_model()
                parent = model.get_iter_first()
                child = model.iter_children(parent)
                self.assertEqual(model.get_value(parent, 0), 'packages.install')
                self.assertEqual(model.get_value(parent, 2), 'verified')
                self.assertEqual(model.get_value(child, 1), '<literal backend detail>')
                self.assertEqual(len(view.get_columns()), 4)
        finally:
            for dialog in dialogs:
                dialog.destroy()
            window.destroy()

    def test_late_audit_result_does_not_reopen_a_closed_window(self):
        window = SimpleNamespace(_audit_closed=True,
            history_store=SimpleNamespace(active_session_id='owner'),
            actions=SimpleNamespace(audit=SimpleNamespace(events=Mock(return_value=[]))))
        with patch.object(self.module.threading, 'Thread', side_effect=lambda target, daemon: SimpleNamespace(start=target)), \
                patch.object(self.module.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)) as idle:
            self.module.MainWindow._show_action_audit(window)
        self.assertEqual(idle.call_count, 1)
        self.assertIs(idle.call_args.args[0]([], ''), False)
