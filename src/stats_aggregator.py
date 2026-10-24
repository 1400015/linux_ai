"""GTK-free usage statistics aggregation and formatting.

Reads the token-usage data maintained by ``AIClient`` (persisted in
``~/.config/linux_ai_assistant/usage.json``) and produces structured
summaries suitable for a dashboard, CLI output, or export.

The aggregator never touches GTK; the dialog layer formats the result.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass(frozen=True)
class ProviderStats:
    """Token usage for one provider."""

    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0

    @property
    def ratio(self) -> float:
        """Output/input ratio (0.0 when no input)."""
        if self.input_tokens == 0:
            return 0.0
        return self.output_tokens / self.input_tokens


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage across all providers."""

    providers: tuple  # tuple[ProviderStats, ...]
    total_input: int = 0
    total_output: int = 0
    total_tokens: int = 0
    total_requests: int = 0
    most_used_provider: Optional[str] = None

    @property
    def provider_count(self) -> int:
        return len(self.providers)

    def provider_share(self, provider: str) -> float:
        """Fraction of total tokens used by ``provider`` (0.0–1.0)."""
        if self.total_tokens == 0:
            return 0.0
        for stats in self.providers:
            if stats.provider == provider:
                return stats.total_tokens / self.total_tokens
        return 0.0


@dataclass
class DailyStats:
    """Usage for a single day."""

    date: str  # YYYY-MM-DD
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0

    @property
    def total(self) -> int:
        return self.total_tokens


def load_usage_file(path: Union[str, Path]) -> Dict[str, Any]:
    """Load the raw usage.json file, returning an empty dict on failure."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def aggregate_usage(
    token_usage: Dict[str, Dict[str, int]],
    request_counts: Optional[Dict[str, int]] = None,
) -> UsageSummary:
    """Aggregate per-provider token usage into a :class:`UsageSummary`.

    ``token_usage`` is the dict maintained by ``AIClient``: provider names
    mapped to ``{"input": int, "output": int, "total": int}``.
    ``request_counts`` optionally maps provider names to request counts.
    """
    request_counts = request_counts or {}
    providers: List[ProviderStats] = []
    total_input = total_output = total_tokens = total_requests = 0

    for provider, usage in sorted(token_usage.items()):
        if not isinstance(usage, dict):
            continue
        input_tokens = int(usage.get("input", 0))
        output_tokens = int(usage.get("output", 0))
        total = int(usage.get("total", input_tokens + output_tokens))
        requests = int(request_counts.get(provider, 0))

        providers.append(ProviderStats(
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            requests=requests,
        ))
        total_input += input_tokens
        total_output += output_tokens
        total_tokens += total
        total_requests += requests

    most_used = None
    if providers:
        most_used = max(providers, key=lambda p: p.total_tokens).provider

    return UsageSummary(
        providers=tuple(providers),
        total_input=total_input,
        total_output=total_output,
        total_tokens=total_tokens,
        total_requests=total_requests,
        most_used_provider=most_used,
    )


def format_summary_text(summary: UsageSummary) -> str:
    """Format a usage summary as plain text (for CLI or logs)."""
    lines = ["Usage Statistics", "=" * 40]
    if not summary.providers:
        lines.append("No usage recorded yet.")
        return "\n".join(lines)

    for stats in summary.providers:
        lines.append(
            f"  {stats.provider}: "
            f"input={stats.input_tokens}, output={stats.output_tokens}, "
            f"total={stats.total_tokens}, requests={stats.requests}"
        )
    lines.append("-" * 40)
    lines.append(
        f"  Total: input={summary.total_input}, output={summary.total_output}, "
        f"tokens={summary.total_tokens}, requests={summary.total_requests}"
    )
    if summary.most_used_provider:
        lines.append(f"  Most used: {summary.most_used_provider}")
    return "\n".join(lines)


def format_summary_markdown(summary: UsageSummary) -> str:
    """Format a usage summary as Markdown (for export or reports)."""
    lines = ["# Usage Statistics", ""]
    if not summary.providers:
        lines.append("No usage recorded yet.")
        return "\n".join(lines)

    lines.append("| Provider | Input | Output | Total | Requests |")
    lines.append("|----------|-------|--------|-------|----------|")
    for stats in summary.providers:
        lines.append(
            f"| {stats.provider} | {stats.input_tokens} | {stats.output_tokens} "
            f"| {stats.total_tokens} | {stats.requests} |"
        )
    lines.append("")
    lines.append(
        f"**Total:** {summary.total_tokens} tokens "
        f"({summary.total_input} in / {summary.total_output} out), "
        f"{summary.total_requests} requests"
    )
    if summary.most_used_provider:
        share = summary.provider_share(summary.most_used_provider)
        lines.append(f"**Most used:** {summary.most_used_provider} ({share:.0%})")
    return "\n".join(lines)


def estimate_cost(
    summary: UsageSummary,
    pricing: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """Estimate cost per provider given pricing per 1M tokens.

    ``pricing`` maps provider names to ``{"input": float, "output": float}``
    costs per 1,000,000 tokens. Providers without pricing are skipped.
    """
    costs: Dict[str, float] = {}
    for stats in summary.providers:
        price = pricing.get(stats.provider)
        if price is None:
            continue
        input_cost = (stats.input_tokens / 1_000_000) * price.get("input", 0.0)
        output_cost = (stats.output_tokens / 1_000_000) * price.get("output", 0.0)
        costs[stats.provider] = round(input_cost + output_cost, 6)
    return costs


def daily_breakdown(
    entries: List[Dict[str, Any]],
    days: int = 7,
) -> List[DailyStats]:
    """Build a per-day breakdown from timestamped usage entries.

    Each entry is ``{"timestamp": float, "input": int, "output": int, "requests": int}``.
    Returns the last ``days`` days (oldest first), including empty days.
    """
    by_day: Dict[str, DailyStats] = {}
    for entry in entries:
        ts = entry.get("timestamp", 0)
        if not isinstance(ts, (int, float)):
            continue
        date = time.strftime("%Y-%m-%d", time.localtime(ts))
        input_tokens = int(entry.get("input", 0))
        output_tokens = int(entry.get("output", 0))
        requests = int(entry.get("requests", 1))

        if date not in by_day:
            by_day[date] = DailyStats(date=date)
        existing = by_day[date]
        by_day[date] = DailyStats(
            date=date,
            input_tokens=existing.input_tokens + input_tokens,
            output_tokens=existing.output_tokens + output_tokens,
            total_tokens=existing.total_tokens + input_tokens + output_tokens,
            requests=existing.requests + requests,
        )

    # Fill in missing days
    result: List[DailyStats] = []
    for i in range(days - 1, -1, -1):
        date = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        result.append(by_day.get(date, DailyStats(date=date)))
    return result
