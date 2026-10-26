"""User-friendly error presentation, GTK-free and CLI-compatible.

Maps structured exceptions (AI provider errors, network errors, credential
issues) to localized, actionable messages. The window and CLI both use
:func:`present_error` so the wording stays consistent across frontends.

Design rules:
- Never expose raw exception messages, URLs, or credentials to the user.
- Every message is actionable: it says what happened and what to do next.
- Categories are stable so tests can pin them and translators can rely on
  consistent phrasing.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCategory(Enum):
    """Stable categories for error presentation and testing."""

    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    NOT_CONFIGURED = "not_configured"
    MODEL_UNAVAILABLE = "model_unavailable"
    RESPONSE_LIMIT = "response_limit"
    IMAGE_UNSUPPORTED = "image_unsupported"
    CANCELLED = "cancelled"
    CREDENTIAL_SHADOWED = "credential_shadowed"
    FILE_PERMISSION = "file_permission"
    FILE_NOT_FOUND = "file_not_found"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PresentedError:
    """A user-facing error message with its category."""

    category: ErrorCategory
    message: str
    action: Optional[str] = None
    technical: Optional[str] = None

    def __str__(self) -> str:
        text = self.message
        if self.action:
            text += " " + self.action
        return text


def _classify(error: BaseException) -> ErrorCategory:
    """Map an exception to a stable category using type and attributes."""
    name = type(error).__name__
    module = type(error).__module__

    # AI client errors (src.ai_client)
    if name == "AIRequestCancelled":
        return ErrorCategory.CANCELLED
    if name == "ProviderNotConfigured":
        return ErrorCategory.NOT_CONFIGURED
    if name == "AIResponseLimitError":
        return ErrorCategory.RESPONSE_LIMIT
    if name in ("AIImageRequestError", "AIUnsupportedImageProvider", "AIUnsupportedImageModel"):
        return ErrorCategory.IMAGE_UNSUPPORTED
    if name == "AIProviderError":
        return _classify_provider_error(error)

    # Network errors (requests)
    if module == "requests.exceptions":
        if name == "Timeout":
            return ErrorCategory.TIMEOUT
        if name == "ConnectionError":
            return ErrorCategory.NETWORK
        if name == "HTTPError":
            return _classify_http_error(error)

    # HTTP read errors (src.bounded_http)
    if name == "HTTPReadCancelled":
        return ErrorCategory.CANCELLED
    if name == "HTTPReadLimitError":
        return ErrorCategory.RESPONSE_LIMIT
    if name == "HTTPReadError":
        return ErrorCategory.NETWORK

    # File errors
    if isinstance(error, PermissionError):
        return ErrorCategory.FILE_PERMISSION
    if isinstance(error, FileNotFoundError):
        return ErrorCategory.FILE_NOT_FOUND
    if isinstance(error, ValueError):
        return ErrorCategory.VALIDATION

    return ErrorCategory.UNKNOWN


def _classify_provider_error(error: BaseException) -> ErrorCategory:
    """Sub-classify AIProviderError using its message (never shown raw)."""
    message = str(error).lower()
    if "rate" in message or "429" in message or "quota" in message:
        return ErrorCategory.RATE_LIMIT
    if "auth" in message or "401" in message or "403" in message or "key" in message:
        return ErrorCategory.AUTH
    if "timeout" in message or "timed out" in message:
        return ErrorCategory.TIMEOUT
    if "model" in message and ("not found" in message or "unavailable" in message):
        return ErrorCategory.MODEL_UNAVAILABLE
    return ErrorCategory.NETWORK


def _classify_http_error(error: BaseException) -> ErrorCategory:
    """Sub-classify requests.HTTPError by status code."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status == 401 or status == 403:
        return ErrorCategory.AUTH
    if status == 429:
        return ErrorCategory.RATE_LIMIT
    if status == 404:
        return ErrorCategory.MODEL_UNAVAILABLE
    if status is not None and 500 <= status < 600:
        return ErrorCategory.NETWORK
    return ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Message templates (English source; localized via _() at presentation time)
# ---------------------------------------------------------------------------

