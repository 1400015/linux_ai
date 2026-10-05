"""Synthetic credentials must not survive shared diagnostic or trial reports."""

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.diagnostics import build_report, export_report
from src.log_privacy import redact_argv, redact_text, secret_option
from src.trial_recorder import TrialRecorder


LABELS = (
    'DB_PASSWORD', 'SECRET_KEY', 'AUTH_TOKEN', 'PGPASSWORD',
    'APP_CLIENT_SECRET', 'APP_ENCRYPTION_KEY', 'AWS_SECRET_ACCESS_KEY',
    '_DATABASE_PASSWORD', 'database-password',
)
SYNTHETIC_VALUE = 'synthetic-regression-value-47'
SYNTHETIC_JWT = (
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.'
    'c3ludGhldGljLXNpZ25hdHVyZQ'
)
SYNTHETIC_TOKENS = (
    SYNTHETIC_JWT,
    # Assemble synthetic token shapes at runtime so repository protection
    # cannot mistake a complete test fixture for a stored Slack credential.
    '-'.join(('xoxb', '123456789012', '123456789012', 'syntheticfixture')),
    '-'.join(('xoxp', '123456789012', '123456789012', 'syntheticfixture')),
    'xoxe.' + '-'.join(('xoxb', '123456789012', 'syntheticfixture')),
    'AKIA1234567890ABCDEF',
    'ASIA1234567890ABCDEF',
)


class LogPrivacyRegressionTests(unittest.TestCase):
    def test_compound_assignments_and_quoted_values_are_redacted(self):
        for label in LABELS:
            for separator in ('=', ': '):
                for quote in ('', '"', "'"):
                    with self.subTest(label=label, separator=separator, quote=quote):
                        text = (label + separator + quote + SYNTHETIC_VALUE + quote
                                + '\nconnection refused')
                        clean = redact_text(text)
                        self.assertNotIn(SYNTHETIC_VALUE, clean)
                        self.assertIn(label + separator, clean)
                        self.assertIn('connection refused', clean)

    def test_quoted_values_keep_diagnostic_context_and_ambiguous_values_hide_tail(self):
        clean = redact_text('DB_PASSWORD="synthetic phrase" status=failed')
        self.assertEqual(clean, 'DB_PASSWORD="[redacted]" status=failed')
        clean = redact_text('DB_PASSWORD=synthetic phrase status=failed\nretry denied')
        self.assertEqual(clean, 'DB_PASSWORD=[redacted]\nretry denied')
        clean = redact_text('AUTH_TOKEN="https://example.invalid/private?data=synthetic" status=failed')
        self.assertEqual(clean, 'AUTH_TOKEN="[redacted]" status=failed')

    def test_compound_argv_options_and_assignments_preserve_boundaries(self):
        for label in LABELS:
            with self.subTest(label=label):
                self.assertTrue(secret_option('--' + label))
                self.assertEqual(
                    redact_argv(['fixture', '--' + label, SYNTHETIC_VALUE, '--verbose']),
                    ['fixture', '--' + label, '[redacted]', '--verbose'],
                )
                self.assertEqual(
                    redact_argv(['fixture', label + '=' + SYNTHETIC_VALUE, 'operand']),
                    ['fixture', label + '=[redacted]', 'operand'],
                )

    def test_compound_bare_labels_are_hidden_in_command_logs(self):
        clean = redact_text('Executing command: fixture DB_PASSWORD ' + SYNTHETIC_VALUE)
        self.assertNotIn(SYNTHETIC_VALUE, clean)
        self.assertIn('DB_PASSWORD [redacted]', clean)

    def test_bare_token_families_are_hidden_and_id_redaction_is_identified(self):
        for value in SYNTHETIC_TOKENS:
            with self.subTest(value=value):
                clean = redact_text('authentication failed: ' + value + '.\nconnection refused')
                self.assertNotIn(value, clean)
                self.assertIn('connection refused', clean)
                self.assertEqual(redact_text(clean), clean)
                self.assertNotIn(value, ' '.join(redact_argv(['fixture', value])))
        self.assertEqual(redact_text(SYNTHETIC_TOKENS[-1]), '[AWS access key ID removed]')

    def test_benign_prose_identifiers_and_urls_keep_their_context(self):
        for text in (
            'No password required; connection refused',
            'Token usage - input=100, output=20',
            'DB_PASSWORD_FILE=/tmp/fixture',
            'SECRET_KEY_ENABLED=true',
            'AUTH_TOKEN_COUNT=3',
            'https://example.invalid/help/password-reset?attempt=3',
            'The secret key must be rotated after a connection failure.',
            'AKIA1234 is a prefix, not a complete access-key identifier.',
            'eyJheader.payload is an incomplete token.',
            'xoxb-example is documentation, not a token-shaped value.',
        ):
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), text)

    def test_existing_url_credentials_and_token_patterns_remain_redacted(self):
        clean = redact_text(
            'https://fixture-user:fixture-pass@example.invalid/resource\n'
            'sk-syntheticfixture\nghp_syntheticfixture\nBearer synthetic-bearer\n'
            'Authorization: synthetic-header\n'
        )
        for value in ('fixture-user', 'fixture-pass', 'sk-syntheticfixture',
                      'ghp_syntheticfixture', 'synthetic-bearer', 'synthetic-header'):
            self.assertNotIn(value, clean)
        self.assertIn('example.invalid/resource', clean)

    def test_diagnostic_markdown_and_json_exports_hide_synthetic_credentials(self):
        pasted = '\n'.join(label + '=' + SYNTHETIC_VALUE for label in LABELS)
        pasted += '\n' + '\n'.join(SYNTHETIC_TOKENS) + '\nconnection refused'
        report = build_report('Synthetic connection failure', pasted, None)
        for format_name in ('markdown', 'json'):
            with self.subTest(format=format_name):
                exported = export_report(report, format_name)
                self.assertNotIn(SYNTHETIC_VALUE, exported)
                for token in SYNTHETIC_TOKENS:
                    self.assertNotIn(token, exported)
                self.assertIn('connection refused', exported)
                self.assertIn('The connection was refused.', exported)
        json.loads(export_report(report, 'json'))

    def test_trial_freeform_notes_and_text_attachment_export_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            text = '\n'.join(label + '=' + SYNTHETIC_VALUE for label in LABELS)
            text += '\n' + '\n'.join(SYNTHETIC_TOKENS) + '\nconnection refused'
            recorder = TrialRecorder(
                base / 'trials', history_path=base / 'history.json',
                log_path=base / 'app.log', context_factory=lambda: {},
                clock=lambda: 1000.0,
            )
            recorder.start('Synthetic trial', 'fixture', build_ref='fixture-build',
                           mode='offline', test_type='fixture')
            recorder.begin_case('PRIVACY-01', session_id='synthetic-session',
                                interface='cli', notes=text)
            recorder.end_case('PASS', notes=text)
            recorder.finish()
            attachment = base / 'synthetic.txt'
            attachment.write_text(text, encoding='utf-8')
            recorder.attach(attachment)
            destination = base / 'reviewed.zip'
            preview = recorder.preview()
            recorder.export(destination, reviewed=True)
            with zipfile.ZipFile(destination) as archive:
                exported = '\n'.join(archive.read(item).decode('utf-8')
                                     for item in archive.namelist())
            for output in (preview, exported):
                self.assertNotIn(SYNTHETIC_VALUE, output)
                for token in SYNTHETIC_TOKENS:
                    self.assertNotIn(token, output)
                self.assertIn('connection refused', output)
            self.assertEqual(attachment.read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()
