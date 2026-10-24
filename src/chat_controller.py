"""GTK-free chat orchestration: message building, streaming coalescing, routing.

Extracted from ``main_window.py`` so the request-construction and
stream-coalescing logic can be unit-tested headless. The window keeps the
GTK callbacks; this module owns the data flow:

- :func:`build_request_messages` — trim the in-memory conversation to the
  configured message/character budget.
- :func:`build_full_history` — prepend the system context message and
  optionally append document context.
- :class:`StreamCoalescer` — accumulate streamed chunks and flush at most
  ~12×/second, so thousands of chunks do not each schedule a GLib idle.
- :class:`ChatRoute` — the routing decision (provider ready, model, images).
- :func:`select_route` — decide whether the request goes to the provider or
  the offline assistant, including the empty-override credential warning.

Nothing here imports GTK; the window passes plain callables for the
side-effecting parts (idle_add, config reads, offline handle).
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .assistant_context import build_system_message
from .document_context import with_document_context
from .history_store import MAX_MESSAGE_CHARS
from .theme_style import MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES, STREAM_FLUSH_INTERVAL, safe_number

logger = logging.getLogger(__name__)

Message = Dict[str, str]


def build_request_messages(
    conversation_history: Sequence[Dict[str, Any]],
    config_get: Callable[[str, Any], Any],
) -> List[Message]:
    """Build the provider message list from the in-memory conversation.

    Removes ``timestamp`` (only present in on-disk history) and trims to the
    configured ``context.max_messages`` / ``context.max_chars`` budget.
    """
    messages: List[Message] = [
        {"role": m["role"], "content": m["content"]}
        for m in conversation_history
        if m.get("role") and m.get("content")
    ]

    max_messages = safe_number(
        config_get("context.max_messages", None), MAX_CONTEXT_MESSAGES,
        int, minimum=2, maximum=500,
    )
    max_chars = safe_number(
        config_get("context.max_chars", None), MAX_CONTEXT_CHARS,
        int, minimum=1000, maximum=400000,
    )

    if len(messages) > max_messages:
        logger.debug(
            "Context truncated from %s to %s messages", len(messages), max_messages
        )
        messages = messages[-max_messages:]

    total = sum(len(m["content"]) for m in messages)
    while len(messages) > 1 and total > max_chars:
        removed = messages.pop(0)
        total -= len(removed["content"])
        logger.debug("Context truncated by character budget")

    return messages


def build_context_message(
    expert_mode: bool,
    distro: Any,
    conversation_history: Sequence[Dict[str, Any]],
    language: str,
    system_context: Any = None,
) -> Optional[Message]:
    """Build the system context message, or ``None`` when there is no query."""
    query = next(
        (item["content"] for item in reversed(conversation_history)
         if item.get("role") == "user"),
        "",
    )
    return build_system_message(expert_mode, distro, query, language, system_context)


def build_full_history(
    context: Optional[Message],
    messages: Sequence[Message],
    document_context: str = "",
) -> List[Message]:
    """Assemble the final history: system context + messages + document context."""
    full: List[Message] = ([context] if context else []) + list(messages)
    if document_context:
        full = with_document_context(full, document_context)
    return full


@dataclass
class ChatRoute:
    """The routing decision for one chat turn."""

    provider: Optional[str] = None
    ready: bool = False
    model: Optional[str] = None
    has_images: bool = False
    has_documents: bool = False

    @property
    def goes_offline(self) -> bool:
        return not self.ready

    @property
    def offline_blocked(self) -> Optional[str]:
        """Return a reason when offline is impossible, else ``None``."""
        if not self.ready and self.has_images:
            return "image_offline"
        if not self.ready and self.has_documents:
            return "document_offline"
        return None


def select_route(
    ai_client: Any,
    provider_ready: Optional[bool] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    has_images: bool = False,
    has_documents: bool = False,
) -> ChatRoute:
    """Decide the route for a chat turn.

    When ``provider_ready`` is ``None`` the client is queried. The result
    captures whether the provider is ready, which provider/model to use,
    and whether the request carries images or documents (both of which
    block the offline fallback).
    """
    if provider_ready is None:
        ready = bool(ai_client.provider_ready())
    else:
        ready = bool(provider_ready)
    return ChatRoute(
        provider=provider,
        ready=ready,
        model=model,
        has_images=has_images,
        has_documents=has_documents,
    )


@dataclass
class CredentialWarning:
    """A warning about a stored key disabled by an empty environment override."""

    provider: str
    env_var: str
    can_remove: bool


def check_credential_shadow(
    config: Any,
    provider: Optional[str],
) -> Optional[CredentialWarning]:
    """Return a warning when a stored key is shadowed by an empty env var."""
    if not provider:
        return None
    try:
        if not config.stored_key_shadowed_by_empty_env(provider):
            return None
        env_var = config.api_key_env_var(provider)
        can_remove = bool(config.can_remove_empty_api_key_override(provider))
        return CredentialWarning(provider=provider, env_var=env_var, can_remove=can_remove)
    except Exception:
        return None


class StreamCoalescer:
    """Accumulate streamed chunks and flush at a bounded rate.

    The window schedules one GLib idle per flush (not per chunk), keeping
    the UI responsive even for very fast streams. Call :meth:`add` for each
    chunk and :meth:`flush` to force a final delivery; :meth:`pending`
    returns the unflushed text.
    """

    def __init__(self, interval: float = STREAM_FLUSH_INTERVAL) -> None:
        self._interval = interval
        self._pending: List[str] = []
        self._last_flush = time.monotonic()
        self._total = ""

    @property
    def total(self) -> str:
        """All text accumulated so far (flushed + pending)."""
        return self._total + "".join(self._pending)

    @property
    def pending(self) -> str:
        """Unflushed text (since the last flush)."""
        return "".join(self._pending)

    @pending.setter
    def pending(self, value: str) -> None:
        """Replace the unflushed buffer (used when discarding partial output)."""
        self._pending = [value] if value else []

    def add(self, chunk: str, max_chars: int = MAX_MESSAGE_CHARS) -> Optional[str]:
        """Add a chunk; return the text to flush now, or ``None`` to wait.

        Raises :class:`ValueError` when the accumulated total exceeds
        ``max_chars``.
        """
        if len(self._total) + len(self.pending) + len(chunk) > max_chars:
            raise ValueError("response_exceeds_limit")
        self._pending.append(chunk)
        now = time.monotonic()
        if now - self._last_flush >= self._interval:
            return self.flush()
        return None

    def flush(self) -> str:
        """Return and clear the pending buffer."""
        text = "".join(self._pending)
        self._total += text
        self._pending = []
        self._last_flush = time.monotonic()
        return text

    def clear(self) -> None:
        """Discard pending text without flushing (e.g. on offline fallback)."""
        self._pending = []

    def reset(self) -> None:
        """Discard all accumulated state."""
        self._pending = []
        self._total = ""
        self._last_flush = time.monotonic()


@dataclass
class ProcessResult:
    """The outcome of processing one chat turn (worker side, GTK-free)."""

    response_text: str = ""
    cancelled: bool = False
    offline_reply: Any = None
    action: Any = None
    pending_chunks: str = ""
    error: Optional[Exception] = None
    early_return: bool = False
    early_text: str = ""


def should_use_offline(route: ChatRoute, response_text: str, cancelled: bool) -> bool:
    """Decide whether to fall back to the offline assistant.

    Offline is used when the provider is not ready, when the provider failed
    with a structured error, or when the provider returned no text. It is
    never used for image requests or when the user cancelled.
    """
    if cancelled:
        return False
    if route.has_images:
        return False
    if route.goes_offline:
        return True
    return not response_text
