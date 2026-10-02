"""GTK trial boundaries: session binding, explicit consent and background I/O."""

import copy
import json
from pathlib import Path
import threading
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


class FakeRecorder:
    def __init__(self):
        self.run = None
        self.calls = []
        self.threads = []
        self.begin_gate = None

    def _called(self, name, **data):
        self.calls.append((name, data))
        self.threads.append(threading.get_ident())

    def current(self):
        self._called('current')
        return copy.deepcopy(self.run)

    def list_runs(self):
        self._called('list_runs')
        return [{'id': self.run['id'], 'title': self.run['title']}] if self.run else []

    def preview(self):
        self._called('preview')
        return json.dumps(self.run, ensure_ascii=False)

    def start(self, title, environment, **data):
        self._called('start', title=title, environment=environment, **data)
        self.run = dict(id='RUN-1', title=title, environment=environment, finished_at=None,
                        cases=[], attachments=[])
        self.run.update(data)
        return copy.deepcopy(self.run)

    def begin_case(self, case_id, session_id, **data):
        self._called('begin_case', case_id=case_id, session_id=session_id, **data)
        if self.begin_gate:
            self.begin_gate.wait(3)
        case = dict(id='CASE-1', case_id=case_id, session_id=session_id, result=None, **data)
        self.run['cases'].append(case)
        return copy.deepcopy(case)

    def end_case(self, result, **data):
        self._called('end_case', result=result, **data)
        self.run['cases'][-1].update(result=result, **data)
        return copy.deepcopy(self.run['cases'][-1])

    def finish(self):
        self._called('finish')
        self.run['finished_at'] = 0
        return copy.deepcopy(self.run)

    def export(self, path, reviewed=False):
        self._called('export', path=path, reviewed=reviewed)
        return path


class TestTrialDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import GLib, Gtk
            if not Gtk.init_check()[0]:
                raise unittest.SkipTest('GTK display unavailable')
            from src.trial_dialog import TrialDialog, show_trials
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest(str(error))
        cls.Gtk, cls.GLib, cls.TrialDialog, cls.show_trials = Gtk, GLib, TrialDialog, staticmethod(show_trials)

    def setUp(self):
        from src.i18n import set_language
        set_language('en')
        self.owner = self.Gtk.Window()
        self.recorder = FakeRecorder()
        self.owner.trial_recorder = self.recorder
        self.owner.history_store = SimpleNamespace(active_session_id='SESSION-ONE')
        self.owner.offline = SimpleNamespace(distro=None)
        self.owner.config = SimpleNamespace(get_assistance_mode=lambda: 'offline',
                                            get=lambda key, default='': default)
        self.dialog = self.show_trials(self.owner)
        self.wait()

    def tearDown(self):
        from src.i18n import set_language
        self.wait()
        self.dialog.destroy()
        self.owner.destroy()
        set_language('en')

    def wait(self):
        deadline = time.monotonic() + 4
        context = self.GLib.MainContext.default()
        while self.dialog.busy and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)
        self.assertFalse(self.dialog.busy, 'Background operation did not complete')

    def start(self):
        self.dialog.title.set_text('Synthetic trial')
        self.dialog.environment.set_text('ENV-01')
        self.dialog._start()
        self.wait()

    def begin(self):
        self.dialog.case_id.set_text('SAFE-01')
        self.dialog._begin()
        self.wait()

    def test_panel_is_nonmodal_and_reopening_reuses_existing_panel(self):
        self.assertFalse(self.dialog.get_modal())
        self.assertIs(self.show_trials(self.owner), self.dialog)
        self.assertFalse(any(name in ('start', 'begin_case', 'export') for name, data in self.recorder.calls))

    def test_case_freezes_conversation_at_click_and_collects_no_logs_by_default(self):
        self.start()
        self.recorder.begin_gate = threading.Event()
        self.dialog.case_id.set_text('SAFE-01')
        self.dialog._begin()
        self.owner.history_store.active_session_id = 'SESSION-TWO'
        self.recorder.begin_gate.set()
        self.wait()
        self.assertEqual(self.recorder.run['cases'][0]['session_id'], 'SESSION-ONE')
        self.assertIn('SESSION-ONE', self.dialog.state.get_text())
        self.dialog._end()
        self.wait()
        end = [data for name, data in self.recorder.calls if name == 'end_case'][-1]
        self.assertFalse(end['collect_logs'])
        self.assertIsNone(end['operation_id'])

    def test_end_case_captures_explicit_log_consent_notes_and_operation(self):
        self.start()
        self.begin()
        self.dialog.notes.get_buffer().set_text('Synthetic observed failure')
        self.dialog.operation.set_text('OPERATION-ONE')
        self.dialog.result.set_active_id('FAIL')
        self.dialog.collect_logs.set_active(True)
        self.dialog._end()
        self.wait()
        end = [data for name, data in self.recorder.calls if name == 'end_case'][-1]
        self.assertEqual(end, dict(result='FAIL', notes='Synthetic observed failure',
                                   operation_id='OPERATION-ONE', collect_logs=True))
        self.assertFalse(self.dialog.collect_logs.get_active())

    def test_every_recorder_call_runs_off_main_thread_and_ui_remains_responsive(self):
        self.start()
        main_thread = threading.get_ident()
        self.recorder.begin_gate = threading.Event()
        self.dialog.case_id.set_text('SAFE-01')
        self.dialog._begin()
        self.assertTrue(self.dialog.busy)
        self.assertFalse(self.dialog.begin_button.get_sensitive())
        dispatched = []
        self.GLib.idle_add(lambda: dispatched.append(True) and False)
        context = self.GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        self.assertTrue(dispatched)
        self.recorder.begin_gate.set()
        self.wait()
        self.assertTrue(self.recorder.threads)
        self.assertNotIn(main_thread, self.recorder.threads)

    def test_open_case_prevents_export_or_finish_and_review_invalidates_after_change(self):
        self.start()
        self.begin()
        self.dialog.reviewed.set_active(True)
        self.assertFalse(self.dialog.export_button.get_sensitive())
        self.assertFalse(self.dialog.finish_button.get_sensitive())
        self.dialog._export()
        self.assertFalse(any(name == 'export' for name, data in self.recorder.calls))
        self.dialog._end()
        self.wait()
        self.dialog.reviewed.set_active(True)
        self.assertTrue(self.dialog.export_button.get_sensitive())
        self.dialog.notes.get_buffer().set_text('Changed draft')
        self.assertFalse(self.dialog.reviewed.get_active())
        self.assertFalse(self.dialog.export_button.get_sensitive())
        self.dialog.reviewed.set_active(True)
        self.dialog._refresh()
        self.wait()
        self.assertFalse(self.dialog.reviewed.get_active())
        self.dialog._finish()
        self.wait()
        self.assertTrue(self.dialog.start_button.get_sensitive())
        self.assertFalse(self.dialog.begin_button.get_sensitive())
        self.assertIn('Finished', self.dialog.state.get_text())

    def test_close_preserves_open_case_and_new_panel_resumes_only_recording(self):
        self.start()
        self.begin()
        before = copy.deepcopy(self.recorder.run)
        self.dialog._response(self.dialog, self.Gtk.ResponseType.CLOSE)
        self.assertIsNone(self.owner.trial_dialog)
        self.assertEqual(self.recorder.run, before)
        self.assertFalse(any(name in ('end_case', 'finish', 'export') for name, data in self.recorder.calls))
        self.dialog = self.show_trials(self.owner)
        self.wait()
        self.assertTrue(self.dialog.end_button.get_sensitive())
        self.assertIn('SESSION-ONE', self.dialog.state.get_text())

    def test_export_requires_review_and_uses_background_backend(self):
        self.start()
        self.begin()
        self.dialog._end()
        self.wait()
        self.dialog._export()
        self.assertFalse(any(name == 'export' for name, data in self.recorder.calls))
        self.dialog.reviewed.set_active(True)
        chooser = SimpleNamespace(
            add_button=lambda *args: None, set_default_response=lambda *args: None,
            set_current_name=lambda *args: None, run=lambda: self.Gtk.ResponseType.OK,
            get_filename=lambda: '/private/reviewed-new.zip', destroy=lambda: None,
        )
        with patch('src.trial_dialog.Gtk.FileChooserDialog', return_value=chooser):
            self.dialog._export()
            self.wait()
        export = [data for name, data in self.recorder.calls if name == 'export'][-1]
        self.assertEqual(export, dict(path='/private/reviewed-new.zip', reviewed=True))
        self.assertIn('/private/reviewed-new.zip', self.dialog.notice.get_text())
        self.assertFalse(self.dialog.reviewed.get_active())

    def test_large_preview_renders_incrementally_without_enabling_early_review(self):
        self.start()
        preview = 'Synthetic reviewed line\n' * 12000
        context = self.GLib.MainContext.default()
        with patch.object(self.recorder, 'preview', return_value=preview):
            self.dialog._refresh()
            deadline = time.monotonic() + 3
            while self.dialog.notice.get_text() != 'Rendering export preview…' and time.monotonic() < deadline:
                context.iteration(False)
                time.sleep(0.002)
            self.assertTrue(self.dialog.busy)
            self.assertFalse(self.dialog.reviewed.get_sensitive())
            observed_lengths = []

            def observe():
                observed_lengths.append(self.dialog.preview_view.get_buffer().get_char_count())
                return False

            self.GLib.idle_add(observe)
            self.wait()
        self.assertTrue(observed_lengths)
        self.assertLess(observed_lengths[0], len(preview))
        buffer = self.dialog.preview_view.get_buffer()
        self.assertEqual(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True), preview)
        self.assertTrue(self.dialog.reviewed.get_sensitive())

    def test_long_titles_and_case_fields_keep_controls_inside_default_viewport(self):
        self.start()
        self.recorder.run['id'] = 'run-' + 'a' * 32
        self.recorder.run['title'] = 'Long synthetic trial title ' * 7
        self.recorder.run['cases'] = [dict(case_id='C' * 64, variant='V' * 200,
                                         result='PASS', session_id='S' * 128)]
        self.dialog._refresh()
        self.wait()
        self.dialog.resize(800, 760)
        context = self.GLib.MainContext.default()
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.003)
        viewport = self.dialog.content_scroll.get_child()
        width = viewport.get_allocated_width()
        self.assertGreater(width, 600)
        for widget in (self.dialog.runs, self.dialog.select_button, self.dialog.refresh_button):
            coordinates = widget.translate_coordinates(viewport, 0, 0)
            self.assertIsNotNone(coordinates)
            x = coordinates[-2]
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + widget.get_allocated_width(), width)
        adjustment = self.dialog.content_scroll.get_hadjustment()
        self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size() + 1)
        case_scroll = self.dialog.case_tree.get_parent()
        adjustment = case_scroll.get_hadjustment()
        self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size() + 1)

    def test_real_collector_roundtrip_keeps_only_the_case_conversation(self):
        from src.action_audit import ActionAudit
        from src.trial_recorder import TrialRecorder
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = TrialRecorder(root / 'trials', history_path=root / 'history.json',
                                     log_path=root / 'app.log',
                                     context_factory=lambda: {'fixture': True, 'state': 'unknown'})
            self.dialog.destroy()
            self.owner.trial_recorder = recorder
            self.dialog = self.show_trials(self.owner)
            self.wait()
            self.start()
            self.begin()
            audit = ActionAudit(root / 'actions.json')
            audit.append('OP-ONE', 'SESSION-ONE', 'files.checksum', 'synthetic-file',
                         'read', 'verified', 'Synthetic result')
            audit.append('OP-TWO', 'SESSION-TWO', 'files.checksum', 'other-synthetic-file',
                         'read', 'verified', 'Other conversation')
            self.owner.history_store.active_session_id = 'SESSION-TWO'
            self.dialog.notes.get_buffer().set_text('Synthetic independently checked observation')
            self.dialog.operation.set_text('OP-ONE')
            self.dialog._end()
            self.wait()
            self.assertIsNotNone(self.dialog.run_data)
            case = self.dialog.run_data['cases'][0]
            self.assertEqual(case['session_id'], 'SESSION-ONE')
            self.assertEqual([event['operation_id'] for event in case['events']], ['OP-ONE'])
            self.assertIsNone(case['log_excerpt'])
            preview_buffer = self.dialog.preview_view.get_buffer()
            preview = preview_buffer.get_text(preview_buffer.get_start_iter(), preview_buffer.get_end_iter(), True)
            self.assertIn('Synthetic independently checked observation', preview)
            self.assertNotIn('Other conversation', preview)
            self.dialog._finish()
            self.wait()
            self.dialog.reviewed.set_active(True)
            output = root / 'reviewed-new.zip'
            chooser = SimpleNamespace(
                add_button=lambda *args: None, set_default_response=lambda *args: None,
                set_current_name=lambda *args: None, run=lambda: self.Gtk.ResponseType.OK,
                get_filename=lambda: str(output), destroy=lambda: None,
            )
            with patch('src.trial_dialog.Gtk.FileChooserDialog', return_value=chooser):
                self.dialog._export()
                self.wait()
            self.assertTrue(output.is_file(), self.dialog.notice.get_text())
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {'trial.json', 'report.md', 'results.csv', 'manifest.json'})
                exported = json.loads(archive.read('trial.json'))
            self.assertEqual(exported['cases'][0]['result'], 'PASS')
            self.assertIsNotNone(exported['finished_at'])
            self.assertNotIn('_review_digest', exported)