_MESSAGES = {
    ErrorCategory.NETWORK: (
        "Could not reach the AI provider.",
        "Check your internet connection and try again.",
    ),
    ErrorCategory.TIMEOUT: (
        "The AI provider took too long to respond.",
        "Try again, or use a faster model.",
    ),
    ErrorCategory.RATE_LIMIT: (
        "The AI provider rate limit was reached.",
        "Wait a moment and try again.",
    ),
    ErrorCategory.AUTH: (
        "The API key was rejected by the provider.",
        "Check your API key in Settings → API.",
    ),
    ErrorCategory.NOT_CONFIGURED: (
        "No API key is configured for this provider.",
        "Add your API key in Settings → API, or switch to offline mode.",
    ),
    ErrorCategory.MODEL_UNAVAILABLE: (
        "The selected model is not available.",
        "Choose a different model in Settings → API.",
    ),
    ErrorCategory.RESPONSE_LIMIT: (
        "The response was too long and was cut off.",
        "Ask for a shorter or more focused answer.",
    ),
    ErrorCategory.IMAGE_UNSUPPORTED: (
        "Image requests are not supported in this mode.",
        "Use a text-only conversation, or switch to a provider that supports images.",
    ),
    ErrorCategory.CANCELLED: (
        "The request was cancelled.",
        None,
    ),
    ErrorCategory.CREDENTIAL_SHADOWED: (
        "A stored API key is disabled by an empty environment variable.",
        "Open Settings → API to remove the empty override, or set the variable.",
    ),
    ErrorCategory.FILE_PERMISSION: (
        "Permission denied for this file operation.",
        "Check file permissions, or choose a file inside your home directory.",
    ),
    ErrorCategory.FILE_NOT_FOUND: (
        "The file could not be found.",
        "Check the path and try again.",
    ),
    ErrorCategory.VALIDATION: (
        "The input could not be processed.",
        "Check the format and try again.",
    ),
    ErrorCategory.UNKNOWN: (
        "An unexpected error occurred.",
        "Try again, or check the logs for details.",
    ),
}


def present_error(error: BaseException, *, lang: str = "en") -> PresentedError:
    """Convert an exception into a user-friendly :class:`PresentedError`.

    The returned message is safe to show in the UI: it never contains the
    raw exception text, URLs, or credentials. The ``technical`` field
    preserves a redacted error name for debugging (logged, not shown).
    """
    category = _classify(error)
    message, action = _MESSAGES.get(category, _MESSAGES[ErrorCategory.UNKNOWN])

    # Localize via i18n if available (lazy import to avoid circular deps)
    try:
        from .i18n import _
        message = _(message)
        if action:
            action = _(action)
    except Exception:
        pass

    technical = f"{type(error).__module__}.{type(error).__name__}"
    return PresentedError(
        category=category,
        message=message,
        action=action,
        technical=technical,
    )


def present_credential_shadow(provider: str, env_var: str, can_remove: bool, *, lang: str = "en") -> PresentedError:
    """Build a PresentedError for a credential shadowed by an empty env var."""
    try:
        from .i18n import _
        message = _("The stored key for {provider} is disabled by the empty {var} environment variable.").format(
            provider=provider, var=env_var,
        )
        if can_remove:
            action = _("Open Settings → API and choose '{button}' to remove the empty assignment from .env.").format(
                button=_("Use stored key (remove empty override from .env)"),
            )
        else:
            action = _("Remove or fill this variable at its source, then restart the application.")
    except Exception:
        message = f"The stored key for {provider} is disabled by the empty {env_var} environment variable."
        action = "Open Settings → API to fix the configuration."

    return PresentedError(
        category=ErrorCategory.CREDENTIAL_SHADOWED,
        message=message,
        action=action,
        technical="credential_shadow",
    )


def error_summary(errors: list) -> str:
    """Summarize a list of errors as a single user-facing string."""
    if not errors:
        return ""
    if len(errors) == 1:
        return str(present_error(errors[0]))
    presented = [present_error(e) for e in errors]
    categories = {p.category for p in presented}
    if len(categories) == 1:
        return str(presented[0]) + f" (+{len(errors) - 1} more)"
    return f"{len(errors)} errors occurred. First: {presented[0]}"
