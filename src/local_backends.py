"""Native protocol support for local LLM backends (Ollama, LM Studio).

The ``local_llm`` provider speaks the OpenAI-compatible contract
(``/v1/chat/completions``) by default, which both Ollama and LM Studio also
expose. Some deployments, however, only run the *native* API:

- **Ollama native**: ``POST /api/chat`` (and ``/api/generate``) with a
  different payload shape and NDJSON streaming (no ``data:`` SSE prefix).
- **LM Studio**: exposes the OpenAI-compatible API at ``/v1`` *and* a native
  REST API at ``/api/v0`` (``/api/v0/models`` for discovery, ``/api/v0/chat``
  for inference). LM Studio's model identifiers include a publisher prefix
  (``publisher/model-name``) that the OpenAI endpoint also accepts.

This module is GTK-free and network-free at import time. It provides:

- ``LOCAL_BACKENDS``: the set of supported backend identifiers.
- ``BACKEND_LABELS``: human-readable labels for the settings UI.
- ``normalize_base_url()``: per-backend URL normalization on save.
- ``discovery_path()``: the GET path used to list installed models.
- ``chat_path()``: the POST path used for inference.
- ``build_chat_payload()``: native request body for a backend.
- ``iter_native_chat_events()``: bounded NDJSON parser for native streams.

The OpenAI-compatible path (``backend == "openai"`` or ``"ollama"`` with the
``/v1`` shim) stays in ``ai_client.py``; this module only handles the native
protocols so the dispatch stays small.
"""

import json
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .provider_modes import LocalModelError, validate_model_name
from .stream_events import iter_bounded_lines, StreamLimitError, StreamReadCancelled

logger = logging.getLogger(__name__)

# Backend identifiers accepted in api.providers.local_llm.backend.
# - "ollama": Ollama via its OpenAI-compatible /v1 shim (default, unchanged).
# - "openai": any OpenAI-compatible server (vLLM, llama.cpp, text-gen-webui).
# - "ollama-native": Ollama native API (/api/chat, /api/tags).
# - "lmstudio": LM Studio native API (/api/v0/chat, /api/v0/models).
LOCAL_BACKENDS = ("ollama", "openai", "ollama-native", "lmstudio")

BACKEND_LABELS = {
    "ollama": "Ollama (OpenAI-compatible)",
    "openai": "OpenAI-compatible",
    "ollama-native": "Ollama (native API)",
    "lmstudio": "LM Studio",
}

# Backends whose URL is normalized to end with /v1 (OpenAI-compatible path).
_V1_BACKENDS = ("ollama", "openai")

# Default ports per backend, used only for documentation/onboarding hints.
DEFAULT_PORTS = {
    "ollama": 11434,
    "ollama-native": 11434,
    "openai": 1234,
    "lmstudio": 1234,
}


def normalize_base_url(base_url: str, backend: str) -> str:
    """Normalize a base URL for the selected backend.

    OpenAI-compatible backends (``ollama``, ``openai``) get a trailing ``/v1``
    so the chat path can append ``/chat/completions``. Native backends keep
    the bare server root: Ollama native uses ``/api/*`` and LM Studio uses
    ``/api/v0/*``.
    """
    if backend not in LOCAL_BACKENDS:
        raise LocalModelError("Unknown local model backend")
    if backend in _V1_BACKENDS and not base_url.endswith("/v1"):
        return base_url + "/v1"
    if backend in ("ollama-native", "lmstudio"):
        # Strip a trailing /v1 if the user pasted the OpenAI-compatible URL;
        # the native paths are rooted at the server root.
        if base_url.endswith("/v1"):
            return base_url[:-3]
    return base_url


def discovery_path(backend: str) -> str:
    """GET path (relative to the normalized base) that lists installed models."""
    if backend == "ollama-native":
        return "/api/tags"
    if backend == "lmstudio":
        return "/api/v0/models"
    if backend == "openai":
        return "/models"
    # "ollama" (OpenAI-compatible shim): same as openai, rooted at /v1.
    return "/models"


def chat_path(backend: str) -> str:
    """POST path (relative to the normalized base) for chat inference."""
    if backend == "ollama-native":
        return "/api/chat"
    if backend == "lmstudio":
        return "/api/v0/chat"
    return "/chat/completions"


