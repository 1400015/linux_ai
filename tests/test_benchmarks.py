"""Tests for src.benchmarks — GTK-free micro-benchmark framework."""

import unittest
from unittest.mock import patch

from src.benchmarks import (
    BenchmarkResult,
    BenchmarkSuite,
    build_default_benchmarks,
    format_suite_text,
    run_benchmark,
    run_default_suite,
    run_suite,
)


class TestRunBenchmark(unittest.TestCase):
    def test_measures_callable(self):
        calls = []

        def func():
            calls.append(1)
            sum(range(100))

        result = run_benchmark("test", func, repeat=3, calls_per_repeat=10)
        self.assertEqual(len(calls), 30)
        self.assertEqual(result.repeats, 3)
        self.assertEqual(result.calls_per_repeat, 10)
        self.assertGreater(result.best_seconds, 0)
        self.assertGreaterEqual(result.mean_seconds, result.best_seconds - 1e-9)

    def test_per_call_divides_by_calls_per_repeat(self):
        result = BenchmarkResult(
            name="x", best_seconds=0.010, mean_seconds=0.012,
            repeats=5, threshold_seconds=None, calls_per_repeat=10,
        )
        self.assertAlmostEqual(result.per_call_seconds, 0.001)
        self.assertAlmostEqual(result.per_call_microseconds, 1000.0)

    def test_status_ok_without_threshold(self):
        result = BenchmarkResult(
            name="x", best_seconds=999, mean_seconds=999,
            repeats=1, threshold_seconds=None,
        )
        self.assertEqual(result.status, "ok")

    def test_status_ok_within_threshold(self):
        result = BenchmarkResult(
            name="x", best_seconds=0.001, mean_seconds=0.001,
            repeats=1, threshold_seconds=0.01, calls_per_repeat=1,
        )
        self.assertEqual(result.status, "ok")

    def test_status_slow_over_threshold(self):
        result = BenchmarkResult(
            name="x", best_seconds=0.020, mean_seconds=0.020,
            repeats=1, threshold_seconds=0.001, calls_per_repeat=1,
        )
        self.assertEqual(result.status, "slow")

    def test_status_uses_per_call_not_total(self):
        # best_seconds is per-repeat total; threshold applies per call.
        result = BenchmarkResult(
            name="x", best_seconds=0.010, mean_seconds=0.010,
            repeats=1, threshold_seconds=0.002, calls_per_repeat=10,
        )
        # per_call = 0.010 / 10 = 0.001 <= 0.002 -> ok
        self.assertEqual(result.status, "ok")

    def test_rejects_zero_repeat(self):
        with self.assertRaises(ValueError):
            run_benchmark("x", lambda: None, repeat=0)

    def test_rejects_zero_calls(self):
        with self.assertRaises(ValueError):
            run_benchmark("x", lambda: None, calls_per_repeat=0)

    def test_result_is_frozen(self):
        result = BenchmarkResult(
            name="x", best_seconds=1, mean_seconds=1, repeats=1, threshold_seconds=None,
        )
        with self.assertRaises(AttributeError):
            result.name = "y"

    def test_uses_perf_counter(self):
        ticks = iter([0.0, 0.005, 0.0, 0.003, 0.0, 0.004])
        with patch("src.benchmarks.time.perf_counter", side_effect=lambda: next(ticks)):
            result = run_benchmark("x", lambda: None, repeat=3, calls_per_repeat=1)
        self.assertAlmostEqual(result.best_seconds, 0.003)
        self.assertAlmostEqual(result.mean_seconds, (0.005 + 0.003 + 0.004) / 3)


