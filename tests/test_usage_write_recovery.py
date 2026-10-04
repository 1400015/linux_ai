"""Usage deltas are applied once after a published JSON write reports failure."""

import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.ai_client import AIClient


def usage_client(path):
    # Skip sessions, real home configuration, timers and atexit hooks.
    result = AIClient.__new__(AIClient)
    result._usage_path = Path(path)
    result._usage_lock = threading.Lock()
    result._usage_timer = None
    result._usage_dirty = False
    result._usage_pending = {}
    result._usage_reset = False
    result.token_usage = {}
    result._schedule_usage_save = Mock(side_effect=lambda: setattr(result, '_usage_dirty', True))
    return result


def write_other_process_usage(path):
    client = usage_client(path)
    client._update_token_usage('shared', 7, 3)
    client._update_token_usage('other', 2, 4)
    client.flush_usage()


class UsageWriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'usage.json'
        self.first = self.client()
        self.second = self.client()
        self.original_fsync = os.fsync
        self.warning = patch('src.ai_client.logger.warning')
        self.warning.start()
        self.addCleanup(self.warning.stop)

    def client(self):
        return usage_client(self.path)

    def fail_directory_fsync(self, descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError('synthetic directory synchronization failure')
        return self.original_fsync(descriptor)

    def persisted(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def publish_with_sync_failure(self, client):
        with patch('src.storage.os.fsync', side_effect=self.fail_directory_fsync):
            client.flush_usage()

    def test_repeated_sync_failure_then_recovery_does_not_apply_same_delta_again(self):
        self.first._update_token_usage('fixture', 10, 5)
        for _ in range(3):
            self.publish_with_sync_failure(self.first)
            self.assertEqual(self.persisted()['fixture'], {'input': 10, 'output': 5, 'total': 15})
            self.assertEqual(self.first.token_usage, self.persisted())
            self.assertEqual(self.first._usage_pending, {})
            self.assertFalse(self.first._usage_reset)
            self.assertTrue(self.first._usage_dirty)
        self.first.flush_usage()
        self.assertEqual(self.persisted()['fixture']['total'], 15)
        self.assertFalse(self.first._usage_dirty)
        self.first.flush_usage()
        self.assertEqual(self.persisted()['fixture']['total'], 15)

    def test_stale_second_client_totals_survive_retry_for_same_and_different_provider(self):
        self.first._update_token_usage('shared', 10, 5)
        self.publish_with_sync_failure(self.first)
        # The second client has never loaded the first client's publication.
        self.second._update_token_usage('shared', 7, 3)
        self.second._update_token_usage('other', 2, 4)
        self.second.flush_usage()
        self.first.flush_usage()
        self.assertEqual(self.persisted(), {
            'shared': {'input': 17, 'output': 8, 'total': 25},
            'other': {'input': 2, 'output': 4, 'total': 6},
        })
        self.assertEqual(self.first.token_usage, self.persisted())

    def test_new_delta_after_uncertain_publication_is_added_once_to_latest_disk_totals(self):
        self.first._update_token_usage('shared', 10, 5)
        self.publish_with_sync_failure(self.first)
        self.second._update_token_usage('shared', 7, 3)
        self.second.flush_usage()
        self.first._update_token_usage('shared', 2, 1)
        self.publish_with_sync_failure(self.first)
        self.assertEqual(self.persisted()['shared'], {'input': 19, 'output': 9, 'total': 28})
        self.assertEqual(self.first._usage_pending, {})
        self.first.flush_usage()
        self.assertEqual(self.persisted()['shared']['total'], 28)

    def test_other_process_usage_survives_first_clients_durability_retry(self):
        self.first._update_token_usage('shared', 10, 5)
        self.publish_with_sync_failure(self.first)
        process = multiprocessing.get_context('spawn').Process(
            target=write_other_process_usage, args=(str(self.path),))
        process.start()
        try:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(2)
        self.first.flush_usage()
        self.assertEqual(self.persisted(), {
            'shared': {'input': 17, 'output': 8, 'total': 25},
            'other': {'input': 2, 'output': 4, 'total': 6},
        })

    def test_published_reset_is_not_repeated_over_another_clients_later_usage(self):
        self.first._update_token_usage('old', 10, 5)
        self.first.flush_usage()
        with patch('src.storage.os.fsync', side_effect=self.fail_directory_fsync):
            self.first.reset_token_usage()
        self.assertEqual(self.persisted(), {})
        self.assertFalse(self.first._usage_reset)
        self.assertTrue(self.first._usage_dirty)
        self.second._update_token_usage('later', 7, 3)
        self.second.flush_usage()
        self.first.flush_usage()
        self.assertEqual(self.persisted(), {'later': {'input': 7, 'output': 3, 'total': 10}})

    def test_other_clients_later_reset_is_respected_by_empty_delta_retry(self):
        self.first._update_token_usage('shared', 10, 5)
        self.publish_with_sync_failure(self.first)
        self.second.reset_token_usage()
        self.first.flush_usage()
        self.assertEqual(self.persisted(), {})
        self.assertEqual(self.first.token_usage, {})

    def test_replace_failure_keeps_increments_until_they_can_be_published(self):
        self.first._update_token_usage('fixture', 10, 5)
        with patch('src.storage.os.replace', side_effect=OSError('synthetic replacement failure')):
            self.first.flush_usage()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.first._usage_pending['fixture']['total'], 15)
        self.assertTrue(self.first._usage_dirty)
        self.first.flush_usage()
        self.assertEqual(self.persisted()['fixture']['total'], 15)
        self.assertEqual(self.first._usage_pending, {})

    def test_file_sync_failure_before_replacement_keeps_delta_and_old_file(self):
        self.first._update_token_usage('fixture', 10, 5)
        self.first.flush_usage()
        before = self.path.read_bytes()
        self.first._update_token_usage('fixture', 7, 3)
        with patch('src.storage.os.fsync', side_effect=OSError('synthetic file synchronization failure')):
            self.first.flush_usage()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.first._usage_pending['fixture']['total'], 10)
        self.first.flush_usage()
        self.assertEqual(self.persisted()['fixture']['total'], 25)

    def test_reset_failure_before_replacement_retains_reset_for_eventual_publication(self):
        self.first._update_token_usage('old', 10, 5)
        self.first.flush_usage()
        with patch('src.storage.os.replace', side_effect=OSError('synthetic replacement failure')):
            self.first.reset_token_usage()
        self.assertEqual(self.persisted()['old']['total'], 15)
        self.assertTrue(self.first._usage_reset)
        self.assertTrue(self.first._usage_dirty)
        self.first._update_token_usage('new', 7, 3)
        self.first.flush_usage()
        self.assertEqual(self.persisted(), {'new': {'input': 7, 'output': 3, 'total': 10}})
        self.assertFalse(self.first._usage_reset)
