"""Secret Service ownership and migration guarantees without a live bus."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.credential_store import APPLICATION_ID, CredentialStoreError, SecretServiceCredentialStore


PROFILE = 'test-profile-47'
PROVIDER = 'openrouter'
SECRET = 'synthetic-credential-not-for-production-ß'


class LockedException(Exception):
    pass


class PromptDismissedException(Exception):
    pass


class SecretServiceNotAvailableException(Exception):
    pass


class FakeItem:
    def __init__(self, collection, attributes, secret, locked=False):
        self.collection = collection
        self.attributes = dict(attributes)
        self.secret = secret
        self.locked = locked
        self.reads = 0
        self.writes = []
        self.deletes = 0

    def is_locked(self):
        return self.locked

    def get_attributes(self):
        return dict(self.attributes)

    def get_secret(self):
        self.reads += 1
        return self.secret

    def set_secret(self, secret):
        self.writes.append(secret)
        self.secret = secret

    def delete(self):
        self.deletes += 1
        self.collection.items.remove(self)


class FakeCollection:
    def __init__(self, locked=False):
        self.locked = locked
        self.items = []
        self.searches = []
        self.creates = []
        self.unlocks = 0
        self.dismissed = False
        self.remain_locked = False

    def is_locked(self):
        return self.locked

    def unlock(self):
        self.unlocks += 1
        if not self.dismissed and not self.remain_locked:
            self.locked = False
        return self.dismissed

    def search_items(self, attributes):
        self.searches.append(dict(attributes))
        return iter(
            item for item in self.items
            if all(item.attributes.get(key) == value for key, value in attributes.items())
        )

    def create_item(self, label, attributes, secret, replace=False):
        self.creates.append((label, dict(attributes), secret, replace))
        return self.add_item(attributes, secret)

    def add_item(self, attributes, secret=SECRET.encode('utf-8'), locked=False):
        item = FakeItem(self, attributes, secret, locked=locked)
        self.items.append(item)
        return item


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.collection = FakeCollection()
        self.connector = Mock(return_value=self.collection)
        self.store = SecretServiceCredentialStore(PROFILE, connector=self.connector)
        self.attributes = {'application': APPLICATION_ID, 'profile': PROFILE, 'provider': PROVIDER}

    def assert_reason(self, reason, operation):
        with self.assertRaises(CredentialStoreError) as raised:
            operation()
        self.assertEqual(raised.exception.reason, reason)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SECRET, repr(raised.exception))
        return raised.exception

    def test_construction_is_lazy(self):
        self.connector.assert_not_called()

    def test_absent_lookup_neither_creates_nor_unlocks(self):
        self.assertIsNone(self.store.lookup(PROVIDER))
        self.assertEqual(self.collection.searches, [self.attributes])
        self.assertEqual(self.collection.creates, [])
        self.assertEqual(self.collection.unlocks, 0)

    def test_locked_collection_never_searches_writes_or_unlocks_implicitly(self):
        self.collection.locked = True
        for operation in (
            lambda: self.store.lookup(PROVIDER),
            lambda: self.store.store(PROVIDER, SECRET),
            lambda: self.store.delete(PROVIDER),
        ):
            self.assert_reason('locked', operation)
        self.assertEqual(self.collection.searches, [])
        self.assertEqual(self.collection.creates, [])
        self.assertEqual(self.collection.unlocks, 0)

    def test_locked_item_is_not_read_changed_or_deleted(self):
        item = self.collection.add_item(self.attributes, locked=True)
        for operation in (
            lambda: self.store.lookup(PROVIDER),
            lambda: self.store.store(PROVIDER, SECRET),
            lambda: self.store.delete(PROVIDER),
        ):
            self.assert_reason('locked', operation)
        self.assertEqual(item.reads, 0)
        self.assertEqual(item.writes, [])
        self.assertEqual(item.deletes, 0)
        self.assertEqual(self.collection.unlocks, 0)

    def test_lookup_returns_exact_utf8_secret_without_unlocking(self):
        self.collection.add_item(self.attributes)
        self.assertEqual(self.store.lookup(PROVIDER), SECRET)
        self.assertEqual(self.collection.unlocks, 0)

    def test_explicit_unlock_checks_dismissed_result_and_lock_state(self):
        self.collection.locked = True
        self.collection.dismissed = True
        self.assert_reason('cancelled', self.store.unlock)
        self.assertTrue(self.collection.locked)
        self.collection.dismissed = False
        self.collection.remain_locked = True
        self.assert_reason('locked', self.store.unlock)
        self.collection.remain_locked = False
        self.store.unlock()
        self.assertFalse(self.collection.locked)
        self.assertEqual(self.collection.unlocks, 3)
        self.store.unlock()
        self.assertEqual(self.collection.unlocks, 3)

    def test_new_item_has_exact_ownership_and_replace_is_disabled(self):
        self.store.store(PROVIDER, SECRET)
        self.assertEqual(self.collection.creates, [
            ('Linux AI Assistant (openrouter)', self.attributes, SECRET.encode('utf-8'), False)
        ])
        self.assertEqual(self.collection.items[0].reads, 2)
        self.assertEqual(self.store.lookup(PROVIDER), SECRET)
        self.assertEqual(self.collection.unlocks, 0)

    def test_existing_owned_item_is_updated_in_place_and_verified(self):
        item = self.collection.add_item(self.attributes, b'old-synthetic-credential')
        self.store.store(PROVIDER, SECRET)
        self.assertEqual(item.writes, [SECRET.encode('utf-8')])
        self.assertEqual(item.reads, 2)
        self.assertEqual(self.collection.creates, [])

    def test_superset_item_is_not_read_or_overwritten(self):
        other_attributes = dict(self.attributes, owner='someone-else')
        other = self.collection.add_item(other_attributes)
        self.assertIsNone(self.store.lookup(PROVIDER))
        self.store.store(PROVIDER, 'new-synthetic-value')
        self.assertEqual(other.reads, 0)
        self.assertEqual(other.writes, [])
        self.assertEqual(other.deletes, 0)
        self.assertEqual(len(self.collection.items), 2)

    def test_items_with_different_application_profile_or_provider_are_ignored(self):
        others = []
        for field in self.attributes:
            attributes = dict(self.attributes)
            attributes[field] = 'different-owner'
            others.append(self.collection.add_item(attributes))
        # Even a misbehaving/superset search response cannot broaden ownership.
        self.collection.search_items = Mock(side_effect=lambda _: iter(self.collection.items))
        self.assertIsNone(self.store.lookup(PROVIDER))
        self.store.delete(PROVIDER)
        self.assertEqual(len(self.collection.items), 3)
        for item in others:
            self.assertEqual((item.reads, item.writes, item.deletes), (0, [], 0))

    def test_duplicate_owned_items_fail_without_read_write_or_delete(self):
        first = self.collection.add_item(self.attributes)
        second = self.collection.add_item(self.attributes)
        for operation in (
            lambda: self.store.lookup(PROVIDER),
            lambda: self.store.store(PROVIDER, SECRET),
            lambda: self.store.delete(PROVIDER),
        ):
            self.assert_reason('duplicate', operation)
        for item in (first, second):
            self.assertEqual((item.reads, item.writes, item.deletes), (0, [], 0))

    def test_concurrent_duplicate_after_create_is_not_silently_accepted(self):
        create = self.collection.create_item

        def duplicate(*args, **kwargs):
            item = create(*args, **kwargs)
            self.collection.add_item(self.attributes)
            return item

        self.collection.create_item = duplicate
        self.assert_reason('duplicate', lambda: self.store.store(PROVIDER, SECRET))
        self.assertEqual(len(self.collection.items), 2)
        self.assertTrue(all(item.deletes == 0 for item in self.collection.items))

    def test_write_readback_mismatch_is_not_reported_as_success(self):
        item = self.collection.add_item(self.attributes)
        item.set_secret = Mock()
        self.assert_reason('verification', lambda: self.store.store(PROVIDER, 'new-synthetic-value'))

    def test_missing_or_altered_created_item_cannot_pass_verification(self):
        create = self.collection.create_item

        def altered(*args, **kwargs):
            item = create(*args, **kwargs)
            item.attributes['owner'] = 'different-owner'
            return item

        self.collection.create_item = altered
        self.assert_reason('verification', lambda: self.store.store(PROVIDER, SECRET))
        self.assertEqual(self.collection.items[0].deletes, 0)

    def test_fresh_search_verifies_persistence_after_successful_item_read(self):
        search = self.collection.search_items
        searches = 0

        def vanishing(attributes):
            nonlocal searches
            searches += 1
            return search(attributes) if searches == 1 else iter(())

        self.collection.search_items = vanishing
        self.assert_reason('verification', lambda: self.store.store(PROVIDER, SECRET))

    def test_delete_removes_only_owned_exact_item_and_is_idempotent(self):
        owned = self.collection.add_item(self.attributes)
        other = self.collection.add_item(dict(self.attributes, owner='someone-else'))
        self.store.delete(PROVIDER)
        self.store.delete(PROVIDER)
        self.assertEqual(owned.deletes, 1)
        self.assertEqual(self.collection.items, [other])
        self.assertEqual(other.deletes, 0)
        self.assertEqual(self.collection.unlocks, 0)

    def test_delete_must_verify_absence(self):
        item = self.collection.add_item(self.attributes)
        item.delete = Mock()
        self.assert_reason('verification', lambda: self.store.delete(PROVIDER))

    def test_lookup_rejects_corrupt_or_empty_secret_without_showing_contents(self):
        for raw in (b'\xff', b'', SECRET, None):
            with self.subTest(raw_type=type(raw).__name__):
                self.collection.items = []
                self.collection.add_item(self.attributes, raw)
                self.assert_reason('verification', lambda: self.store.lookup(PROVIDER))

    def test_invalid_identifiers_and_credentials_do_not_contact_backend(self):
        for identifier in ('', 'contains spaces', '../path', '-prefix', 'ß', 'x' * 129, None):
            with self.subTest(identifier_type=type(identifier).__name__):
                self.assert_reason('invalid', lambda: SecretServiceCredentialStore(identifier))
                self.assert_reason('invalid', lambda: self.store.lookup(identifier))
        for secret in ('', None, b'bytes', '\ud800'):
            self.assert_reason('invalid', lambda: self.store.store(PROVIDER, secret))
        self.connector.assert_not_called()

    def test_backend_error_contents_are_suppressed_and_reason_is_stable(self):
        for exception, reason in (
            (RuntimeError(SECRET), 'failed'),
            (LockedException(SECRET), 'locked'),
            (PromptDismissedException(SECRET), 'cancelled'),
            (SecretServiceNotAvailableException(SECRET), 'unavailable'),
        ):
            self.collection.search_items = Mock(side_effect=exception)
            error = self.assert_reason(reason, lambda: self.store.lookup(PROVIDER))
            self.assertTrue(error.__suppress_context__)

    def test_errors_during_search_iteration_or_metadata_read_are_safe(self):
        def broken_iterator(_):
            yield self.collection.add_item(self.attributes)
            raise RuntimeError(SECRET)

        self.collection.search_items = broken_iterator
        self.assert_reason('failed', lambda: self.store.lookup(PROVIDER))
        self.collection.search_items = Mock(return_value=iter(self.collection.items))
        self.collection.items[0].get_attributes = Mock(side_effect=RuntimeError(SECRET))
        self.assert_reason('failed', lambda: self.store.lookup(PROVIDER))

    def test_connector_errors_and_absent_dependency_are_safe_without_fallback(self):
        self.connector.side_effect = RuntimeError(SECRET)
        self.assert_reason('unavailable', lambda: self.store.lookup(PROVIDER))
        self.connector.side_effect = None
        self.connector.return_value = None
        self.assert_reason('unavailable', lambda: self.store.lookup(PROVIDER))
        default = SecretServiceCredentialStore(PROFILE)
        with patch('src.credential_store.importlib.import_module', side_effect=ImportError(SECRET)):
            self.assert_reason('unavailable', lambda: default.lookup(PROVIDER))

    def test_default_connector_resolves_existing_alias_and_closes_its_connection(self):
        connection = Mock()
        module = SimpleNamespace(
            dbus_init=Mock(return_value=connection),
            get_collection_by_alias=Mock(return_value=self.collection),
            get_default_collection=Mock(side_effect=AssertionError('must not create collection')),
        )
        default = SecretServiceCredentialStore(PROFILE)
        with patch('src.credential_store.importlib.import_module', return_value=module) as importer:
            self.assertIsNone(default.lookup(PROVIDER))
        importer.assert_called_once_with('secretstorage')
        module.get_collection_by_alias.assert_called_once_with(connection, 'default')
        module.get_default_collection.assert_not_called()
        connection.close.assert_called_once_with()

    def test_missing_default_collection_does_not_create_or_choose_another(self):
        connection = Mock()
        module = SimpleNamespace(
            dbus_init=Mock(return_value=connection),
            get_collection_by_alias=Mock(side_effect=RuntimeError(SECRET)),
            get_default_collection=Mock(),
            get_any_collection=Mock(),
        )
        default = SecretServiceCredentialStore(PROFILE)
        with patch('src.credential_store.importlib.import_module', return_value=module):
            self.assert_reason('unavailable', lambda: default.lookup(PROVIDER))
        module.get_default_collection.assert_not_called()
        module.get_any_collection.assert_not_called()
        connection.close.assert_called_once_with()

    def test_connection_is_closed_on_failure_and_close_error_does_not_mask_verified_write(self):
        connection = Mock()
        module = SimpleNamespace(
            dbus_init=Mock(return_value=connection),
            get_collection_by_alias=Mock(return_value=self.collection),
        )
        default = SecretServiceCredentialStore(PROFILE)
        with patch('src.credential_store.importlib.import_module', return_value=module):
            self.collection.locked = True
            self.assert_reason('locked', lambda: default.lookup(PROVIDER))
            connection.close.assert_called_once_with()
            self.collection.locked = False
            connection.close.side_effect = RuntimeError(SECRET)
            default.store(PROVIDER, SECRET)
        self.assertEqual(self.collection.items[0].secret, SECRET.encode('utf-8'))

    def test_unknown_error_reason_cannot_be_used_to_disclose_a_secret(self):
        error = CredentialStoreError(SECRET)
        self.assertEqual(error.reason, 'failed')
        self.assertNotIn(SECRET, str(error))


if __name__ == '__main__':
    unittest.main()
