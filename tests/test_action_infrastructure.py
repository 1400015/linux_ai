"""Authorization, provenance, changed targets and offline composition invariants."""

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.action_audit import ActionAudit, command_detail
from src.action_contract import ActionDefinition, ActionExecutor, ActionResult
from src.conversation_actions import ConversationActions
from src.history_store import HistoryStore
from src.inspection_actions import ChecksumAdapter, StorageAdapter
from src.knowledge_loader import bundled_modules, compose_modules, load_module, validate_modules
from src.local_knowledge import PROCEDURE_BY_ID
from src.offline_assistant import DistroInfo
from src.schema_validation import SchemaError
from src.service_actions import ServiceCandidate, ServiceService
from src.system_context import Component, SystemContext, compatible_distro, detect_system_context


def context(manager='runit', state='active', distro='void', package='xbps'):
    result = detect_system_context(DistroInfo(distro_id=distro, pkg_manager=package),
        which=lambda name: '/usr/bin/' + name if name in {'sv', 'xbps-install'} else None,
        environ={}, exists=lambda path: False, read_text=lambda path: 'runit' if path == '/proc/1/comm' else '', clock=lambda: 100)
    return replace(result, service_manager=Component(manager, state))


class ContextTests(unittest.TestCase):
    def detect(self, **kwargs):
        defaults = dict(which=lambda tool: None, environ={}, read_text=lambda path: '', exists=lambda path: False, clock=lambda: 100)
        defaults.update(kwargs)
        return detect_system_context(DistroInfo(distro_id='void', pkg_manager='xbps', service_manager='runit'), **defaults)

    def test_installed_service_client_does_not_prove_activity(self):
        ctx = self.detect(which=lambda tool: '/usr/bin/sv' if tool == 'sv' else None)
        self.assertEqual(ctx.service_manager, Component('runit', 'installed', ctx.service_manager.evidence))
        self.assertFalse(ctx.service_manager.active)

    def test_ambiguous_clients_remain_unknown(self):
        ctx = self.detect(which=lambda tool: '/usr/bin/' + tool if tool in {'sv', 'systemctl', 'apt-get', 'xbps-install'} else None)
        self.assertEqual(ctx.service_manager.identifier, 'unknown')
        self.assertEqual(ctx.package_manager.identifier, 'xbps')

    def test_distro_does_not_override_observed_init(self):
        ctx = self.detect(read_text=lambda path: 'systemd' if path == '/proc/1/comm' else '')
        self.assertEqual(ctx.service_manager.identifier, 'systemd')
        old = DistroInfo(distro_id='void', service_manager='runit')
        self.assertEqual(compatible_distro(old, ctx).service_manager, 'systemd')

    def test_wayland_wins_over_xwayland_display(self):
        ctx = self.detect(environ={'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0', 'XDG_CURRENT_DESKTOP': 'KDE'})
        self.assertEqual(ctx.session.identifier, 'wayland')
        self.assertEqual(ctx.compositor.state, 'inferred')

    def test_context_roundtrip_revision_and_strict_schema(self):
        ctx = context()
        self.assertEqual(SystemContext.from_dict(ctx.to_dict()), ctx)
        self.assertEqual(replace(ctx, observed_at=200).revision, ctx.revision)
        self.assertNotEqual(replace(ctx, service_manager=Component()).revision, ctx.revision)
        value = ctx.to_dict()
        value['execute'] = 'rm -rf /'
        with self.assertRaises(SchemaError):
            SystemContext.from_dict(value)


