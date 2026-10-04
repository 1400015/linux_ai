"""Explicit, bounded model discovery on the selected provider's API host."""

import json
import time
from typing import List
from urllib.parse import urlsplit

import requests
from requests.auth import AuthBase

MAX_BYTES = 2 * 1024 * 1024
MAX_MODELS = 2000
MAX_PAGES = 5
_HOSTS = {
    'openrouter': {'openrouter.ai'}, 'google_ai_studio': {'generativelanguage.googleapis.com'},
    'anthropic': {'api.anthropic.com'}, 'mistral': {'api.mistral.ai'},
    'groq': {'api.groq.com'}, 'cohere': {'api.cohere.ai', 'api.cohere.com'},
}
_PATHS = {
    'openrouter': {'/api/v1'}, 'google_ai_studio': {'/v1', '/v1beta'},
    'anthropic': {'/v1'}, 'mistral': {'/v1'}, 'groq': {'/openai/v1'}, 'cohere': {'/v1', '/v2'},
}


class ModelDiscoveryError(ValueError):
    """Messages must contain neither credentials nor provider response bodies."""


class _ExplicitAuth(AuthBase):
    def __call__(self, request):
        return request  # Retain proxies/CA, but never pick up .netrc credentials.


def _endpoint(provider, base_url):
    if provider not in _HOSTS or not isinstance(base_url, str):
        raise ModelDiscoveryError('Model listing is unavailable for this provider.')
    try:
        parsed = urlsplit(base_url)
        allowed = (parsed.scheme == 'https' and parsed.hostname in _HOSTS[provider]
                   and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                   and not parsed.query and not parsed.fragment and parsed.path.rstrip('/') in _PATHS[provider])
    except ValueError:
        allowed = False
    if not allowed:
        raise ModelDiscoveryError('Model listing requires the standard HTTPS endpoint of the selected provider.')
    return base_url.rstrip('/') + '/models'


def _model_name(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and all(character.isprintable() and not character.isspace() for character in value)
            and '?' not in value and '#' not in value)


def _extract(provider, payload):
    if not isinstance(payload, dict):
        raise ModelDiscoveryError('The provider returned an invalid model list.')
    records = payload.get('models' if provider in ('google_ai_studio', 'cohere') else 'data')
    if not isinstance(records, list) or len(records) > MAX_MODELS:
        raise ModelDiscoveryError('The provider returned an invalid or oversized model list.')
    models = []
    for record in records:
        if not isinstance(record, dict):
            raise ModelDiscoveryError('The provider returned an invalid model record.')
        if record.get('active') is False or record.get('is_deprecated') is True:
            continue
        if provider == 'google_ai_studio':
            methods = record.get('supportedGenerationMethods', [])
            if not isinstance(methods, list) or 'generateContent' not in methods:
                continue
            name = record.get('name')
            if isinstance(name, str) and name.startswith('models/'):
                name = name[len('models/'):]
        elif provider == 'cohere':
            endpoints = record.get('endpoints')
            if endpoints is not None and (not isinstance(endpoints, list) or 'chat' not in endpoints):
                continue
            name = record.get('name')
        else:
            name = record.get('id')
            capabilities = record.get('capabilities')
            if isinstance(capabilities, dict) and capabilities.get('completion_chat') is False:
                continue
            architecture = record.get('architecture')
            if isinstance(architecture, dict) and 'output_modalities' in architecture:
                outputs = architecture['output_modalities']
                if not isinstance(outputs, list) or 'text' not in outputs:
                    continue
            if isinstance(name, str) and any(word in name.lower() for word in ('whisper', 'embed', 'tts', 'rerank')):
                continue
        if not _model_name(name):
            raise ModelDiscoveryError('The provider returned an invalid model identifier.')
        models.append(name)
    return models


def discover_remote_models(provider, settings, key, *, timeout=8.0, session_factory=requests.Session,
                           clock=time.monotonic) -> List[str]:
    """Called only by an explicit UI/client request; never chooses a model."""
    endpoint = _endpoint(provider, settings.get('base_url'))
    if not isinstance(key, str) or not key or any(ord(character) < 32 for character in key):
        raise ModelDiscoveryError('Configure a valid API key before listing models.')
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ModelDiscoveryError('The model-listing timeout is invalid.')
    headers = {'Accept': 'application/json'}
    if provider == 'google_ai_studio':
        headers['x-goog-api-key'] = key
    elif provider == 'anthropic':
        headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + key
    params = {'endpoint': 'chat'} if provider == 'cohere' else {}
    deadline = clock() + timeout
    total_bytes = 0
    names = set()
    seen_pages = set()
    try:
        with session_factory() as session:
            for _ in range(MAX_PAGES):
                remaining = deadline - clock()
                if remaining <= 0:
                    raise ModelDiscoveryError('Model listing exceeded its deadline.')
                with session.get(endpoint, headers=headers, params=dict(params), stream=True,
                                 allow_redirects=False, auth=_ExplicitAuth(),
                                 timeout=(min(3.0, remaining), remaining)) as response:
                    if response.status_code != 200:
                        raise ModelDiscoveryError('The provider refused model listing; check the key and endpoint.')
                    chunks = bytearray()
                    # Checking each received byte prevents slow partial chunks
                    # from keeping this request alive past its overall deadline.
                    for chunk in response.iter_content(chunk_size=1):
                        if clock() >= deadline:
                            raise ModelDiscoveryError('Model listing exceeded its deadline.')
                        total_bytes += len(chunk)
                        if total_bytes > MAX_BYTES:
                            raise ModelDiscoveryError('The model list exceeded its size limit.')
                        chunks.extend(chunk)
                    if clock() >= deadline:
                        raise ModelDiscoveryError('Model listing exceeded its deadline.')
                    try:
                        payload = json.loads(chunks.decode('utf-8'))
                    except (ValueError, UnicodeError):
                        raise ModelDiscoveryError('The provider returned invalid JSON.') from None
                names.update(_extract(provider, payload))
                if len(names) > MAX_MODELS:
                    raise ModelDiscoveryError('The model list exceeded its item limit.')
                if provider == 'google_ai_studio':
                    token, field = payload.get('nextPageToken'), 'pageToken'
                elif provider == 'anthropic' and payload.get('has_more'):
                    token, field = payload.get('last_id'), 'after_id'
                elif provider == 'cohere':
                    token, field = payload.get('next_page_token'), 'page_token'
                else:
                    token, field = None, ''
                if token is None or token == '':
                    if provider == 'anthropic' and payload.get('has_more'):
                        raise ModelDiscoveryError('The provider returned invalid pagination.')
                    return sorted(names)
                if not isinstance(token, str) or len(token) > 2048 or token in seen_pages:
                    raise ModelDiscoveryError('The provider returned invalid pagination.')
                seen_pages.add(token)
                params[field] = token
            raise ModelDiscoveryError('The model list exceeded its page limit.')
    except ModelDiscoveryError:
        raise
    except Exception:
        raise ModelDiscoveryError('Model listing failed; check the connection and provider settings.') from None