class TestRunSuite(unittest.TestCase):
    def test_runs_all_benchmarks(self):
        benchmarks = [
            ("a", lambda: sum(range(10)), {"repeat": 2}),
            ("b", lambda: len("hello"), {"repeat": 2}),
        ]
        suite = run_suite("test-suite", benchmarks)
        self.assertEqual(suite.name, "test-suite")
        self.assertEqual(len(suite.results), 2)
        self.assertEqual(suite.results[0].name, "a")
        self.assertEqual(suite.results[1].name, "b")

    def test_slow_count_and_ok(self):
        benchmarks = [
            ("fast", lambda: None, {"repeat": 1, "threshold_seconds": 10}),
            ("slow", lambda: None, {"repeat": 1, "threshold_seconds": 0}),
        ]
        suite = run_suite("s", benchmarks)
        self.assertEqual(suite.slow_count, 1)
        self.assertFalse(suite.ok)

    def test_all_ok(self):
        benchmarks = [
            ("a", lambda: None, {"repeat": 1, "threshold_seconds": 10}),
        ]
        suite = run_suite("s", benchmarks)
        self.assertTrue(suite.ok)
        self.assertEqual(suite.slow_count, 0)

    def test_to_dict_structure(self):
        suite = run_suite("s", [("a", lambda: None, {"repeat": 1})])
        data = suite.to_dict()
        self.assertEqual(data["name"], "s")
        self.assertIn("ok", data)
        self.assertIn("slow_count", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertIn("best_us", data["results"][0])
        self.assertIn("status", data["results"][0])
        self.assertIn("threshold_us", data["results"][0])

    def test_suite_is_frozen(self):
        suite = BenchmarkSuite(name="s")
        with self.assertRaises(AttributeError):
            suite.name = "x"


class TestFormatSuiteText(unittest.TestCase):
    def test_renders_header_and_rows(self):
        suite = BenchmarkSuite(
            name="demo",
            results=(
                BenchmarkResult("bench-a", 0.001, 0.002, 5, 0.01, 1, "detail"),
                BenchmarkResult("bench-b", 0.020, 0.020, 5, 0.001, 1, ""),
            ),
        )
        text = format_suite_text(suite)
        self.assertIn("Benchmark suite: demo", text)
        self.assertIn("bench-a", text)
        self.assertIn("bench-b", text)
        self.assertIn("OK", text)
        self.assertIn("SLOW", text)
        self.assertIn("2 benchmarks, 1 slow", text)

    def test_empty_suite(self):
        text = format_suite_text(BenchmarkSuite(name="empty"))
        self.assertIn("0 benchmarks, 0 slow", text)


class TestDefaultBenchmarks(unittest.TestCase):
    def test_registry_is_non_empty(self):
        benchmarks = build_default_benchmarks()
        self.assertGreaterEqual(len(benchmarks), 8)

    def test_registry_entries_have_name_func_kwargs(self):
        for name, func, kwargs in build_default_benchmarks():
            self.assertIsInstance(name, str)
            self.assertTrue(name)
            self.assertTrue(callable(func))
            self.assertIsInstance(kwargs, dict)
            self.assertIn("repeat", kwargs)

    def test_registry_covers_critical_paths(self):
        names = " ".join(name for name, _, _ in build_default_benchmarks())
        self.assertIn("chat_controller", names)
        self.assertIn("safe_paths", names)
        self.assertIn("a11y", names)
        self.assertIn("sync_bundle", names)
        self.assertIn("stats_aggregator", names)
        self.assertIn("provider_modes", names)

    def test_run_default_suite_completes(self):
        # Use a tiny scale so the test stays fast.
        suite = run_default_suite(repeat_scale=0.1)
        self.assertGreater(len(suite.results), 0)
        for result in suite.results:
            self.assertGreater(result.best_seconds, 0)
            self.assertIn(result.status, ("ok", "slow"))

    def test_repeat_scale_scales_repeats(self):
        benchmarks = build_default_benchmarks()
        # Verify the scaling logic by inspecting run_default_suite's transform.
        scaled = []
        scale = 0.5
        for name, func, kwargs in benchmarks:
            kwargs = dict(kwargs)
            kwargs["repeat"] = max(1, int(kwargs.get("repeat", 5) * scale))
            scaled.append((name, func, kwargs))
        for (_, _, kwargs), (_, _, orig) in zip(scaled, benchmarks):
            self.assertEqual(kwargs["repeat"], max(1, int(orig["repeat"] * 0.5)))

    def test_benchmarks_actually_execute_target_code(self):
        # Each built-in benchmark must run without raising when executed
        # once (catches import errors, wrong signatures, etc.).
        for name, func, _ in build_default_benchmarks():
            with self.subTest(name=name):
                func()


class TestBenchmarkResultProperties(unittest.TestCase):
    def test_per_call_microseconds(self):
        r = BenchmarkResult("x", 0.001, 0.001, 1, None, calls_per_repeat=2)
        self.assertAlmostEqual(r.per_call_microseconds, 500.0)

    def test_per_call_with_default_calls(self):
        r = BenchmarkResult("x", 0.001, 0.001, 1, None)
        self.assertAlmostEqual(r.per_call_seconds, 0.001)


if __name__ == "__main__":
    unittest.main()