class KnowledgeTests(unittest.TestCase):
    def source(self):
        return Path(__file__).parents[1].joinpath('src/knowledge_data/linux-core.yaml').read_text()

    def test_bundled_composition_preserves_provenance_and_retrieval(self):
        modules = bundled_modules()
        self.assertEqual(len(modules), 7)
        self.assertEqual(sum(len(item['procedures']) for item in modules), 9)
        self.assertEqual(PROCEDURE_BY_ID['service-runit'].reviewed_at, '2026-10-02')
        self.assertEqual(PROCEDURE_BY_ID['service-runit'].tested_versions, ())
        ids = {item['id'] for item in compose_modules(context('systemd'))}
        self.assertIn('distro-void', ids)
        self.assertIn('services-systemd', ids)
        self.assertNotIn('services-runit', ids)

    def test_duplicate_keys_aliases_tags_unknown_fields_and_oversize_rejected(self):
        for source in (self.source() + '\nid: duplicate\n', 'id: &x bad\nrequires: [*x]',
                       '!!python/object/apply:os.system ["touch /tmp/bad"]', self.source() + '\nshell: ls\n', 'x' * 140000):
            with self.subTest(source=source[:32]), self.assertRaises(SchemaError):
                load_module(source)

    def test_unknown_action_and_probe_cannot_become_executable(self):
        import yaml
        value = load_module(self.source())
        value['action_ids'] = ['shell.run']
        with self.assertRaises(SchemaError):
            load_module(yaml.safe_dump(value))
        value = next(item for item in bundled_modules() if item['id'] == 'services-runit')
        value = json.loads(json.dumps(value))
        value['procedures'][0]['steps'][0]['probe_key'] = 'shell.run'
        with self.assertRaises(SchemaError):
            load_module(yaml.safe_dump(value))

    def test_missing_duplicate_and_cyclic_dependencies_rejected(self):
        for dependencies in (['missing'], ['linux-core']):
            module = load_module(self.source())
            module['requires'] = dependencies
            with self.assertRaises(SchemaError):
                validate_modules([module])
        module = load_module(self.source())
        with self.assertRaises(SchemaError):
            validate_modules([module, module])


class FakeAdapter:
    definition = ActionDefinition('test.change', True, 'change', 'user', 'fixture recovery')

    def __init__(self):
        self.calls = 0

    def prepare(self, parameters):
        if set(parameters) != {'target'} or parameters['target'] != 'one':
            raise ValueError('Invalid fixture target')
        return parameters, 'one', 'change'

    def execute(self, parameters, is_current):
        self.calls += 1
        return ActionResult('done', verified=True)


class ContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'actions.json'
        self.audit = ActionAudit(self.path)
        self.executor = ActionExecutor(self.audit)
        self.adapter = FakeAdapter()
        self.executor.register(self.adapter)

    def prepare(self):
        return self.executor.prepare('test.change', {'target': 'one'}, 'own')

    def test_scoped_one_use_grant_and_audit_identity(self):
        prepared = self.prepare()
        with self.assertRaises(ValueError):
            self.executor.authorize(prepared, 'another', explicit_request=True)
        token = self.executor.authorize(prepared, 'own', explicit_request=True)
        self.assertTrue(self.executor.execute(prepared, grant=token).verified)
        self.assertFalse(self.executor.execute(prepared, grant=token).ok)
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual([item['phase'] for item in self.audit.events('own')], ['prepared', 'running', 'verified'])
        self.assertEqual(self.audit.events('another'), [])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_imported_and_forged_preparations_do_not_authorize_mutation(self):
        prepared = self.prepare()
        with self.assertRaises(ValueError):
            self.executor.authorize({'operation_id': prepared.operation_id}, 'own', explicit_request=True)
        with self.assertRaises(ValueError):
            self.executor.authorize(prepared, 'own', explicit_request='true')
        self.assertFalse(self.executor.execute({'operation_id': prepared.operation_id}).ok)
        token = self.executor.authorize(prepared, 'own', explicit_request=True)
        for forged in (replace(prepared, parameters_json='{"target":"two"}'),
                       replace(prepared, definition=replace(prepared.definition, changes_system=False))):
            self.assertFalse(self.executor.execute(forged, grant=token).ok)
        self.assertEqual(self.adapter.calls, 0)
        other = ActionExecutor(self.audit)
        other.register(self.adapter)
        self.assertFalse(other.execute(prepared, grant=token).ok)

    def test_cancel_close_unknown_actions_and_unregistered_commands_fail_closed(self):
        prepared = self.prepare()
        token = self.executor.authorize(prepared, 'own', explicit_request=True)
        self.assertFalse(self.executor.execute(prepared, grant=token, is_current=lambda: False).ok)
        with self.assertRaises(ValueError):
            self.executor.prepare('shell.run', {'command': 'ls'}, 'own')
        prepared = self.prepare()
        token = self.executor.authorize(prepared, 'own', explicit_request=True)
        self.executor.close()
        self.assertFalse(self.executor.execute(prepared, grant=token).ok)
        self.assertEqual(self.adapter.calls, 0)

    def test_audit_failure_before_execution_prevents_mutation(self):
        prepared = self.prepare()
        token = self.executor.authorize(prepared, 'own', explicit_request=True)
        with patch.object(self.audit, 'append', side_effect=OSError('fixture disk full')):
            self.assertFalse(self.executor.execute(prepared, grant=token).ok)
        self.assertEqual(self.adapter.calls, 0)

    def test_contextual_high_risk_needs_exact_resource_confirmation(self):
        self.adapter.definition = replace(self.adapter.definition, risk='high')
        self.adapter.prepare = lambda params: (params, 'one', 'high')
        prepared = self.prepare()
        with self.assertRaises(ValueError):
            self.executor.authorize(prepared, 'own', explicit_request=True)
        self.executor.authorize(prepared, 'own', explicit_request=True, confirmed_resource='one')

    def test_command_event_redacts_known_credentials(self):
        detail = command_detail(['tool', 'https://user:secret@repo.invalid', 'token=abcdef', '--password', 'a secret value'], True)
        self.assertNotIn('secret@', detail)
        self.assertNotIn('abcdef', detail)
        self.assertNotIn('a secret value', detail)

    def test_success_without_verification_is_reported_as_failure(self):
        prepared = self.prepare()
        token = self.executor.authorize(prepared, 'own', explicit_request=True)
        self.adapter.execute = lambda params, current: ActionResult('done', 'exit status zero')
        self.assertFalse(self.executor.execute(prepared, grant=token).ok)


