"""Atomic, shared conversation history and explicit sessions, independent of GTK.

Legacy flat histories migrate into one conversation on the first session operation.
Queued messages retain the session in which they were submitted. All writers and
session operations use the same locked, atomic JSON transaction.
"""

import atexit
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import queue
import shutil
import stat
import tempfile
import threading
import time
import uuid

from .storage import JsonLimitError, atomic_json_write, json_lock, read_json, update_json
from .task_state import validate_task_state

MAX_HISTORY_MESSAGES = 1000
MAX_SESSIONS = 100
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_HISTORY_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_CHARS = 128 * 1024
MAX_SEARCH_RESULTS = 200
HISTORY_VERSION = 1
EXPORT_FORMAT = "linux-ai-conversation"
LEGACY_SESSION_ID = "legacy"


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Conversation title must contain 1–200 characters")
    return value.strip()


def _timestamp(value, default=None):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 0 <= value < 253402300800 and math.isfinite(value):
            return value
    return time.time() if default is None else default


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _diagnostic_state(value):
    """Only an article identity and bounded step; never instructions or commands."""
    if not isinstance(value, dict) or set(value) != {"id", "step"}:
        raise ValueError("Invalid diagnostic state")
    identifier, step = value["id"], value["step"]
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 64:
        raise ValueError("Invalid diagnostic identity")
    if type(step) is not int or not 0 <= step <= 99:
        raise ValueError("Invalid diagnostic step")
    return {"id": identifier, "step": step}


def _entries(items, session_id=None):
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content or len(content) > MAX_MESSAGE_CHARS:
            continue
        if session_id is not None and item.get("session_id", LEGACY_SESSION_ID) != session_id:
            continue
        entry = {"role": item["role"], "content": content}
        if "timestamp" in item:
            entry["timestamp"] = _timestamp(item["timestamp"], 0)
        result.append(entry)
    return result


def _new_session(title="New conversation", session_id=None, messages=None):
    now = time.time()
    return {
        "id": session_id or uuid.uuid4().hex,
        "title": _title(title),
        "created_at": now,
        "updated_at": now,
        "archived": False,
        "messages": messages or [],
    }


def _document(loaded):
    if isinstance(loaded, dict) and type(loaded.get("version")) is int and loaded["version"] == HISTORY_VERSION:
        sessions = loaded.get("sessions")
        if not isinstance(sessions, list) or not sessions or len(sessions) > MAX_SESSIONS:
            raise ValueError("Invalid conversation history")
        identifiers = set()
        for session in sessions:
            if not isinstance(session, dict) or not isinstance(session.get("id"), str):
                raise ValueError("Invalid conversation metadata")
            identifier = session["id"]
            if not identifier or len(identifier) > 64 or identifier in identifiers:
                raise ValueError("Invalid conversation identity")
            identifiers.add(identifier)
            _title(session.get("title"))
            if not isinstance(session.get("archived"), bool) or not isinstance(session.get("messages"), list):
                raise ValueError("Invalid conversation metadata")
            if any(_timestamp(session.get(key), -1) == -1 for key in ("created_at", "updated_at")):
                raise ValueError("Invalid conversation timestamp")
            if len(_entries(session["messages"])) != len(session["messages"]):
                raise ValueError("Invalid conversation messages")
            if "diagnostic" in session:
                _diagnostic_state(session["diagnostic"])
            if "task" in session:
                try:
                    session["task"] = validate_task_state(session["task"])
                except ValueError:
                    # A corrupt choice must not hide the user's messages.
                    session.pop("task", None)
        if loaded.get("active_session_id") not in identifiers:
            raise ValueError("Invalid active conversation")
        return loaded
    if isinstance(loaded, list):
        messages = _entries(loaded)
        title = "Previous conversations" if messages else "New conversation"
        session = _new_session(title, LEGACY_SESSION_ID, messages)
        dates = [entry["timestamp"] for entry in messages if entry.get("timestamp")]
        if dates:
            session["created_at"], session["updated_at"] = min(dates), max(dates)
        return {"version": HISTORY_VERSION, "active_session_id": session["id"], "sessions": [session]}
    raise ValueError("Unsupported conversation history format; original file preserved. "
                     "Use history recover --yes to back it up and start a new history.")


