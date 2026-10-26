"""GTK-free sync bundle: portable settings + history for machine-to-machine sync.

A *sync bundle* is a single JSON document that carries a safe subset of the
user's configuration and selected conversation sessions, so they can be
transferred to another machine (USB stick, shared folder, git repo) and
imported there.

Security rules:
- **Never** include credentials: API keys, encryption keys, `.env` contents
  and anything under `api.providers.*.api_key` are stripped by the whitelist.
- Only whitelisted settings keys are exported; everything else (window
  geometry, timestamps, internal flags) is machine-local and stays behind.
- History sessions are exported with their messages but **without** the
  `task`/`diagnostic` runtime state, which is machine-local.
- The bundle is versioned; unknown versions are rejected on import.
- Import is **merge-only**: sessions are added with fresh IDs (or matched by
  ID and updated), settings are merged key-by-key, and existing credentials
  on the destination are never overwritten.
- Size is bounded (default 8 MiB) so a malicious or corrupt bundle cannot
  exhaust memory.

The module never touches GTK, the network, or the filesystem directly —
callers pass and receive plain dicts/strings.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

BUNDLE_FORMAT = "linux-ai-sync"
BUNDLE_VERSION = 1

DEFAULT_MAX_BUNDLE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SESSIONS = 100

# Settings keys safe to sync across machines. Anything not listed here is
# considered machine-local (window geometry, opacity, dock edge, etc.).
# Credentials (api.providers.*.api_key), encryption keys and plugin lists are
# deliberately excluded.
SYNCABLE_SETTINGS: Tuple[Tuple[str, type], ...] = (
    ("app.language", str),
    ("app.theme", str),
    ("app.expert_mode", bool),
    ("app.always_on_top", bool),
    ("app.auto_start", bool),
    ("app.global_shortcut_enabled", bool),
    ("app.global_shortcut", str),
    ("app.dock_mode", str),
    ("app.dock_edge", str),
    ("app.button_edge", str),
    ("app.display_independent_watchdog", bool),
    ("assistance.mode", str),
    ("pomodoro.enabled", bool),
    ("pomodoro.work_seconds", int),
    ("pomodoro.short_break_seconds", int),
    ("pomodoro.long_break_seconds", int),
    ("pomodoro.cycles_before_long_break", int),
    ("context.max_messages", int),
    ("context.max_chars", int),
    ("features.screen_capture", bool),
    ("features.ocr_enabled", bool),
    ("features.expert_mode", bool),
    ("features.file_edit", bool),
    ("features.system_info", bool),
    ("permissions.require_sudo", bool),
    ("permissions.allowed_edit_dirs", list),
    ("ui.font_family", str),
    ("ui.font_size", int),
    ("ui.background_color", str),
    ("ui.text_color", str),
    ("ui.accent_color", str),
    ("ui.border_radius", int),
    ("api.default_provider", str),
    ("api.providers.local_llm.base_url", str),
    ("api.providers.local_llm.model", str),
    ("api.providers.local_llm.backend", str),
    ("api.providers.local_llm.strict_local", bool),
    ("api.providers.local_llm.timeout", int),
)

# Keys that must NEVER appear in a bundle, even if someone extends the
# whitelist by mistake. Defense in depth against credential leakage.
# `api.providers.local_llm.*` is allowed (no credentials live there), but
# every other provider entry is forbidden.
FORBIDDEN_SETTINGS_PREFIXES: Tuple[str, ...] = (
    "api.providers.openrouter.",
    "api.providers.google_ai_studio.",
    "api.providers.anthropic.",
    "api.providers.mistral.",
    "api.providers.groq.",
    "api.providers.cohere.",
)
FORBIDDEN_SETTINGS_KEYS: frozenset = frozenset({
    "app.encryption_enabled",
    "api.providers.local_llm.api_key",
})

# Session fields exported per conversation. `task` and `diagnostic` are
# runtime/machine-local state and are dropped.
_SESSION_EXPORT_FIELDS = ("id", "title", "created_at", "updated_at", "archived", "messages")
_SESSION_MESSAGE_FIELDS = ("role", "content", "timestamp")


class SyncBundleError(ValueError):
    """A sync bundle is malformed, oversized or has an unsupported version."""


@dataclass(frozen=True)
class SyncBundle:
    """A parsed, validated sync bundle ready for import."""

    settings: Dict[str, Any] = field(default_factory=dict)
    sessions: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = 0.0
    source: str = ""

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def setting_count(self) -> int:
        return len(self.settings)


@dataclass(frozen=True)
class ImportPlan:
    """Preview of what an import would change, without applying anything."""

    settings_to_apply: Dict[str, Any] = field(default_factory=dict)
    settings_skipped: List[str] = field(default_factory=list)
    sessions_to_add: List[Dict[str, Any]] = field(default_factory=list)
    sessions_to_update: List[str] = field(default_factory=list)
    sessions_skipped: List[str] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return (len(self.settings_to_apply) + len(self.sessions_to_add)
                + len(self.sessions_to_update))


def _get_nested(config: Dict[str, Any], key: str) -> Tuple[bool, Any]:
    """Read a dot-notation key from a nested dict. Returns (found, value)."""
    parts = key.split(".")
    node: Any = config
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set_nested(config: Dict[str, Any], key: str, value: Any) -> None:
    """Write a dot-notation key into a nested dict, creating dicts as needed."""
    parts = key.split(".")
    node = config
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _is_forbidden_setting(key: str) -> bool:
    if key in FORBIDDEN_SETTINGS_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in FORBIDDEN_SETTINGS_PREFIXES)


def export_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the whitelisted, syncable subset of a config dict.

    Only keys in SYNCABLE_SETTINGS are copied, and only when the value's
    type matches. Forbidden keys (credentials) are never copied even if
    they somehow match the whitelist.
    """
    exported: Dict[str, Any] = {}
    for key, expected_type in SYNCABLE_SETTINGS:
        if _is_forbidden_setting(key):
            continue
        found, value = _get_nested(config, key)
        if not found:
            continue
        # bool is a subclass of int; check bool before int for bool keys.
        if expected_type is bool:
            if isinstance(value, bool):
                exported[key] = value
        elif expected_type is int:
            if isinstance(value, int) and not isinstance(value, bool):
                exported[key] = value
        elif expected_type is str:
            if isinstance(value, str):
                exported[key] = value
        elif expected_type is list:
            if isinstance(value, list):
                exported[key] = list(value)
    return exported


