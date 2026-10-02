"""Bounded conversation choices, without commands, secrets or executable plans."""

import math

from .display_actions import DisplayMode
from .package_actions import PackageCandidate

TASK_TTL_SECONDS = 15 * 60
MAX_TASK_OPTIONS = 30


def _text(value, limit, empty=True):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("Invalid task text")
    return value


def validate_task_state(value):
    """Validate only observed choices; callers must rediscover before changes."""
    keys = {"version", "kind", "operation", "created_at", "query", "options"}
    if not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Invalid task state")
    kind = value["kind"]
    if kind not in ("packages", "display"):
        raise ValueError("Invalid task kind")
    allowed = ("inspect", "install") if kind == "packages" else ("inspect", "set_mode")
    if value["operation"] not in allowed:
        raise ValueError("Invalid task operation")
    created = value["created_at"]
    if (type(created) not in (int, float) or not 0 <= created < 253402300800
            or not math.isfinite(created)):
        raise ValueError("Invalid task timestamp")
    query = _text(value["query"], 200)
    options = value["options"]
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_TASK_OPTIONS:
        raise ValueError("Invalid task choices")
    clean = []
    for option in options:
        if kind == "packages":
            if not isinstance(option, dict) or set(option) != {"name", "version", "source", "summary"}:
                raise ValueError("Invalid package choice")
            candidate = PackageCandidate.from_dict(option)
            if not candidate.version or not candidate.source:
                raise ValueError("Incomplete repository package identity")
            clean.append(candidate.to_dict())
        else:
            # The backend validates its native identifier and rediscovered mode.
            keys = {"output", "width", "height", "refresh", "identifier", "scale", "current"}
            if not isinstance(option, dict) or set(option) != keys:
                raise ValueError("Invalid display choice")
            clean.append(DisplayMode.from_dict(option).to_dict())
    return {"version": 1, "kind": kind, "operation": value["operation"],
            "created_at": created, "query": query, "options": clean}
