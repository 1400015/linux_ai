"""Dedicated elevation uses installed paths, never the caller's checkout."""

import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from src import privileged_helpers as helpers


class PrivilegedInstallationTests(unittest.TestCase):
    def setUp(self):
        # A trusted path cannot live under world-writable /tmp. Ownership is
        # synthesized below so these tests also run as an ordinary CI user.
        self.directory = tempfile.TemporaryDirectory(dir=str(Path.cwd()))
        self.addCleanup(self.directory.cleanup)
        self.stage = Path(self.directory.name)
        self.project = Path(__file__).resolve().parents[1]
        result = subprocess.run(['bash', str(self.project / 'scripts/install-privileged-helpers.sh')],
                                env=dict(os.environ, DESTDIR=str(self.stage)),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.base = self.stage / 'usr/libexec/linux-ai-assistant'
        self.policy = self.stage / 'usr/share/polkit-1/actions/org.linux_ai_assistant.policy'
        self.pkexec = self.stage / 'pkexec'
        self.python = self.stage / 'python3'
        for path in (self.pkexec, self.python):
            path.write_text('synthetic executable')
            path.chmod(0o755)
        self.policy.write_text(self.policy.read_text().replace(
            '/usr/libexec/linux-ai-assistant', str(self.base)))
        self.original_lstat = Path.lstat
        self.original_fstat = os.fstat

    def trust_fixture(self):
        def lstat(path):
            original = self.original_lstat(path)
            return SimpleNamespace(st_uid=0, st_mode=original.st_mode)

        def fstat(descriptor):
            original = self.original_fstat(descriptor)
            return SimpleNamespace(st_uid=0, st_mode=original.st_mode)

        stack = [patch.object(helpers, 'HELPER_DIRECTORY', self.base),
                 patch.object(helpers, 'POLICY_PATH', self.policy),
                 patch.object(helpers, 'PKEXEC_PATH', self.pkexec),
                 patch.object(helpers, 'PYTHON_PATH', self.python),
                 patch.object(Path, 'lstat', lstat), patch('os.fstat', fstat)]
        from contextlib import ExitStack
        context = ExitStack()
        for item in stack:
            context.enter_context(item)
        self.addCleanup(context.close)
        return context

    def test_staged_installer_preserves_protocol_checksums_and_modes(self):
        manifest = json.loads((self.base / 'manifest.json').read_text())
        self.assertEqual(manifest['protocol'], helpers.PROTOCOL)
        for name, digest in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((self.base / name).read_bytes()).hexdigest(), digest)
            expected = 0o755 if name in ('files', 'services') else 0o644
            self.assertEqual((self.base / name).stat().st_mode & 0o777, expected)

    def test_launcher_interpreter_is_isolated_and_does_not_import_site(self):
        for kind in ('files', 'services'):
            self.assertEqual((self.base / kind).read_bytes().splitlines()[0],
                             b'#!/usr/bin/python3 -IS')
        result = subprocess.run(['/usr/bin/python3', '-IS', '-c',
                                 'import sys; print(sys.flags.isolated, sys.flags.no_site); '
                                 'print("site" in sys.modules)'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines(), ['1 1', 'False'])

    def test_commands_use_only_fixed_installed_launchers(self):
        with self.trust_fixture():
            self.assertEqual(helpers.file_helper_command(), [str(self.pkexec), str(self.base / 'files')])
            self.assertEqual(helpers.service_helper_command(), [str(self.pkexec), str(self.base / 'services')])

    def test_absent_installation_fails_closed_before_pkexec(self):
        with self.trust_fixture():
            (self.base / 'files').unlink()
            with self.assertRaisesRegex(PermissionError, 'install-privileged-helpers'):
                helpers.file_helper_command()

    def test_modified_helper_is_rejected_even_when_root_owned(self):
        (self.base / 'privileged_write.py').write_text('synthetic substituted module')
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'incomplete'):
                helpers.file_helper_command()

    def test_launcher_that_imports_global_site_code_is_rejected(self):
        launcher = self.base / 'files'
        launcher.write_text(launcher.read_text().replace('python3 -IS', 'python3 -I'))
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'without site code'):
                helpers.file_helper_command()

    def test_group_writable_launcher_is_rejected(self):
        (self.base / 'files').chmod(0o775)
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'writable'):
                helpers.file_helper_command()

    def test_writable_ancestor_is_rejected(self):
        self.base.parent.chmod(0o777)
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'writable'):
                helpers.file_helper_command()
        self.base.parent.chmod(0o755)

    def test_nonroot_owned_launcher_is_rejected(self):
        with self.trust_fixture():
            original = Path.lstat

            def changed(path):
                info = original(path)
                return SimpleNamespace(st_uid=1000 if path == self.base / 'files' else info.st_uid,
                                       st_mode=info.st_mode)

            with patch.object(Path, 'lstat', changed):
                with self.assertRaisesRegex(PermissionError, 'owned by root'):
                    helpers.file_helper_command()

    def test_launcher_symlink_is_rejected(self):
        alternate = self.base / 'actual-files'
        (self.base / 'files').rename(alternate)
        (self.base / 'files').symlink_to(alternate)
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'links'):
                helpers.file_helper_command()

    def test_root_interpreter_alias_is_allowed_only_with_a_trusted_target(self):
        actual = self.stage / 'python3-real'
        self.python.rename(actual)
        self.python.symlink_to(actual)
        with self.trust_fixture():
            self.assertEqual(helpers.file_helper_command()[1], str(self.base / 'files'))
            actual.chmod(0o777)
            with self.assertRaisesRegex(PermissionError, 'writable'):
                helpers.file_helper_command()

    def test_interpreter_alias_cannot_hide_an_untrusted_intermediate_link(self):
        actual = self.stage / 'python3-real'
        middle = self.stage / 'python3-middle'
        self.python.rename(actual)
        middle.symlink_to(actual)
        self.python.symlink_to(middle)
        with self.trust_fixture():
            trusted_lstat = Path.lstat

            def lstat(path):
                info = trusted_lstat(path)
                return SimpleNamespace(st_uid=1000 if path == middle else info.st_uid,
                                       st_mode=info.st_mode)

            with patch.object(Path, 'lstat', lstat):
                with self.assertRaisesRegex(PermissionError, 'owned by root'):
                    helpers.file_helper_command()

    def test_interpreter_alias_cannot_hide_a_writable_intermediate_directory(self):
        actual = self.stage / 'python3-real'
        middle_directory = self.stage / 'intermediate'
        middle_directory.mkdir(mode=0o777)
        middle_directory.chmod(0o777)
        middle = middle_directory / 'python3-middle'
        self.python.rename(actual)
        middle.symlink_to(actual)
        self.python.symlink_to(middle)
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'writable'):
                helpers.file_helper_command()

    def test_interpreter_alias_cycle_is_rejected(self):
        self.python.unlink()
        middle = self.stage / 'python3-middle'
        self.python.symlink_to(middle)
        middle.symlink_to(self.python)
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'link chain'):
                helpers.file_helper_command()

    def test_root_dispatchers_recheck_module_permissions_before_import(self):
        original = os.lstat

        def metadata(path):
            info = original(path)
            return SimpleNamespace(st_uid=0, st_mode=info.st_mode)

        for kind, module in (('files', 'privileged_write'), ('services', 'privileged_service')):
            with self.subTest(kind=kind):
                source = (self.base / kind).read_text().replace(
                    '/usr/libexec/linux-ai-assistant', str(self.base))
                tree = ast.parse(source)
                statements = []
                for item in tree.body:
                    if isinstance(item, ast.ImportFrom) and item.module == module:
                        break
                    statements.append(item)
                (self.base / (module + '.py')).chmod(0o666)
                executable = compile(ast.Module(body=statements, type_ignores=[]), '<installed-wrapper>', 'exec')
                with patch('os.geteuid', return_value=0), patch('os.lstat', side_effect=metadata), \
                        patch.object(sys, 'path', []), patch('sys.stderr', io.StringIO()) as errors:
                    with self.assertRaises(SystemExit) as caught:
                        exec(executable, {})
                    self.assertEqual(caught.exception.code, 1)
                    self.assertIn('unsafe', errors.getvalue())
                    self.assertEqual(sys.path, [])
                (self.base / (module + '.py')).chmod(0o644)

    def test_policy_comment_cannot_substitute_for_the_actual_action(self):
        path = str(self.base / 'files')
        self.policy.write_text('<policyconfig><!-- <annotate key="org.freedesktop.policykit.exec.path">'
                               + path + '</annotate> --></policyconfig>')
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'action is not installed'):
                helpers.file_helper_command()

    def test_policy_must_require_fresh_administrator_authentication(self):
        original = self.policy.read_text()
        for altered in (
                original.replace('<allow_active>auth_admin</allow_active>',
                                 '<allow_active>auth_admin_keep</allow_active>'),
                original.replace('<allow_any>no</allow_any>', '<allow_any>yes</allow_any>'),
                original.replace('<allow_inactive>no</allow_inactive>',
                                 '<allow_inactive>auth_admin</allow_inactive>')):
            with self.subTest(policy=altered):
                self.policy.write_text(altered)
                with self.trust_fixture():
                    with self.assertRaisesRegex(PermissionError, 'fresh administrator'):
                        helpers.file_helper_command()

    def test_conflicting_or_argument_dependent_action_selectors_are_rejected(self):
        original = self.policy.read_text()
        marker = '<annotate key="org.freedesktop.policykit.exec.path">' + str(self.base / 'files') + '</annotate>'
        for extra in (
                '<annotate key="org.freedesktop.policykit.exec.argv1">-I</annotate>',
                '<annotate key="org.freedesktop.policykit.exec.path">/usr/bin/python3</annotate>',
                marker):
            with self.subTest(selector=extra):
                self.policy.write_text(original.replace(marker, marker + extra))
                with self.trust_fixture():
                    with self.assertRaisesRegex(PermissionError, 'incompatible executable selectors'):
                        helpers.file_helper_command()

    def test_policy_with_invalid_document_root_is_rejected(self):
        self.policy.write_text(self.policy.read_text().replace('policyconfig', 'synthetic-config'))
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'configuration is invalid'):
                helpers.file_helper_command()

    def test_fixed_dispatchers_call_only_their_installed_entry_point(self):
        original_lstat = os.lstat

        def metadata(path):
            info = original_lstat(path)
            return SimpleNamespace(st_uid=0, st_mode=info.st_mode)

        for kind, module_name in (('files', 'privileged_write'), ('services', 'privileged_service')):
            with self.subTest(kind=kind):
                called = []
                module = ModuleType(module_name)

                def main(*arguments):
                    called.append(arguments)
                    return 0

                module.main = main
                source = (self.base / kind).read_text().replace(
                    '/usr/libexec/linux-ai-assistant', str(self.base))
                argv = [str(self.base / kind), 'disable', 'cups', 'a' * 64]
                with patch('os.geteuid', return_value=0), patch('os.lstat', side_effect=metadata), \
                        patch.object(sys, 'path', []), patch.object(sys, 'argv', argv), \
                        patch.dict(sys.modules, {module_name: module}):
                    with self.assertRaises(SystemExit) as caught:
                        exec(compile(source, '<installed-wrapper>', 'exec'), {})
                    self.assertEqual(caught.exception.code, 0)
                    self.assertEqual(sys.path, [str(self.base)])
                self.assertEqual(called, [()] if kind == 'files' else [(argv[1:],)])

    def test_runit_stop_reuses_the_fixed_validated_elevation_binary(self):
        from dataclasses import replace
        from unittest.mock import Mock
        from src.service_actions import ServiceCandidate, ServiceService

        candidate = ServiceCandidate('cups', 'runit', 'a' * 64, 'active', 'enabled', 0)
        service = ServiceService(which=lambda name: '/caller-controlled/bin/pkexec'
                                 if name == 'pkexec' else '/usr/bin/' + name)
        service.status = Mock(side_effect=[candidate, replace(candidate, active='inactive'),
                                           replace(candidate, active='inactive', enabled='disabled')])
        service._call = Mock(return_value=(True, ''))
        with patch('src.privileged_helpers.service_helper_command', return_value=[
                '/usr/bin/pkexec', '/usr/libexec/linux-ai-assistant/services']):
            result = service.control(candidate, 'disable', lambda: True)
        self.assertTrue(result.verified)
        self.assertEqual(service._call.call_args_list[0].args[0], [
            '/usr/bin/pkexec', '/usr/bin/sv', '-w', '10', 'stop', '/var/service/cups'])
        self.assertEqual(service._call.call_args_list[1].args[0][:2], [
            '/usr/bin/pkexec', '/usr/libexec/linux-ai-assistant/services'])

    def test_incompatible_manifest_is_rejected(self):
        manifest = json.loads((self.base / 'manifest.json').read_text())
        manifest['protocol'] = 1
        (self.base / 'manifest.json').write_text(json.dumps(manifest))
        with self.trust_fixture():
            with self.assertRaisesRegex(PermissionError, 'Incompatible'):
                helpers.file_helper_command()

    def test_policy_requires_authentication_and_matches_exact_launchers(self):
        root = ElementTree.parse(self.project / 'polkit/org.linux_ai_assistant.policy').getroot()
        self.assertEqual(len(root.findall('action')), 2)
        for action in root.findall('action'):
            self.assertEqual(action.findtext('defaults/allow_active'), 'auth_admin')
            self.assertEqual(action.findtext('defaults/allow_inactive'), 'no')
            self.assertEqual(action.findtext('defaults/allow_any'), 'no')
            kind = action.get('id').rsplit('.', 1)[1]
            self.assertEqual(action.findtext('annotate'), '/usr/libexec/linux-ai-assistant/' + kind)

    def test_installer_refuses_linked_staging_destination(self):
        linked_stage = self.stage / 'link'
        linked_stage.symlink_to(self.stage)
        result = subprocess.run(['bash', str(self.project / 'scripts/install-privileged-helpers.sh')],
                                env=dict(os.environ, DESTDIR=str(linked_stage)),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('without links', result.stderr)

    def test_installer_refuses_parent_traversal_before_writing(self):
        escaping = str(self.stage / 'nested') + '/../other'
        result = subprocess.run(['bash', str(self.project / 'scripts/install-privileged-helpers.sh')],
                                env=dict(os.environ, DESTDIR=escaping),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('normalized', result.stderr)
        self.assertFalse((self.stage / 'other').exists())


if __name__ == '__main__':
    unittest.main()