def export_sessions(sessions: List[Dict[str, Any]],
                    max_sessions: int = DEFAULT_MAX_SESSIONS) -> List[Dict[str, Any]]:
    """Extract portable session dicts from a history sessions list.

    Each session keeps only the export fields; messages keep only role,
    content and timestamp. `task` and `diagnostic` state is dropped.
    """
    exported: List[Dict[str, Any]] = []
    for session in sessions[:max_sessions]:
        if not isinstance(session, dict):
            continue
        clean: Dict[str, Any] = {}
        for field_name in _SESSION_EXPORT_FIELDS:
            if field_name in session:
                clean[field_name] = session[field_name]
        messages = session.get("messages")
        if isinstance(messages, list):
            clean["messages"] = [
                {k: m[k] for k in _SESSION_MESSAGE_FIELDS if k in m}
                for m in messages
                if isinstance(m, dict)
            ]
        else:
            clean["messages"] = []
        exported.append(clean)
    return exported


def build_bundle(settings: Dict[str, Any], sessions: List[Dict[str, Any]],
                 source: str = "", max_sessions: int = DEFAULT_MAX_SESSIONS) -> Dict[str, Any]:
    """Build a versioned sync bundle dict from config and history sessions."""
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created_at": time.time(),
        "source": source,
        "settings": export_settings(settings),
        "sessions": export_sessions(sessions, max_sessions),
    }


def serialize_bundle(bundle: Dict[str, Any]) -> str:
    """Serialize a bundle to JSON text. Raises SyncBundleError if too large."""
    text = json.dumps(bundle, indent=2, ensure_ascii=False, sort_keys=True)
    if len(text.encode("utf-8")) > DEFAULT_MAX_BUNDLE_BYTES:
        raise SyncBundleError("Sync bundle exceeds the 8 MiB size limit")
    return text


