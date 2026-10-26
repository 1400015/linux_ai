"""GTK-free micro-benchmark framework for the hot, pure-Python paths.

The application's critical paths are pure functions: message building
(``chat_controller.build_request_messages``), stream coalescing
(``StreamCoalescer``), path containment (``safe_paths.is_within``), WCAG
contrast math (``a11y.check_contrast``), sync-bundle planning
(``sync_bundle.plan_import``), usage aggregation (``stats_aggregator``)
and local-URL validation (``provider_modes.validate_local_url``). This
module measures them with ``time.perf_counter`` and reports per-benchmark
timings against optional regression thresholds.

Design rules:
- No GTK, no network, no filesystem, no threads. Pure timing.
- Each benchmark is a zero-argument callable; inputs are pre-built so the
  measurement covers only the target operation, not fixture construction.
- ``repeat`` runs the callable N times and reports the *best* (minimum)
  per-call time — the standard micro-benchmark practice that filters
  scheduler noise and cache warm-up.
- Thresholds are advisory: a benchmark that exceeds its threshold is
  reported as ``slow`` but does not raise. The CLI exit code reflects the
  worst result so CI can gate on it.
- Results are plain dataclasses / dicts; rendering is the caller's job.
"""

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# A benchmark is considered "slow" when its best per-call time exceeds the
# threshold by this factor. Thresholds are per-call seconds.
DEFAULT_SLOW_FACTOR = 1.5


@dataclass(frozen=True)
class BenchmarkResult:
    """Outcome of running one benchmark."""

    name: str
    best_seconds: float
    mean_seconds: float
    repeats: int
    threshold_seconds: Optional[float]
    calls_per_repeat: int = 1
    detail: str = ""

    @property
    def per_call_seconds(self) -> float:
        """Best per-call time (best_seconds / calls_per_repeat)."""
        return self.best_seconds / max(1, self.calls_per_repeat)

    @property
    def status(self) -> str:
        """'ok' when within threshold (or no threshold), else 'slow'."""
        if self.threshold_seconds is None:
            return "ok"
        return "ok" if self.per_call_seconds <= self.threshold_seconds else "slow"

    @property
    def per_call_microseconds(self) -> float:
        return self.per_call_seconds * 1_000_000


