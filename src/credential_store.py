"""Optional Linux Secret Service storage for explicitly selected API keys.

Lookup never creates a collection or requests an unlock. Mutations require an
unlocked default collection, and callers must retain their previous credential
until ``store`` has verified its write. There is no alternate or file backend.
"""

from contextlib import contextmanager
import importlib
import re
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar


APPLICATION_ID = 'io.github.linux_ai_assistant'
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
_RESULT = TypeVar('_RESULT')
_ERROR_MESSAGES = {
    'unavailable': 'The default Secret Service collection is unavailable.',
    'locked': 'The Secret Service collection or credential is locked.',
    'cancelled': 'The Secret Service operation was cancelled.',
    'duplicate': 'More than one matching Secret Service credential exists.',
    'verification': 'The Secret Service credential could not be verified.',
    'invalid': 'The credential or storage identifier is invalid.',
    'failed': 'The Secret Service operation failed.',
}


class CredentialStoreError(RuntimeError):
    """A stable reason and safe message, without backend exception contents."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _ERROR_MESSAGES else 'failed'
        super().__init__(_ERROR_MESSAGES[self.reason])


def _error_reason(error: Exception, connecting: bool = False) -> str:
    # Importing SecretStorage is optional. The public exception names have been
    # stable since 3.3; examining names also keeps injected backends independent.
    names = {base.__name__ for base in type(error).__mro__}
    if 'PromptDismissedException' in names:
        return 'cancelled'
    if 'LockedException' in names:
        return 'locked'
    if connecting or 'SecretServiceNotAvailableException' in names:
        return 'unavailable'
    return 'failed'


class SecretServiceCredentialStore:
    """Own exact application/profile/provider items in the default collection.

    ``connector`` optionally returns a SecretStorage-compatible collection for
    tests or embedding. The caller owns an injected connection. Normal calls
    open and close their own session-bus connection, avoiding shared D-Bus
    connections between application threads.
    """

    def __init__(
        self,
        profile: str,
        connector: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._validate_identifier(profile)
        self._profile = profile
        self._connector = connector

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise CredentialStoreError('invalid')

    def _attributes(self, provider: str) -> Dict[str, str]:
        self._validate_identifier(provider)
        return {'application': APPLICATION_ID, 'profile': self._profile, 'provider': provider}

    @contextmanager
    def _collection(self) -> Iterator[Any]:
        if self._connector is not None:
            try:
                collection = self._connector()
                if collection is None:
                    raise CredentialStoreError('unavailable')
            except CredentialStoreError:
                raise
            except Exception as error:
                raise CredentialStoreError(_error_reason(error, connecting=True)) from None
            yield collection
            return

        connection = None
        try:
            try:
                secretstorage = importlib.import_module('secretstorage')
                connection = secretstorage.dbus_init()
                # get_default_collection() creates a missing collection, which
                # can prompt. Only resolve the existing persistent default alias.
                collection = secretstorage.get_collection_by_alias(connection, 'default')
            except Exception as error:
                raise CredentialStoreError(_error_reason(error, connecting=True)) from None
            yield collection
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    # Closing a verified write's transport must not turn it
                    # into an ambiguous migration failure or disclose details.
                    pass

    def _execute(self, operation: Callable[[Any], _RESULT]) -> _RESULT:
        try:
            with self._collection() as collection:
                return operation(collection)
        except CredentialStoreError:
            raise
        except Exception as error:
            raise CredentialStoreError(_error_reason(error)) from None

    @staticmethod
    def _require_unlocked(target: Any) -> None:
        if target.is_locked():
            raise CredentialStoreError('locked')

    @staticmethod
    def _find_item(collection: Any, attributes: Dict[str, str]) -> Optional[Any]:
        found = None
        # Search matches subsets. An item with additional or changed attributes
        # belongs to another owner and must never be read, changed or deleted.
        for item in collection.search_items(attributes):
            if item.get_attributes() != attributes:
                continue
            if found is not None:
                raise CredentialStoreError('duplicate')
            found = item
        return found

    def lookup(self, provider: str) -> Optional[str]:
        """Read without creating a collection, unlocking or showing prompts."""
        attributes = self._attributes(provider)

        def read(collection: Any) -> Optional[str]:
            self._require_unlocked(collection)
            item = self._find_item(collection, attributes)
            if item is None:
                return None
            self._require_unlocked(item)
            raw = item.get_secret()
            if not isinstance(raw, bytes) or not raw:
                raise CredentialStoreError('verification')
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError:
                raise CredentialStoreError('verification') from None

        return self._execute(read)

    def unlock(self) -> None:
        """Explicitly request the desktop's unlock prompt when locked."""
        def request(collection: Any) -> None:
            if not collection.is_locked():
                return
            # SecretStorage returns True when the prompt was dismissed.
            if collection.unlock():
                raise CredentialStoreError('cancelled')
            self._require_unlocked(collection)

        self._execute(request)

    def store(self, provider: str, secret: str) -> None:
        """Write and verify an exact item; require an already unlocked vault."""
        attributes = self._attributes(provider)
        if not isinstance(secret, str) or not secret:
            raise CredentialStoreError('invalid')
        try:
            encoded = secret.encode('utf-8')
        except UnicodeEncodeError:
            raise CredentialStoreError('invalid') from None

        def write(collection: Any) -> None:
            self._require_unlocked(collection)
            item = self._find_item(collection, attributes)
            if item is None:
                # Broad replacement could overwrite someone else's superset
                # attributes. A duplicate concurrent insertion fails verification.
                item = collection.create_item(
                    'Linux AI Assistant ({})'.format(provider), attributes, encoded, replace=False
                )
            else:
                self._require_unlocked(item)
                item.set_secret(encoded)
            self._require_unlocked(item)
            if item.get_attributes() != attributes or item.get_secret() != encoded:
                raise CredentialStoreError('verification')
            verified = self._find_item(collection, attributes)
            if verified is None:
                raise CredentialStoreError('verification')
            self._require_unlocked(verified)
            if verified.get_secret() != encoded:
                raise CredentialStoreError('verification')

        self._execute(write)

    def delete(self, provider: str) -> None:
        """Explicitly delete only the exact owned item and verify its absence."""
        attributes = self._attributes(provider)

        def remove(collection: Any) -> None:
            self._require_unlocked(collection)
            item = self._find_item(collection, attributes)
            if item is None:
                return
            self._require_unlocked(item)
            item.delete()
            if self._find_item(collection, attributes) is not None:
                raise CredentialStoreError('verification')

        self._execute(remove)