def _find(document, session_id):
    for session in document["sessions"]:
        if session.get("id") == session_id:
            return session
    raise ValueError("Conversation does not exist")


def _metadata(session):
    result = {key: session[key] for key in ("id", "title", "created_at", "updated_at", "archived")}
    result["message_count"] = len(session["messages"])
    return result


def _ensure_active(document):
    active = document.get("active_session_id")
    if any(session["id"] == active and not session["archived"] for session in document["sessions"]):
        return
    available = [session for session in document["sessions"] if not session["archived"]]
    if available:
        document["active_session_id"] = max(available, key=lambda session: session["updated_at"])["id"]
    else:
        if len(document["sessions"]) >= MAX_SESSIONS:
            raise ValueError("Keep at least one conversation available")
        session = _new_session()
        document["sessions"].append(session)
        document["active_session_id"] = session["id"]


class HistoryStore:
    """FIFO history writer with synchronous, atomic session management."""

    def __init__(self, path=None, max_messages: int = MAX_HISTORY_MESSAGES):
        self.path = Path(path) if path else Path.home() / ".config" / "linux_ai_assistant" / "history.json"
        if not isinstance(max_messages, int) or max_messages < 1:
            raise ValueError("max_messages must be positive")
        self.max_messages = max_messages
        self._queue = queue.SimpleQueue()
        self._closed = False
        self._state_lock = threading.Lock()
        self.last_error = None
        loaded = self._read()
        self._session_id = loaded.get("active_session_id", LEGACY_SESSION_ID) if isinstance(loaded, dict) else LEGACY_SESSION_ID
        self._generation = loaded.get("recovery_generation") if isinstance(loaded, dict) else None
        self._writer = threading.Thread(target=self._loop, name="history-writer", daemon=True)
        self._writer.start()
        atexit.register(self.close)

    @property
    def active_session_id(self):
        return self._session_id

    def _read(self):
        try:
            loaded = read_json(self.path, MAX_HISTORY_BYTES)
            self._adopt_recovery(loaded)
            return loaded
        except FileNotFoundError:
            return []
        except JsonLimitError as error:
            raise JsonLimitError(str(error) + ". Use history recover --yes to back it up and start a new history.") from None
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).error("Error loading history: %s", error)
            return []

    def _adopt_recovery(self, document):
        if not isinstance(document, dict) or not hasattr(self, "_generation"):
            return
        generation = document.get("recovery_generation")
        if (type(document.get("version")) is int and document["version"] == HISTORY_VERSION
                and isinstance(generation, str) and generation != self._generation):
            _document(document)
            # Queued entries keep their old generation, so a reset cannot
            # silently discard them as messages for a deleted conversation.
            with self._state_lock:
                self._generation = generation
                self._session_id = document["active_session_id"]

    @staticmethod
    def recover_file(path=None, confirmed=False):
        """Explicitly preserve all bytes privately before starting fresh.

        Unknown versions are never interpreted or downgraded. The stable lock
        serializes backup/reset with every cooperating history writer.
        """
        if confirmed is not True:
            raise PermissionError("Explicit approval is required to recover history")
        path = Path(path) if path is not None else Path.home() / ".config/linux_ai_assistant/history.json"
        generation = uuid.uuid4().hex
        session = _new_session("Recovered conversation")
        document = {"version": HISTORY_VERSION, "active_session_id": session["id"],
                    "sessions": [session], "recovery_generation": generation}
        backup = None
        complete = False
        try:
            with json_lock(path):
                source_fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(source_fd, "rb") as source:
                    original = os.fstat(source.fileno())
                    if not stat.S_ISREG(original.st_mode):
                        raise ValueError("History recovery requires a regular file")
                    backup_fd, name = tempfile.mkstemp(prefix=path.name + ".recovered-", dir=str(path.parent))
                    backup = Path(name)
                    with os.fdopen(backup_fd, "wb") as target:
                        shutil.copyfileobj(source, target, 65536)
                        target.flush()
                        os.fsync(target.fileno())
                    current = os.stat(str(path), follow_symlinks=False)
                    if (_file_identity(original) != _file_identity(current)
                            or _file_identity(original) != _file_identity(os.fstat(source.fileno()))):
                        raise OSError("History changed during recovery; original file preserved")
                    directory_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    complete = True
                    atomic_json_write(path, document, max_bytes=MAX_HISTORY_BYTES)
        except BaseException as error:
            if backup is not None:
                if complete:
                    error.recovery_backup = backup
                else:
                    try:
                        backup.unlink()
                    except OSError:
                        pass
            raise
        return backup

    def recover(self, confirmed=False):
        backup = self.recover_file(self.path, confirmed=confirmed)
        self._read()
        if not self.flush():
            error = OSError("History was backed up and reset, but pending messages could not be saved")
            error.recovery_backup = backup
            raise error from self.last_error
        return backup

    def load_entries(self, session_id=None):
        """Read only the selected conversation, preserving message timestamps."""
        self.flush()
        loaded = self._read()
        selected = session_id or self._session_id
        if isinstance(loaded, list):
            return _entries(loaded, selected)[-self.max_messages:]
        try:
            return _entries(_find(_document(loaded), selected)["messages"])[-self.max_messages:]
        except ValueError:
            return []

    def load_messages(self, session_id=None):
        """Normalized provider context: no timestamps or other conversations."""
        return [{"role": entry["role"], "content": entry["content"]} for entry in self.load_entries(session_id)]

    def append(self, role: str, content: str, timestamp: float = None, session_id=None):
        if role not in ("user", "assistant") or not isinstance(content, str) or not content:
            raise ValueError("Invalid conversation message")
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError("Conversation message is too large")
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Conversation history is closed")
            self._queue.put({"timestamp": _timestamp(timestamp), "role": role, "content": content,
                             "session_id": session_id or self._session_id,
                             "_history_generation": self._generation})

    def flush(self, timeout=None):
        """Wait for all already queued messages; return False after a write failure."""
        with self._state_lock:
            if self._closed:
                return self.last_error is None and not self._writer.is_alive()
            barrier = threading.Event()
            self._queue.put(barrier)
        return barrier.wait(timeout) and self.last_error is None

    def _transaction(self, action):
        if not self.flush():
            raise OSError("Pending conversation messages could not be saved") from self.last_error
        result = []

        def update(loaded):
            document = _document(loaded)
            result.append(action(document))
            return document

        update_json(self.path, update, [], max_bytes=MAX_HISTORY_BYTES)
        return result[0]

    def _snapshot(self):
        """Atomic replacement makes snapshots safe; only legacy migration writes."""
        if not self.flush():
            raise OSError("Pending conversation messages could not be saved") from self.last_error
        loaded = self._read()
        if isinstance(loaded, list):
            return self._transaction(lambda document: document)
        return _document(loaded)

    def list_sessions(self, include_archived=False):
        document = self._snapshot()
        return [_metadata(session) for session in sorted(document["sessions"],
                key=lambda session: session["updated_at"], reverse=True)
                if include_archived or not session["archived"]]

    def create_session(self, title="New conversation", select=True):
        title = _title(title)

        def create(document):
            if len(document["sessions"]) >= MAX_SESSIONS:
                raise ValueError("Conversation limit reached; export and delete an older conversation")
            session = _new_session(title)
            document["sessions"].append(session)
            if select:
                document["active_session_id"] = session["id"]
            return _metadata(session)

        session = self._transaction(create)
        if select:
            self._session_id = session["id"]
        return session

    def select_session(self, session_id):
        def select(document):
            session = _find(document, session_id)
            if session["archived"]:
                raise ValueError("Restore an archived conversation before selecting it")
            document["active_session_id"] = session_id
            return _metadata(session)
        session = self._transaction(select)
        self._session_id = session_id
        return session

    def rename_session(self, session_id, title):
        title = _title(title)

        def rename(document):
            session = _find(document, session_id)
            session["title"] = title
            session["updated_at"] = time.time()
            return _metadata(session)
        return self._transaction(rename)

    def archive_session(self, session_id, archived=True):
        def archive(document):
            session = _find(document, session_id)
            session["archived"] = bool(archived)
            session["updated_at"] = time.time()
            _ensure_active(document)
            return _metadata(session), document["active_session_id"]
        metadata, active = self._transaction(archive)
        if self._session_id == session_id and archived:
            self._session_id = active
        return metadata

    def delete_session(self, session_id):
        def delete(document):
            _find(document, session_id)
            document["sessions"] = [session for session in document["sessions"] if session["id"] != session_id]
            _ensure_active(document)
            return document["active_session_id"]
        active = self._transaction(delete)
        if self._session_id == session_id:
            self._session_id = active

    def clear_session(self, session_id=None):
        """Clear messages only in the selected conversation, retaining its identity."""
        selected = session_id or self._session_id

        def clear(document):
            session = _find(document, selected)
            session["messages"] = []
            session.pop("diagnostic", None)
            session.pop("task", None)
            session["updated_at"] = time.time()
            return _metadata(session)
        return self._transaction(clear)

    def get_diagnostic_state(self, session_id=None):
        """Restore only the selected conversation's guide position."""
        selected = session_id or self._session_id
        session = _find(self._snapshot(), selected)
        state = session.get("diagnostic")
        return _diagnostic_state(state) if state is not None else None

    def set_diagnostic_state(self, state, session_id=None):
        """Persist a guide position, or clear it with None."""
        clean = _diagnostic_state(state) if state is not None else None
        selected = session_id or self._session_id

        def set_state(document):
            session = _find(document, selected)
            if clean is None:
                session.pop("diagnostic", None)
            else:
                session["diagnostic"] = clean
            session["updated_at"] = time.time()
        self._transaction(set_state)

    def get_task_state(self, session_id=None):
        """Get choices for this conversation only; imported chats have none."""
        selected = session_id or self._session_id
        state = _find(self._snapshot(), selected).get("task")
        return validate_task_state(state) if state is not None else None

    def set_task_state(self, state, session_id=None):
        clean = validate_task_state(state) if state is not None else None
        selected = session_id or self._session_id

        def set_state(document):
            session = _find(document, selected)
            if clean is None:
                session.pop("task", None)
            else:
                session["task"] = clean
            session["updated_at"] = time.time()
        self._transaction(set_state)

    def search(self, query, include_archived=False):
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("Search must contain at most 500 characters")
        query = query.strip().casefold()
        if not query:
            return []

        def search_current(document):
            matches = []
            for session in document["sessions"]:
                if session["archived"] and not include_archived:
                    continue
                if query in session["title"].casefold():
                    matches.append({"session_id": session["id"], "title": session["title"],
                                    "message_index": None, "preview": session["title"]})
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        return matches
                for index, entry in enumerate(session["messages"]):
                    offset = entry["content"].casefold().find(query)
                    if offset >= 0:
                        start = max(0, offset - 60)
                        matches.append({"session_id": session["id"], "title": session["title"],
                                        "message_index": index, "role": entry["role"],
                                        "preview": entry["content"][start:start + 240]})
                        if len(matches) >= MAX_SEARCH_RESULTS:
                            return matches
            return matches
        return search_current(self._snapshot())

    def export_session(self, session_id=None, format="markdown"):
        selected = session_id or self._session_id

        session = _find(self._snapshot(), selected)
        if format == "json":
            exported = {key: value for key, value in session.items() if key != "task"}
            return json.dumps({"format": EXPORT_FORMAT, "version": 1, "session": exported},
                              indent=2, ensure_ascii=False)
        if format != "markdown":
            raise ValueError("Export format must be markdown or json")
        lines = ["# " + session["title"].replace("\n", " "), ""]
        for entry in session["messages"]:
            lines.extend(["## " + ("User" if entry["role"] == "user" else "Assistant"), ""])
            if entry.get("timestamp"):
                lines.extend([datetime.fromtimestamp(entry["timestamp"], timezone.utc).isoformat(), ""])
            lines.extend([entry["content"], ""])
        return "\n".join(lines)

    def import_session(self, text, select=True):
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError("Conversation import exceeds the 2 MiB limit")
        try:
            payload = json.loads(text)
        except (ValueError, RecursionError) as error:
            raise ValueError("Invalid conversation JSON") from error
        if (not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT
                or type(payload.get("version")) is not int or payload["version"] != 1):
            raise ValueError("Unsupported conversation export format")
        source = payload.get("session")
        if not isinstance(source, dict) or not isinstance(source.get("messages"), list):
            raise ValueError("Invalid conversation export")
        title = _title(source.get("title"))
        messages = source["messages"]
        if len(messages) > self.max_messages:
            raise ValueError("Conversation import has too many messages")
        clean = _entries(messages)
        if len(clean) != len(messages):
            raise ValueError("Conversation import contains invalid messages")
        for entry in messages:
            if "timestamp" in entry and _timestamp(entry["timestamp"], -1) == -1:
                raise ValueError("Conversation import contains an invalid timestamp")

        def import_current(document):
            if len(document["sessions"]) >= MAX_SESSIONS:
                raise ValueError("Conversation limit reached")
            session = _new_session(title, messages=clean)
            document["sessions"].append(session)
            if select:
                document["active_session_id"] = session["id"]
            return _metadata(session)
        metadata = self._transaction(import_current)
        if select:
            self._session_id = metadata["id"]
        return metadata

    def close(self, timeout=None):
        with self._state_lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)
        try:
            self._writer.join(timeout)
        except RuntimeError:
            pass
        return not self._writer.is_alive() and self.last_error is None

    @staticmethod
    def save_entries(path, entries, max_messages=MAX_HISTORY_MESSAGES, session_id=None):
        """Merge legacy or scoped entries under the shared inter-process lock."""
        incoming = list(entries)

        def merge_messages(old, new):
            seen, merged = set(), []
            for item in _entries(old) + _entries(new):
                key = json.dumps([item["role"], item["content"], item.get("timestamp")], sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
            return merged[-max_messages:]

        def merge(loaded):
            targets = {session_id or item.get("session_id", LEGACY_SESSION_ID)
                       for item in incoming if isinstance(item, dict)}
            if isinstance(loaded, list) and targets <= {LEGACY_SESSION_ID}:
                return merge_messages(loaded, incoming)
            document = _document(loaded)
            groups = {}
            for target in targets:
                messages = [item for item in incoming if isinstance(item, dict)
                            and (session_id or item.get("session_id", LEGACY_SESSION_ID)) == target]
                try:
                    session = _find(document, target)
                except ValueError:
                    if session_id is not None:
                        raise
                    generation = document.get("recovery_generation")
                    recovered = [item for item in messages if generation is not None
                                 and item.get("_history_generation") != generation]
                    if recovered:
                        groups.setdefault(document["active_session_id"], []).extend(recovered)
                    messages = [item for item in messages if item not in recovered]
                    if not messages:
                        continue
                    # A delayed command/stream can finish after another process
                    # deletes its captured session. Do not recreate that session
                    # or retain this batch forever and poison unrelated writes.
                    logging.getLogger(__name__).warning(
                        "Discarding queued messages for deleted conversation %r", target)
                    continue
                groups.setdefault(target, []).extend(messages)
            for target, messages in groups.items():
                session = _find(document, target)
                session["messages"] = merge_messages(session["messages"], messages)
                session["updated_at"] = time.time()
            return document
        return update_json(path, merge, [], max_bytes=MAX_HISTORY_BYTES)

    def _loop(self):
        pending = []
        while True:
            item = self._queue.get()
            barriers, stop = [], False
            while True:
                if item is None:
                    stop = True
                elif isinstance(item, threading.Event):
                    barriers.append(item)
                else:
                    pending.append(item)
                if stop:
                    break
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
            if pending:
                try:
                    saved = self.save_entries(self.path, pending, self.max_messages)
                    self._adopt_recovery(saved)
                    pending = []
                    self.last_error = None
                except Exception as error:
                    self.last_error = error
                    logging.getLogger(__name__).error("Error saving history: %s", error)
            for barrier in barriers:
                barrier.set()
            if stop:
                return