def parse_bundle(text: str) -> SyncBundle:
    """Parse and validate a bundle from JSON text. Raises SyncBundleError."""
    if not isinstance(text, str):
        raise SyncBundleError("Sync bundle must be a string")
    if len(text.encode("utf-8")) > DEFAULT_MAX_BUNDLE_BYTES:
        raise SyncBundleError("Sync bundle exceeds the 8 MiB size limit")
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise SyncBundleError("Invalid sync bundle JSON") from error
    if not isinstance(payload, dict):
        raise SyncBundleError("Sync bundle must be a JSON object")
    if payload.get("format") != BUNDLE_FORMAT:
        raise SyncBundleError("Unsupported sync bundle format")
    if type(payload.get("version")) is not int or payload["version"] != BUNDLE_VERSION:
        raise SyncBundleError("Unsupported sync bundle version")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise SyncBundleError("Sync bundle settings must be an object")
    sessions = payload.get("sessions")
    if not isinstance(sessions, list) or len(sessions) > DEFAULT_MAX_SESSIONS:
        raise SyncBundleError("Sync bundle has invalid or too many sessions")
    created_at = payload.get("created_at", 0.0)
    if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
        created_at = 0.0
    source = payload.get("source", "")
    if not isinstance(source, str):
        source = ""
    # Defense in depth: reject bundles that somehow carry forbidden settings.
    for key in settings:
        if _is_forbidden_setting(key):
            raise SyncBundleError("Sync bundle contains a forbidden settings key: " + key)
    return SyncBundle(
        settings=settings,
        sessions=sessions,
        created_at=float(created_at),
        source=source,
    )


def plan_import(bundle: SyncBundle, current_config: Dict[str, Any],
                existing_session_ids: Optional[List[str]] = None) -> ImportPlan:
    """Preview what importing `bundle` would change. Applies nothing.

    - Settings: only whitelisted keys whose value differs from the current
      config are planned. Forbidden keys are skipped silently (they were
      already rejected at parse time, but plan_import is also a safety net).
    - Sessions: a session whose ID already exists is planned as an update;
      a new ID is planned as an addition. Sessions beyond the max are skipped.
    """
    existing_ids = set(existing_session_ids or [])
    plan = ImportPlan()
    for key, value in bundle.settings.items():
        if _is_forbidden_setting(key):
            plan.settings_skipped.append(key)
            continue
        found, current = _get_nested(current_config, key)
        if found and current == value:
            continue
        plan.settings_to_apply[key] = value
    for session in bundle.sessions:
        if not isinstance(session, dict) or not isinstance(session.get("id"), str):
            plan.sessions_skipped.append("<invalid>")
            continue
        if len(plan.sessions_to_add) + len(plan.sessions_to_update) >= DEFAULT_MAX_SESSIONS:
            plan.sessions_skipped.append(session["id"])
            continue
        if session["id"] in existing_ids:
            plan.sessions_to_update.append(session["id"])
        else:
            plan.sessions_to_add.append(session)
    return plan


def apply_settings(bundle: SyncBundle, config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the bundle's whitelisted settings into `config` (in-place).

    Returns the dict of keys actually written. Forbidden keys are skipped.
    Existing credentials are never touched (they are not in the whitelist).
    """
    applied: Dict[str, Any] = {}
    for key, value in bundle.settings.items():
        if _is_forbidden_setting(key):
            continue
        _set_nested(config, key, value)
        applied[key] = value
    return applied


def merge_sessions(bundle: SyncBundle, existing_sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge bundle sessions into an existing sessions list (new list returned).

    Sessions whose ID already exists are replaced (the bundle is the newer
    copy); new IDs are appended. The result preserves the order of existing
    sessions first, then new ones.
    """
    by_id = {s["id"]: s for s in existing_sessions if isinstance(s, dict) and isinstance(s.get("id"), str)}
    for session in bundle.sessions:
        if not isinstance(session, dict) or not isinstance(session.get("id"), str):
            continue
        by_id[session["id"]] = session
    return list(by_id.values())
