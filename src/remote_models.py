"""Explicit, bounded model discovery on the selected provider's API host."""

import json
import math
import multiprocessing
import os
import select
import signal
import subprocess
import sys
import time
from typing import List
from urllib.parse import urlsplit

import requests
from requests.auth import AuthBase

if __package__:
    from .bounded_http import HTTPReadError, HTTPReadLimitError, _decoded_chunks
else:  # The production worker runs this installed file with Python -I.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.bounded_http import HTTPReadError, HTTPReadLimitError, _decoded_chunks

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
    'anthropic': {'/v1'}, 'mistral': {'/v1'}, 'groq': {'/openai/v1'}, 'cohere': {'/v1'},
}


class ModelDiscoveryError(ValueError):
    """Messages must contain neither credentials nor provider response bodies."""


def _http_error(status):
    """Explain refusal using fixed messages without reading a response body."""
    messages = {
        400: 'The provider rejected the model-listing request (HTTP 400). Check the API key and endpoint.',
        401: 'The provider rejected the API key (HTTP 401).',
        403: 'The provider denied access (HTTP 403). Check the API key permissions.',
        404: 'The model-listing endpoint was not found (HTTP 404).',
        429: 'The provider rate limit or quota was exceeded (HTTP 429). Try again later.',
    }
    if status in messages:
        return messages[status]
    if 500 <= status < 600:
        return 'The provider service is unavailable (HTTP 5xx). Try again later.'
    return 'The provider refused model listing; check the key and endpoint.'


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


def _collect_models(provider, endpoint, headers, timeout, session_factory, clock):
    """Run exclusively in the disposable worker, with no background readers."""
    deadline = clock() + timeout
    params = {'endpoint': 'chat'} if provider == 'cohere' else {}
    total_bytes = 0
    names = set()
    seen_pages = set()

    def remaining_time():
        remaining = deadline - clock()
        if remaining <= 0:
            raise ModelDiscoveryError('Model listing exceeded its deadline.')
        return remaining

    with session_factory() as session:
        for _ in range(MAX_PAGES):
            remaining = remaining_time()
            if total_bytes >= MAX_BYTES:
                raise ModelDiscoveryError('The model list exceeded its size limit.')
            response = session.get(
                endpoint, headers=headers, params=dict(params), stream=True,
                allow_redirects=False, auth=_ExplicitAuth(),
                timeout=(min(3.0, remaining), remaining))
            try:
                if response.status_code != 200:
                    raise ModelDiscoveryError(_http_error(response.status_code))
                remaining_time()
                chunks = bytearray()
                available = MAX_BYTES - total_bytes
                # Read/decode synchronously in the worker. Its supervisor can
                # terminate even a stuck socket, gzip header or decoder call.
                # Decoding remains bounded per allocation and in aggregate.
                for chunk in _decoded_chunks(response, 8192, available,
                                             available + 65536, remaining_time):
                    remaining_time()
                    total_bytes += len(chunk)
                    chunks.extend(chunk)
                remaining_time()
                try:
                    payload = json.loads(chunks.decode('utf-8'))
                except (ValueError, UnicodeError):
                    raise ModelDiscoveryError('The provider returned invalid JSON.') from None
            finally:
                response.close()
            names.update(_extract(provider, payload))
            remaining_time()
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


_RESULT_BYTES = 3 * 1024 * 1024
_POLL_SECONDS = 0.05


def _discovery_worker(read_fd, write_fd, arguments):
    """Send only bounded model IDs or fixed errors, never exception details."""
    if read_fd is not None:
        os.close(read_fd)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            result = {'models': _collect_models(*arguments)}
        except ModelDiscoveryError as error:
            result = {'error': str(error)}
        except HTTPReadLimitError:
            result = {'error': 'Model listing exceeded its size or time limit.'}
        except HTTPReadError:
            result = {'error': 'Model listing failed while reading the provider response.'}
        except BaseException:
            result = {'error': 'Model listing failed; check the connection and provider settings.'}
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > _RESULT_BYTES:
            encoded = b'{"error":"The model list exceeded its size limit."}'
        offset = 0
        while offset < len(encoded):
            offset += os.write(write_fd, encoded[offset:offset + 8192])
    except (OSError, ValueError):
        pass
    finally:
        os.close(write_fd)


def _production_worker(command_fd, result_fd):
    global MAX_BYTES, MAX_MODELS, MAX_PAGES
    try:
        command = bytearray()
        while True:
            block = os.read(command_fd, 4096)
            if not block:
                break
            command.extend(block)
            if len(command) > 16384:
                raise ValueError('Oversized discovery control record.')
        values = json.loads(command.decode('utf-8'))
        MAX_BYTES, MAX_MODELS, MAX_PAGES = values['limits']
        arguments = (values['provider'], values['endpoint'], values['headers'], values['timeout'],
                     requests.Session, time.monotonic)
    except (KeyError, ValueError, TypeError, OSError):
        os.close(result_fd)
        return
    finally:
        os.close(command_fd)
    # Parent supervision is primary. This backup also bounds the worker's
    # lifetime if the frontend dies during headers or a blocked decoder read.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, values['timeout'])
    _discovery_worker(None, result_fd, arguments)


