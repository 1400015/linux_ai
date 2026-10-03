"""Offline trial evidence, scope, privacy and local export boundaries."""

import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from src.action_audit import ActionAudit
from src.offline_assistant import DistroInfo
from src.system_context import detect_system_context
from src.trial_recorder import TrialRecorder


class TrialRecorderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'trials'
        self.history = self.base / 'history.json'
        self.log = self.base / 'app.log'
        self.now = 1000.0
        self.recorder = self.make_recorder()

    def context(self):
        return detect_system_context(DistroInfo(distro_id='void', pkg_manager='xbps'),
            which=lambda tool: None, environ={}, exists=lambda path: False,
            read_text=lambda path: '', clock=lambda: self.now,
            kernel='fixture-kernel', architecture='fixture-arch').to_dict()

    def make_recorder(self, **kwargs):
        values = dict(history_path=self.history, log_path=self.log,
                      context_factory=self.context, clock=lambda: self.now)
        values.update(kwargs)
        return TrialRecorder(self.root, **values)

    def write_private(self, path, content):
        path.write_text(content, encoding='utf-8')
        os.chmod(path, 0o600)
        return path

    def start(self, **kwargs):
        values = dict(title='Fixture trial', environment='ENV-01',
                      build_ref='fixture-build', mode='offline', test_type='fixture')
        values.update(kwargs)
        return self.recorder.start(**values)

    def begin(self, case_id='PKG-01', **kwargs):
        values = dict(session_id='own', interface='cli')
        values.update(kwargs)
        return self.recorder.begin_case(case_id, **values)

    def event(self, operation='operation-one', session='own', detail='fixture event'):
        ActionAudit(self.history.with_name('actions.json'), clock=lambda: self.now).append(
            operation, session, 'packages.search', 'fixture-package', 'read', 'verified', detail)

    def complete(self, **kwargs):
        self.start(**kwargs)
        self.begin()
        self.recorder.end_case('PASS', notes='Independent fixture verification')
        return self.recorder.finish()

    def export(self, name='report.zip'):
        self.recorder.preview()
        destination = self.base / name
        self.recorder.export(destination, reviewed=True)
        return destination

    @staticmethod
    def contents(destination):
        with zipfile.ZipFile(destination) as archive:
            return {item.filename: archive.read(item) for item in archive.infolist()}

    @classmethod
    def text_contents(cls, destination):
        return '\n'.join(data.decode('utf-8', errors='replace')
                         for name, data in cls.contents(destination).items()
                         if name.endswith(('.json', '.md', '.csv', '.txt', '.log')))

    def test_records_are_private_and_survive_reopening_without_raw_history(self):
        self.write_private(self.history, '{"messages":"HISTORY-MUST-NOT-BE-COLLECTED"}')
        run = self.complete()
        reopened = self.make_recorder()
        self.assertIn(run['id'], [item['id'] for item in reopened.list_runs()])
        reopened.select(run['id'])
        self.assertEqual(reopened.current()['id'], run['id'])
        for path in [self.root, *self.root.rglob('*')]:
            self.assertEqual(path.stat().st_mode & 0o077, 0, str(path))
        self.assertNotIn('HISTORY-MUST-NOT-BE-COLLECTED', self.text_contents(self.export()))
        self.assertIn('HISTORY-MUST-NOT-BE-COLLECTED', self.history.read_text())

    def test_explicit_build_collection_does_not_execute_commands_or_open_network(self):
        with patch('subprocess.run', side_effect=AssertionError('Unexpected command')), \
                patch('subprocess.Popen', side_effect=AssertionError('Unexpected command')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
            self.complete()
            self.export()

    def test_audit_excludes_baseline_other_conversation_and_other_operation(self):
        self.event(detail='OLD-BEFORE-CASE')
        self.start()
        self.begin()
        self.now += 1
        self.event(detail='OWN-NEW-EVENT')
        self.event(operation='operation-two', detail='OTHER-OPERATION-EVENT')
        self.event(session='other', detail='OTHER-CONVERSATION-EVENT')
        self.recorder.end_case('PASS', operation_id='operation-one')
        self.recorder.finish()
        text = self.text_contents(self.export())
        self.assertIn('OWN-NEW-EVENT', text)
        for marker in ('OLD-BEFORE-CASE', 'OTHER-OPERATION-EVENT', 'OTHER-CONVERSATION-EVENT'):
            self.assertNotIn(marker, text)

    def test_audit_baseline_handles_clock_moving_backwards(self):
        self.event(detail='OLD-FUTURE-EVENT')
        self.start()
        self.begin()
        self.now -= 10
        self.event(detail='NEW-PAST-EVENT')
        self.recorder.end_case('PASS')
        self.recorder.finish()
        text = self.text_contents(self.export())
        self.assertIn('NEW-PAST-EVENT', text)
        self.assertNotIn('OLD-FUTURE-EVENT', text)

    def test_change_journal_collects_new_owned_metadata_without_backup_content(self):
        backup = self.write_private(self.base / 'original.backup', 'BACKUP-MUST-STAY-PRIVATE')
        def record(identifier, session='own'):
            return {'id': identifier, 'session_id': session,
                    'path': str(self.base / 'affected.conf'), 'backup': str(backup),
                    'created_at': '2026-10-02T10:00:00+00:00',
                    'before': '0' * 64, 'after': '1' * 64, 'status': 'applied'}
        journal = self.history.with_name('changes.json')
        old = record('OLD-CHANGE-METADATA')
        self.write_private(journal, json.dumps({'version': 1, 'changes': [old]}))
        self.start()
        self.begin()
        self.write_private(journal, json.dumps({'version': 1, 'changes': [
            old, record('OWN-NEW-CHANGE'), record('OTHER-NEW-CHANGE', 'other')]}))
        original = journal.read_bytes()
        self.recorder.end_case('PASS')
        self.recorder.finish()
        text = self.text_contents(self.export())
        self.assertIn('OWN-NEW-CHANGE', text)
        for marker in ('OLD-CHANGE-METADATA', 'OTHER-NEW-CHANGE', 'BACKUP-MUST-STAY-PRIVATE'):
            self.assertNotIn(marker, text)
        self.assertEqual(journal.read_bytes(), original)
        self.assertEqual(backup.read_text(), 'BACKUP-MUST-STAY-PRIVATE')

    def test_logs_are_opt_in_and_only_append_since_begin(self):
        self.write_private(self.log, 'OLD-LOG-PREFIX\n')
        self.start()
        self.begin()
        with self.log.open('a') as stream:
            stream.write('FIRST-LOG-NOT-SELECTED\n')
        self.recorder.end_case('PASS', collect_logs=False)
        self.begin(case_id='LOG-02')
        with self.log.open('a') as stream:
            stream.write('SECOND-LOG-SELECTED password=test-log-secret\n')
        self.recorder.end_case('PASS', collect_logs=True)
        self.recorder.finish()
        text = self.text_contents(self.export())
        self.assertIn('SECOND-LOG-SELECTED', text)
        for marker in ('OLD-LOG-PREFIX', 'FIRST-LOG-NOT-SELECTED', 'test-log-secret'):
            self.assertNotIn(marker, text)

    def test_log_rotation_preserves_new_tail_without_old_prefix(self):
        self.write_private(self.log, 'OLD-ROTATION-PREFIX\n')
        self.start()
        self.begin()
        with self.log.open('a') as stream:
            stream.write('TAIL-BEFORE-ROTATION\n')
        self.log.rename(self.log.with_name('app.log.1'))
        self.write_private(self.log, 'TAIL-AFTER-ROTATION\n')
        self.recorder.end_case('PASS', collect_logs=True)
        self.recorder.finish()
        text = self.text_contents(self.export())
        self.assertIn('TAIL-BEFORE-ROTATION', text)
        self.assertIn('TAIL-AFTER-ROTATION', text)
        self.assertNotIn('OLD-ROTATION-PREFIX', text)

    def test_redacts_notes_context_events_and_text_before_persisting(self):
        factory = self.context
        def secret_context():
            data = factory()
            data['pretty_name'] = 'Linux token=test-context-secret'
            return data
        self.recorder = self.make_recorder(context_factory=secret_context)
        self.start(title='Trial password=test-title-secret')
        self.begin(notes='Before api_key=test-note-secret')
        self.event(detail='token=test-event-secret')
        self.recorder.end_case('FAIL', notes='After secret=test-end-secret')
        attachment = self.write_private(self.base / 'evidence.txt',
            'password=test-attachment-secret\nfixture evidence\n')
        self.recorder.attach(attachment)
        self.recorder.finish()
        destination = self.export()
        text = self.text_contents(destination)
        for secret in ('test-context-secret', 'test-title-secret', 'test-note-secret',
                       'test-event-secret', 'test-end-secret', 'test-attachment-secret'):
            self.assertNotIn(secret, text)
            for path in self.root.rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))
        self.assertIn('test-attachment-secret', attachment.read_text())
        self.assertIn('fixture evidence', text)

    def test_export_requires_exact_confirmation_and_prior_preview(self):
        self.complete()
        destination = self.base / 'report.zip'
        for reviewed in (False, True, 'yes', 1, None):
            with self.subTest(reviewed=reviewed), self.assertRaises((ValueError, PermissionError)):
                self.recorder.export(destination, reviewed=reviewed)
            self.assertFalse(destination.exists())
        self.recorder.preview()
        for reviewed in ('yes', 1):
            with self.subTest(reviewed=reviewed), self.assertRaises((ValueError, PermissionError)):
                self.recorder.export(destination, reviewed=reviewed)
        self.recorder.export(destination, reviewed=True)
        self.assertTrue(destination.is_file())

    def test_attachment_mutation_invalidates_previous_review(self):
        self.complete()
        self.recorder.preview()
        attachment = self.write_private(self.base / 'evidence.txt', 'new evidence\n')
        self.recorder.attach(attachment)
        destination = self.base / 'report.zip'
        with self.assertRaises((ValueError, PermissionError)):
            self.recorder.export(destination, reviewed=True)
        self.assertFalse(destination.exists())
        self.recorder.preview()
        self.recorder.export(destination, reviewed=True)

    def test_export_never_overwrites_existing_destination_or_follows_symlink(self):
        self.complete()
        self.recorder.preview()
        existing = self.write_private(self.base / 'existing.zip', 'KEEP-ORIGINAL')
        with self.assertRaises((FileExistsError, ValueError, PermissionError)):
            self.recorder.export(existing, reviewed=True)
        self.assertEqual(existing.read_text(), 'KEEP-ORIGINAL')
        link = self.base / 'linked.zip'
        link.symlink_to(existing)
        with self.assertRaises((FileExistsError, ValueError, PermissionError)):
            self.recorder.export(link, reviewed=True)
        self.assertEqual(existing.read_text(), 'KEEP-ORIGINAL')

    def test_manual_attachment_is_snapshot_and_exclusion_removes_from_export(self):
        self.complete()
        source = self.write_private(self.base / 'evidence.txt', 'COLLECTED-COPY\n')
        meta = self.recorder.attach(source)
        self.write_private(source, 'CHANGED-SOURCE\n')
        preview = self.recorder.attachment_preview(meta['id'])
        self.assertEqual(preview['kind'], 'text')
        self.assertIn('COLLECTED-COPY', preview['text'])
        self.assertNotIn('CHANGED-SOURCE', preview['text'])
        self.recorder.exclude_attachment(meta['id'])
        text = self.text_contents(self.export())
        self.assertNotIn('COLLECTED-COPY', text)
        self.assertNotIn('CHANGED-SOURCE', text)

    def test_rejects_symlink_and_hardlinked_manual_sources(self):
        self.complete()
        source = self.write_private(self.base / 'evidence.txt', 'private fixture\n')
        link = self.base / 'evidence-link.txt'
        link.symlink_to(source)
        with self.assertRaises((OSError, ValueError, PermissionError)):
            self.recorder.attach(link)
        hardlink = self.base / 'evidence-hardlink.txt'
        os.link(source, hardlink)
        with self.assertRaises((OSError, ValueError, PermissionError)):
            self.recorder.attach(hardlink)

    def test_nonregular_source_cannot_block_collection(self):
        self.complete()
        fifo = self.base / 'evidence.txt'
        os.mkfifo(fifo, 0o600)
        original_open = os.open
        def guarded_open(path, flags, *args, **kwargs):
            if str(path) == str(fifo) and not flags & os.O_NONBLOCK:
                raise AssertionError('A FIFO must not be opened in blocking mode')
            return original_open(path, flags, *args, **kwargs)
        with patch('src.trial_recorder.os.open', side_effect=guarded_open):
            with self.assertRaises((OSError, ValueError, PermissionError)):
                self.recorder.attach(fifo)

    def test_rejects_symlink_root_or_ancestor(self):
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        link = self.base / 'linked-root'
        link.symlink_to(target, target_is_directory=True)
        for root in (link, link / 'nested'):
            with self.subTest(root=str(root)), self.assertRaises((OSError, ValueError, PermissionError)):
                TrialRecorder(root, context_factory=self.context)

    def test_source_parent_replacement_cannot_redirect_attachment_read(self):
        self.complete()
        folder = self.base / 'sources'
        folder.mkdir(mode=0o700)
        source = self.write_private(folder / 'evidence.txt', 'INTENDED-CONTENT')
        other = self.base / 'other-sources'
        other.mkdir(mode=0o700)
        self.write_private(other / 'evidence.txt', 'UNINTENDED-CONTENT')
        original_open, replaced = os.open, []
        def racing_open(path, flags, *args, **kwargs):
            matches = str(path) == str(source) or (
                str(path) == folder.name and flags & os.O_DIRECTORY and 'dir_fd' in kwargs)
            if matches and not replaced:
                folder.rename(self.base / 'original-sources')
                folder.symlink_to(other, target_is_directory=True)
                replaced.append(True)
            return original_open(path, flags, *args, **kwargs)
        with patch('src.trial_recorder.os.open', side_effect=racing_open):
            with self.assertRaises((OSError, ValueError, PermissionError)):
                self.recorder.attach(source)
        self.assertTrue(replaced)
        self.assertEqual(self.recorder.current()['attachments'], [])

    def test_export_parent_replacement_cannot_redirect_bundle_write(self):
        self.complete()
        self.recorder.preview()
        folder = self.base / 'exports'
        folder.mkdir(mode=0o700)
        other = self.base / 'other-exports'
        other.mkdir(mode=0o700)
        destination = folder / 'report.zip'
        original_open, replaced = os.open, []
        def racing_open(path, flags, *args, **kwargs):
            matches = str(path) == str(destination) or (
                str(path) == folder.name and flags & os.O_DIRECTORY and 'dir_fd' in kwargs)
            if matches and not replaced:
                folder.rename(self.base / 'original-exports')
                folder.symlink_to(other, target_is_directory=True)
                replaced.append(True)
            return original_open(path, flags, *args, **kwargs)
        with patch('src.trial_recorder.os.open', side_effect=racing_open):
            with self.assertRaises((OSError, ValueError, PermissionError)):
                self.recorder.export(destination, reviewed=True)
        self.assertTrue(replaced)
        self.assertFalse((other / 'report.zip').exists())

    def test_image_is_manual_reviewable_and_manifest_matches_exported_bytes(self):
        self.complete()
        source = self.base / 'evidence.png'
        source.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR1EAAAAASUVORK5CYII='))
        os.chmod(source, 0o600)
        meta = self.recorder.attach(source)
        preview = self.recorder.attachment_preview(meta['id'])
        self.assertEqual(preview['kind'], 'image')
        self.assertTrue(Path(preview['path']).is_file())
        content = self.contents(self.export())
        manifest_name = next(name for name in content if Path(name).name == 'manifest.json')
        manifest = json.loads(content[manifest_name])
        self.assertIn('files', manifest)
        files = manifest['files']
        if isinstance(files, dict):
            entries = [(name, value) for name, value in files.items()]
        else:
            entries = [(item.get('path', item.get('name')), item) for item in files]
        self.assertTrue(entries)
        for name, item in entries:
            with self.subTest(name=name):
                self.assertIn(name, content)
                self.assertEqual(item['sha256'], hashlib.sha256(content[name]).hexdigest())
                if 'bytes' in item:
                    self.assertEqual(item['bytes'], len(content[name]))

    def test_csv_fields_cannot_be_interpreted_as_formulas(self):
        self.start(title='=HYPERLINK("https://fixture.invalid")')
        self.begin(variant='@SUM(1+1)', notes='+2+2')
        self.recorder.end_case('FAIL', notes='=2+2')
        self.recorder.finish()
        content = self.contents(self.export())
        csv_text = next(data.decode('utf-8') for name, data in content.items() if name.endswith('.csv'))
        self.assertIn('2+2', csv_text)
        for row in csv.reader(io.StringIO(csv_text)):
            for cell in row:
                self.assertFalse(cell.lstrip().startswith(('=', '+', '-', '@')), repr(cell))

    def test_raw_unregistered_files_are_excluded_from_bundle(self):
        self.complete()
        self.write_private(self.root / 'unregistered.txt', 'UNREGISTERED-MUST-STAY-PRIVATE')
        for folder in list(self.root.rglob('*')):
            if folder.is_dir():
                self.write_private(folder / 'extra.txt', 'UNREGISTERED-MUST-STAY-PRIVATE')
        content = self.contents(self.export())
        self.assertNotIn('UNREGISTERED-MUST-STAY-PRIVATE', '\n'.join(
            data.decode('utf-8', errors='replace') for data in content.values()))
        for name in content:
            self.assertFalse(Path(name).is_absolute())
            self.assertNotIn('..', Path(name).parts)

    def test_invalid_case_result_preserves_open_case(self):
        self.start()
        case = self.begin()
        for result in ('SUCCESS', '', None):
            with self.subTest(result=result), self.assertRaises((ValueError, TypeError)):
                self.recorder.end_case(result)
        self.recorder.end_case('BLOCKED', notes='Fixture unavailable')
        run = self.recorder.finish()
        matching = next(item for item in run['cases'] if item['id'] == case['id'])
        self.assertEqual(matching['result'], 'BLOCKED')

    def test_context_failure_is_visible_and_preserves_case(self):
        def broken_context():
            raise OSError('Fixture inaccessible password=test-error-secret')
        self.recorder = self.make_recorder(context_factory=broken_context)
        run = self.complete()
        case = run['cases'][0]
        self.assertEqual(case['result'], 'PASS')
        self.assertIsNone(case['before'])
        self.assertIsNone(case['after'])
        self.assertTrue(case['warnings'])
        self.assertNotIn('test-error-secret', self.text_contents(self.export()))

    def test_selection_snapshots_follow_changes_between_and_during_cases(self):
        settings = {'mode': 'offline', 'provider': '', 'model': ''}
        self.recorder = self.make_recorder(settings_factory=lambda: dict(settings))
        self.start()
        first = self.begin()
        self.assertEqual(first['settings_before'], settings)
        settings.update(mode='local', provider='local_llm', model='fixture-local')
        first = self.recorder.end_case('PASS')
        self.assertEqual(first['settings_after'], settings)
        self.assertTrue(first['warnings'])
        second = self.begin(case_id='MODE-02')
        self.assertEqual(second['settings_before'], settings)
        settings.update(mode='remote', provider='fixture-provider', model='fixture-remote')
        second = self.recorder.end_case('PASS')
        self.assertEqual(second['settings_after'], settings)
        self.recorder.finish()
        text = self.text_contents(self.export())
        self.assertIn('fixture-local', text)
        self.assertIn('fixture-remote', text)

    def test_malformed_case_or_attachment_metadata_is_rejected_and_preserved(self):
        run = self.complete()
        stored = self.root / run['id'] / 'run.json'
        original = stored.read_bytes()
        for collection in ('cases', 'attachments'):
            with self.subTest(collection=collection):
                malformed = json.loads(original)
                malformed[collection] = [None]
                self.write_private(stored, json.dumps(malformed))
                damaged = stored.read_bytes()
                with self.assertRaises(ValueError):
                    self.recorder.current()
                self.assertEqual(stored.read_bytes(), damaged)
                self.write_private(stored, original.decode('utf-8'))

    def test_forbidden_audit_journal_and_log_sources_are_not_collected_or_repaired(self):
        audit_target = self.base / 'separate-audit.json'
        ActionAudit(audit_target, clock=lambda: self.now).append('operation-one', 'own',
            'packages.search', 'fixture-package', 'read', 'verified', 'FORBIDDEN-AUDIT-CONTENT')
        self.history.with_name('actions.json').symlink_to(audit_target)
        journal_target = self.write_private(self.base / 'separate-journal.json', json.dumps({
            'version': 1, 'changes': [{'id': 'FORBIDDEN-JOURNAL-CONTENT', 'session_id': 'own'}]}))
        os.link(journal_target, self.history.with_name('changes.json'))
        self.write_private(self.log, 'FORBIDDEN-LOG-CONTENT\n')
        os.chmod(self.log, 0o644)
        audit_before, journal_before, log_before = (audit_target.read_bytes(),
            journal_target.read_bytes(), self.log.read_bytes())
        self.start()
        self.begin()
        case = self.recorder.end_case('PASS', collect_logs=True)
        self.recorder.finish()
        self.assertGreaterEqual(len(case['warnings']), 3)
        text = self.text_contents(self.export())
        for marker in ('FORBIDDEN-AUDIT-CONTENT', 'FORBIDDEN-JOURNAL-CONTENT', 'FORBIDDEN-LOG-CONTENT'):
            self.assertNotIn(marker, text)
        self.assertEqual(audit_target.read_bytes(), audit_before)
        self.assertEqual(journal_target.read_bytes(), journal_before)
        self.assertEqual(self.log.read_bytes(), log_before)
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o644)

    def test_finish_refuses_open_case_and_retry_preserves_original_result(self):
        self.start()
        self.begin()
        with self.assertRaises(ValueError):
            self.recorder.finish()
        self.recorder.end_case('FAIL', notes='First confirmed failure')
        with self.assertRaises(ValueError):
            self.recorder.end_case('PASS')
        run = self.recorder.finish()
        self.assertEqual(run['cases'][0]['result'], 'FAIL')

    def test_oversize_text_is_rejected_without_registering_attachment(self):
        self.complete()
        source = self.write_private(self.base / 'large.txt', 'x' * (2 * 1024 * 1024 + 1))
        previous = self.recorder.current()['attachments']
        with self.assertRaises((OSError, ValueError, PermissionError)):
            self.recorder.attach(source)
        self.assertEqual(self.recorder.current()['attachments'], previous)

    def test_tampered_collected_attachment_cannot_be_exported_as_reviewed(self):
        self.complete()
        source = self.write_private(self.base / 'evidence.txt', 'COLLECTED-CONTENT\n')
        meta = self.recorder.attach(source)
        self.recorder.preview()
        candidates = [path for path in self.root.rglob('*')
                      if path.is_file() and meta['id'] in path.name and path.suffix == '.txt']
        self.assertEqual(len(candidates), 1)
        self.write_private(candidates[0], 'CHANGED-COLLECTED-CONTENT\n')
        destination = self.base / 'report.zip'
        with self.assertRaises((ValueError, PermissionError)):
            self.recorder.export(destination, reviewed=True)
        self.assertFalse(destination.exists())

    def test_log_truncation_has_notice_and_current_bytes_are_preserved(self):
        self.write_private(self.log, 'BEFORE-' + 'x' * 1024 + '\n')
        self.start()
        self.begin()
        self.write_private(self.log, 'NEW-AFTER-TRUNCATION\n')
        case = self.recorder.end_case('PASS', collect_logs=True)
        self.recorder.finish()
        self.assertTrue(case['warnings'])
        text = self.text_contents(self.export())
        self.assertIn('NEW-AFTER-TRUNCATION', text)
        self.assertNotIn('BEFORE-xxx', text)

    def test_positional_secrets_in_global_logs_and_json_arrays_are_redacted(self):
        self.write_private(self.log, 'before trial\n')
        self.start()
        self.begin()
        with self.log.open('a') as stream:
            stream.write("Running command: ['password', 'test positional secret with spaces']\n")
            stream.write('Running command: fixture --token test-positional-token\n')
        self.recorder.end_case('PASS', collect_logs=True)
        source = self.write_private(self.base / 'argv.json', json.dumps({
            'argv': ['fixture', 'password', 'test array secret with spaces',
                     '--token', 'test-array-token']}))
        self.recorder.attach(source)
        self.recorder.finish()
        text = self.text_contents(self.export())
        secrets = ('test positional secret with spaces', 'test-positional-token',
                   'test array secret with spaces', 'test-array-token')
        for secret in secrets:
            self.assertNotIn(secret, text)
            for path in self.root.rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))
        self.assertIn('test array secret with spaces', source.read_text())
        self.assertIn('test positional secret with spaces', self.log.read_text())

    def test_csv_attachment_neutralizes_formulas_and_preserves_original(self):
        self.complete()
        original = 'name,value\nformula,=SUM(1+1)\nspaces,  +2+2\ntab,\t@SUM(1+1)\n'
        source = self.write_private(self.base / 'evidence.csv', original)
        meta = self.recorder.attach(source)
        preview = self.recorder.attachment_preview(meta['id'])
        self.assertEqual(preview['kind'], 'text')
        content = self.contents(self.export())
        selected = next(data.decode('utf-8') for name, data in content.items()
                        if name.startswith('attachments/') and name.endswith('.csv'))
        for text in (preview['text'], selected):
            self.assertIn('SUM(1+1)', text)
            for row in csv.reader(io.StringIO(text)):
                for cell in row:
                    self.assertFalse(cell.lstrip().startswith(('=', '+', '-', '@')), repr(cell))
        self.assertEqual(source.read_text(), original)

    def test_unquoted_multiword_log_secrets_are_absent_from_storage_and_export(self):
        self.write_private(self.log, 'before trial\n')
        self.start()
        self.begin()
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write('Running command: fixture --password correct horse battery staple\n')
            stream.write('token=another complete credential phrase\n')
            stream.write('No password required; connection refused\n')
            stream.write('Token usage - openrouter: input=100, output=20\n')
        self.recorder.end_case('PASS', collect_logs=True)
        source = self.write_private(self.base / 'passphrase.json', json.dumps({
            'passphrase': 'a separate complete secret',
            'argv': ['fixture', '--passphrase', 'array phrase with spaces'],
            'commands': [
                ['curl', '--user', 'person:correct horse battery staple'],
                ['curl', '--user=person:correct horse battery staple'],
                ['curl', '-b', 'session=correct horse battery staple'],
                ['curl', '-bsession=correct horse battery staple'],
            ],
            'large_argv': ['fixture'] + ['non-secret argument'] * 260
                + ['--password', 'late credential phrase', 'retained final argument']}))
        self.recorder.attach(source)
        self.recorder.finish()
        text = self.text_contents(self.export())
        for part in ('correct', 'horse', 'battery', 'staple', 'credential phrase',
                     'separate complete secret', 'array phrase with spaces'):
            self.assertNotIn(part, text)
            for path in self.root.rglob('*'):
                if path.is_file():
                    self.assertNotIn(part.encode(), path.read_bytes())
        self.assertIn('retained final argument', text)
        self.assertIn('No password required; connection refused', text)
        self.assertIn('Token usage - openrouter: input=100, output=20', text)
        self.assertIn('horse battery staple', self.log.read_text(encoding='utf-8'))
        self.assertIn('array phrase with spaces', source.read_text(encoding='utf-8'))

    def test_png_dimension_limit_refuses_large_header_after_valid_small_image(self):
        self.complete()
        data = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR1EAAAAASUVORK5CYII=')
        small = self.base / 'small.png'
        small.write_bytes(data)
        os.chmod(small, 0o600)
        meta = self.recorder.attach(small)
        self.assertEqual(self.recorder.attachment_preview(meta['id'])['kind'], 'image')
        # A synthetic PNG header declares 25 million pixels. Its CRC is valid,
        # so rejection must happen before decoding or allocating those pixels.
        header = b'IHDR' + struct.pack('>II', 5000, 5000) + data[24:29]
        large = self.base / 'large-header.png'
        large.write_bytes(data[:12] + header + struct.pack('>I', zlib.crc32(header) & 0xffffffff) + data[33:])
        os.chmod(large, 0o600)
        before = self.recorder.current()['attachments']
        with self.assertRaises(ValueError):
            self.recorder.attach(large)
        self.assertEqual(self.recorder.current()['attachments'], before)

    def test_excluded_attachment_still_counts_towards_retained_storage_limit(self):
        self.complete()
        first = self.write_private(self.base / 'first.txt', 'A' * 32)
        second = self.write_private(self.base / 'second.txt', 'B' * 16)
        with patch('src.trial_recorder.MAX_ATTACHMENT_BYTES', 40):
            meta = self.recorder.attach(first)
            self.recorder.exclude_attachment(meta['id'])
            self.assertEqual(self.recorder.current()['attachments'], [])
            self.assertTrue(any(meta['id'] in path.name for path in self.root.rglob('*')))
            with self.assertRaises(ValueError):
                self.recorder.attach(second)
            self.assertEqual(self.recorder.current()['attachments'], [])


if __name__ == '__main__':
    unittest.main()
