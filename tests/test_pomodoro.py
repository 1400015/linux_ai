"""Tests for src.pomodoro — GTK-free Pomodoro focus timer."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.pomodoro import (
    DEFAULT_CYCLES_BEFORE_LONG_BREAK,
    DEFAULT_LONG_BREAK_SECONDS,
    DEFAULT_SHORT_BREAK_SECONDS,
    DEFAULT_WORK_SECONDS,
    MAX_DURATION_SECONDS,
    MIN_DURATION_SECONDS,
    Phase,
    PomodoroConfig,
    PomodoroTimer,
    format_duration,
    format_phase_label,
    load_state,
    next_break_is_long,
    save_state,
    session_summary,
)


class TestPomodoroConfig(unittest.TestCase):
    def test_defaults(self):
        config = PomodoroConfig()
        self.assertEqual(config.work_seconds, DEFAULT_WORK_SECONDS)
        self.assertEqual(config.short_break_seconds, DEFAULT_SHORT_BREAK_SECONDS)
        self.assertEqual(config.long_break_seconds, DEFAULT_LONG_BREAK_SECONDS)
        self.assertEqual(config.cycles_before_long_break, DEFAULT_CYCLES_BEFORE_LONG_BREAK)

    def test_duration_for_each_phase(self):
        config = PomodoroConfig(work_seconds=1500, short_break_seconds=300, long_break_seconds=900)
        self.assertEqual(config.duration_for(Phase.WORK), 1500)
        self.assertEqual(config.duration_for(Phase.SHORT_BREAK), 300)
        self.assertEqual(config.duration_for(Phase.LONG_BREAK), 900)

    def test_rejects_too_short(self):
        with self.assertRaises(ValueError):
            PomodoroConfig(work_seconds=MIN_DURATION_SECONDS - 1)

    def test_rejects_too_long(self):
        with self.assertRaises(ValueError):
            PomodoroConfig(short_break_seconds=MAX_DURATION_SECONDS + 1)

    def test_accepts_boundaries(self):
        config = PomodoroConfig(
            work_seconds=MIN_DURATION_SECONDS,
            short_break_seconds=MAX_DURATION_SECONDS,
        )
        self.assertEqual(config.work_seconds, MIN_DURATION_SECONDS)

    def test_rejects_bad_cycle_count(self):
        with self.assertRaises(ValueError):
            PomodoroConfig(cycles_before_long_break=0)
        with self.assertRaises(ValueError):
            PomodoroConfig(cycles_before_long_break=21)

    def test_from_dict_uses_defaults_for_missing(self):
        config = PomodoroConfig.from_dict({})
        self.assertEqual(config.work_seconds, DEFAULT_WORK_SECONDS)

    def test_from_dict_overrides(self):
        config = PomodoroConfig.from_dict({"work_seconds": 1200, "cycles_before_long_break": 2})
        self.assertEqual(config.work_seconds, 1200)
        self.assertEqual(config.cycles_before_long_break, 2)

    def test_from_dict_rejects_invalid(self):
        with self.assertRaises(ValueError):
            PomodoroConfig.from_dict({"work_seconds": 10})


class TestPhaseEnum(unittest.TestCase):
    def test_is_break(self):
        self.assertFalse(Phase.WORK.is_break)
        self.assertTrue(Phase.SHORT_BREAK.is_break)
        self.assertTrue(Phase.LONG_BREAK.is_break)

    def test_values(self):
        self.assertEqual(Phase.WORK.value, "work")
        self.assertEqual(Phase.SHORT_BREAK.value, "short_break")
        self.assertEqual(Phase.LONG_BREAK.value, "long_break")


class TestTimerLifecycle(unittest.TestCase):
    def test_starts_in_work_phase(self):
        timer = PomodoroTimer()
        self.assertEqual(timer.phase, Phase.WORK)
        self.assertEqual(timer.cycle_count, 0)
        self.assertFalse(timer.running)

    def test_start_sets_running(self):
        timer = PomodoroTimer()
        phase = timer.start(now=100.0)
        self.assertTrue(timer.running)
        self.assertEqual(phase, Phase.WORK)
        self.assertEqual(timer.started_at, 100.0)

    def test_remaining_counts_down(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        self.assertAlmostEqual(timer.remaining(now=0.0), 1500.0)
        self.assertAlmostEqual(timer.remaining(now=600.0), 900.0)
        self.assertAlmostEqual(timer.remaining(now=1500.0), 0.0)
        self.assertAlmostEqual(timer.remaining(now=2000.0), 0.0)

    def test_progress(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1000))
        timer.start(now=0.0)
        self.assertAlmostEqual(timer.progress(now=0.0), 0.0)
        self.assertAlmostEqual(timer.progress(now=500.0), 0.5)
        self.assertAlmostEqual(timer.progress(now=1500.0), 1.0)

    def test_pause_accumulates_elapsed(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        elapsed = timer.pause(now=600.0)
        self.assertFalse(timer.running)
        self.assertAlmostEqual(elapsed, 600.0)
        self.assertAlmostEqual(timer.remaining(now=600.0), 900.0)

    def test_resume_after_pause(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        timer.pause(now=600.0)
        timer.start(now=1000.0)
        self.assertAlmostEqual(timer.remaining(now=1000.0), 900.0)
        self.assertAlmostEqual(timer.remaining(now=1600.0), 300.0)

    def test_pause_when_not_running_is_noop(self):
        timer = PomodoroTimer()
        self.assertEqual(timer.pause(now=100.0), 0.0)

    def test_reset(self):
        timer = PomodoroTimer()
        timer.start(now=0.0)
        timer.skip(now=2000.0)
        timer.reset()
        self.assertEqual(timer.phase, Phase.WORK)
        self.assertEqual(timer.cycle_count, 0)
        self.assertFalse(timer.running)
        self.assertEqual(timer.completed_work_sessions, 0)


class TestPhaseTransitions(unittest.TestCase):
    def test_work_to_short_break(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500, short_break_seconds=300))
        timer.start(now=0.0)
        new_phase = timer.poll(now=1500.0)
        self.assertEqual(new_phase, Phase.SHORT_BREAK)
        self.assertEqual(timer.phase, Phase.SHORT_BREAK)
        self.assertEqual(timer.cycle_count, 1)
        self.assertEqual(timer.completed_work_sessions, 1)

    def test_short_break_to_work(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500, short_break_seconds=300))
        timer.start(now=0.0)
        timer.poll(now=1500.0)
        new_phase = timer.poll(now=1800.0)
        self.assertEqual(new_phase, Phase.WORK)
        self.assertEqual(timer.phase, Phase.WORK)

    def test_long_break_after_n_cycles(self):
        timer = PomodoroTimer(config=PomodoroConfig(
            work_seconds=1500, short_break_seconds=300, long_break_seconds=900,
            cycles_before_long_break=2,
        ))
        timer.start(now=0.0)
        timer.poll(now=1500.0)
        self.assertEqual(timer.phase, Phase.SHORT_BREAK)
        timer.poll(now=1800.0)
        self.assertEqual(timer.phase, Phase.WORK)
        timer.poll(now=3300.0)
        self.assertEqual(timer.phase, Phase.LONG_BREAK)
        self.assertEqual(timer.cycle_count, 2)

    def test_tick_returns_transitions(self):
        timer = PomodoroTimer(config=PomodoroConfig(
            work_seconds=100, short_break_seconds=100, long_break_seconds=100,
        ))
        timer.start(now=0.0)
        transitions = timer.tick(now=250.0)
        self.assertEqual(transitions, [Phase.WORK, Phase.SHORT_BREAK])
        self.assertEqual(timer.phase, Phase.WORK)
        self.assertEqual(timer.cycle_count, 1)
        self.assertEqual(timer.completed_work_sessions, 1)

    def test_skip_advances_phase(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        new_phase = timer.skip(now=100.0)
        self.assertEqual(new_phase, Phase.SHORT_BREAK)
        self.assertEqual(timer.completed_work_sessions, 1)

    def test_callback_fires_on_transition(self):
        callback = Mock()
        timer = PomodoroTimer(
            config=PomodoroConfig(work_seconds=1500, short_break_seconds=300),
            on_phase_change=callback,
        )
        timer.start(now=0.0)
        timer.poll(now=1500.0)
        callback.assert_called_once_with(Phase.WORK, Phase.SHORT_BREAK)

    def test_callback_exception_is_swallowed(self):
        def bad_callback(old, new):
            raise RuntimeError("boom")

        timer = PomodoroTimer(
            config=PomodoroConfig(work_seconds=100),
            on_phase_change=bad_callback,
        )
        timer.start(now=0.0)
        new_phase = timer.poll(now=100.0)
        self.assertEqual(new_phase, Phase.SHORT_BREAK)

    def test_overflow_carries_into_next_phase(self):
        timer = PomodoroTimer(config=PomodoroConfig(
            work_seconds=100, short_break_seconds=100,
        ))
        timer.start(now=0.0)
        timer.poll(now=150.0)
        self.assertEqual(timer.phase, Phase.SHORT_BREAK)
        self.assertAlmostEqual(timer.elapsed_in_phase, 50.0)
        self.assertAlmostEqual(timer.remaining(now=150.0), 50.0)

    def test_poll_returns_none_when_no_transition(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        self.assertIsNone(timer.poll(now=100.0))


class TestPersistence(unittest.TestCase):
    def test_round_trip(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500, cycles_before_long_break=3))
        timer.start(now=0.0)
        timer.poll(now=1500.0)
        timer.pause(now=1600.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(timer, path)
            restored = load_state(path)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.phase, Phase.SHORT_BREAK)
        self.assertEqual(restored.cycle_count, 1)
        self.assertEqual(restored.config.work_seconds, 1500)
        self.assertEqual(restored.config.cycles_before_long_break, 3)
        self.assertFalse(restored.running)
        self.assertAlmostEqual(restored.elapsed_in_phase, 100.0)

    def test_load_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_state(Path(tmp) / "nope.json"))

    def test_load_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not json{{{", encoding="utf-8")
            self.assertIsNone(load_state(path))

    def test_load_non_dict_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertIsNone(load_state(path))

    def test_load_invalid_phase_falls_back_to_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"phase": "bogus"}), encoding="utf-8")
            restored = load_state(path)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.phase, Phase.WORK)

    def test_save_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "state.json"
            save_state(PomodoroTimer(), path)
            self.assertTrue(path.exists())

    def test_to_dict_from_dict_round_trip(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1200))
        timer.start(now=0.0)
        timer.poll(now=1200.0)
        data = timer.to_dict()
        restored = PomodoroTimer.from_dict(data)
        self.assertEqual(restored.phase, timer.phase)
        self.assertEqual(restored.cycle_count, timer.cycle_count)
        self.assertEqual(restored.config.work_seconds, 1200)


class TestFormatting(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0:00")
        self.assertEqual(format_duration(59), "0:59")
        self.assertEqual(format_duration(60), "1:00")
        self.assertEqual(format_duration(1500), "25:00")
        self.assertEqual(format_duration(3600), "60:00")
        self.assertEqual(format_duration(-5), "0:00")

    def test_format_duration_rounds(self):
        self.assertEqual(format_duration(59.6), "1:00")
        self.assertEqual(format_duration(59.4), "0:59")

    def test_format_phase_label_en(self):
        self.assertEqual(format_phase_label(Phase.WORK, "en"), "Focus")
        self.assertEqual(format_phase_label(Phase.SHORT_BREAK, "en"), "Short break")
        self.assertEqual(format_phase_label(Phase.LONG_BREAK, "en"), "Long break")

    def test_format_phase_label_pt(self):
        self.assertEqual(format_phase_label(Phase.WORK, "pt"), "Foco")
        self.assertEqual(format_phase_label(Phase.SHORT_BREAK, "pt"), "Pausa curta")
        self.assertEqual(format_phase_label(Phase.LONG_BREAK, "pt"), "Pausa longa")

    def test_format_phase_label_unknown_lang_falls_back(self):
        self.assertEqual(format_phase_label(Phase.WORK, "xx"), "Focus")


class TestHelpers(unittest.TestCase):
    def test_next_break_is_long(self):
        timer = PomodoroTimer(config=PomodoroConfig(cycles_before_long_break=4))
        self.assertFalse(next_break_is_long(timer))
        timer.cycle_count = 3
        self.assertTrue(next_break_is_long(timer))
        timer.cycle_count = 4
        self.assertFalse(next_break_is_long(timer))

    def test_session_summary(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        timer.poll(now=1500.0)
        summary = session_summary(timer)
        self.assertEqual(summary["phase"], "short_break")
        self.assertEqual(summary["cycle"], 1)
        self.assertEqual(summary["completed_work_sessions"], 1)
        self.assertTrue(summary["running"])
        self.assertEqual(summary["total_config"], timer.config.short_break_seconds)


class TestEdgeCases(unittest.TestCase):
    def test_zero_elapsed_when_not_started(self):
        timer = PomodoroTimer()
        self.assertEqual(timer.remaining(now=1000.0), timer.config.work_seconds)

    def test_double_start_does_not_reset_clock(self):
        timer = PomodoroTimer(config=PomodoroConfig(work_seconds=1500))
        timer.start(now=0.0)
        timer.start(now=100.0)
        self.assertEqual(timer.started_at, 0.0)
        self.assertAlmostEqual(timer.remaining(now=600.0), 900.0)

    def test_many_cycles(self):
        timer = PomodoroTimer(config=PomodoroConfig(
            work_seconds=100, short_break_seconds=100, long_break_seconds=100,
            cycles_before_long_break=2,
        ))
        timer.start(now=0.0)
        now = 0.0
        for _ in range(10):
            now += 100
            timer.poll(now=now)
        self.assertGreaterEqual(timer.cycle_count, 5)
        self.assertGreaterEqual(timer.completed_work_sessions, 5)


if __name__ == "__main__":
    unittest.main()