def _start_worker(read_fd, write_fd, arguments):
    provider, endpoint, headers, timeout, session_factory, clock = arguments
    if session_factory is not requests.Session or clock is not time.monotonic:
        # Explicit test/transport injection requires Linux fork. The normal
        # application path always execs a clean interpreter from its GUI thread.
        context = multiprocessing.get_context('fork')
        worker = context.Process(target=_discovery_worker, args=(read_fd, write_fd, arguments),
                                 name='linux-ai-model-discovery', daemon=True)
        worker.start()
        return worker, None, None
    command_read, command_write = os.pipe()
    try:
        payload = json.dumps({'provider': provider, 'endpoint': endpoint, 'headers': headers,
                              'timeout': timeout, 'limits': [MAX_BYTES, MAX_MODELS, MAX_PAGES]},
                             separators=(',', ':')).encode('utf-8')
        if len(payload) > 16384:
            raise ModelDiscoveryError('The model-listing control record is too large.')
        os.set_blocking(command_write, False)
        worker = subprocess.Popen(
            [sys.executable, '-I', os.path.abspath(__file__), str(command_read), str(write_fd)],
            pass_fds=(command_read, write_fd), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return worker, command_write, payload
    except BaseException:
        os.close(command_write)
        raise
    finally:
        os.close(command_read)


def _reap_worker(worker):
    """Never leave a timed-out request or an unreaped child behind."""
    if isinstance(worker, subprocess.Popen):
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                worker.kill()
        worker.wait()
    else:
        if worker.is_alive():
            worker.terminate()
            worker.join(0.1)
        if worker.is_alive():
            worker.kill()
        worker.join()
        worker.close()


def discover_remote_models(provider, settings, key, *, timeout=8.0, session_factory=requests.Session,
                           clock=time.monotonic, cancel_event=None) -> List[str]:
    """Explicit discovery with a total deadline over an isolated HTTP worker.

    A clean Python -I subprocess creates its own Session; a transport that
    ignores timeouts cannot consume a permanent reader slot. Fork is reserved
    for explicitly injected test transports/clocks, never the GUI's normal path.
    TLS, proxy and CA behavior stays with Requests; redirects and netrc auth
    remain disabled. Cancellation/deadline always terminates and reaps it.
    """
    endpoint = _endpoint(provider, settings.get('base_url'))
    if (not isinstance(key, str) or not key or len(key) > 4096
            or any(ord(character) < 32 for character in key)):
        raise ModelDiscoveryError('Configure a valid API key before listing models.')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ModelDiscoveryError('The model-listing timeout is invalid.')
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'gzip, deflate'}
    if provider == 'google_ai_studio':
        headers['x-goog-api-key'] = key
    elif provider == 'anthropic':
        headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + key
    deadline = clock() + timeout

    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise ModelDiscoveryError('Model listing was cancelled.')
        if clock() >= deadline:
            raise ModelDiscoveryError('Model listing exceeded its deadline.')

    check()
    read_fd, write_fd = os.pipe()
    worker = None
    command_fd = None
    try:
        os.set_blocking(read_fd, False)
        arguments = (provider, endpoint, headers, timeout, session_factory, clock)
        worker, command_fd, command = _start_worker(read_fd, write_fd, arguments)
        os.close(write_fd)
        write_fd = None
        result = bytearray()
        while True:
            check()
            ready, writable, _ = select.select([read_fd], [command_fd] if command_fd is not None else [],
                                               [], min(_POLL_SECONDS, max(0, deadline - clock())))
            if writable:
                count = os.write(command_fd, command[:4096])
                command = command[count:]
                if not command:
                    os.close(command_fd)
                    command_fd = None
            if not ready:
                continue
            block = os.read(read_fd, 65536)
            if not block:
                break
            result.extend(block)
            if len(result) > _RESULT_BYTES:
                raise ModelDiscoveryError('The model list exceeded its size limit.')
        check()
        try:
            payload = json.loads(result.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ModelDiscoveryError('Model listing failed while reading the provider response.') from None
        if not isinstance(payload, dict):
            raise ModelDiscoveryError('Model listing failed while reading the provider response.')
        if 'error' in payload:
            raise ModelDiscoveryError(payload['error'])
        models = payload.get('models')
        if not isinstance(models, list) or len(models) > MAX_MODELS or not all(_model_name(name) for name in models):
            raise ModelDiscoveryError('The provider returned an invalid model list.')
        return models
    except ModelDiscoveryError:
        raise
    except Exception:
        raise ModelDiscoveryError('Model listing failed; check the connection and provider settings.') from None
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if command_fd is not None:
            os.close(command_fd)
        if worker is not None:
            _reap_worker(worker)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(2)
    _production_worker(int(sys.argv[1]), int(sys.argv[2]))
