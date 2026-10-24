"""Tests for src.stats_aggregator — GTK-free usage statistics."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from src.stats_aggregator import (
    DailyStats,
    ProviderStats,
    aggregate_usage,
    daily_breakdown,
    estimate_cost,
    format_summary_markdown,
    format_summary_text,
    load_usage_file,
)


class TestProviderStats(unittest.TestCase):
    def test_defaults(self):
        stats = ProviderStats(provider="openrouter")
        self.assertEqual(stats.input_tokens, 0)
        self.assertEqual(stats.output_tokens, 0)
        self.assertEqual(stats.total_tokens, 0)
        self.assertEqual(stats.requests, 0)

    def test_ratio_with_input(self):
        stats = ProviderStats(provider="x", input_tokens=100, output_tokens=50)
        self.assertAlmostEqual(stats.ratio, 0.5)

    def test_ratio_zero_input(self):
        stats = ProviderStats(provider="x", input_tokens=0, output_tokens=50)
        self.assertEqual(stats.ratio, 0.0)


class TestAggregateUsage(unittest.TestCase):
    def test_empty(self):
        summary = aggregate_usage({})
        self.assertEqual(summary.providers, ())
        self.assertEqual(summary.total_tokens, 0)
        self.assertEqual(summary.provider_count, 0)
        self.assertIsNone(summary.most_used_provider)

    def test_single_provider(self):
        summary = aggregate_usage({
            "openrouter": {"input": 1000, "output": 500, "total": 1500},
        })
        self.assertEqual(summary.provider_count, 1)
        self.assertEqual(summary.total_input, 1000)
        self.assertEqual(summary.total_output, 500)
        self.assertEqual(summary.total_tokens, 1500)
        self.assertEqual(summary.most_used_provider, "openrouter")

    def test_multiple_providers(self):
        summary = aggregate_usage({
            "openrouter": {"input": 1000, "output": 500, "total": 1500},
            "google": {"input": 2000, "output": 1000, "total": 3000},
        })
        self.assertEqual(summary.provider_count, 2)
        self.assertEqual(summary.total_tokens, 4500)
        self.assertEqual(summary.most_used_provider, "google")

    def test_with_request_counts(self):
        summary = aggregate_usage(
            {"openrouter": {"input": 100, "output": 50, "total": 150}},
            request_counts={"openrouter": 5},
        )
        self.assertEqual(summary.total_requests, 5)
        self.assertEqual(summary.providers[0].requests, 5)

    def test_skips_non_dict_usage(self):
        summary = aggregate_usage({
            "bad": "not a dict",
            "good": {"input": 10, "output": 5, "total": 15},
        })
        self.assertEqual(summary.provider_count, 1)
        self.assertEqual(summary.providers[0].provider, "good")

    def test_total_defaults_to_input_plus_output(self):
        summary = aggregate_usage({
            "x": {"input": 100, "output": 50},  # no "total" key
        })
        self.assertEqual(summary.providers[0].total_tokens, 150)

    def test_providers_sorted(self):
        summary = aggregate_usage({
            "z_provider": {"input": 1, "output": 1, "total": 2},
            "a_provider": {"input": 1, "output": 1, "total": 2},
        })
        self.assertEqual(summary.providers[0].provider, "a_provider")
        self.assertEqual(summary.providers[1].provider, "z_provider")


class TestUsageSummary(unittest.TestCase):
    def _summary(self):
        return aggregate_usage({
            "openrouter": {"input": 1000, "output": 500, "total": 1500},
            "google": {"input": 1000, "output": 500, "total": 1500},
        })

    def test_provider_share(self):
        summary = self._summary()
        self.assertAlmostEqual(summary.provider_share("openrouter"), 0.5)
        self.assertAlmostEqual(summary.provider_share("google"), 0.5)

    def test_provider_share_unknown(self):
        summary = self._summary()
        self.assertEqual(summary.provider_share("unknown"), 0.0)

    def test_provider_share_zero_total(self):
        summary = aggregate_usage({})
        self.assertEqual(summary.provider_share("anything"), 0.0)


class TestFormatSummaryText(unittest.TestCase):
    def test_empty(self):
        summary = aggregate_usage({})
        text = format_summary_text(summary)
        self.assertIn("No usage recorded", text)

    def test_with_data(self):
        summary = aggregate_usage({
            "openrouter": {"input": 1000, "output": 500, "total": 1500},
        })
        text = format_summary_text(summary)
        self.assertIn("openrouter", text)
        self.assertIn("1500", text)
        self.assertIn("Total", text)


class TestFormatSummaryMarkdown(unittest.TestCase):
    def test_empty(self):
        summary = aggregate_usage({})
        md = format_summary_markdown(summary)
        self.assertIn("No usage recorded", md)

    def test_with_data(self):
        summary = aggregate_usage({
            "openrouter": {"input": 1000, "output": 500, "total": 1500},
            "google": {"input": 2000, "output": 1000, "total": 3000},
        })
        md = format_summary_markdown(summary)
        self.assertIn("| Provider |", md)
        self.assertIn("openrouter", md)
        self.assertIn("google", md)
        self.assertIn("**Total:**", md)
        self.assertIn("**Most used:**", md)


class TestEstimateCost(unittest.TestCase):
    def test_basic(self):
        summary = aggregate_usage({
            "openrouter": {"input": 1_000_000, "output": 500_000, "total": 1_500_000},
        })
        pricing = {"openrouter": {"input": 0.10, "output": 0.30}}
        costs = estimate_cost(summary, pricing)
        # 1M input * 0.10/1M = 0.10; 0.5M output * 0.30/1M = 0.15; total = 0.25
        self.assertAlmostEqual(costs["openrouter"], 0.25, places=4)

    def test_missing_pricing(self):
        summary = aggregate_usage({
            "unknown": {"input": 1000, "output": 500, "total": 1500},
        })
        costs = estimate_cost(summary, {})
        self.assertEqual(costs, {})

    def test_zero_usage(self):
        summary = aggregate_usage({
            "openrouter": {"input": 0, "output": 0, "total": 0},
        })
        pricing = {"openrouter": {"input": 1.0, "output": 1.0}}
        costs = estimate_cost(summary, pricing)
        self.assertEqual(costs["openrouter"], 0.0)


class TestDailyStats(unittest.TestCase):
    def test_defaults(self):
        stats = DailyStats(date="2026-01-01")
        self.assertEqual(stats.input_tokens, 0)
        self.assertEqual(stats.total, 0)

    def test_total_property(self):
        stats = DailyStats(date="2026-01-01", total_tokens=500)
        self.assertEqual(stats.total, 500)


class TestDailyBreakdown(unittest.TestCase):
    def test_empty_entries(self):
        result = daily_breakdown([], days=3)
        self.assertEqual(len(result), 3)
        for day in result:
            self.assertEqual(day.total_tokens, 0)

    def test_single_entry_today(self):
        now = time.time()
        entries = [{"timestamp": now, "input": 100, "output": 50, "requests": 1}]
        result = daily_breakdown(entries, days=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].input_tokens, 100)
        self.assertEqual(result[0].output_tokens, 50)
        self.assertEqual(result[0].requests, 1)

    def test_multiple_entries_same_day(self):
        now = time.time()
        entries = [
            {"timestamp": now, "input": 100, "output": 50, "requests": 1},
            {"timestamp": now, "input": 200, "output": 100, "requests": 1},
        ]
        result = daily_breakdown(entries, days=1)
        self.assertEqual(result[0].input_tokens, 300)
        self.assertEqual(result[0].output_tokens, 150)
        self.assertEqual(result[0].requests, 2)

    def test_invalid_timestamp_skipped(self):
        entries = [{"timestamp": "not a number", "input": 100, "output": 50}]
        result = daily_breakdown(entries, days=1)
        self.assertEqual(result[0].total_tokens, 0)

    def test_fills_missing_days(self):
        result = daily_breakdown([], days=7)
        self.assertEqual(len(result), 7)
        # Days should be consecutive and end today
        today = time.strftime("%Y-%m-%d", time.localtime())
        self.assertEqual(result[-1].date, today)


class TestLoadUsageFile(unittest.TestCase):
    def test_valid_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"openrouter": {"input": 100, "output": 50, "total": 150}}, f)
            path = f.name
        try:
            data = load_usage_file(path)
            self.assertIn("openrouter", data)
        finally:
            Path(path).unlink()

    def test_missing_file(self):
        data = load_usage_file("/nonexistent/path/usage.json")
        self.assertEqual(data, {})

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json {{{")
            path = f.name
        try:
            data = load_usage_file(path)
            self.assertEqual(data, {})
        finally:
            Path(path).unlink()

    def test_non_dict_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(["not", "a", "dict"], f)
            path = f.name
        try:
            data = load_usage_file(path)
            self.assertEqual(data, {})
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
