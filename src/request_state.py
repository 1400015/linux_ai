"""Per-request lifecycle: ids, cancellation, and the in-flight flags.

Every send and every capture allocates a fresh ``(id, event)`` pair, and every
main-loop callback asks ``is_current`` before it touches the UI. The five
values used to live spread across the window's methods, which is how a stale
worker could re-arm the "Thinking..." placeholder: the flags it flipped were
not owned by the id it was handed.

Chat and capture share one id sequence on purpose — a capture allocates from
the same counters, so a chat callback that outlives its request can never
address the capture that replaced it.

The transitions below are functions over the five-name contract
(``_request_seq``, ``_active_request``, ``_cancel_event``, ``is_loading``,
``streaming``) rather than methods on a container, so they work both on a real
window — where those names are properties backed by :class:`RequestState` —
and on the ``SimpleNamespace`` stand-ins the tests bind them onto.
"""

import threading


class RequestState:
    """Storage for the five per-request values (GTK-free, testable)."""

    def __init__(self):
        self._request_seq = 0
        self._active_request = 0
        self._cancel_event = threading.Event()
        self.is_loading = False
        self.streaming = False

    def is_current(self, request_id):
        """True when `request_id` is the request the UI currently trusts."""
        return request_id == self._active_request

    def is_cancelled(self):
        return self._cancel_event.is_set()


def ensure_request_state(obj):
    """Return ``obj._request_state``, materialising it on first access.

    Tests assemble windows via `__new__` (skipping `__init__`) and as
    ``SimpleNamespace`` stand-ins, so the storage has to appear lazily on
    whatever object the transition functions receive.
    """
    state = getattr(obj, '_request_state', None)
    if state is None:
        state = RequestState()
        obj._request_state = state
    return state


def begin_chat(window):
    """Allocate the next chat request and make it the active one.

    Returns ``(request_id, cancel_event)`` for the worker thread.
    """
    window._request_seq += 1
    window._active_request = window._request_seq
    window._cancel_event = threading.Event()
    window.is_loading = True
    window.streaming = True
    return window._active_request, window._cancel_event


def begin_capture(window):
    """Allocate a capture request, cancelling whichever request ran before.

    A capture is its own request: reusing the previous event would discard the
    new capture, and reusing its id would let the old request's callbacks
    alter it. The id advances past both counters so a chat callback that is
    still draining cannot address the capture that replaced it.
    """
    previous_event = getattr(window, '_cancel_event', None)
    if previous_event is not None:
        previous_event.set()
    window._request_seq = max(getattr(window, '_request_seq', 0) or 0,
                              getattr(window, '_active_request', 0) or 0) + 1
    window._active_request = window._request_seq
    window._cancel_event = threading.Event()
    window.is_loading = True
    window.streaming = False
    return window._active_request, window._cancel_event


def abandon(window):
    """Invalidate the active request (session change / history recovery).

    The abandoned worker keeps the cancelled event it was given; the fresh
    event lets the newly selected conversation render its stored replies
    without a late callback dropping them.
    """
    window._cancel_event.set()
    window._request_seq += 1
    window._active_request = window._request_seq
    window._cancel_event = threading.Event()
    window.is_loading = False
    window.streaming = False
