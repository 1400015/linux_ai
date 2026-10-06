"""Bounded, validated bundled content. Knowledge cannot register executable code."""

from functools import lru_cache
from datetime import date
import logging
from pathlib import Path

from .schema_validation import SchemaError, validate_schema

MAX_MODULE_BYTES = 128 * 1024
KNOWN_ACTIONS = frozenset(('packages.search', 'packages.install', 'display.list_modes',
    'display.apply_mode', 'services.list', 'services.status', 'services.control',
    'files.checksum', 'storage.list'))
KNOWN_PROBES = frozenset(('', 'links', 'addresses', 'routes', 'routes6', 'disk', 'inodes', 'memory'))
_bundled_unavailable = False


class _YamlUnavailable(ImportError):
    """The YAML dependency could not be imported by the strict loader."""


def load_module(source):
    try:
        import yaml
    except ImportError as error:
        raise _YamlUnavailable('PyYAML is unavailable') from error

    class StrictLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise SchemaError('YAML aliases are not supported')
            if self.peek_event().anchor is not None:
                raise SchemaError('YAML anchors are not supported')
            self.depth = getattr(self, 'depth', 0) + 1
            if self.depth > 20:
                raise SchemaError('Knowledge nesting exceeds the limit')
            try:
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1

        def construct_mapping(self, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if type(key) is not str or key in mapping:
                    raise SchemaError('Knowledge keys must be unique strings')
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    if type(source) is not str or len(source.encode('utf-8')) > MAX_MODULE_BYTES:
        raise SchemaError('Knowledge module exceeds the size limit')
    try:
        value = yaml.load(source, Loader=StrictLoader)
    except yaml.YAMLError as error:
        raise SchemaError('Invalid safe YAML content') from error
    value = validate_schema(value, 'knowledge-module')
    if set(value['action_ids']) - KNOWN_ACTIONS:
        raise SchemaError('Unknown capability reference')
    for procedure in value['procedures']:
        try:
            date.fromisoformat(procedure['reviewed_at'])
        except ValueError as error:
            raise SchemaError('Invalid procedure review date') from error
        if any(step['probe_key'] not in KNOWN_PROBES for step in procedure['steps']):
            raise SchemaError('Unknown read probe reference')
        if procedure['verification'] == 'tested' and not procedure['tested_versions']:
            raise SchemaError('Tested procedures require explicit tested versions')
    return value


def validate_modules(modules):
    by_id = {}
    procedures, families = set(), set()
    for module in modules:
        if module['id'] in by_id:
            raise SchemaError('Duplicate knowledge module')
        by_id[module['id']] = module
        for values, field, seen in ((module['procedures'], 'id', procedures), (module['facts'], 'family', families)):
            for record in values:
                if record[field] in seen:
                    raise SchemaError('Duplicate knowledge record')
                seen.add(record[field])
    visiting, visited = set(), set()

    def visit(identifier):
        if identifier not in by_id:
            raise SchemaError('Missing knowledge dependency')
        if identifier in visiting:
            raise SchemaError('Cyclic knowledge dependency')
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in by_id[identifier]['requires']:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in by_id:
        visit(identifier)
    return modules


@lru_cache(maxsize=1)
def bundled_modules():
    directory = Path(__file__).with_name('knowledge_data')
    paths = sorted(directory.glob('*.yaml'))
    if not paths or len(paths) > 64:
        raise SchemaError('Missing or oversized bundled knowledge collection')
    modules = []
    for path in paths:
        with path.open(encoding='utf-8') as stream:
            modules.append(load_module(stream.read(MAX_MODULE_BYTES + 1)))
    return tuple(validate_modules(modules))


def available_bundled_modules():
    """Disable unavailable YAML references without blocking startup.

    The strict loader remains available to validation tools. No invalid or
    partially validated module gains authority; built-in Python references can
    still be used with an explicit degraded-knowledge warning.
    """
    global _bundled_unavailable
    try:
        modules = bundled_modules()
    except (_YamlUnavailable, OSError, ValueError, UnicodeError, RecursionError):
        if not _bundled_unavailable:
            logging.getLogger(__name__).warning(
                'Bundled YAML knowledge is unavailable; reinstall the application. '
                'Only built-in reference material remains available.')
        _bundled_unavailable = True
        return ()
    _bundled_unavailable = False
    return modules


def knowledge_warning(lang='en'):
    """Describe degraded knowledge without including source data or errors."""
    if not _bundled_unavailable:
        return ''
    if lang == 'pt':
        return ('A base YAML está indisponível. Só as referências incorporadas '
                'continuam disponíveis; reinstala a aplicação para recuperar os módulos.')
    return ('Bundled YAML knowledge is unavailable. Only built-in references '
            'remain available; reinstall the application to recover the modules.')


def compose_modules(context, platform=None):
    """Select components independently; distro dependencies never claim activity.

    ``platform`` modules declare their platforms in ``match.distro_ids`` and
    compose only when the detected (or explicitly validated) platform matches.
    """
    from .platform import effective_platform
    current = effective_platform(platform)
    identifiers = {getattr(context, name).identifier for name in ('package_manager', 'service_manager', 'audio', 'network')}
    return tuple(module for module in available_bundled_modules() if module['kind'] == 'linux'
                 or (module['kind'] == 'platform' and current in module['match']['distro_ids'])
                 or context.distro_id in module['match']['distro_ids']
                 or set(context.id_like).intersection(module['match']['distro_ids'])
                 or identifiers.intersection(module['match']['components']))


def legacy_facts(record):
    result = dict(record)
    for key in ('docs_urls', 'distinct'):
        result[key] = tuple(result[key])
    result['firewall_status'] = tuple(tuple(argv) for argv in result['firewall_status'])
    return result


def legacy_procedure(record, step_type):
    result = dict(record)
    for key in ('title', 'summary', 'keywords', 'sources', 'families', 'versions', 'tested_versions', 'version_scope', 'recovery'):
        result[key] = tuple(result[key])
    result['steps'] = tuple(step_type(tuple(step['instruction']), tuple(step['interpretation']),
                                    step['command'], step['probe_key']) for step in result['steps'])
    return result
