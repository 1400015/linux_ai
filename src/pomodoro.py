"""GTK-free Pomodoro focus timer.

The Pomodoro technique alternates focused work intervals with short and
long breaks. This module owns the *state machine* and *duration math*;
the UI (a tray icon, a dialog, or the CLI) drives it with a monotonic
clock and renders the phase changes.

Design rules:
- No GTK, no threading, no I/O except an optional JSON state file.
- Time is always injected (`now` parameter) so tests are deterministic.
- Durations are in seconds; the classic defaults are 25/5/15 minutes.
- State is a plain dict for JSON persistence; ``PomodoroTimer`` wraps it.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_WORK_SECONDS = 25 * 60
DEFAULT_SHORT_BREAK_SECONDS = 5 * 60
DEFAULT_LONG_BREAK_SECONDS = 15 * 60
DEFAULT_CYCLES_BEFORE_LONG_BREAK = 4

MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 4 * 60 * 60

STATE_FILE_NAME = "pomodoro_state.json"


class Phase(Enum):
    """The three phases of a Pomodoro cycle."""

    WORK = "work"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"

    @property
    def is_break(self) -> bool:
        return self is not Phase.WORK


@dataclass(frozen=True)
class PomodoroConfig:
    """Durations and cycle length for a Pomodoro timer."""

    work_seconds: int = DEFAULT_WORK_SECONDS
    short_break_seconds: int = DEFAULT_SHORT_BREAK_SECONDS
    long_break_seconds: int = DEFAULT_LONG_BREAK_SECONDS
    cycles_before_long_break: int = DEFAULT_CYCLES_BEFORE_LONG_BREAK

    def __post_init__(self) -> None:
        for name in ("work_seconds", "short_break_seconds", "long_break_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int) or not (MIN_DURATION_SECONDS <= value <= MAX_DURATION_SECONDS):
                raise ValueError(
                    "{name} must be an int in [{lo}, {hi}] seconds, got {value!r}".format(
                        name=name, lo=MIN_DURATION_SECONDS, hi=MAX_DURATION_SECONDS, value=value
                    )
                )
        if not isinstance(self.cycles_before_long_break, int) or not (1 <= self.cycles_before_long_break <= 20):
            raise ValueError(
                "cycles_before_long_break must be an int in [1, 20], got {!r}".format(
                    self.cycles_before_long_break
                )
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PomodoroConfig":
        """Build from a config dict, falling back to defaults per key."""
        return cls(
            work_seconds=int(data.get("work_seconds", DEFAULT_WORK_SECONDS)),
            short_break_seconds=int(data.get("short_break_seconds", DEFAULT_SHORT_BREAK_SECONDS)),
            long_break_seconds=int(data.get("long_break_seconds", DEFAULT_LONG_BREAK_SECONDS)),
            cycles_before_long_break=int(data.get("cycles_before_long_break", DEFAULT_CYCLES_BEFORE_LONG_BREAK)),
        )

    def duration_for(self, phase: Phase) -> int:
        """Total seconds for `phase` under this configuration."""
        if phase is Phase.WORK:
            return self.work_seconds
        if phase is Phase.SHORT_BREAK:
            return self.short_break_seconds
        return self.long_break_seconds


@dataclass
class PomodoroTimer:
    """Stateful Pomodoro timer driven by an injected clock.

    The timer does not run its own thread. Callers advance it with
    :meth:`remaining` / :meth:`tick` and receive phase transitions via
    the ``on_phase_change`` callback (or by polling :meth:`poll`).
    """

    config: PomodoroConfig = field(default_factory=PomodoroConfig)
    phase: Phase = Phase.WORK
    cycle_count: int = 0
    started_at: Optional[float] = None
    elapsed_in_phase: float = 0.0
    running: bool = False
    completed_work_sessions: int = 0
    on_phase_change: Optional[Callable[[Phase, Phase], None]] = field(default=None, repr=False)

    def start(self, now: Optional[float] = None) -> Phase:
        """Start (or resume) the current phase. Returns the active phase."""
        if now is None:
            now = time.monotonic()
        if not self.running:
            self.started_at = now
            self.running = True
        return self.phase

    def pause(self, now: Optional[float] = None) -> float:
        """Pause the timer; returns the accumulated seconds in this phase."""
        if self.running:
            if now is None:
                now = time.monotonic()
            started = self.started_at if self.started_at is not None else now
            self.elapsed_in_phase += now - started
            self.running = False
            self.started_at = None
        return self.elapsed_in_phase

    def reset(self) -> None:
        """Reset to the beginning of a fresh WORK phase."""
        self.phase = Phase.WORK
        self.cycle_count = 0
        self.started_at = None
        self.elapsed_in_phase = 0.0
        self.running = False
        self.completed_work_sessions = 0

    def skip(self, now: Optional[float] = None) -> Phase:
        """Skip the current phase and advance to the next one."""
        return self._advance(now)

    def remaining(self, now: Optional[float] = None) -> float:
        """Seconds left in the current phase (0 when the phase is over)."""
        total = self.config.duration_for(self.phase)
        return max(0.0, total - self.elapsed_in_phase - self._live_elapsed(now))

    def progress(self, now: Optional[float] = None) -> float:
        """Fraction of the current phase completed, in [0.0, 1.0]."""
        total = self.config.duration_for(self.phase)
        if total <= 0:
            return 1.0
        used = self.elapsed_in_phase + self._live_elapsed(now)
        return min(1.0, used / total)

    def tick(self, now: Optional[float] = None) -> List[Phase]:
        """Advance past any completed phases; return the transitions.

        Returns a list of (old_phase -> new_phase) pairs. Each transition
        fires the ``on_phase_change`` callback exactly once.
        """
        transitions: List[Phase] = []
        while self.remaining(now) <= 0.0:
            old = self.phase
            self._advance(now)
            transitions.append(old)
        return transitions

    def poll(self, now: Optional[float] = None) -> Optional[Phase]:
        """Check for a phase transition; return the new phase or None."""
        transitions = self.tick(now)
        return self.phase if transitions else None

    def _live_elapsed(self, now: Optional[float]) -> float:
        if not self.running or self.started_at is None:
            return 0.0
        if now is None:
            now = time.monotonic()
        return max(0.0, now - self.started_at)

    def _advance(self, now: Optional[float]) -> Phase:
        """Move to the next phase, consuming the elapsed overflow."""
        if now is None:
            now = time.monotonic()
        overflow = self.elapsed_in_phase + self._live_elapsed(now) - self.config.duration_for(self.phase)
        old_phase = self.phase
        if self.phase is Phase.WORK:
            self.completed_work_sessions += 1
            self.cycle_count += 1
            if self.cycle_count % self.config.cycles_before_long_break == 0:
                self.phase = Phase.LONG_BREAK
            else:
                self.phase = Phase.SHORT_BREAK
        else:
            self.phase = Phase.WORK
        self.elapsed_in_phase = max(0.0, overflow)
        self.started_at = now if self.running else None
        if self.on_phase_change is not None:
            try:
                self.on_phase_change(old_phase, self.phase)
            except Exception as exc:
                logger.exception("Pomodoro on_phase_change callback failed: %s", exc)
        return self.phase

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the timer state for persistence."""
        return {
            "phase": self.phase.value,
            "cycle_count": self.cycle_count,
            "elapsed_in_phase": self.elapsed_in_phase,
            "running": self.running,
            "completed_work_sessions": self.completed_work_sessions,
            "config": {
                "work_seconds": self.config.work_seconds,
                "short_break_seconds": self.config.short_break_seconds,
                "long_break_seconds": self.config.long_break_seconds,
                "cycles_before_long_break": self.config.cycles_before_long_break,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PomodoroTimer":
        """Restore a timer from :meth:`to_dict` output."""
        config = PomodoroConfig.from_dict(data.get("config", {}))
        try:
            phase = Phase(data.get("phase", Phase.WORK.value))
        except ValueError:
            phase = Phase.WORK
        return cls(
            config=config,
            phase=phase,
            cycle_count=int(data.get("cycle_count", 0)),
            elapsed_in_phase=float(data.get("elapsed_in_phase", 0.0)),
            running=bool(data.get("running", False)),
            completed_work_sessions=int(data.get("completed_work_sessions", 0)),
        )


def save_state(timer: PomodoroTimer, path: Union[str, Path]) -> None:
    """Persist timer state as JSON (atomic write via temp file + rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(timer.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(target)


def load_state(path: Union[str, Path]) -> Optional[PomodoroTimer]:
    """Load timer state from JSON; None when missing or corrupt."""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return PomodoroTimer.from_dict(data)
    except (ValueError, TypeError) as exc:
        logger.warning("Ignoring corrupt pomodoro state: %s", exc)
        return None


def format_duration(seconds: float) -> str:
    """Format seconds as M:SS for display (e.g. 1500 -> '25:00')."""
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return "{m}:{s:02d}".format(m=minutes, s=secs)


def format_phase_label(phase: Phase, lang: str = "en") -> str:
    """Localized one-word label for a phase."""
    labels = {
        "en": {Phase.WORK: "Focus", Phase.SHORT_BREAK: "Short break", Phase.LONG_BREAK: "Long break"},
        "pt": {Phase.WORK: "Foco", Phase.SHORT_BREAK: "Pausa curta", Phase.LONG_BREAK: "Pausa longa"},
    }
    catalog = labels.get(lang) or labels["en"]
    return catalog[phase]


def next_break_is_long(timer: PomodoroTimer) -> bool:
    """True when completing the current WORK phase triggers a LONG break."""
    return (timer.cycle_count + 1) % timer.config.cycles_before_long_break == 0


def session_summary(timer: PomodoroTimer) -> Dict[str, Any]:
    """One-line summary of the timer's progress for UI or CLI display."""
    return {
        "phase": timer.phase.value,
        "cycle": timer.cycle_count,
        "completed_work_sessions": timer.completed_work_sessions,
        "running": timer.running,
        "elapsed_in_phase": timer.elapsed_in_phase,
        "total_config": timer.config.duration_for(timer.phase),
    }
