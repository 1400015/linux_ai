"""GTK-free pair programming core: code review, suggestions, and diff analysis.

This module provides the building blocks for AI-assisted pair programming:

- :func:`extract_code_blocks` — pull fenced code blocks (with optional file
  path headers) out of an AI response, reusing ``render_core.FileBlock``.
- :func:`analyze_diff` — compute a structured diff between two versions of
  a file (or text), returning added/removed/changed line counts and a
  unified diff string.
- :func:`review_suggestion` — generate a structured review of a proposed
  code change: size classification, risk heuristics (dangerous patterns),
  and actionable recommendations.
- :func:`compare_versions` — side-by-side comparison of two texts with
  similarity ratio.
- :class:`PairSession` — tracks a pair-programming exchange: the user's
  question, the AI's response, extracted code blocks, and review results.
- :class:`PairConfig` — configuration for pair programming features
  (auto-review, max diff lines, language hints).

All functions are pure; none import GTK. The GUI and CLI both use them.
"""

import difflib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Diff size classifications (by number of changed lines).
SIZE_THRESHOLDS = {
    "tiny": 5,
    "small": 20,
    "medium": 100,
    "large": 500,
}

#: Patterns that flag a proposed change as potentially risky.
#: Each entry is (regex, severity, description). Severity: "high" | "medium" | "low".
RISK_PATTERNS: List[Tuple[str, str, str]] = [
    (r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)", "high",
     "recursive force delete (rm -rf)"),
    (r"sudo\s+", "high", "uses sudo"),
    (r"curl\s+[^|]*\|\s*(ba)?sh", "high", "pipes curl to shell"),
    (r"wget\s+[^|]*\|\s*(ba)?sh", "high", "pipes wget to shell"),
    (r"eval\s*\(", "high", "uses eval()"),
    (r"exec\s*\(", "high", "uses exec()"),
    (r"os\.system\s*\(", "high", "uses os.system()"),
    (r"subprocess.*shell\s*=\s*True", "high", "subprocess with shell=True"),
    (r"chmod\s+777", "medium", "world-writable permissions (chmod 777)"),
    (r"password\s*=\s*['\"][^'\"]+['\"]", "medium", "hardcoded password"),
    (r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "medium", "hardcoded API key"),
    (r"token\s*=\s*['\"][^'\"]{8,}['\"]", "medium", "hardcoded token"),
    (r"\bDROP\s+(TABLE|DATABASE)\b", "high", "drops a table or database"),
    (r"\bDELETE\s+FROM\b(?![^;]*\bWHERE\b)", "high", "DELETE without WHERE"),
    (r"\bTRUNCATE\b", "medium", "truncates a table"),
    (r">\s*/dev/sd[a-z]", "high", "writes to a block device"),
    (r"mkfs\.", "high", "formats a filesystem"),
    (r":\(\)\s*\{\s*:\|:\s*\|", "medium", "fork bomb pattern"),
    (r"pickle\.loads?\s*\(", "medium", "deserializes with pickle (unsafe)"),
    (r"yaml\.load\s*\(\s*[^)]*\)", "medium", "yaml.load without SafeLoader"),
]

_COMPILED_RISKS = [(re.compile(p, re.IGNORECASE), sev, desc) for p, sev, desc in RISK_PATTERNS]

