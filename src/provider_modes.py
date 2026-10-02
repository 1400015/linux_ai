"""Assistance modes and bounded discovery of locally installed models.

Discovery never downloads models, sends prompts, follows redirects or uses an
environment proxy. A local server is still a trust boundary: the client can
reject Ollama's advertised remote models, not audit arbitrary server code.
"""

import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests


ASSISTANCE_MODES = ("auto", "offline", "local", "remote")
DISCOVERY_MAX_BYTES = 256 * 1024
DISCOVERY_MAX_MODELS = 500
DISCOVERY_MAX_SECONDS = 10.0
_CLOUD_TAG = re.compile(r"(?:^|[:/._-])cloud(?:$|[:/._-])", re.I)


@dataclass(frozen=True)
class ProviderStatus:
    mode: str
    provider: Optional[str]
    state: str
    configured: bool = False
    reachable: Optional[bool] = None
    model: str = ""
    models: Tuple[str, ...] = ()
    detail: str = ""

    @property
    def ready(self) -> bool:
        """Readiness is checked, unlike merely valid configuration."""
        return self.state == "ready"


class LocalModelError(ValueError):
    """A configuration, protocol or local model policy failure."""


def validate_local_url(base_url: str, strict_local: bool = True) -> str:
    """Validate and normalize a server URL without DNS or network access."""
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise LocalModelError("Invalid local server URL")
    try:
        parsed = urlsplit(base_url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise LocalModelError("Invalid local server URL") from error
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(ord(character) < 32 for character in base_url)):
        raise LocalModelError("Use an HTTP(S) server URL without credentials, query or fragment")
    if strict_local:
        if host.lower() == "localhost":
            # Pin the conventional name to an address rather than trusting DNS.
            host = "127.0.0.1"
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("Not loopback")
        except ValueError as error:
            raise LocalModelError("Local mode requires a loopback server (localhost, 127.0.0.1 or ::1)") from error
    if "%" in host or "\\" in parsed.path or any(part in {".", ".."} for part in parsed.path.split("/")):
        raise LocalModelError("Invalid local server URL path")
    address = "[" + host + "]" if ":" in host else host
    if port is not None:
        address += ":" + str(port)
    return urlunsplit((parsed.scheme, address, parsed.path.rstrip("/"), "", ""))


def is_cloud_model(name: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
    metadata = metadata or {}
    return bool(_CLOUD_TAG.search(name) or metadata.get("remote_host")
                or metadata.get("remote_model"))


def validate_model_name(model: str, strict_local: bool = True) -> str:
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise LocalModelError("Choose a local model")
    model = model.strip()
    if any(ord(character) < 32 for character in model):
        raise LocalModelError("Invalid model name")
    if strict_local and is_cloud_model(model):
        raise LocalModelError("Cloud models are unavailable in local mode")
    return model


def discovery_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as error:
        raise LocalModelError("Invalid connection timeout") from error
    if not math.isfinite(value) or value <= 0:
        raise LocalModelError("Invalid connection timeout")
    return min(value, DISCOVERY_MAX_SECONDS)


def _fetch_json(url: str, timeout: float) -> Dict[str, Any]:
    """One bounded GET; redirects and proxies cannot escape the local host."""
    seconds = discovery_timeout(timeout)
    deadline = time.monotonic() + seconds
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, timeout=(seconds, seconds), stream=True, allow_redirects=False) as response:
            if 300 <= response.status_code < 400:
                raise LocalModelError("Local server redirects are not followed")
            response.raise_for_status()
            try:
                length = int(response.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > DISCOVERY_MAX_BYTES:
                raise LocalModelError("Local model list exceeds the size limit")
            body = bytearray()
            # One-byte reads also enforce the deadline on trickling servers;
            # a large read can otherwise wait indefinitely for a full chunk.
            for chunk in response.iter_content(chunk_size=1):
                if time.monotonic() > deadline:
                    raise requests.Timeout("Local model discovery timed out")
                body.extend(chunk)
                if len(body) > DISCOVERY_MAX_BYTES:
                    raise LocalModelError("Local model list exceeds the size limit")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise LocalModelError("Invalid JSON from the local model server") from error
    if not isinstance(data, dict):
        raise LocalModelError("Invalid local model list")
    return data


def discover_local_models(base_url: str, backend: str = "ollama", strict_local: bool = True,
                          timeout: float = 3.0) -> Tuple[List[str], List[str]]:
    """Return installed usable models and separately rejected remote models."""
    base_url = validate_local_url(base_url, strict_local)
    timeout = discovery_timeout(timeout)
    if backend == "ollama":
        root = base_url[:-3] if base_url.endswith("/v1") else base_url
        data = _fetch_json(root + "/api/tags", timeout)
        entries = data.get("models")
        name_key = "name"
    elif backend == "openai":
        data = _fetch_json(base_url + "/models", timeout)
        entries = data.get("data")
        name_key = "id"
    else:
        raise LocalModelError("Unknown local model backend")
    if not isinstance(entries, list) or len(entries) > DISCOVERY_MAX_MODELS:
        raise LocalModelError("Invalid or oversized local model list")
    names, rejected = [], []
    for entry in entries:
        if not isinstance(entry, dict):
            raise LocalModelError("Invalid local model entry")
        name = entry.get(name_key) or entry.get("model")
        name = validate_model_name(name, strict_local=False)
        target = rejected if strict_local and is_cloud_model(name, entry) else names
        if name not in target:
            target.append(name)
    return names, rejected


def model_is_installed(model: str, installed: List[str], backend: str = "ollama") -> bool:
    if model in installed:
        return True
    # Ollama treats an absent tag as :latest, including namespace/model names.
    return backend == "ollama" and ":" not in model and model + ":latest" in installed


def make_local_request(url: str, payload: Dict[str, Any], headers: Dict[str, str],
                       timeout: float, stream: bool = False):
    """Keep inference traffic on the selected server, without proxy/redirects."""
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(url, json=payload, headers=headers, timeout=timeout,
                                stream=stream, allow_redirects=False)
        if 300 <= response.status_code < 400:
            response.close()
            raise LocalModelError("Local server redirects are not followed")
    except Exception:
        session.close()
        raise
    # The existing provider readers close their responses after JSON/stream
    # consumption. Also close the dedicated session at the same boundary.
    close_response = response.close

    def close():
        try:
            close_response()
        finally:
            session.close()

    response.close = close
    return response