class InspectionTests(unittest.TestCase):
    def test_checksum_regular_file_and_changed_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.iso'
            path.write_bytes(b'fixture image bytes')
            adapter = ChecksumAdapter([directory])
            params, _, _ = adapter.prepare({'path': str(path)})
            self.assertEqual(adapter.execute(params, lambda: True).data['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            path.unlink()
            path.symlink_to('/etc/passwd')
            self.assertFalse(adapter.execute(params, lambda: True).ok)

    def test_checksum_cancellation_and_directory_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture'
            path.write_text('bytes')
            adapter = ChecksumAdapter([directory])
            params, _, _ = adapter.prepare({'path': str(path)})
            self.assertFalse(adapter.execute(params, lambda: False).ok)
            with self.assertRaises(ValueError):
                adapter.prepare({'path': '/etc/passwd'})

    def test_storage_inventory_is_read_only_and_marks_mounted_children(self):
        value = {'blockdevices': [{'path': '/dev/sda', 'size': 1000000, 'type': 'disk', 'rm': True, 'ro': False, 'maj:min': '8:0',
                  'mountpoints': [None], 'children': [{'path': '/dev/sda1', 'size': 900000, 'type': 'part', 'ro': False, 'rm': True,
                                                     'maj:min': '8:1', 'mountpoints': ['/']}]}]}
        runner = Mock(return_value=(True, json.dumps(value)))
        result = StorageAdapter(runner).execute({}, lambda: True)
        self.assertTrue(result.verified)
        self.assertTrue(result.data[0]['mounted'])
        self.assertIn('not available', result.text)
        self.assertEqual(runner.call_args.args[0][0], 'lsblk')

    def test_malformed_storage_and_shell_options_rejected(self):
        adapter = StorageAdapter(Mock(return_value=(True, '{invalid}')))
        self.assertFalse(adapter.execute({}, lambda: True).ok)
        with self.assertRaises(ValueError):
            adapter.prepare({'command': 'dd'})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.candidate = ServiceCandidate('cups.service', 'systemd', 'a' * 64, 'active', 'enabled', 123)

    def test_inactive_manager_cannot_be_used_even_with_a_client(self):
        service = ServiceService(context_supplier=lambda: context('systemd', 'installed'))
        with self.assertRaises(ValueError):
            service.list_names()

    def test_zero_exit_status_without_effect_is_not_success(self):
        service = ServiceService(which=lambda tool: '/usr/bin/' + tool)
        service.status = Mock(return_value=self.candidate)
        service._call = Mock(return_value=(True, ''))
        for verb in ('stop', 'restart'):
            self.assertFalse(service.control(self.candidate, verb, lambda: True).verified)

    def test_changed_definition_cancellation_and_protected_targets_prevent_pkexec(self):
        service = ServiceService(which=lambda tool: '/usr/bin/' + tool)
        service.status = Mock(return_value=replace(self.candidate, identity='b' * 64))
        service._call = Mock()
        with self.assertRaises(ValueError):
            service.control(self.candidate, 'stop', lambda: True)
        service.status.return_value = self.candidate
        with self.assertRaises(ValueError):
            service.control(self.candidate, 'stop', lambda: False)
        with self.assertRaises(ValueError):
            service.control(replace(self.candidate, name='dbus.service'), 'stop', lambda: True)
        service._call.assert_not_called()

    def test_verified_restart_and_boot_disable_use_exact_target(self):
        service = ServiceService(which=lambda tool: '/usr/bin/' + tool)
        service.status = Mock(side_effect=[self.candidate, replace(self.candidate, pid=124)])
        service._call = Mock(return_value=(True, ''))
        self.assertTrue(service.control(self.candidate, 'restart', lambda: True).verified)
        self.assertEqual(service._call.call_args.args[0][-3:], ['restart', '--', 'cups.service'])

    def test_unvalidated_metadata_and_runit_boot_mutations_refused(self):
        value = self.candidate.to_dict()
        value['name'] = '../../etc/passwd'
        with self.assertRaises(ValueError):
            ServiceCandidate.from_dict(value)
        with self.assertRaises(ValueError):
            ServiceService(context_supplier=lambda: context('runit', 'installed')).control(
                replace(self.candidate, name='cups', manager='runit'), 'enable', lambda: True)

    def test_systemd_discovery_and_restart_from_backend_fixtures(self):
        state = {'pid': 12}
        def runner(argv, timeout=15):
            if 'list-unit-files' in argv:
                return True, 'cups.service enabled enabled\n'
            if 'show' in argv:
                return True, ('Id=cups.service\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n'
                    'FragmentPath=/usr/lib/systemd/system/cups.service\nDropInPaths=\nTransient=no\nMainPID={}').format(state['pid'])
            self.assertEqual(argv, ['/usr/bin/pkexec', '/usr/bin/systemctl', '--system', 'restart', '--', 'cups.service'])
            state['pid'] += 1
            return True, ''
        service = ServiceService(context_supplier=lambda: context('systemd'), runner=runner,
            which=lambda name: '/usr/bin/' + name, file_identity=lambda path: [path, 1, 2, 'definition'])
        candidate = service.status('cups')
        self.assertEqual(candidate.name, 'cups.service')
        self.assertTrue(service.control(candidate, 'restart', lambda: True).verified)

    def test_runit_activation_uses_restricted_helper_and_verifies_link_state(self):
        service = ServiceService(which=lambda name: '/usr/bin/' + name)
        candidate = replace(self.candidate, manager='runit', name='cups', active='inactive', enabled='disabled', pid=0)
        service.status = Mock(side_effect=[candidate, replace(candidate, enabled='enabled')])
        service._call = Mock(return_value=(True, ''))
        with patch('src.privileged_helpers.service_helper_command', return_value=[
                '/usr/bin/pkexec', '/usr/libexec/linux-ai-assistant/services']):
            result = service.control(candidate, 'enable', lambda: True)
        self.assertTrue(result.verified)
        argv = service._call.call_args.args[0]
        self.assertEqual(argv[-3:], ['enable', 'cups', candidate.identity])
        self.assertEqual(argv[:2], ['/usr/bin/pkexec', '/usr/libexec/linux-ai-assistant/services'])

    def test_runit_disable_does_not_remove_a_running_service_after_failed_stop(self):
        service = ServiceService(which=lambda name: '/usr/bin/' + name)
        candidate = replace(self.candidate, manager='runit', name='cups')
        service.status = Mock(return_value=candidate)
        service._call = Mock(return_value=(False, 'stop failed'))
        with patch('src.privileged_helpers.service_helper_command', return_value=[
                '/usr/bin/pkexec', '/usr/libexec/linux-ai-assistant/services']):
            self.assertFalse(service.control(candidate, 'disable', lambda: True).ok)
        self.assertEqual(service._call.call_count, 1)
        self.assertIn('stop', service._call.call_args.args[0])

    def test_missing_activation_helper_does_not_stop_a_running_service(self):
        service = ServiceService(which=lambda name: '/usr/bin/' + name)
        candidate = replace(self.candidate, manager='runit', name='cups')
        service.status = Mock(return_value=candidate)
        service._call = Mock()
        with patch('src.privileged_helpers.service_helper_command', side_effect=PermissionError('not installed')):
            with self.assertRaises(PermissionError):
                service.control(candidate, 'disable', lambda: True)
        service._call.assert_not_called()

    def test_privileged_entry_point_rejects_free_paths_and_nonroot_requests(self):
        from src import privileged_service
        with patch.object(privileged_service.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError):
                privileged_service._change('enable', 'cups', 'a' * 64, '/var/service', '/etc/sv')
        self.assertEqual(privileged_service.main(['enable', '../../home', 'a' * 64, '/tmp']), 2)
        with patch('builtins.open', return_value=io.StringIO('systemd\n')), patch.object(privileged_service, '_change') as core:
            with self.assertRaises(ValueError):
                privileged_service.change('enable', 'cups', 'a' * 64)
        core.assert_not_called()


class ExtendedConversationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = HistoryStore(Path(temporary.name) / 'history.json')
        self.addCleanup(self.store.close)
        self.store.list_sessions()
        self.session = self.store.active_session_id
        self.services = Mock(spec=ServiceService)
        self.candidate = ServiceCandidate('cups.service', 'systemd', 'a' * 64, 'active', 'enabled', 123)
        self.services.status.return_value = self.candidate
        self.services.list_names.return_value = ['cups.service']
        self.services.control.return_value = ActionResult('done', 'verified service', verified=True)
        self.engine = ConversationActions(self.store, 'apt', services=self.services, context=context('systemd'))
        self.addCleanup(self.engine.close)

    def handle(self, message, **kwargs):
        return self.engine.handle(message, self.session, 'pt', **kwargs)

    def test_service_confirmation_is_session_bound_and_one_use(self):
        result = self.handle('reinicia serviço cups')
        self.assertEqual(result.status, 'needs_choice')
        self.assertIn('confirma reiniciar cups.service', result.text)
        self.services.control.assert_not_called()
        another = self.store.create_session('Another conversation', select=False)['id']
        self.assertIsNone(self.engine.handle('confirma reiniciar cups.service', another, 'pt'))
        self.assertEqual(self.handle('confirma reiniciar cups.service').status, 'done')
        self.handle('confirma reiniciar cups.service')
        self.assertEqual(self.services.control.call_count, 1)

    def test_pasted_question_refusal_wrong_confirmation_and_cancel_cannot_mutate(self):
        self.handle('reinicia serviço cups')
        for message in ('Como reinicia serviço cups?', 'não reinicia serviço cups', 'reinicia serviço cups\ntexto', 'confirma stop cups.service'):
            self.handle(message)
        self.handle('cancelar')
        self.handle('confirma reiniciar cups.service')
        self.services.control.assert_not_called()

    def test_noninteractive_confirmation_proposes_and_does_not_execute(self):
        self.handle('reinicia serviço cups')
        self.assertEqual(self.handle('confirma reiniciar cups.service', can_execute=False).status, 'proposal')
        self.services.control.assert_not_called()

    def test_restart_does_not_restore_imported_authorization(self):
        self.handle('reinicia serviço cups')
        other = ConversationActions(self.store, 'apt', services=self.services, context=context('systemd'))
        self.addCleanup(other.close)
        other.handle('confirma reiniciar cups.service', self.session, 'pt')
        self.services.control.assert_not_called()

    def test_read_actions_audit_and_unknown_iso_never_use_a_provider(self):
        self.assertIn('cups.service', self.handle('lista serviços').text)
        self.assertIn('services.list', self.handle('mostra ações').text)
        self.assertEqual(self.handle('grava iso numa pen').status, 'proposal')
        self.services.control.assert_not_called()
