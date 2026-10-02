"""SSE framing independent of any provider or GTK."""

import json


def iter_sse_json(response):
    fields = []
    for raw in response.iter_lines():
        line = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        if not line:
            if fields:
                value = '\n'.join(fields)
                fields = []
                if value.strip() == '[DONE]':
                    return
                data = json.loads(value)
                if isinstance(data, dict):
                    yield data
            continue
        if not line.startswith('data:'):
            continue
        value = line[5:]
        if value.startswith(' '):
            value = value[1:]
        # Accept providers sending adjacent complete JSON events without blank
        # separators, while retaining support for multiline SSE JSON fields.
        if fields:
            previous = '\n'.join(fields)
            try:
                data = json.loads(previous)
            except json.JSONDecodeError:
                pass
            else:
                fields = []
                if isinstance(data, dict):
                    yield data
        if value.strip() == '[DONE]':
            return
        fields.append(value)
    if fields:
        data = json.loads('\n'.join(fields))
        if isinstance(data, dict):
            yield data