#: Language hints for fenced code blocks (extension -> language name).
LANG_BY_EXTENSION: Dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".rs": "rust", ".go": "go", ".java": "java",
    ".rb": "ruby", ".php": "php", ".lua": "lua",
    ".sql": "sql", ".html": "html", ".css": "css",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".xml": "xml", ".toml": "toml", ".ini": "ini",
    ".md": "markdown", ".rst": "rst",
}

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PairProgrammingError(Exception):
    """Raised when a pair programming operation cannot be completed."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PairConfig:
    """Pair programming configuration (mirrors the ``pair`` config section)."""

    enabled: bool = True
    auto_review: bool = True
    max_diff_lines: int = 2000
    show_line_numbers: bool = True
    highlight_risks: bool = True
    default_language: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PairConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=bool(data.get("enabled", True)),
            auto_review=bool(data.get("auto_review", True)),
            max_diff_lines=int(data.get("max_diff_lines", 2000)),
            show_line_numbers=bool(data.get("show_line_numbers", True)),
            highlight_risks=bool(data.get("highlight_risks", True)),
            default_language=str(data.get("default_language", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_review": self.auto_review,
            "max_diff_lines": self.max_diff_lines,
            "show_line_numbers": self.show_line_numbers,
            "highlight_risks": self.highlight_risks,
            "default_language": self.default_language,
        }

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if not 10 <= self.max_diff_lines <= 100000:
            warnings.append("max_diff_lines must be between 10 and 100000")
        if self.default_language and len(self.default_language) > 20:
            warnings.append("default_language must be at most 20 characters")
        return warnings


def load_pair_config(config: Any) -> PairConfig:
    """Build a :class:`PairConfig` from a ConfigManager."""
    raw = config.get("pair", {})
    if not isinstance(raw, dict):
        return PairConfig()
    return PairConfig.from_dict(raw)


# ---------------------------------------------------------------------------
# Code block extraction
# ---------------------------------------------------------------------------


@dataclass
class ExtractedBlock:
    """A code block extracted from an AI response."""

    content: str
    language: str = ""
    file_path: Optional[str] = None
    start_line: int = 0  # line number in the source text (0-based)

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())

    @property
    def suggested_filename(self) -> Optional[str]:
        """Derive a filename from the file_path header or the language."""
        if self.file_path:
            return os.path.basename(self.file_path)
        if self.language:
            for ext, lang in LANG_BY_EXTENSION.items():
                if lang == self.language:
                    return "snippet" + ext
        return None


_FENCE_RE = re.compile(
    r"```([a-zA-Z0-9_+#-]*)\s*\n(.*?)```",
    re.DOTALL,
)

_FILE_HEADER_RE = re.compile(r"^(/[^\s]+|~/[^\s]+|[^\s]+\.[a-zA-Z0-9]+)\s*$")


def extract_code_blocks(text: str) -> List[ExtractedBlock]:
    """Extract fenced code blocks from `text`.

    Each block may have a language tag (`` ```python ``) and optionally a
    file path on the first line of the body (`` /path/to/file.py ``).
    Blocks without a recognizable language or path still get extracted
    with empty fields.
    """
    blocks: List[ExtractedBlock] = []
    for match in _FENCE_RE.finditer(text):
        language = match.group(1).strip().lower()
        body = match.group(2)
        # Strip trailing newline if present.
        if body.endswith("\n"):
            body = body[:-1]
        # Compute the start line in the source text.
        start_line = text[:match.start()].count("\n")
        # Check if the first line is a file path.
        file_path: Optional[str] = None
        lines = body.split("\n")
        if lines and _FILE_HEADER_RE.match(lines[0].strip()):
            candidate = lines[0].strip()
            # Use render_core.FileBlock's path detection for consistency.
            if candidate.startswith("/") or candidate.startswith("~/"):
                file_path = candidate
                body = "\n".join(lines[1:])
                if body.startswith("\n"):
                    body = body[1:]
        # If no language tag but we have a file path, infer from extension.
        if not language and file_path:
            ext = os.path.splitext(file_path)[1].lower()
            language = LANG_BY_EXTENSION.get(ext, "")
        blocks.append(ExtractedBlock(
            content=body,
            language=language,
            file_path=file_path,
            start_line=start_line,
        ))
    return blocks


# ---------------------------------------------------------------------------
# Diff analysis
# ---------------------------------------------------------------------------


@dataclass
class DiffStats:
    """Structured statistics for a diff."""

    added_lines: int = 0
    removed_lines: int = 0
    changed_lines: int = 0
    context_lines: int = 0
    total_lines_old: int = 0
    total_lines_new: int = 0
    hunks: int = 0

    @property
    def total_changes(self) -> int:
        return self.added_lines + self.removed_lines

    @property
    def size_class(self) -> str:
        """Classify the diff size: tiny/small/medium/large/huge."""
        total = self.total_changes
        if total <= SIZE_THRESHOLDS["tiny"]:
            return "tiny"
        if total <= SIZE_THRESHOLDS["small"]:
            return "small"
        if total <= SIZE_THRESHOLDS["medium"]:
            return "medium"
        if total <= SIZE_THRESHOLDS["large"]:
            return "large"
        return "huge"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added_lines": self.added_lines,
            "removed_lines": self.removed_lines,
            "changed_lines": self.changed_lines,
            "total_changes": self.total_changes,
            "total_lines_old": self.total_lines_old,
            "total_lines_new": self.total_lines_new,
            "hunks": self.hunks,
            "size_class": self.size_class,
        }


def analyze_diff(old_text: str, new_text: str, context_lines: int = 3) -> Tuple[DiffStats, str]:
    """Compute structured stats and a unified diff string.

    Returns (DiffStats, unified_diff_text). The diff text is empty when
    there are no changes.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    differ = difflib.unified_diff(
        old_lines, new_lines,
        fromfile="a/original",
        tofile="b/proposed",
        n=context_lines,
    )
    diff_lines = list(differ)

    stats = DiffStats(
        total_lines_old=len(old_lines),
        total_lines_new=len(new_lines),
    )
    in_hunk = False
    for line in diff_lines:
        if line.startswith("@@"):
            stats.hunks += 1
            in_hunk = True
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif in_hunk:
            if line.startswith("+"):
                stats.added_lines += 1
            elif line.startswith("-"):
                stats.removed_lines += 1
            else:
                stats.context_lines += 1

    # changed_lines = min(added, removed) — lines that were modified (not
    # purely added or removed).
    stats.changed_lines = min(stats.added_lines, stats.removed_lines)

    diff_text = "".join(diff_lines)
    return stats, diff_text


def compare_versions(old_text: str, new_text: str) -> Dict[str, Any]:
    """Side-by-side comparison with similarity ratio.

    Returns a dict with similarity (0.0-1.0), identical (bool), and
    line counts.
    """
    if old_text == new_text:
        return {
            "similarity": 1.0,
            "identical": True,
            "old_lines": len(old_text.splitlines()),
            "new_lines": len(new_text.splitlines()),
        }
    matcher = difflib.SequenceMatcher(
        a=old_text.splitlines(),
        b=new_text.splitlines(),
    )
    return {
        "similarity": round(matcher.ratio(), 4),
        "identical": False,
        "old_lines": len(old_text.splitlines()),
        "new_lines": len(new_text.splitlines()),
    }


# ---------------------------------------------------------------------------
# Risk analysis
# ---------------------------------------------------------------------------


@dataclass
class RiskFinding:
    """A single risk detected in proposed code."""

    severity: str  # "high" | "medium" | "low"
    pattern: str
    description: str
    line_number: int = 0  # 1-based line in the proposed text
    matched_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "description": self.description,
            "line_number": self.line_number,
            "matched_text": self.matched_text[:80],
        }


def find_risks(code: str) -> List[RiskFinding]:
    """Scan `code` for risky patterns and return findings.

    Each finding includes the severity, a human-readable description, and
    the 1-based line number where the pattern was found.
    """
    findings: List[RiskFinding] = []
    lines = code.splitlines()
    for pattern, severity, description in _COMPILED_RISKS:
        for line_no, line in enumerate(lines, start=1):
            match = pattern.search(line)
            if match:
                findings.append(RiskFinding(
                    severity=severity,
                    pattern=pattern.pattern,
                    description=description,
                    line_number=line_no,
                    matched_text=match.group(0),
                ))
    # Sort by line number, then severity (high first).
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (f.line_number, severity_order.get(f.severity, 99)))
    return findings


def max_severity(findings: List[RiskFinding]) -> str:
    """Return the highest severity among findings, or 'none'."""
    if not findings:
        return "none"
    severities = {f.severity for f in findings}
    for sev in ("high", "medium", "low"):
        if sev in severities:
            return sev
    return "none"


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


@dataclass
class ReviewResult:
    """Structured review of a proposed code change."""

    stats: DiffStats
    risks: List[RiskFinding] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    summary: str = ""

    @property
    def risk_level(self) -> str:
        return max_severity(self.risks)

    @property
    def passed(self) -> bool:
        """True when there are no high-severity risks."""
        return self.risk_level not in ("high",)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stats": self.stats.to_dict(),
            "risks": [r.to_dict() for r in self.risks],
            "recommendations": self.recommendations,
            "summary": self.summary,
            "risk_level": self.risk_level,
            "passed": self.passed,
        }


def review_change(
    old_text: str,
    new_text: str,
    config: Optional[PairConfig] = None,
) -> ReviewResult:
    """Review a proposed code change and return a structured result.

    Combines diff analysis, risk scanning, and heuristic recommendations.
    """
    if config is None:
        config = PairConfig()

    stats, _diff_text = analyze_diff(old_text, new_text)
    risks = find_risks(new_text) if config.highlight_risks else []
    recommendations: List[str] = []

    # Generate recommendations based on stats and risks.
    if stats.size_class in ("large", "huge"):
        recommendations.append(
            "Large change ({} lines). Consider splitting into smaller, "
            "reviewable commits.".format(stats.total_changes)
        )
    if stats.removed_lines > stats.added_lines * 3 and stats.removed_lines > 10:
        recommendations.append(
            "Many lines removed ({}) relative to added ({}). Verify that "
            "no important logic was deleted.".format(stats.removed_lines, stats.added_lines)
        )
    high_risks = [r for r in risks if r.severity == "high"]
    if high_risks:
        recommendations.append(
            "{} high-severity risk(s) detected. Review each one carefully "
            "before applying.".format(len(high_risks))
        )
    medium_risks = [r for r in risks if r.severity == "medium"]
    if medium_risks and not high_risks:
        recommendations.append(
            "{} medium-severity risk(s) detected. Consider addressing them.".format(
                len(medium_risks))
        )
    if stats.total_changes == 0:
        recommendations.append("No changes detected.")
    if not risks and stats.total_changes > 0 and stats.total_changes <= SIZE_THRESHOLDS["small"]:
        recommendations.append("Small, clean change with no detected risks.")

    # Build a summary string.
    parts = ["{} change: +{} -{} lines".format(
        stats.size_class.capitalize(), stats.added_lines, stats.removed_lines)]
    if risks:
        parts.append("{} risk(s) ({})".format(len(risks), max_severity(risks)))
    else:
        parts.append("no risks detected")
    summary = ", ".join(parts) + "."

    return ReviewResult(
        stats=stats,
        risks=risks,
        recommendations=recommendations,
        summary=summary,
    )


def review_code_block(block: ExtractedBlock) -> ReviewResult:
    """Review a single extracted code block (no old version for diff).

    Performs risk scanning and line-count analysis on the block content.
    """
    risks = find_risks(block.content)
    recommendations: List[str] = []

    line_count = block.line_count
    if line_count > 200:
        recommendations.append(
            "Large code block ({} lines). Consider splitting into smaller "
            "functions or files.".format(line_count)
        )
    high_risks = [r for r in risks if r.severity == "high"]
    if high_risks:
        recommendations.append(
            "{} high-severity risk(s) in this code block. Review before "
            "using.".format(len(high_risks))
        )
    if not risks:
        recommendations.append("No risks detected in this code block.")

    stats = DiffStats(
        added_lines=line_count,
        total_lines_new=line_count,
        hunks=1,
    )
    summary = "Code block: {} lines, {} risk(s) ({})".format(
        line_count, len(risks), max_severity(risks))

    return ReviewResult(
        stats=stats,
        risks=risks,
        recommendations=recommendations,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Pair session tracking
# ---------------------------------------------------------------------------


@dataclass
class PairExchange:
    """One exchange in a pair programming session."""

    prompt: str
    response: str
    blocks: List[ExtractedBlock] = field(default_factory=list)
    reviews: List[ReviewResult] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "response": self.response,
            "blocks": [
                {
                    "content": b.content,
                    "language": b.language,
                    "file_path": b.file_path,
                    "line_count": b.line_count,
                }
                for b in self.blocks
            ],
            "reviews": [r.to_dict() for r in self.reviews],
            "timestamp": self.timestamp,
        }


@dataclass
class PairSession:
    """Tracks a pair programming session with multiple exchanges."""

    exchanges: List[PairExchange] = field(default_factory=list)

    def add_exchange(self, prompt: str, response: str, timestamp: float = 0.0) -> PairExchange:
        """Record a new exchange: extract code blocks and auto-review them."""
        blocks = extract_code_blocks(response)
        reviews = [review_code_block(b) for b in blocks]
        exchange = PairExchange(
            prompt=prompt,
            response=response,
            blocks=blocks,
            reviews=reviews,
            timestamp=timestamp,
        )
        self.exchanges.append(exchange)
        return exchange

    @property
    def total_blocks(self) -> int:
        return sum(len(e.blocks) for e in self.exchanges)

    @property
    def total_risks(self) -> int:
        return sum(len(r.risks) for e in self.exchanges for r in e.reviews)

    @property
    def high_risk_count(self) -> int:
        return sum(
            1 for e in self.exchanges for r in e.reviews for risk in r.risks
            if risk.severity == "high"
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "exchanges": len(self.exchanges),
            "total_blocks": self.total_blocks,
            "total_risks": self.total_risks,
            "high_risk_count": self.high_risk_count,
            "languages": sorted({b.language for e in self.exchanges for b in e.blocks if b.language}),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchanges": [e.to_dict() for e in self.exchanges],
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_review_text(result: ReviewResult, diff_text: str = "") -> str:
    """Human-readable review report."""
    lines = ["Review: " + result.summary, ""]
    lines.append("Changes: +{} -{} ({} hunks, {} lines old -> {} lines new)".format(
        result.stats.added_lines, result.stats.removed_lines,
        result.stats.hunks, result.stats.total_lines_old, result.stats.total_lines_new,
    ))
    if result.risks:
        lines.append("")
        lines.append("Risks ({}):".format(len(result.risks)))
        for risk in result.risks:
            lines.append("  [{}] line {}: {}".format(
                risk.severity.upper(), risk.line_number, risk.description))
    if result.recommendations:
        lines.append("")
        lines.append("Recommendations:")
        for rec in result.recommendations:
            lines.append("  - " + rec)
    if diff_text:
        lines.append("")
        lines.append("Diff:")
        lines.append(diff_text.rstrip())
    return "\n".join(lines)


def format_session_text(session: PairSession) -> str:
    """Human-readable session summary."""
    s = session.summary()
    lines = [
        "Pair Programming Session",
        "=" * 40,
        "Exchanges: {}".format(s["exchanges"]),
        "Code blocks: {}".format(s["total_blocks"]),
        "Risks found: {} ({} high)".format(s["total_risks"], s["high_risk_count"]),
    ]
    if s["languages"]:
        lines.append("Languages: " + ", ".join(s["languages"]))
    for i, exchange in enumerate(session.exchanges, 1):
        lines.append("")
        lines.append("--- Exchange {} ---".format(i))
        lines.append("Prompt: {}".format(exchange.prompt[:100]))
        lines.append("Blocks: {}".format(len(exchange.blocks)))
        for j, review in enumerate(exchange.reviews):
            lines.append("  Block {}: {}".format(j + 1, review.summary))
    return "\n".join(lines)
