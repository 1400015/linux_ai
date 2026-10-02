"""Validate the bundled v0.1 contracts without resolving external schemas."""

from functools import lru_cache
import json
from pathlib import Path


class SchemaError(ValueError):
    pass


@lru_cache(maxsize=8)
def validator(name):
    if name not in {'system-context', 'knowledge-module', 'action-request', 'audit-event'}:
        raise SchemaError('Unknown bundled schema')
    from jsonschema import Draft202012Validator
    path = Path(__file__).with_name('knowledge_data') / 'schema' / (name + '.schema.json')
    schema = json.loads(path.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_schema(value, name):
    """Return an independent JSON value; arbitrary Python objects are rejected."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 512 * 1024:
            raise SchemaError('Schema input exceeds the size limit')
        clean = json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise SchemaError('Invalid JSON-compatible contract data') from error
    try:
        error = next(validator(name).iter_errors(clean), None)
    except RecursionError as error:
        raise SchemaError('Contract nesting exceeds the limit') from error
    if error is not None:
        location = '.'.join(str(part) for part in error.path) or 'root'
        # Do not interpolate offending values: they may contain a secret.
        raise SchemaError('Invalid {} at {}'.format(name, location))
    return clean
