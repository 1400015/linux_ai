"""The request state machine is the guard every main-loop callback relies on.

These transitions lived inline in `MainWindow` where no test could reach them,
and the flags were owned by nobody: a stale worker could re-arm "Thinking..."
because the id it held and the flags it flipped were maintained in different
methods. `request_state` makes the ownership explicit, so these tests pin the
invariants the window depends on: every begin retires the previous id, a
cancelled event never comes back, and the flags always describe the active
request.

The transitions are functions over the five-name contract, so they are driven
here on a plain `RequestState` and on the `SimpleNamespace` stand-ins the GUI
tests bind them onto — both must behave identically.
"""

import threading
import unittest
from types import SimpleNamespace

from src.request_state import (
    RequestState, abandon, begin_capture, begin_chat, ensure_request_state,
)


class RequestStateTests(unittest.TestCase):
    def setUp(self):
        self.state = RequestState()

    def test_starts_idle_with_no_active_request(self):
        self.assertFalse(self.state.is_loading)
        self.assertFalse(self.state.streaming)
        self.assertEqual(self.state._active_request, 0)
        self.assertFalse(self.state.is_cancelled())

    def test_begin_chat_returns_a_fresh_id_and_event_and_marks_busy(self):
        request_id, event = begin_chat(self.state)
        self.assertEqual(request_id, 1)
        self.assertIs(event, self.state._cancel_event)
        self.assertTrue(self.state.is_loading)
        self.assertTrue(self.state.streaming)
        self.assertTrue(self.state.is_current(request_id))

    def test_each_chat_retires_the_previous_request(self):
        first, first_event = begin_chat(self.state)
        second, second_event = begin_chat(self.state)
        self.assertGreater(second, first)
        # The superseded worker still holds its own (uncancelled) event, but
        # its id is no longer the one the UI trusts.
        self.assertFalse(first_event.is_set())
        self.assertFalse(self.state.is_current(first))
        self.assertTrue(self.state.is_current(second))

    def test_begin_capture_cancels_the_running_chat(self):
        chat_id, chat_event = begin_chat(self.state)
        capture_id, capture_event = begin_capture(self.state)
        self.assertTrue(chat_event.is_set(), "the chat worker must be told to stop")
        self.assertFalse(capture_event.is_set())
        self.assertGreater(capture_id, chat_id)
        self.assertFalse(self.state.is_current(chat_id))
        self.assertTrue(self.state.is_current(capture_id))

    def test_capture_raises_streaming_but_keeps_the_window_busy(self):
        begin_chat(self.state)
        capture_id, _ = begin_capture(self.state)
        self.assertTrue(self.state.is_loading, "the capture is still in flight")
        self.assertFalse(self.state.streaming, "nothing is streaming during a capture")
        self.assertTrue(self.state.is_current(capture_id))

    def test_capture_id_advances_even_after_an_abandon(self):
        """A session change bumps the sequence; the next capture must still win."""
        begin_chat(self.state)
        abandon(self.state)
        previous_id = self.state._active_request
        capture_id, _ = begin_capture(self.state)
        self.assertGreater(capture_id, previous_id)
        self.assertTrue(self.state.is_current(capture_id))

    def test_abandon_invalidates_the_request_and_resets_the_flags(self):
        request_id, event = begin_chat(self.state)
        abandon(self.state)
        self.assertTrue(event.is_set(), "the abandoned worker keeps a cancelled event")
        self.assertFalse(self.state.is_loading)
        self.assertFalse(self.state.streaming)
        self.assertFalse(self.state.is_current(request_id))
        # The fresh event is not cancelled, so the newly selected conversation
        # can render its stored replies.
        self.assertFalse(self.state.is_cancelled())

    def test_a_late_cancel_never_leaks_into_the_next_request(self):
        """The bug the whole machine exists to prevent."""
        first_id, _ = begin_chat(self.state)
        abandon(self.state)
        second_id, second_event = begin_chat(self.state)
        # The first worker's cancel arrives late; the second request is live.
        self.assertFalse(second_event.is_set())
        self.assertTrue(self.state.is_current(second_id))
        self.assertFalse(self.state.is_current(first_id))

    def test_begin_chat_after_a_capture_returns_to_streaming(self):
        begin_chat(self.state)
        begin_capture(self.state)
        request_id, _ = begin_chat(self.state)
        self.assertTrue(self.state.streaming)
        self.assertTrue(self.state.is_loading)
        self.assertTrue(self.state.is_current(request_id))

    def test_is_current_rejects_a_foreign_id(self):
        begin_chat(self.state)
        self.assertFalse(self.state.is_current(self.state._active_request + 99))

    def test_is_cancelled_reflects_the_active_event(self):
        _, event = begin_chat(self.state)
        self.assertFalse(self.state.is_cancelled())
        event.set()
        self.assertTrue(self.state.is_cancelled())


class StandInContractTests(unittest.TestCase):
    """The GUI tests bind these functions onto SimpleNamespace stand-ins."""

    def setUp(self):
        # A stand-in carrying only the five names, exactly as the GUI tests
        # assemble them; the state object is not involved.
        self.window = SimpleNamespace(
            _request_seq=0, _active_request=0, _cancel_event=threading.Event(),
            is_loading=False, streaming=False,
        )

    def test_transitions_drive_plain_attributes(self):
        request_id, event = begin_chat(self.window)
        self.assertIs(event, self.window._cancel_event)
        self.assertEqual(request_id, self.window._active_request)
        self.assertTrue(self.window.is_loading)
        self.assertTrue(self.window.streaming)

        abandon(self.window)
        self.assertTrue(event.is_set())
        self.assertFalse(self.window._cancel_event.is_set())
        self.assertFalse(self.window.is_loading)

    def test_begin_capture_on_a_stand_in_cancels_the_previous_event(self):
        _, chat_event = begin_chat(self.window)
        capture_id, capture_event = begin_capture(self.window)
        self.assertTrue(chat_event.is_set())
        self.assertFalse(capture_event.is_set())
        self.assertEqual(capture_id, self.window._active_request)

    def test_ensure_request_state_materialises_lazily(self):
        holder = SimpleNamespace()
        state = ensure_request_state(holder)
        self.assertIsInstance(state, RequestState)
        self.assertIs(ensure_request_state(holder), state, "created once, then reused")

    def test_ensure_request_state_keeps_an_existing_state(self):
        existing = RequestState()
        holder = SimpleNamespace(_request_state=existing)
        self.assertIs(ensure_request_state(holder), existing)


if __name__ == "__main__":
    unittest.main()