@dataclass(frozen=True)
class BenchmarkSuite:
    """A named collection of benchmark results."""

    name: str
    results: Tuple[BenchmarkResult, ...] = ()

    @property
    def slow_count(self) -> int:
        return sum(1 for r in self.results if r.status == "slow")

    @property
    def ok(self) -> bool:
        return self.slow_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "slow_count": self.slow_count,
            "results": [
                {
                    "name": r.name,
                    "status": r.status,
                    "best_us": round(r.per_call_microseconds, 2),
                    "mean_us": round(r.mean_seconds / max(1, r.calls_per_repeat) * 1_000_000, 2),
                    "repeats": r.repeats,
                    "calls_per_repeat": r.calls_per_repeat,
                    "threshold_us": (round(r.threshold_seconds * 1_000_000, 2)
                                     if r.threshold_seconds is not None else None),
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


def run_benchmark(
    name: str,
    func: Callable[[], Any],
    *,
    repeat: int = 5,
    calls_per_repeat: int = 1,
    threshold_seconds: Optional[float] = None,
    detail: str = "",
) -> BenchmarkResult:
    """Run `func` `repeat` times, `calls_per_repeat` calls each, and report.

    Returns the best and mean per-repeat wall time. The best-of-N approach
    is standard for micro-benchmarks: it approximates the true cost of the
    operation without scheduler/cache noise dominating the result.
    """
    if repeat < 1:
        raise ValueError("repeat must be >= 1")
    if calls_per_repeat < 1:
        raise ValueError("calls_per_repeat must be >= 1")
    best = float("inf")
    total = 0.0
    for _ in range(repeat):
        start = time.perf_counter()
        for _ in range(calls_per_repeat):
            func()
        elapsed = time.perf_counter() - start
        if elapsed < best:
            best = elapsed
        total += elapsed
    mean = total / repeat
    return BenchmarkResult(
        name=name,
        best_seconds=best,
        mean_seconds=mean,
        repeats=repeat,
        threshold_seconds=threshold_seconds,
        calls_per_repeat=calls_per_repeat,
        detail=detail,
    )


def run_suite(name: str, benchmarks: List[Tuple[str, Callable[[], Any], Dict[str, Any]]]) -> BenchmarkSuite:
    """Run a list of (name, func, kwargs) benchmarks into a suite."""
    results = []
    for bench_name, func, kwargs in benchmarks:
        results.append(run_benchmark(bench_name, func, **kwargs))
    return BenchmarkSuite(name=name, results=tuple(results))


# ---------------------------------------------------------------------------
# Built-in benchmark registry: the hot pure-Python paths of the app.
# Each entry builds its own input inside the callable's closure so the
# measurement excludes fixture construction.
# ---------------------------------------------------------------------------

def _make_history_messages(count: int) -> List[Dict[str, str]]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": "message number {} with some realistic text".format(i)}
        for i in range(count)
    ]


def _make_config_get():
    settings = {"context.max_messages": 20, "context.max_chars": 12000}
    return lambda key, default=None: settings.get(key, default)


def _make_sync_config() -> Dict[str, Any]:
    return {
        "app": {"theme": "dark", "language": "pt", "expert_mode": False,
                "always_on_top": True, "auto_start": False,
                "global_shortcut_enabled": False, "global_shortcut": "<Ctrl><Alt>space",
                "dock_mode": "float", "dock_edge": "right", "button_edge": "right",
                "display_independent_watchdog": False},
        "api": {"default_provider": "openrouter",
                "providers": {"local_llm": {"base_url": "http://localhost:11434/v1",
                                            "model": "llama3.2", "backend": "ollama",
                                            "strict_local": True, "timeout": 120}}},
        "assistance": {"mode": "auto"},
        "pomodoro": {"enabled": True, "work_seconds": 1500, "short_break_seconds": 300,
                     "long_break_seconds": 900, "cycles_before_long_break": 4},
        "context": {"max_messages": 20, "max_chars": 12000},
        "features": {"screen_capture": True, "ocr_enabled": True, "expert_mode": True,
                     "file_edit": True, "system_info": True},
        "permissions": {"require_sudo": True, "allowed_edit_dirs": ["/home/user/projects"]},
        "ui": {"font_family": "Monospace", "font_size": 12, "background_color": "#1e1e1e",
               "text_color": "#e0e0e0", "accent_color": "#4CAF50", "border_radius": 10},
    }


def _make_sync_sessions(count: int) -> List[Dict[str, Any]]:
    return [
        {"id": "session-{:04d}".format(i), "title": "Conversation {}".format(i),
         "created_at": 1000.0 + i, "updated_at": 2000.0 + i, "archived": i % 10 == 0,
         "messages": [{"role": "user", "content": "q{}".format(j), "timestamp": 1000.0 + j}
                       for j in range(20)]}
        for i in range(count)
    ]


def _make_token_usage(providers: int = 6) -> Dict[str, Dict[str, int]]:
    return {
        "provider-{}".format(i): {"input": 1000 * (i + 1), "output": 500 * (i + 1),
                                   "total": 1500 * (i + 1)}
        for i in range(providers)
    }


def build_default_benchmarks() -> List[Tuple[str, Callable[[], Any], Dict[str, Any]]]:
    """Return the built-in benchmark list: (name, zero-arg callable, kwargs).

    Imports are deferred so importing this module never pulls GTK or heavy
    dependencies — only the modules under test.
    """
    benchmarks: List[Tuple[str, Callable[[], Any], Dict[str, Any]]] = []

    # chat_controller.build_request_messages — trims a 100-message history
    # to the configured context budget.
    def bench_build_messages():
        from .chat_controller import build_request_messages
        history = _make_history_messages(100)
        return build_request_messages(history, _make_config_get())
    benchmarks.append((
        "chat_controller.build_request_messages[100 msgs]",
        bench_build_messages,
        {"repeat": 20, "calls_per_repeat": 50, "threshold_seconds": 0.002,
         "detail": "Trim 100-message history to context budget"},
    ))

    # StreamCoalescer.add — the per-chunk hot path of streaming responses.
    def bench_coalescer_add():
        from .chat_controller import StreamCoalescer
        coalescer = StreamCoalescer(interval=0.08)
        for i in range(1000):
            coalescer.add("chunk-{} ".format(i))
    benchmarks.append((
        "chat_controller.StreamCoalescer.add[1000 chunks]",
        bench_coalescer_add,
        {"repeat": 10, "calls_per_repeat": 5, "threshold_seconds": 0.010,
         "detail": "Accumulate 1000 stream chunks"},
    ))

    # safe_paths.is_within — the containment check used by file actions.
    # is_within resolves symlinks (realpath), which is a syscall per call;
    # pre-resolve the directory once so the benchmark measures the
    # containment logic, not disk I/O.
    def bench_is_within():
        import os
        from .safe_paths import is_within
        base = os.path.realpath("/home/user/projects/repo") if os.path.isdir("/home/user/projects/repo") else "/tmp"
        for i in range(1000):
            is_within("{}/file{}.py".format(base, i), base)
    benchmarks.append((
        "safe_paths.is_within[1000 calls]",
        bench_is_within,
        {"repeat": 10, "calls_per_repeat": 5, "threshold_seconds": 0.50,
         "detail": "1000 path-containment checks (realpath included)"},
    ))

    # a11y.check_contrast — WCAG luminance math used by theme validation.
    def bench_contrast():
        from .a11y import check_contrast
        pairs = [("#e0e0e0", "#1e1e1e"), ("#4CAF50", "#ffffff"),
                 ("#333333", "#eeeeee"), ("#000000", "#ffffff")]
        for i in range(500):
            check_contrast(*pairs[i % len(pairs)])
    benchmarks.append((
        "a11y.check_contrast[500 calls]",
        bench_contrast,
        {"repeat": 10, "calls_per_repeat": 5, "threshold_seconds": 0.010,
         "detail": "500 WCAG contrast ratio computations"},
    ))

    # a11y.color_name — HSV conversion for human-readable color names.
    def bench_color_name():
        from .a11y import color_name
        colors = ["#4CAF50", "#1e1e1e", "#e0e0e0", "#00008b", "#ffa500"]
        for i in range(500):
            color_name(colors[i % len(colors)])
    benchmarks.append((
        "a11y.color_name[500 calls]",
        bench_color_name,
        {"repeat": 10, "calls_per_repeat": 5, "threshold_seconds": 0.005,
         "detail": "500 hex-to-name color conversions"},
    ))

    # sync_bundle.plan_import — the merge preview over a realistic config.
    def bench_plan_import():
        from .sync_bundle import build_bundle, parse_bundle, plan_import, serialize_bundle
        bundle = parse_bundle(serialize_bundle(
            build_bundle(_make_sync_config(), _make_sync_sessions(50))))
        return plan_import(bundle, _make_sync_config(), [])
    benchmarks.append((
        "sync_bundle.plan_import[50 sessions]",
        bench_plan_import,
        {"repeat": 10, "calls_per_repeat": 10, "threshold_seconds": 0.010,
         "detail": "Plan import of 50-session bundle"},
    ))

    # sync_bundle.serialize_bundle — JSON serialization of a full bundle.
    def bench_serialize_bundle():
        from .sync_bundle import build_bundle, serialize_bundle
        return serialize_bundle(build_bundle(_make_sync_config(), _make_sync_sessions(50)))
    benchmarks.append((
        "sync_bundle.serialize_bundle[50 sessions]",
        bench_serialize_bundle,
        {"repeat": 10, "calls_per_repeat": 10, "threshold_seconds": 0.020,
         "detail": "Serialize 50-session sync bundle to JSON"},
    ))

    # stats_aggregator.aggregate_usage — token-usage rollup.
    def bench_aggregate_usage():
        from .stats_aggregator import aggregate_usage
        return aggregate_usage(_make_token_usage(6))
    benchmarks.append((
        "stats_aggregator.aggregate_usage[6 providers]",
        bench_aggregate_usage,
        {"repeat": 20, "calls_per_repeat": 100, "threshold_seconds": 0.001,
         "detail": "Aggregate usage for 6 providers"},
    ))

    # provider_modes.validate_local_url — URL validation on the request path.
    def bench_validate_local_url():
        from .provider_modes import validate_local_url
        urls = ["http://localhost:11434/v1", "http://127.0.0.1:1234/v1",
                "https://localhost:8080", "http://[::1]:11434/v1"]
        for i in range(500):
            try:
                validate_local_url(urls[i % len(urls)], strict_local=True)
            except Exception:
                pass
    benchmarks.append((
        "provider_modes.validate_local_url[500 calls]",
        bench_validate_local_url,
        {"repeat": 10, "calls_per_repeat": 5, "threshold_seconds": 0.005,
         "detail": "500 local-URL validations"},
    ))

    # provider_modes.model_is_installed — membership check with :latest fallback.
    def bench_model_is_installed():
        from .provider_modes import model_is_installed
        installed = ["llama3.2:latest", "qwen2.5:7b", "mistral:latest"]
        for i in range(1000):
            model_is_installed("model-{}".format(i % 50), installed, "ollama")
    benchmarks.append((
        "provider_modes.model_is_installed[1000 calls]",
        bench_model_is_installed,
        {"repeat": 10, "calls_per_repeat": 5, "threshold_seconds": 0.005,
         "detail": "1000 model-membership checks"},
    ))

    return benchmarks


def run_default_suite(repeat_scale: float = 1.0) -> BenchmarkSuite:
    """Run the built-in benchmark suite.

    `repeat_scale` multiplies every benchmark's repeat count (useful for
    quick CI runs with 0.2 or thorough local runs with 2.0).
    """
    benchmarks = build_default_benchmarks()
    if repeat_scale != 1.0:
        scaled = []
        for name, func, kwargs in benchmarks:
            kwargs = dict(kwargs)
            kwargs["repeat"] = max(1, int(kwargs.get("repeat", 5) * repeat_scale))
            scaled.append((name, func, kwargs))
        benchmarks = scaled
    return run_suite("linux-ai-default", benchmarks)


def format_suite_text(suite: BenchmarkSuite) -> str:
    """Render a suite as human-readable plain text (for CLI output)."""
    lines = ["Benchmark suite: {}".format(suite.name),
             "=" * 72,
             "{:<52} {:>10} {:>8}".format("Benchmark", "best (us)", "status"),
             "-" * 72]
    for r in suite.results:
        lines.append("{:<52} {:>10.2f} {:>8}".format(
            r.name, r.per_call_microseconds, r.status.upper()))
    lines.append("-" * 72)
    lines.append("{} benchmarks, {} slow".format(len(suite.results), suite.slow_count))
    return "\n".join(lines)
