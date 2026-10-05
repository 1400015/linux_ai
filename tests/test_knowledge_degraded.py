"""Corrupt bundled data disables YAML references without blocking startup."""

import subprocess
import sys
import unittest
from unittest.mock import patch

from src import knowledge_loader
from src.schema_validation import SchemaError


class TestDegradedKnowledge(unittest.TestCase):
    def tearDown(self):
        knowledge_loader.available_bundled_modules()

    def test_failed_collection_is_disabled_and_error_data_is_not_logged(self):
        secret = 'synthetic-sensitive-source-data'
        with patch.object(knowledge_loader, 'bundled_modules', side_effect=SchemaError(secret)), \
                self.assertLogs('src.knowledge_loader', level='WARNING') as logs:
            self.assertEqual(knowledge_loader.available_bundled_modules(), ())
        self.assertNotIn(secret, '\n'.join(logs.output))
        self.assertIn('unavailable', knowledge_loader.knowledge_warning())
        self.assertIn('indisponível', knowledge_loader.knowledge_warning('pt'))

    def test_imports_and_answers_survive_corrupt_yaml(self):
        # A fresh interpreter avoids replacing dataclass identities underneath
        # unrelated tests which already imported the healthy catalogs.
        code = '''
from unittest.mock import patch
from types import SimpleNamespace
from src import knowledge_loader
from src.schema_validation import SchemaError
with patch.object(knowledge_loader, 'bundled_modules', side_effect=SchemaError('fixture')):
    import src.knowledge_base
    import src.local_knowledge as procedures
    from src.offline_assistant import OfflineAssistant
    from src.assistant_context import build_system_message
    from src.diagnostics import analyze, build_report
    import src.cli
    assistant = OfflineAssistant(os_release={'ID': 'debian'}, which=lambda tool: None,
                                 is_systemd_running=False)
    assert 'indisponível' in assistant.handle('help', 'pt').text
    assert procedures.PROCEDURES
    assert 'service-runit' not in procedures.PROCEDURE_BY_ID
    assert 'unavailable' in build_system_message()['content']
    part = SimpleNamespace(identifier='')
    context = SimpleNamespace(distro_id='debian', id_like=(), package_manager=part,
                              service_manager=part, audio=part, network=part)
    assert knowledge_loader.compose_modules(context) == ()
    assert all(item['guide'] in procedures.PROCEDURE_BY_ID
               for item in analyze('169.254.1.2', probe_key='addresses'))
    build_report('fixture', 'Failed at step EXEC', None)
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