def build_chat_payload(backend: str, model: str, messages: List[Dict[str, str]],
                       temperature: float, max_tokens: int,
                       stream: bool = False) -> Dict[str, Any]:
    """Build the native request body for a backend.

    Raises LocalModelError for an unknown backend or an invalid model name
    so callers fail before any network traffic.
    """
    if backend not in LOCAL_BACKENDS:
        raise LocalModelError("Unknown local model backend")
    model = validate_model_name(model, strict_local=False)
    if backend == "ollama-native":
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": bool(stream),
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        return payload
    if backend == "lmstudio":
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": bool(stream),
        }
        return payload
    # OpenAI-compatible (ollama shim, openai generic).
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stream:
        payload["stream"] = True
    return payload


def parse_discovery_response(backend: str, data: Dict[str, Any]) -> Tuple[List[str], str]:
    """Extract (model names, name key used) from a discovery response body.

    Returns the list of raw entries and the key that held each model name, so
    the caller can apply cloud-model rejection uniformly. Raises
    LocalModelError for malformed bodies.
    """
    if backend == "ollama-native":
        entries = data.get("models")
        name_key = "name"
    elif backend == "lmstudio":
        # LM Studio /api/v0/models returns {"object": "list", "data": [...]}.
        entries = data.get("data")
        name_key = "id"
    elif backend in ("ollama", "openai"):
        entries = data.get("data")
        name_key = "id"
    else:
        raise LocalModelError("Unknown local model backend")
    if not isinstance(entries, list):
        raise LocalModelError("Invalid local model list")
    return entries, name_key


def iter_native_chat_events(response, cancel_event=None) -> Iterator[Dict[str, Any]]:
    """Parse a native NDJSON chat stream into event dicts.

    Both Ollama native (``/api/chat``) and LM Studio (``/api/v0/chat``) stream
    newline-delimited JSON objects — one per line, no ``data:`` prefix. Each
    line is a JSON object; empty lines are ignored. Bounded by the same
    stream limits as SSE (bytes, events, seconds, cancellation).
    """
    for raw in iter_bounded_lines(response, cancel_event):
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            raise LocalModelError("Invalid JSON from the local model server") from error
        if isinstance(data, dict):
            yield data


def extract_chat_text(backend: str, event: Dict[str, Any]) -> Optional[str]:
    """Extract the text delta from one native stream event, or None.

    - Ollama native: ``{"message": {"content": "..."}, "done": false}``
    - LM Studio: OpenAI-compatible shape inside /api/v0:
      ``{"choices": [{"delta": {"content": "..."}}]}``
    """
    if backend == "ollama-native":
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content:
                return content
        return None
    if backend == "lmstudio":
        choices = event.get("choices") or []
        if choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                return content
        return None
    return None


def extract_chat_usage(backend: str, event: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Extract token usage from a native stream's final event, or None.

    Ollama native reports ``prompt_eval_count`` / ``eval_count`` on the final
    ``done: true`` event. LM Studio may include an OpenAI-style ``usage``
    object on the last chunk.
    """
    if backend == "ollama-native":
        if not event.get("done"):
            return None
        prompt = event.get("prompt_eval_count")
        output = event.get("eval_count")
        if isinstance(prompt, int) and isinstance(output, int):
            return {"prompt_tokens": prompt, "completion_tokens": output,
                    "total_tokens": prompt + output}
        return None
    if backend == "lmstudio":
        usage = event.get("usage")
        if isinstance(usage, dict):
            return usage
        return None
    return None


def is_stream_done(backend: str, event: Dict[str, Any]) -> bool:
    """True when a native stream event marks the end of the response."""
    if backend == "ollama-native":
        return bool(event.get("done"))
    if backend == "lmstudio":
        choices = event.get("choices") or []
        if choices and isinstance(choices[0], dict):
            return choices[0].get("finish_reason") is not None
        return False
    return False


def extract_error_message(event: Dict[str, Any]) -> Optional[str]:
    """Extract a human-readable error from a native event, if present."""
    error = event.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None


__all__ = [
    "LOCAL_BACKENDS",
    "BACKEND_LABELS",
    "DEFAULT_PORTS",
    "LocalModelError",
    "normalize_base_url",
    "discovery_path",
    "chat_path",
    "build_chat_payload",
    "parse_discovery_response",
    "iter_native_chat_events",
    "extract_chat_text",
    "extract_chat_usage",
    "is_stream_done",
    "extract_error_message",
    "StreamLimitError",
    "StreamReadCancelled",
]
