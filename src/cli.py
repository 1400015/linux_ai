#!/usr/bin/env python3
"""
Linux AI Assistant - Interface de Linha de Comandos (CLI)

Uso:
    python -m src.cli chat "What is my operating system?"
    python -m src.cli capture --ocr
    python -m src.cli expert "Como configurar o iptables?"
    python -m src.cli system info
    python -m src.cli --help
"""

import sys
import json
import time
import argparse
import getpass
import logging
import select
from pathlib import Path
from typing import Optional, Dict

from .config_manager import ConfigManager
from .ai_client import AIClient, AIProviderError, AIImageRequestError, AIRequestCancelled, AIResponseLimitError
from .system_utils import SystemUtils
from . import offline_assistant
from .device_actions import (
    choose_numbered, collect_printers, collect_scanners, collect_wifi,
    confirmed, connect_wifi, format_command_offer, printer_add_argv,
    queue_name_for, split_offer, wifi_needs_password,
)
from .i18n import _, get_language, set_language_from_config
from .history_store import HistoryStore
from .assistant_context import build_system_message
from .conversation_io import read_conversation, write_conversation
from .local_knowledge import PROCEDURE_BY_ID, search_procedures, render_procedure
from .diagnostics import PROBES, build_report, export_report
from .change_journal import ChangeJournal
from .conversation_actions import ConversationActions
from .document_context import MAX_DOCUMENT_CONTEXT_CHARS, display_document_context, with_document_context
from .document_store import DocumentStore
from .image_attachments import prepare_image
from .request_preparation import RequestPreparationError, prepare_request

logger = logging.getLogger(__name__)


class _ActionResult(str):
    """Keep action text compatible with callers while carrying its outcome.

    No action (including a non-interactive proposal) and user cancellation
    are not execution failures. Only an attempted operation that fails gives
    the chat command a nonzero exit code.
    """

    status: str

    def __new__(cls, text: str = "", status: str = "none") -> "_ActionResult":
        result = super().__new__(cls, text)
        result.status = status
        return result

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "failed" else 0


class CLIApp:
    """Linux AI Assistant CLI application"""

    def __init__(self):
        self.config = ConfigManager()
        set_language_from_config(self.config)
        self.ai_client = AIClient(self.config)
        self.system_utils = SystemUtils(self.config)
        # Local answers when there is no key or no connection.
        self.offline = offline_assistant.OfflineAssistant(
            self.system_utils, self.config
        )
        # Load the shared history (written by the GUI too) so saving it back
        # never wipes entries from other sessions.
        self.history_store = None
        self._pending_exchanges = []
        self.conversation_history = self._load_history()
        self.expert_mode = False
        from .system_context import detect_system_context
        self.system_context = detect_system_context(self.offline.distro)
        self.offline.set_system_context(self.system_context)
        self.actions = ConversationActions(self._store(), self.offline.distro.pkg_manager, context=self.system_context,
                                           independent_display_watchdog=self.config.get('app.display_independent_watchdog', False))

    @staticmethod
    def parse_args(argv=None):
        """Parse the command-line arguments."""
        parser = argparse.ArgumentParser(
            # The installed console script (pyproject [project.scripts]); the
            # epilog examples use %(prog)s, so they name the same command.
            prog='linux-ai-assistant-cli',
            description='Linux AI Assistant - Interface de Linha de Comandos',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Exemplos:
  %(prog)s chat "What is my operating system?"
  %(prog)s chat --provider openrouter "Usa OpenRouter"
  %(prog)s expert "Como configurar o firewall?"
  %(prog)s capture --ocr
  %(prog)s system info
  %(prog)s system commands
  %(prog)s history
  %(prog)s stats
            """
        )

        subparsers = parser.add_subparsers(dest='command', help='Available commands')

        # Command: chat
        chat_parser = subparsers.add_parser('chat', help='Chat with the AI')
        chat_parser.add_argument('message', nargs='*', help='Message to send')
        chat_parser.add_argument('--provider', '-p', default=None,
                                help='AI provider (default: from configuration)')
        chat_parser.add_argument('--model', '-m', default=None,
                                help='Model to use')
        chat_parser.add_argument('--expert', '-e', action='store_true',
                                help='Use expert mode')
        chat_parser.add_argument('--stream', '-s', action='store_true',
                                help='Show the response as a stream')
        chat_parser.add_argument('--no-history', action='store_true',
                                help='Do not save to history')

        # Command: capture
        capture_parser = subparsers.add_parser('capture', help='Capture the screen')
        capture_parser.add_argument('--ocr', action='store_true',
                                    help='Extract text from the capture')
        capture_parser.add_argument('--output', '-o', default=None,
                                    help='Output file')
        capture_parser.add_argument('--window', '-w', action='store_true',
                                    help='Capture the active window')

        # Command: expert
        expert_parser = subparsers.add_parser('expert', help='Expert mode')
        expert_parser.add_argument('message', nargs='*', help='Question for the expert')
        expert_parser.add_argument('--provider', '-p', default=None,
                                    help='AI provider')
        expert_parser.add_argument('--model', '-m', default=None,
                                    help='Model to use')

        for message_parser in (chat_parser, expert_parser):
            message_parser.add_argument('--image', type=Path, help='Select a PNG, JPEG or WebP image; show metadata and destination before sending')
            message_parser.add_argument('--send-image', action='store_true', help='Explicitly send the selected image with this question')
            message_parser.add_argument('--documents', action='store_true', help='Retrieve passages from explicitly indexed local documents')
            message_parser.add_argument('--send-document-context', action='store_true', help='Explicitly send matching document passages to the configured model')
            selection = message_parser.add_mutually_exclusive_group()
            selection.add_argument('--session', help='Conversation identifier to resume')
            selection.add_argument('--new-session', action='store_true', help='Start a new conversation')
            source = message_parser.add_mutually_exclusive_group()
            source.add_argument('--stdin', action='store_true', help='Read text from stdin (up to 64 KiB)')
            source.add_argument('--input', type=Path, help='Read a UTF-8 text file (up to 64 KiB)')
        expert_parser.add_argument('--no-history', action='store_true', help='Do not save the exchange')
        expert_parser.add_argument('--stream', action='store_true', help='Stream the answer')

        sessions = subparsers.add_parser('sessions', help='Manage independent conversations')
        actions = sessions.add_subparsers(dest='session_action', required=True)
        actions.add_parser('list', help='List conversations, including archived ones')
        new = actions.add_parser('new', help='Create and select a conversation')
        new.add_argument('title', nargs='?', default='New conversation')
        for action in ('use', 'archive', 'restore', 'delete'):
            action_parser = actions.add_parser(action)
            action_parser.add_argument('id', help='Conversation identifier')
        rename = actions.add_parser('rename')
        rename.add_argument('id')
        rename.add_argument('title')
        search = actions.add_parser('search')
        search.add_argument('query')
        export = actions.add_parser('export')
        export.add_argument('--session')
        export.add_argument('--format', choices=('markdown', 'json'), default='markdown')
        export.add_argument('--output', type=Path, help='New output file (existing files are never overwritten)')
        imported = actions.add_parser('import', help='Import a versioned JSON or application Markdown conversation')
        imported.add_argument('path', type=Path)
        import_approval = imported.add_mutually_exclusive_group()
        import_approval.add_argument('--preview', action='store_true', help='Validate and show the conversation without importing')
        import_approval.add_argument('--yes', action='store_true', help='Approve the reviewed import')
        documents = subparsers.add_parser('documents', help='Manage selected local document snapshots without a model service')
        document_actions = documents.add_subparsers(dest='document_action', required=True)
        add_documents = document_actions.add_parser('add', help='Index only the files explicitly named')
        add_documents.add_argument('paths', type=Path, nargs='+')
        document_actions.add_parser('list')
        search_documents = document_actions.add_parser('search', help='Show local passages with source references')
        search_documents.add_argument('query', nargs='+')
        remove_document = document_actions.add_parser('remove')
        remove_document.add_argument('id')
        reindex_documents = document_actions.add_parser('reindex', help='Explicitly refresh selected snapshots')
        reindex_documents.add_argument('ids', nargs='*')
        clear_documents = document_actions.add_parser('clear')
        clear_documents.add_argument('--yes', action='store_true', help='Confirm removing all local snapshots')
        mode = subparsers.add_parser('mode', help='Show or select assistance mode')
        mode.add_argument('value', nargs='?', choices=('auto', 'offline', 'local', 'remote'))
        mode.add_argument('--check', action='store_true', help='Test the local model connection')
        subparsers.add_parser('local-models', help='List models installed on the configured local server')
        sync = subparsers.add_parser('sync', help='Export or import a settings+history sync bundle (no credentials)')
        sync_actions = sync.add_subparsers(dest='sync_action', required=True)
        sync_export = sync_actions.add_parser('export', help='Write a sync bundle to a file')
        sync_export.add_argument('path', type=Path, help='Output file (existing files are never overwritten)')
        sync_import = sync_actions.add_parser('import', help='Import a sync bundle (merge-only, never overwrites credentials)')
        sync_import.add_argument('path', type=Path)
        sync_import.add_argument('--preview', action='store_true', help='Show what would change without applying')
        sync_import.add_argument('--yes', action='store_true', help='Apply the reviewed import')

        # Command: theme (create, edit, list, apply, delete, contrast-check themes)
        theme = subparsers.add_parser('theme', help='Create, edit, validate and manage themes')
        theme_actions = theme.add_subparsers(dest='theme_action', required=True)
        theme_actions.add_parser('list', help='List all available themes (bundled + user)')
        theme_show = theme_actions.add_parser('show', help='Show a theme document')
        theme_show.add_argument('name')
        theme_create = theme_actions.add_parser('create', help='Create a new theme from the default palette')
        theme_create.add_argument('name')
        theme_create.add_argument('--description', default='', help='Theme description')
        theme_create.add_argument('--from', dest='source', default='', help='Copy colours from an existing theme')
        theme_set = theme_actions.add_parser('set', help='Set a colour or UI value on a user theme')
        theme_set.add_argument('name')
        theme_set.add_argument('key', help='Colour key (background, text, accent, secondary, tertiary, border) or ui key (font_family, font_size, border_radius, opacity) or syntax key (keyword, string, ...)')
        theme_set.add_argument('value')
        theme_actions.add_parser('delete', help='Delete a user theme (built-in themes are protected)').add_argument('name')
        theme_contrast = theme_actions.add_parser('contrast', help='Show WCAG contrast report for a theme')
        theme_contrast.add_argument('name')
        theme_apply = theme_actions.add_parser('apply', help='Apply a theme to the current configuration')
        theme_apply.add_argument('name')
        theme_export = theme_actions.add_parser('export', help='Export a theme to a JSON file')
        theme_export.add_argument('name')
        theme_export.add_argument('path', type=Path, help='Output file (existing files are never overwritten)')
        theme_import = theme_actions.add_parser('import', help='Import a theme from a JSON file and save it')
        theme_import.add_argument('path', type=Path)
        theme_validate = theme_actions.add_parser('validate', help='Validate a theme and show warnings')
        theme_validate.add_argument('name')

        # Command: shortcuts (list, set, reset, validate configurable keybindings)
        shortcuts = subparsers.add_parser('shortcuts', help='Manage configurable keyboard shortcuts')
        shortcuts_actions = shortcuts.add_subparsers(dest='shortcuts_action', required=True)
        shortcuts_actions.add_parser('list', help='List all keybindings (defaults + user overrides)')
        shortcuts_show = shortcuts_actions.add_parser('show', help='Show one action binding')
        shortcuts_show.add_argument('action')
        shortcuts_set = shortcuts_actions.add_parser('set', help='Set a keybinding for an action')
        shortcuts_set.add_argument('action')
        shortcuts_set.add_argument('accelerator', help='GTK accelerator (e.g. "<Control>Return", "Escape", "<Shift><Alt>F1")')
        shortcuts_reset = shortcuts_actions.add_parser('reset', help='Reset one or all bindings to defaults')
        shortcuts_reset.add_argument('action', nargs='?', default='',
                                     help='Action to reset (omit to reset all)')
        shortcuts_actions.add_parser('validate', help='Check for conflicts and unknown actions')

        # Command: voice (speech-to-text: backends, config, transcribe)
        voice = subparsers.add_parser('voice', help='Voice input (speech-to-text) configuration and transcription')
        voice_actions = voice.add_subparsers(dest='voice_action', required=True)
        voice_actions.add_parser('status', help='Show availability of all STT backends')
        voice_actions.add_parser('backends', help='List supported STT backends')
        voice_config = voice_actions.add_parser('config', help='Show or set voice configuration')
        voice_config.add_argument('key', nargs='?', default='', help='Config key to show (omit to show all)')
        voice_config.add_argument('value', nargs='?', default='', help='Value to set')
        voice_transcribe = voice_actions.add_parser('transcribe', help='Transcribe a WAV audio file')
        voice_transcribe.add_argument('path', type=Path, help='WAV audio file to transcribe')
        voice_transcribe.add_argument('--language', default='', help='Language code (overrides config)')

        # Command: pair (pair programming: extract, review, diff code)
        pair = subparsers.add_parser('pair', help='Pair programming: extract, review and diff code')
        pair_actions = pair.add_subparsers(dest='pair_action', required=True)
        pair_extract = pair_actions.add_parser('extract', help='Extract code blocks from a markdown/AI response file')
        pair_extract.add_argument('path', type=Path, help='File containing the AI response (markdown)')
        pair_extract.add_argument('--json', action='store_true', help='Emit results as JSON')
        pair_review = pair_actions.add_parser('review', help='Review a proposed change (old vs new file)')
        pair_review.add_argument('old', type=Path, help='Original file')
        pair_review.add_argument('new', type=Path, help='Proposed file')
        pair_review.add_argument('--json', action='store_true', help='Emit results as JSON')
        pair_diff = pair_actions.add_parser('diff', help='Show a unified diff between two files')
        pair_diff.add_argument('old', type=Path)
        pair_diff.add_argument('new', type=Path)
        pair_scan = pair_actions.add_parser('scan', help='Scan a file for risky patterns')
        pair_scan.add_argument('path', type=Path, help='File to scan')
        pair_scan.add_argument('--json', action='store_true', help='Emit results as JSON')
        pair_config = pair_actions.add_parser('config', help='Show pair programming configuration')
        pair_config.add_argument('key', nargs='?', default='', help='Config key to show (omit for all)')

        knowledge = subparsers.add_parser('knowledge', help='Search bundled guides without any AI service')
        knowledge.add_argument('query', nargs='+')
        subparsers.add_parser('knowledge-verify', help='Validate bundled YAML schemas and references offline')
        audit_parser = subparsers.add_parser('actions', help='Review operations in the current conversation')
        audit_actions = audit_parser.add_subparsers(dest='audit_action', required=True)
        audit_actions.add_parser('list')
        audit_actions.add_parser('show').add_argument('id')

        trials = subparsers.add_parser(
            'trials', help='Record local test cases and review evidence without AI',
        )
        trial_actions = trials.add_subparsers(dest='trial_action', required=True)
        trial_start = trial_actions.add_parser('start', help='Start a local test run')
        trial_start.add_argument('--title', required=True, help='Name of the test run')
        trial_start.add_argument('--environment', required=True, help='Test environment identifier')
        trial_start.add_argument('--build', default='', help='Commit or package build under test')
        trial_start.add_argument('--type', dest='test_type',
                                 choices=('physical', 'vm', 'wsl', 'fixture', 'unknown'),
                                 default='unknown', help='Kind of environment under test')
        trial_actions.add_parser('status', help='Show the selected test run')
        trial_actions.add_parser('list', help='List locally recorded test runs')
        trial_use = trial_actions.add_parser('use', help='Select a locally recorded test run')
        trial_use.add_argument('id', help='Test run identifier')
        trial_begin = trial_actions.add_parser('begin', help='Begin a case in the selected run')
        trial_begin.add_argument('case_id', help='Case identifier from the test protocol')
        trial_begin.add_argument('--session', help='Conversation to associate; defaults to the active conversation')
        trial_begin.add_argument('--variant', default='', help='Case variant or repetition')
        trial_begin.add_argument('--notes', default='', help='Initial observations')
        trial_end = trial_actions.add_parser('end', help='Record a result and collect case evidence')
        trial_end.add_argument('--result', required=True,
                               choices=('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'N/A'))
        trial_end.add_argument('--notes', default='', help='Expected and observed behavior')
        trial_end.add_argument('--operation', help='Restrict action evidence to one operation')
        trial_end.add_argument('--logs', action='store_true',
                               help='Also collect a bounded application log excerpt; review before sharing')
        trial_actions.add_parser('finish', help='Finish the run after closing the current case')
        trial_preview = trial_actions.add_parser('preview', help='Review all content eligible for export')
        trial_preview.add_argument('--output', type=Path,
                                   help='New private preview file; existing files are never replaced')
        trial_attach = trial_actions.add_parser('attach', help='Add one explicitly selected evidence file')
        trial_attach.add_argument('path', type=Path)
        trial_exclude = trial_actions.add_parser('exclude', help='Exclude an attachment from export')
        trial_exclude.add_argument('id', help='Attachment identifier')
        trial_export = trial_actions.add_parser('export', help='Export a reviewed run as a new local ZIP')
        trial_export.add_argument('--output', type=Path, required=True, help='New ZIP destination')
        trial_export.add_argument('--reviewed', action='store_true',
                                  help='Confirm that trials preview content and attachments have been reviewed')

        diagnose = subparsers.add_parser('diagnose', help='Prepare a local, redacted diagnostic report without AI')
        diagnose.add_argument('symptom', nargs='*')
        diagnostic_input = diagnose.add_mutually_exclusive_group()
        diagnostic_input.add_argument('--input', type=Path, help='UTF-8 log excerpt, up to 64 KiB')
        diagnostic_input.add_argument('--stdin', action='store_true')
        diagnose.add_argument('--collect', action='append', choices=tuple(PROBES), default=[],
                              help='Explicitly collect one fixed local read probe (repeatable)')
        diagnose.add_argument('--format', choices=('markdown', 'json'), default='markdown')
        diagnose.add_argument('--output', type=Path, help='New private export file; existing files are never replaced')
        changes = subparsers.add_parser('changes', help='Review approved file writes and recover their contents')
        change_actions = changes.add_subparsers(dest='change_action', required=True)
        change_list = change_actions.add_parser('list')
        change_list.add_argument('--archived', action='store_true', help='Also list archived changes')
        change_archive = change_actions.add_parser('archive', help='Preserve completed changes and free journal capacity')
        change_archive.add_argument('--yes', action='store_true', help='Approve moving completed records to a private archive')
        for action in ('show', 'restore'):
            change_parser = change_actions.add_parser(action)
            change_parser.add_argument('id')
            if action == 'restore':
                change_parser.add_argument('--yes', action='store_true', help='Approve recovery after reviewing changes show <id>')

        # Command: system
        system_parser = subparsers.add_parser('system', help='System information')
        system_subparsers = system_parser.add_subparsers(dest='system_command')

        # system info
        system_subparsers.add_parser('info', help='Full system information')
        system_subparsers.add_parser('context', help='Observed system components and evidence (JSON)')

        # system commands
        system_subparsers.add_parser('commands', help='List of allowed commands')

        # system processes
        system_subparsers.add_parser('processes', help='List of processes')

        # system network
        system_subparsers.add_parser('network', help='Network information')

        # system exec
        exec_parser = system_subparsers.add_parser('exec', help='Run a command')
        exec_parser.add_argument('command', nargs='+', help='Command to run')

        # Command: history
        history_parser = subparsers.add_parser('history', help='View conversation history')
        history_parser.add_argument('history_action', nargs='?', choices=('recover',),
                                    help='Back up the complete history file privately and start fresh')
        history_parser.add_argument('--yes', action='store_true',
                                    help='Approve history recover after reading the error and preserving the original')
        history_parser.add_argument('--limit', '-n', type=int, default=20,
                                     help='Number of messages to show')
        history_parser.add_argument('--clear', action='store_true',
                                     help='Clear history')

        # Command: stats
        stats_parser = subparsers.add_parser('stats', help='Usage statistics')
        stats_parser.add_argument('--reset', action='store_true',
                                   help='Reset statistics')

        # Command: benchmarks
        benchmarks_parser = subparsers.add_parser('benchmarks',
                                                  help='Run micro-benchmarks of the hot pure-Python paths')
        benchmarks_parser.add_argument('--scale', type=float, default=1.0,
                                       help='Repeat-count scale (0.2 = quick, 2.0 = thorough)')
        benchmarks_parser.add_argument('--json', action='store_true',
                                       help='Emit results as JSON')

        # Command: serve (loopback-only local REST API)
        serve_parser = subparsers.add_parser('serve',
                                             help='Start the loopback-only local REST API (127.0.0.1)')
        serve_parser.add_argument('--host', default='127.0.0.1',
                                  help='Bind address (loopback only; default: 127.0.0.1)')
        serve_parser.add_argument('--port', type=int, default=None,
                                  help='TCP port (default: config local_api.port or 7621; 0 = pick a free port)')
        serve_parser.add_argument('--token', default='',
                                  help='Bearer token (default: read config api.local.token or generate one)')

        # Command: config
        config_parser = subparsers.add_parser('config', help='Configuration')
        config_subparsers = config_parser.add_subparsers(dest='config_command')

        # config list
        config_subparsers.add_parser('list', help='List the current configuration')

        # config set
        set_parser = config_subparsers.add_parser('set', help='Set a configuration value')
        set_parser.add_argument('key', help='Configuration key (e.g. api.default_provider)')
        set_parser.add_argument('value', help='Value to set')

        # config get
        get_parser = config_subparsers.add_parser('get', help='Get a configuration value')
        get_parser.add_argument('key', help='Configuration key')

        # Command: providers
        subparsers.add_parser('providers', help='List the available AI providers')

        # Command: tokens
        tokens_parser = subparsers.add_parser('tokens', help='Token management')
        tokens_subparsers = tokens_parser.add_subparsers(dest='tokens_command')
        tokens_subparsers.add_parser('usage', help='Show token usage')
        tokens_subparsers.add_parser('reset', help='Reset the token count')

        return parser.parse_args(argv)

    def run(self, args=None):
        try:
            return self._run_command(args) if args is not None else self._run_command()
        finally:
            if self.history_store is not None:
                self.history_store.close()
            self.config.flush()
            self.ai_client.flush_usage()

    def _run_command(self, args=None):
        """Run the CLI application.

        Devolve o exit code do comando (0 = sucesso): scripts que encadeiam
        o CLI (`capture --ocr && ...`) precisam de detetar falhas — antes,
        TODAS as falhas esperadas saíam com 0.
        """
        args = self.parse_args() if args is None else args

        if not args.command:
            self.print_help()
            return 0

        if args.command == 'sessions':
            return self.handle_sessions(args)
        if args.command == 'documents':
            return self.handle_documents(args)
        if args.command == 'diagnose':
            return self.handle_diagnose(args)
        if args.command == 'changes':
            return self.handle_changes(args)
        if args.command == 'trials':
            return self.handle_trials(args)
        if args.command == 'mode':
            if args.value:
                self.config.set_assistance_mode(args.value)
                self.config.flush()
            status = self.ai_client.provider_status(check_connection=args.check)
            print(json.dumps(status.__dict__, ensure_ascii=False, indent=2))
            return 1 if status.state in ('blocked', 'unreachable', 'model_missing', 'error') else 0
        if args.command == 'local-models':
            for model in self.ai_client.list_local_models():
                print(model)
            return 0
        if args.command == 'sync':
            return self.handle_sync(args)
        if args.command == 'knowledge-verify':
            from .knowledge_loader import bundled_modules
            print(json.dumps({'schema': '0.1', 'modules': [item['id'] for item in bundled_modules()],
                              'validation': 'structure_and_references_only'}, indent=2))
            return 0
        if args.command == 'actions':
            try:
                print(self.actions.audit.render(self._store().active_session_id, args.id if args.audit_action == 'show' else None))
                return 0
            except (OSError, ValueError):
                print('Cannot read the operation audit.')
                return 1
        if args.command == 'knowledge':
            query = ' '.join(args.query)
            exact = PROCEDURE_BY_ID.get(query)
            matches = ((exact,) if exact and exact.applies_to(self.offline.distro)
                       else search_procedures(query, self.offline.distro, limit=5))
            result = '\n\n'.join(render_procedure(item, get_language(), self.offline.distro) for item in matches)
            print(result or ('Nenhum guia aplicável encontrado.' if get_language() == 'pt' else 'No applicable guide found.'))
            return 0 if result else 1

        # Process command
        if args.command == 'chat':
            return self.handle_chat(args)
        elif args.command == 'capture':
            return self.handle_capture(args)
        elif args.command == 'expert':
            return self.handle_expert(args)
        elif args.command == 'system':
            return self.handle_system(args)
        elif args.command == 'history':
            return self.handle_history(args)
        elif args.command == 'stats':
            return self.handle_stats(args)
        elif args.command == 'benchmarks':
            return self.handle_benchmarks(args)
        elif args.command == 'serve':
            return self.handle_serve(args)
        elif args.command == 'theme':
            return self.handle_theme(args)
        elif args.command == 'shortcuts':
            return self.handle_shortcuts(args)
        elif args.command == 'voice':
            return self.handle_voice(args)
        elif args.command == 'pair':
            return self.handle_pair(args)
        elif args.command == 'config':
            return self.handle_config(args)
        elif args.command == 'providers':
            return self.handle_providers(args)
        elif args.command == 'tokens':
            return self.handle_tokens(args)
        else:
            self.print_help()
            return 0

    def print_help(self):
        """Show the help text."""
        print("""
Linux AI Assistant - Command-Line Interface

Available commands:
  chat [message]            - Chat with the AI
  capture [options]         - Capture the screen
  expert [message]          - Expert mode
  system [subcommand]       - System information
  history [options]         - View conversation history
  sessions [subcommand]     - Manage, search and export conversations
  knowledge [query]         - Search bundled local guides
  diagnose [symptom]        - Prepare a local diagnostic report
  changes [subcommand]     - Review or recover approved file writes
  trials [subcommand]      - Record test cases and export reviewed local evidence
  mode [value]              - Show/select assistance mode
  local-models              - List installed models on the local server
  sync [subcommand]         - Export/import a settings+history sync bundle (no credentials)
  theme [subcommand]        - Create, edit, validate and manage themes
  shortcuts [subcommand]    - Manage configurable keyboard shortcuts
  voice [subcommand]        - Voice input (speech-to-text) configuration and transcription
  pair [subcommand]         - Pair programming: extract, review and diff code
  stats [options]           - Usage statistics
  benchmarks [options]      - Run micro-benchmarks of hot pure-Python paths
  config [subcommand]       - Configuration
  providers                 - List the AI providers
  tokens [subcommand]       - Token management

Use --help with any command for more information.

Examples:
  python -m src.cli chat "What is my operating system?"
  python -m src.cli capture --ocr
  python -m src.cli system info
        """)

    @staticmethod
    def _history_file() -> Path:
        return Path.home() / ".config" / "linux_ai_assistant" / "history.json"

    def _store(self):
        if self.history_store is None:
            self.history_store = HistoryStore(self._history_file())
            self.history_store.list_sessions()
        return self.history_store

    def _load_history(self):
        self.offline.restore_diagnostic(self._store().get_diagnostic_state())
        return self._store().load_entries()

    def _prepare_conversation(self, args):
        if getattr(args, 'session', None):
            self._store().select_session(args.session)
        elif getattr(args, 'new_session', False):
            self._store().create_session()
        else:
            return
        self.conversation_history = self._store().load_entries()
        self._pending_exchanges = []
        self.offline.restore_diagnostic(self._store().get_diagnostic_state())

    @staticmethod
    def _message_from_args(args):
        message = ' '.join(args.message)
        extra = ''
        if getattr(args, 'input', None):
            with args.input.open('rb') as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError('Input file exceeds 64 KiB')
            extra = data.decode('utf-8')
        elif getattr(args, 'stdin', False):
            extra = sys.stdin.read(65537)
        message = '\n\n'.join(part for part in (message, extra) if part).strip()
        if len(message.encode('utf-8')) > 65536:
            raise ValueError('Input exceeds 64 KiB')
        if not message:
            raise ValueError('Provide a message, --stdin or --input')
        return message

    def handle_diagnose(self, args):
        pasted = ''
        if args.input:
            if not args.input.is_file():
                raise ValueError('Input must be a regular file')
            with args.input.open('rb') as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError('Diagnostic input exceeds 64 KiB')
            pasted = data.decode('utf-8')
        elif args.stdin:
            pasted = sys.stdin.read(65537)
        report = build_report(' '.join(args.symptom), pasted, self.offline.distro, get_language(),
                              args.collect, self.system_utils, session_id=self._store().active_session_id)
        content = export_report(report, args.format)
        if args.output:
            write_conversation(args.output, content)
            print(str(args.output))
        else:
            print(content)
        return 0

    def _trial_recorder(self):
        from .trial_recorder import make_recorder
        return make_recorder(self._store(), self.config, self.offline.distro)

    def handle_trials(self, args):
        """Explicit local collection, with logs and sharing review opt-in."""
        try:
            recorder = self._trial_recorder()
            action = args.trial_action
            if action == 'start':
                run = recorder.start(args.title, args.environment,
                                     build_ref=args.build, test_type=args.test_type)
                print(run['id'])
            elif action == 'status':
                print(json.dumps(recorder.current(), ensure_ascii=False, indent=2))
            elif action == 'list':
                print(json.dumps(recorder.list_runs(), ensure_ascii=False, indent=2))
            elif action == 'use':
                print(json.dumps(recorder.select(args.id), ensure_ascii=False, indent=2))
            elif action == 'begin':
                session_id = args.session or self._store().active_session_id
                print(json.dumps(recorder.begin_case(
                    args.case_id, session_id, variant=args.variant,
                    interface='cli', notes=args.notes,
                ), ensure_ascii=False, indent=2))
            elif action == 'end':
                print(json.dumps(recorder.end_case(
                    args.result, notes=args.notes, operation_id=args.operation,
                    collect_logs=args.logs,
                ), ensure_ascii=False, indent=2))
            elif action == 'finish':
                print(json.dumps(recorder.finish(), ensure_ascii=False, indent=2))
            elif action == 'preview':
                content = recorder.preview()
                if args.output:
                    write_conversation(args.output, content)
                    print(str(args.output))
                else:
                    print(content)
            elif action == 'attach':
                print(json.dumps(recorder.attach(args.path), ensure_ascii=False, indent=2))
            elif action == 'exclude':
                recorder.exclude_attachment(args.id)
                print(args.id)
            elif action == 'export':
                if not args.reviewed:
                    print('Review trials preview and selected attachments first; '
                          '--reviewed is required to export.', file=sys.stderr)
                    return 1
                print(str(recorder.export(args.output, reviewed=True)))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print('Cannot record or export the test run: {}'.format(exc), file=sys.stderr)
            return 1

    def handle_changes(self, args):
        journal = ChangeJournal()
        def allowed():
            return self.config.get('permissions.allowed_edit_dirs', [])
        if args.change_action == 'list':
            print(json.dumps(journal.list_changes(include_archived=getattr(args, 'archived', False)), ensure_ascii=False, indent=2))
            return 0
        if args.change_action == 'archive':
            if not args.yes:
                print('--yes is required to preserve completed changes in a private archive.', file=sys.stderr)
                return 1
            print(json.dumps(journal.archive(), ensure_ascii=False, indent=2))
            return 0
        if args.change_action == 'show':
            record = next((item for item in journal.list_changes(include_archived=True) if item['id'] == args.id), None)
            if record is None:
                raise KeyError('Unknown change')
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if not args.yes:
            print('Review changes show <id> first; --yes is required to approve recovery.', file=sys.stderr)
            return 1
        print(json.dumps(journal.restore(args.id, allowed), ensure_ascii=False, indent=2))
        return 0

    def handle_sessions(self, args):
        store = self._store()
        action = args.session_action
        if action == 'list':
            for session in store.list_sessions(include_archived=True):
                marker = '*' if session['id'] == store.active_session_id else ' '
                archived = ' [archived]' if session['archived'] else ''
                print('{} {}  {} ({} messages){}'.format(marker, session['id'], session['title'], session['message_count'], archived))
        elif action == 'new':
            print(store.create_session(args.title)['id'])
        elif action == 'use':
            print(store.select_session(args.id)['id'])
        elif action == 'rename':
            store.rename_session(args.id, args.title)
        elif action in ('archive', 'restore'):
            store.archive_session(args.id, action == 'archive')
        elif action == 'delete':
            store.delete_session(args.id)
        elif action == 'search':
            print(json.dumps(store.search(args.query, include_archived=True), ensure_ascii=False, indent=2))
        elif action == 'export':
            content = store.export_session(args.session, args.format)
            if args.output:
                write_conversation(args.output, content)
                print(args.output)
            else:
                print(content)
        elif action == 'import':
            text = read_conversation(args.path)
            preview = store.preview_import(text)
            if getattr(args, 'preview', False):
                print(preview['preview_text'])
                return 0
            if not getattr(args, 'yes', False):
                print(preview['preview_text'])
                print(_("Review this conversation, then repeat the import with --yes."))
                return 1
            print(store.import_session(text)['id'])
        return 0

    def handle_sync(self, args):
        from .sync_bundle import (
            SyncBundleError, apply_settings, build_bundle, merge_sessions,
            parse_bundle, plan_import, serialize_bundle,
        )
        action = args.sync_action
        if action == 'export':
            store = self._store()
            document = store._snapshot()
            bundle = build_bundle(self.config.config, document.get("sessions", []),
                                  source=getpass.getuser())
            text = serialize_bundle(bundle)
            write_conversation(args.path, text)
            print(args.path)
            return 0
        if action == 'import':
            text = read_conversation(args.path)
            try:
                bundle = parse_bundle(text)
            except SyncBundleError as error:
                print(_("Invalid sync bundle: {error}").format(error=error))
                return 1
            store = self._store()
            document = store._snapshot()
            existing_ids = [s["id"] for s in document.get("sessions", [])]
            plan = plan_import(bundle, self.config.config, existing_ids)
            summary = {
                "settings_to_apply": plan.settings_to_apply,
                "sessions_to_add": len(plan.sessions_to_add),
                "sessions_to_update": plan.sessions_to_update,
                "total_changes": plan.total_changes,
            }
            if getattr(args, 'preview', False):
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            if not getattr(args, 'yes', False):
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                print(_("Review this sync bundle, then repeat the import with --yes."))
                return 1
            applied = apply_settings(bundle, self.config.config)
            self.config.flush()
            merged = merge_sessions(bundle, document.get("sessions", []))
            if merged:
                store.replace_sessions(merged)
            print(json.dumps({"applied_settings": len(applied),
                              "sessions": len(merged)}, ensure_ascii=False, indent=2))
            return 0
        return 0

    def handle_documents(self, args):
        store = None
        try:
            store = DocumentStore()
            action = args.document_action
            if action == 'add':
                print(json.dumps(store.add(args.paths), ensure_ascii=False, indent=2))
            elif action == 'list':
                print(json.dumps(store.list_documents(), ensure_ascii=False, indent=2))
            elif action == 'search':
                result = store.context(' '.join(args.query), max_chars=MAX_DOCUMENT_CONTEXT_CHARS)
                print(display_document_context(result) if result else _("No matching passages in the selected documents."))
                return 0 if result else 1
            elif action == 'remove':
                if not store.remove(args.id):
                    print(_("Document snapshot not found."), file=sys.stderr)
                    return 1
                print(_("Document snapshot removed."))
            elif action == 'reindex':
                print(json.dumps(store.reindex(args.ids or None), ensure_ascii=False, indent=2))
            elif action == 'clear':
                if not args.yes:
                    print(_("Use documents clear --yes to remove all local snapshots."))
                    return 1
                store.clear()
                print(_("All document snapshots removed."))
            return 0
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        finally:
            if store is not None:
                store.close()

    def _build_request_messages(self, message: str):
        """Assemble the request context: system prompt + capped history + message."""
        context = self._get_context_message(message)
        # Cap the context the same way the GUI does - never re-send the whole
        # conversation on every turn.
        try:
            max_messages = max(2, min(500, int(self.config.get("context.max_messages", 20))))
        except (TypeError, ValueError):
            max_messages = 20
        history = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in self.conversation_history[-max_messages:]
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        ]
        try:
            max_chars = max(1000, min(400000, int(self.config.get('context.max_chars', 12000))))
        except (TypeError, ValueError):
            max_chars = 12000
        total = sum(len(item['content']) for item in history) + len(message)
        while history and total > max_chars:
            total -= len(history.pop(0)['content'])
        full_history = ([context] + history) if context else history
        full_history.append({"role": "user", "content": message})
        return full_history

    def _record_exchange(self, message: str, response_text: str):
        """Append a user/assistant exchange to the in-memory history."""
        exchange = [
            {'role': 'user', 'content': message, 'timestamp': time.time()},
            {'role': 'assistant', 'content': response_text, 'timestamp': time.time()},
        ]
        self.conversation_history.extend(exchange)
        self._pending_exchanges.extend(exchange)
        self.conversation_history = self.conversation_history[-1000:]

    def _run_offline(self, message: str) -> _ActionResult:
        """Answer from local knowledge and, on a terminal, offer to run the action."""
        reply = self.offline.handle(message, get_language())
        print(f"\n[OFFLINE]\n{reply.text}\n")
        extra = self._offer_action(reply)
        if extra:
            return _ActionResult(reply.text + "\n\n" + extra, extra.status)
        return _ActionResult(reply.text, extra.status)

    def _offer_model_action(self, message: str) -> _ActionResult:
        """Offer a catalog action for the user's sentence, not for model text."""
        propose = getattr(self.offline, "propose", None)
        if not callable(propose):
            return _ActionResult()
        try:
            reply = propose(message, get_language())
        except Exception as exc:
            logger.exception("Could not prepare a confirmed action: %s", exc)
            return _ActionResult()
        interaction, commands = split_offer(reply)
        if not interaction and not commands:
            return _ActionResult()
        if getattr(reply, "text", ""):
            print(f"\n{reply.text}\n")
        return self._offer_action(reply)

    def _offer_action(self, reply) -> _ActionResult:
        """Print the exact argv. Run it only after a confirmation on a TTY.

        A pipe or a unit test is not a terminal: the commands are shown and
        nothing is scanned or executed.
        """
        interaction, commands = split_offer(reply)
        if interaction:
            if not sys.stdin.isatty():
                self._print_commands(commands)
                print(_("This step needs an interactive terminal."))
                return _ActionResult()
            if interaction == "wifi":
                return self._choose_wifi()
            if interaction == "printer":
                return self._choose_printer(commands)
            if interaction == "scanner":
                return self._choose_scanner(commands)
            return _ActionResult()
        if not commands:
            return _ActionResult()
        self._print_commands(commands)
        if not sys.stdin.isatty():
            print(_("This step needs an interactive terminal."))
            return _ActionResult()
        return self._confirm_and_run(commands)

    def _print_commands(self, commands):
        if not commands:
            return
        print(format_command_offer(
            commands,
            _("These changes need administrator rights (pkexec):"),
            _("Run suggested commands?"),
        ))
        print()

    def _ask(self, prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            return ""

    def _confirm_and_run(self, commands) -> _ActionResult:
        if not commands:
            return _ActionResult()
        if not confirmed(self._ask(_("Run these commands? [y/N] "))):
            print(_("Cancelled"))
            return _ActionResult(status="cancelled")
        chunks = []
        status = "success"
        for command in commands:
            ok, output = offline_assistant.OfflineAssistant.run_command(command)
            line = f"$ {command.display()}"
            detail = output or (_("Done.") if ok else _("Failed."))
            print(line)
            print(detail)
            chunks.append(line)
            chunks.append(detail)
            if not ok:
                status = "failed"
                break
        return _ActionResult("\n".join(chunks), status)

    def _choose_wifi(self) -> _ActionResult:
        networks, error = collect_wifi()
        if not networks:
            text = error or _("No Wi-Fi networks found.")
            print(text)
            return _ActionResult(text, "failed" if error else "none")
        index = choose_numbered(
            networks, self._ask, sys.stdout.write,
            lambda network: (
                f"{network.ssid}  {network.signal}%  {network.security or '--'}"
            ),
            prompt=_("Number (Enter cancels): "),
        )
        if index is None:
            print(_("Cancelled"))
            return _ActionResult(status="cancelled")
        network = networks[index]
        secret = ""
        if wifi_needs_password(network):
            try:
                secret = getpass.getpass(_("Password: "))
            except EOFError:
                secret = ""
            if not secret:
                print(_("Cancelled"))
                return _ActionResult(status="cancelled")
        ok, output = connect_wifi(network.ssid, secret or None)
        secret = ""
        if ok:
            text = _("Connected to {ssid}.").format(ssid=network.ssid)
        else:
            text = _("Could not connect to {ssid}.").format(ssid=network.ssid)
            if output:
                text = text + "\n" + output
        print(text)
        return _ActionResult(text, "success" if ok else "failed")

    def _choose_printer(self, commands) -> _ActionResult:
        devices, error = collect_printers()
        if not devices:
            if commands:
                self._print_commands(commands)
                return self._confirm_and_run(commands)
            text = error or _("No printers found.")
            print(text)
            return _ActionResult(text, "failed" if error else "none")
        index = choose_numbered(
            devices, self._ask, sys.stdout.write,
            lambda device: device.uri + ("" if device.driverless else " (driver)"),
            prompt=_("Number (Enter cancels): "),
        )
        if index is None:
            print(_("Cancelled"))
            return _ActionResult(status="cancelled")
        device = devices[index]
        default = queue_name_for(device.uri)
        typed = self._ask(_("Queue name [{name}]: ").format(name=default)).strip()
        try:
            argv = printer_add_argv(typed or default, device.uri)
        except ValueError as exc:
            print(exc)
            return _ActionResult(str(exc), "failed")
        command = offline_assistant.Command(
            argv=argv, privileged=True, description=f"Add printer {argv[2]}",
        )
        self._print_commands([command])
        return self._confirm_and_run([command])

    def _choose_scanner(self, commands) -> _ActionResult:
        devices, error = collect_scanners()
        if devices:
            lines = [f"{item.device} — {item.description}" for item in devices]
            text = "\n".join(lines)
            print(text)
            return _ActionResult(text, "success")
        if commands:
            self._print_commands(commands)
            return self._confirm_and_run(commands)
        text = error or _("No scanners found.")
        print(text)
        return _ActionResult(text, "failed" if error else "none")

    def _run_conversational_action(self, message, args):
        engine = getattr(self, 'actions', None)
        if engine is None:
            return None
        reply = engine.handle(
            message, self._store().active_session_id, get_language(),
            can_execute=bool(sys.stdin.isatty() and not getattr(args, 'stdin', False)
                             and not getattr(args, 'input', None)),
            persist_choices=not args.no_history,
        )
        if reply is None:
            return None
        print("\n" + reply.text + "\n")
        if reply.change is not None:
            change = reply.change
            try:
                print(_("Keep this display change? [y/N] "), end='', flush=True)
                readable, writable, exceptional = select.select([sys.stdin], [], [], max(0, change.remaining_seconds))
                keep = bool(readable and confirmed(sys.stdin.readline()))
                ok, detail = change.confirm() if keep else change.revert()
            except (OSError, ValueError, KeyboardInterrupt):
                ok, detail = change.revert()
            print(detail)
            reply.text += "\n\n" + detail
            reply.status = "done" if ok else "failed"
        if not args.no_history:
            self._record_exchange(message, reply.text)
            self._save_history()
        return reply.exit_code

    @staticmethod
    def _read_document_context(text, max_chars):
        """Read reviewed excerpts from the local index, closing it afterwards."""
        store = DocumentStore()
        try:
            return store.context(text, max_chars=max_chars)
        finally:
            store.close()

    @staticmethod
    def _confirm_cli_image(attachment, provider, model, args):
        """Print the exact snapshot destination; only --send-image approves it."""
        print(_("Image: {name} ({width} × {height}); destination: {provider}, {model}.")
              .format(name=attachment.filename, width=attachment.width, height=attachment.height,
                      provider=provider, model=model))
        if getattr(args, 'send_image', False):
            return True
        print(_("Review the original image, then repeat with --send-image to send this normalized snapshot."))
        return False

    @staticmethod
    def _confirm_cli_documents(context, args):
        """Print the exact excerpts; only --send-document-context approves them."""
        if getattr(args, 'send_document_context', False):
            return True
        print(display_document_context(context))
        print(_("Review these excerpts, then repeat with --send-document-context to send them to the model."))
        return False

    def handle_chat(self, args):
        """Process the chat command. Devolve exit code (0 sucesso, 1 falha)."""
        image_path = getattr(args, 'image', None)
        use_documents = bool(getattr(args, 'documents', False))
        if getattr(args, 'send_image', False) and image_path is None:
            print(_("Choose an image with --image before using --send-image."), file=sys.stderr)
            return 1
        if getattr(args, 'send_document_context', False) and not use_documents:
            print(_("Use --documents before approving document context."), file=sys.stderr)
            return 1
        if image_path is not None and not getattr(args, 'message', None) and not getattr(args, 'stdin', False) and not getattr(args, 'input', None):
            args.message = [_("Describe this image.")]
        message = self._message_from_args(args)
        try:
            plan = prepare_request(
                message,
                ai_client=self.ai_client,
                image=prepare_image(image_path) if image_path is not None else None,
                provider=getattr(args, 'provider', None),
                model=getattr(args, 'model', None),
                documents_enabled=use_documents,
                resolve_route=image_path is not None or use_documents,
                read_document_context=self._read_document_context,
                confirm_image=lambda attachment, provider, model:
                    self._confirm_cli_image(attachment, provider, model, args),
                confirm_documents=lambda context, provider:
                    self._confirm_cli_documents(context, args),
            )
        except (OSError, ValueError, AIProviderError, RequestPreparationError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if plan is None:
            return 0
        message = plan.text
        images = plan.images
        document_context = plan.document_context

        self._prepare_conversation(args)

        if args.expert:
            self.expert_mode = True

        if not images and not use_documents:
            action_code = self._run_conversational_action(message, args)
            if action_code is not None:
                return action_code

        full_history = self._build_request_messages(message)
        if document_context:
            full_history = with_document_context(full_history, document_context)
        request_options = {'provider': args.provider, 'model': args.model}
        if images:
            request_options['images'] = images

        # Get the response (or answer offline when the provider is unusable)
        answered_offline = False
        action_result = _ActionResult()
        if not self.ai_client.provider_ready(args.provider):
            if images:
                print(_("The image request cannot be answered offline."), file=sys.stderr)
                return 1
            if document_context:
                response_text = _("Local document matches (no model request):") + '\n\n' + display_document_context(document_context)
                print(response_text)
            else:
                response_text = self._run_offline(message)
                action_result = response_text
            answered_offline = True
        elif args.stream:
            print("\n[AI] ", end="", flush=True)
            response_text = ""
            try:
                for chunk in self.ai_client.stream_chat(full_history, **request_options):
                    print(chunk, end="", flush=True)
                    response_text += chunk
                print("\n")
            except (AIImageRequestError, AIRequestCancelled, AIResponseLimitError) as error:
                print(str(error), file=sys.stderr)
                return 1
            except AIProviderError as e:
                # Falha estruturada (rede/HTTP/config): não é conteúdo do
                # modelo — não persistir como resposta, cair para offline.
                print(f"\n[!] {e}\n")
                if images:
                    return 1
                if document_context:
                    response_text = _("The model is unavailable. Local document matches:") + '\n\n' + display_document_context(document_context)
                    print(response_text)
                else:
                    response_text = self._run_offline(message)
                    action_result = response_text
                answered_offline = True
        else:
            try:
                response_text = self.ai_client.chat(full_history, **request_options)
            except (AIImageRequestError, AIRequestCancelled, AIResponseLimitError) as error:
                print(str(error), file=sys.stderr)
                return 1
            if response_text:
                print(f"\n[AI]\n{response_text}\n")
            else:
                if images:
                    print(_("The model returned no answer for the image."), file=sys.stderr)
                    return 1
                if document_context:
                    response_text = _("The model is unavailable. Local document matches:") + '\n\n' + display_document_context(document_context)
                    print(response_text)
                else:
                    response_text = self._run_offline(message)
                    action_result = response_text
                answered_offline = True

        if response_text and not answered_offline and not images and not use_documents:
            extra = self._offer_model_action(message)
            action_result = extra
            if extra:
                response_text = response_text + "\n\n" + extra

        # Save to history
        if not args.no_history:
            history_message = message
            if images:
                history_message += '\n\n' + _("[Image attached for this request; pixels are not stored.]")
            history_response = response_text
            if document_context and answered_offline:
                history_response = _("Local document matches were shown. Excerpts remain only in the local document index.")
                print(_("These local excerpts are not saved in conversation history."))
            self._record_exchange(history_message, history_response)
            self._save_history()
        return getattr(action_result, "exit_code", 0)

    def handle_capture(self, args):
        """Process the capture command. Devolve exit code."""
        if args.window:
            success, image_path = self.system_utils.capture_active_window(args.output)
        else:
            success, image_path = self.system_utils.capture_screen(args.output)

        if success:
            print(f"✓ Capture saved at: {image_path}")

            if args.ocr:
                print("Extracting text from the image...")
                success, text = self.system_utils.extract_text_from_image(image_path)
                if success:
                    print(f"\n[Screen Capture]\n{text}\n")
                    return 0
                print(f"✗ Error extracting text: {text}")
                return 1
            return 0
        print(f"✗ Error while capturing: {image_path}")
        return 1

    def handle_expert(self, args):
        """Expert uses the same routing, sessions and fallback as ordinary chat."""
        args.expert = True
        if not hasattr(args, 'stream'):
            args.stream = False
        if not hasattr(args, 'no_history'):
            args.no_history = False
        return self.handle_chat(args)

    def handle_system(self, args):
        """Process the system command."""
        if not args.system_command:
            # Show the full info
            info = self.system_utils.get_system_info()
            self._print_system_info(info)
            return

        if args.system_command == 'info':
            info = self.system_utils.get_system_info()
            self._print_system_info(info)
        elif args.system_command == 'context':
            from .system_context import detect_system_context
            print(json.dumps(detect_system_context(self.offline.distro).to_dict(), indent=2, ensure_ascii=False))
        elif args.system_command == 'commands':
            commands = self.config.get("permissions.allowed_commands", [])
            print("Allowed commands:")
            for cmd in sorted(commands):
                print(f"  - {cmd}")
        elif args.system_command == 'processes':
            processes = self.system_utils.get_process_list()
            print(f"Processes ({len(processes)}):")
            for proc in processes[:20]:  # Show the first 20
                print(f"  {proc['pid']:>8} {proc['user']:<12} {proc['cpu']:<8} {proc['memory']:<8} {proc['name']}")
            if len(processes) > 20:
                print(f"  ... and {len(processes) - 20} more processes")
        elif args.system_command == 'network':
            network_info = self.system_utils.get_network_info()
            if 'interfaces' in network_info:
                print("Network interfaces:")
                for name, stats in network_info['interfaces'].items():
                    print(f"  {name}:")
                    for key, value in stats.items():
                        print(f"    {key}: {value}")
            if 'connections' in network_info:
                print(f"\nConnections ({len(network_info['connections'])}):")
                for conn in network_info['connections'][:10]:
                    print(f"  {conn['local_addr']:<25} -> {conn['remote_addr']:<25} (PID: {conn['pid']})")
        elif args.system_command == 'exec':
            # args.command já é a lista pós-shell: juntar com espaços e
            # re-dividir com shlex partia argumentos com espaços
            # (`grep "a b" file` virava `grep a b file`).
            success, output = self.system_utils.execute_command(args.command)
            if success:
                print(f"✓ {output}")
                return 0
            print(f"✗ {output}")
            return 1
        return 0

    def _print_system_info(self, info: Dict):
        """Show system information in an organized way."""
        print("\n" + "=" * 50)
        print("SYSTEM INFORMATION")
        print("=" * 50)

        print("\nOperating System:")
        print(f"  Name: {info.get('distro', 'Unknown')}")
        print(f"  ID: {info.get('distro_id', 'Unknown')}")
        print(f"  Version: {info.get('release', 'Unknown')}")
        print(f"  Kernel: {info.get('version', 'Unknown')}")
        print(f"  Architecture: {info.get('machine', 'Unknown')} ({info.get('architecture', 'Unknown')})")

        print("\nHardware:")
        print(f"  Hostname: {info.get('hostname', 'Unknown')}")
        print(f"  CPU: {info.get('cpu_cores', 'N/A')} cores ({info.get('cpu_physical_cores', 'N/A')} physical)")
        print(f"  CPU usage: {info.get('cpu_usage', 'N/A')}")

        print("\nMemory:")
        print(f"  Total: {info.get('memory_total', 'N/A')}")
        print(f"  Used: {info.get('memory_used', 'N/A')}")
        print(f"  Available: {info.get('memory_available', 'N/A')}")
        print(f"  Usage: {info.get('memory_percent', 'N/A')}")

        print("\nDisk:")
        print(f"  Total: {info.get('disk_total', 'N/A')}")
        print(f"  Used: {info.get('disk_used', 'N/A')}")
        print(f"  Free: {info.get('disk_free', 'N/A')}")
        print(f"  Usage: {info.get('disk_percent', 'N/A')}")

        print(f"\nUptime: {info.get('uptime', 'N/A')}")

        print("\nUser:")
        print(f"  Name: {info.get('username', 'Unknown')}")
        print(f"  Is root: {info.get('is_root', False)}")

        print("\nGraphical Environment:")
        print(f"  Type: {'Wayland' if info.get('is_wayland') == 'True' else 'X11'}")
        print(f"  Display: {info.get('display', 'N/A')}")

        if info.get('is_void'):
            print("\n✓ System: Void Linux")
        if info.get('is_d77void'):
            print("✓ System: d77void")

        print("=" * 50 + "\n")

    def handle_history(self, args):
        """View or clear only the current conversation."""
        if getattr(args, 'history_action', None) == 'recover':
            if not args.yes:
                print('history recover --yes is required. The complete original file will be preserved privately.', file=sys.stderr)
                return 1
            print(str(self._store().recover(confirmed=True)))
            self.conversation_history = self._store().load_entries()
            self.offline.reset_conversation()
            return 0
        store = self._store()
        if args.clear:
            store.clear_session()
            self.conversation_history = []
            self._pending_exchanges = []
            self.offline.reset_conversation()
            print('Current conversation cleared')
            return 0
        messages = store.load_entries()
        messages = messages[-args.limit:] if args.limit > 0 else []
        for item in messages:
            print('[{}]\n{}\n'.format(item['role'], item['content']))
        return 0

    def _render_token_usage(self, title: str = "USAGE STATISTICS"):
        """Shared renderer for `stats` and `tokens usage`."""
        token_usage = self.ai_client.get_token_usage()

        print("\n" + "=" * 50)
        print(title)
        print("=" * 50 + "\n")

        if token_usage:
            print("Token usage by provider:\n")
            for provider, usage in token_usage.items():
                print(f"  {provider}:")
                print(f"    Input tokens:  {usage.get('input', 0):>10}")
                print(f"    Output tokens: {usage.get('output', 0):>10}")
                print(f"    Total tokens:  {usage.get('total', 0):>10}")
                print()
        else:
            print("  No statistics available\n")
        print("=" * 50 + "\n")

    def handle_stats(self, args):
        """Process the stats command."""
        self._render_token_usage()
        if args.reset:
            self.ai_client.reset_token_usage()
            print("✓ Statistics reset")

    def handle_benchmarks(self, args):
        """Process the benchmarks command."""
        from .benchmarks import format_suite_text, run_default_suite
        scale = args.scale if args.scale and args.scale > 0 else 1.0
        suite = run_default_suite(repeat_scale=scale)
        if args.json:
            print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(format_suite_text(suite))
        return 0 if suite.ok else 1

    def handle_serve(self, args):
        """Start the loopback-only local REST API and block until Ctrl+C."""
        from .local_api import (
            APIContext, LocalAPIServer, generate_token, is_loopback_host,
        )
        host = args.host or "127.0.0.1"
        if not is_loopback_host(host):
            print(_("serve: refusing to bind to a non-loopback address: %s") % host)
            return 1
        port = int(args.port) if args.port is not None else int(self.config.get("local_api.port", 7621))
        token = args.token or self.config.get("local_api.token", "") or generate_token()
        ctx = APIContext(
            token=token,
            config=self.config,
            ai_client=self.ai_client,
            history_store=self._store(),
            pomodoro=None,
        )
        server = LocalAPIServer(ctx, host=host, port=port)
        server.start_background()
        url = server.url
        print(_("serve: local API listening on %s") % url)
        print(_("serve: bearer token: %s") % token)
        print(_("serve: press Ctrl+C to stop"))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
        finally:
            server.shutdown()
        return 0

    def handle_theme(self, args):
        """Process the theme command (create, edit, list, validate, apply themes)."""
        from .theme_editor import (
            ThemeEditorError, apply_theme, default_theme,
            delete_theme, duplicate_theme, export_theme, format_contrast_text,
            format_theme_text, import_theme, is_user_theme, list_all_themes,
            load_theme_document, save_theme_document, set_color,
            set_syntax_color, set_ui_value, validate_theme_document,
        )
        action = args.theme_action

        if action == 'list':
            themes = list_all_themes()
            current = self.config.get("app.theme", "dark")
            for name in themes:
                marker = "*" if name == current else " "
                editable = "" if is_user_theme(name) else " (built-in)"
                print("{} {}{}".format(marker, name, editable))
            return 0

        if action == 'show':
            try:
                doc = load_theme_document(args.name)
            except ThemeEditorError as error:
                print(_("theme: %s") % str(error))
                return 1
            print(format_theme_text(doc))
            return 0

        if action == 'create':
            try:
                if args.source:
                    doc = duplicate_theme(args.source, args.name)
                    if args.description:
                        doc.description = args.description
                else:
                    doc = default_theme(args.name, args.description)
                save_theme_document(doc)
            except (ThemeEditorError, ValueError) as error:
                print(_("theme: %s") % str(error))
                return 1
            print(_("theme: created '%s'") % args.name)
            return 0

        if action == 'set':
            try:
                doc = load_theme_document(args.name)
                if not is_user_theme(args.name):
                    print(_("theme: '%s' is a built-in theme; create a copy first with: theme create <new-name> --from %s") % (args.name, args.name))
                    return 1
                key = args.key
                if key in ("background", "text", "accent", "secondary", "tertiary", "border"):
                    set_color(doc, key, args.value)
                elif key in ("font_family", "font_size", "border_radius", "opacity"):
                    set_ui_value(doc, key, args.value)
                else:
                    set_syntax_color(doc, key, args.value)
                save_theme_document(doc)
            except (ThemeEditorError, ValueError) as error:
                print(_("theme: %s") % str(error))
                return 1
            print(_("theme: %s.%s = %s") % (args.name, args.key, args.value))
            return 0

        if action == 'delete':
            try:
                result = delete_theme(args.name)
            except ValueError as error:
                print(_("theme: %s") % str(error))
                return 1
            if result is None:
                print(_("theme: '%s' not found") % args.name)
                return 1
            print(_("theme: deleted '%s'") % args.name)
            return 0

        if action == 'contrast':
            try:
                doc = load_theme_document(args.name)
            except ThemeEditorError as error:
                print(_("theme: %s") % str(error))
                return 1
            print(format_contrast_text(doc))
            return 0

        if action == 'apply':
            try:
                doc = load_theme_document(args.name)
                apply_theme(doc, self.config)
            except (ThemeEditorError, ValueError) as error:
                print(_("theme: %s") % str(error))
                return 1
            print(_("theme: applied '%s'") % args.name)
            return 0

        if action == 'export':
            try:
                doc = load_theme_document(args.name)
                export_theme(doc, args.path)
            except (ThemeEditorError, ValueError) as error:
                print(_("theme: %s") % str(error))
                return 1
            print(_("theme: exported '%s' to %s") % (args.name, args.path))
            return 0

        if action == 'import':
            try:
                doc = import_theme(args.path)
                save_theme_document(doc)
            except (ThemeEditorError, ValueError) as error:
                print(_("theme: %s") % str(error))
                return 1
            print(_("theme: imported '%s'") % doc.name)
            return 0

        if action == 'validate':
            try:
                doc = load_theme_document(args.name)
            except ThemeEditorError as error:
                print(_("theme: %s") % str(error))
                return 1
            warnings = validate_theme_document(doc)
            print(format_theme_text(doc))
            print()
            print(format_contrast_text(doc))
            print()
            if warnings:
                print(_("theme: warnings:"))
                for warning in warnings:
                    print("  - " + warning)
                return 1
            print(_("theme: '%s' is valid") % args.name)
            return 0

        print(_("theme: unknown action '%s'") % action)
        return 1

    def handle_shortcuts(self, args):
        """Process the shortcuts command (list, show, set, reset, validate)."""
        from .keybindings import (
            DEFAULT_BINDINGS, KeybindingError, KeybindingSet,
            describe_binding, format_binding_table,
            load_from_config, save_to_config, validate_binding_set,
        )
        action = args.shortcuts_action

        if action == 'list':
            kbs = load_from_config(self.config)
            print(format_binding_table(kbs))
            return 0

        if action == 'show':
            kbs = load_from_config(self.config)
            accel = kbs.get(args.action)
            if accel is None:
                print(_("shortcuts: no binding for '%s'") % args.action)
                return 1
            print(describe_binding(args.action, accel))
            return 0

        if action == 'set':
            try:
                kbs = load_from_config(self.config)
                kbs.set(args.action, args.accelerator)
                warnings = validate_binding_set(kbs)
                save_to_config(kbs, self.config)
            except KeybindingError as error:
                print(_("shortcuts: %s") % str(error))
                return 1
            print(_("shortcuts: %s") % describe_binding(args.action, args.accelerator))
            for warning in warnings:
                print("  " + _("shortcuts: warning: %s") % warning)
            return 0

        if action == 'reset':
            kbs = load_from_config(self.config)
            if args.action:
                if not kbs.remove(args.action):
                    # Not in the merged set; check defaults.
                    if args.action in DEFAULT_BINDINGS:
                        # Removing from merged == disabling; re-add as default.
                        kbs.set(args.action, DEFAULT_BINDINGS[args.action])
                    else:
                        print(_("shortcuts: unknown action '%s'") % args.action)
                        return 1
                else:
                    # If it was a default, re-add it; if custom, it's gone.
                    if args.action in DEFAULT_BINDINGS:
                        kbs.set(args.action, DEFAULT_BINDINGS[args.action])
            else:
                kbs = KeybindingSet.defaults()
            save_to_config(kbs, self.config)
            if args.action:
                accel = kbs.get(args.action)
                print(_("shortcuts: reset '%s' to %s") % (args.action, accel or "(disabled)"))
            else:
                print(_("shortcuts: all bindings reset to defaults"))
            return 0

        if action == 'validate':
            kbs = load_from_config(self.config)
            warnings = validate_binding_set(kbs)
            if warnings:
                print(_("shortcuts: warnings:"))
                for warning in warnings:
                    print("  - " + warning)
                return 1
            print(_("shortcuts: all %d bindings are valid") % len(kbs.bindings))
            return 0

        print(_("shortcuts: unknown action '%s'") % action)
        return 1

    def handle_voice(self, args):
        """Process the voice command (status, backends, config, transcribe)."""
        from .voice_input import (
            BACKEND_LABELS, STT_BACKENDS, VoiceInputError,
            file_capture, format_backend_status, list_backend_status,
            load_voice_config, transcribe_audio,
        )
        action = args.voice_action

        if action == 'status':
            vc = load_voice_config(self.config)
            print(format_backend_status(list_backend_status(vc)))
            return 0

        if action == 'backends':
            for name in STT_BACKENDS:
                print("{:<14s} {}".format(name, BACKEND_LABELS.get(name, name)))
            return 0

        if action == 'config':
            vc = load_voice_config(self.config)
            if not args.key:
                data = vc.to_dict()
                for key in sorted(data):
                    print("{:<24s} {}".format(key, data[key]))
                return 0
            if not args.value:
                # Show one key.
                data = vc.to_dict()
                if args.key in data:
                    print("{}: {}".format(args.key, data[args.key]))
                    return 0
                print(_("voice: unknown config key '%s'") % args.key)
                return 1
            # Set a key.
            return self._voice_set_config_key(vc, args.key, args.value)

        if action == 'transcribe':
            vc = load_voice_config(self.config)
            if args.language:
                vc.language = args.language
            try:
                audio = file_capture(
                    str(args.path), vc.sample_rate,
                    vc.max_duration_seconds, 25 * 1024 * 1024,
                )
                text = transcribe_audio(audio, vc)
            except VoiceInputError as error:
                print(_("voice: %s") % str(error))
                return 1
            print(text)
            return 0

        print(_("voice: unknown action '%s'") % action)
        return 1

    def _voice_set_config_key(self, vc, key: str, value: str):
        """Set one voice config key and persist it."""
        valid_keys = {
            "enabled": bool, "backend": str, "language": str,
            "sample_rate": int, "max_duration_seconds": int,
            "vosk_model_path": str, "whisper_model": str, "whisper_device": str,
            "api_base_url": str, "api_model": str, "api_timeout": int,
            "system_command": str, "system_timeout": int,
        }
        if key not in valid_keys:
            print(_("voice: unknown config key '%s'") % key)
            return 1
        caster = valid_keys[key]
        try:
            if caster is bool:
                typed_value = value.lower() in ("1", "true", "yes", "on")
            elif caster is int:
                typed_value = int(value)
            else:
                typed_value = value
        except ValueError:
            print(_("voice: invalid value for %s: %s") % (key, value))
            return 1
        current = self.config.get("voice", {})
        if not isinstance(current, dict):
            current = {}
        current[key] = typed_value
        self.config.set("voice", current)
        self.config.flush()
        print(_("voice: %s = %s") % (key, typed_value))
        return 0

    def handle_pair(self, args):
        """Process the pair command (extract, review, diff, scan, config)."""
        import json as _json
        from .pair_programming import (
            analyze_diff, extract_code_blocks,
            find_risks, format_review_text, load_pair_config, review_change,
        )
        action = args.pair_action

        if action == 'extract':
            try:
                text = args.path.read_text(encoding="utf-8")
            except OSError as error:
                print(_("pair: cannot read file: %s") % str(error))
                return 1
            blocks = extract_code_blocks(text)
            _ = _("pair: %s")  # keep the translation key referenced
            if args.json:
                print(_json.dumps([
                    {"language": b.language, "file_path": b.file_path,
                     "line_count": b.line_count, "start_line": b.start_line,
                     "content": b.content}
                    for b in blocks
                ], ensure_ascii=False, indent=2))
            else:
                if not blocks:
                    print(_("pair: no code blocks found"))
                    return 0
                for i, block in enumerate(blocks, 1):
                    header = "Block {}".format(i)
                    if block.language:
                        header += " [{}]".format(block.language)
                    if block.file_path:
                        header += " ({})".format(block.file_path)
                    header += " - {} lines".format(block.line_count)
                    print(header)
                    print("-" * min(len(header), 60))
                    # Print first 10 lines as preview.
                    lines = block.content.splitlines()
                    for line in lines[:10]:
                        print("  " + line)
                    if len(lines) > 10:
                        print("  ... ({} more lines)".format(len(lines) - 10))
                    print()
            return 0

        if action == 'review':
            try:
                old_text = args.old.read_text(encoding="utf-8")
                new_text = args.new.read_text(encoding="utf-8")
            except OSError as error:
                print(_("pair: cannot read file: %s") % str(error))
                return 1
            pair_config = load_pair_config(self.config)
            result = review_change(old_text, new_text, pair_config)
            _, diff_text = analyze_diff(old_text, new_text)
            if args.json:
                print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(format_review_text(result, diff_text))
            return 0 if result.passed else 1

        if action == 'diff':
            try:
                old_text = args.old.read_text(encoding="utf-8")
                new_text = args.new.read_text(encoding="utf-8")
            except OSError as error:
                print(_("pair: cannot read file: %s") % str(error))
                return 1
            _stats, diff_text = analyze_diff(old_text, new_text)
            print(diff_text if diff_text else _("pair: no differences"))
            return 0

        if action == 'scan':
            try:
                code = args.path.read_text(encoding="utf-8")
            except OSError as error:
                print(_("pair: cannot read file: %s") % str(error))
                return 1
            findings = find_risks(code)
            if args.json:
                print(_json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2))
            else:
                if not findings:
                    print(_("pair: no risks found"))
                    return 0
                for f in findings:
                    print("[{}] line {}: {}".format(
                        f.severity.upper(), f.line_number, f.description))
            return 1 if any(f.severity == "high" for f in findings) else 0

        if action == 'config':
            pc = load_pair_config(self.config)
            data = pc.to_dict()
            if not args.key:
                for key in sorted(data):
                    print("{:<24s} {}".format(key, data[key]))
                return 0
            if args.key in data:
                print("{}: {}".format(args.key, data[args.key]))
                return 0
            print(_("pair: unknown config key '%s'") % args.key)
            return 1

        print(_("pair: unknown action '%s'") % action)
        return 1

    def handle_config(self, args):
        """Process the config command."""
        if not args.config_command:
            # List configuration
            config = self.config.config
            print("Stored configuration (environment overrides and Secret Service keys are not shown):")
            self._print_config(config)
            return 0

        if args.config_command == 'list':
            config = self.config.config
            print("Stored configuration (environment overrides and Secret Service keys are not shown):")
            self._print_config(config)
            return 0
        elif args.config_command == 'set':
            if not args.key or not args.value:
                print("✗ You must specify both key and value")
                return 1

            # Convert the value
            value = args.value
            if value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            else:
                # int() also accepts negative numbers (isdigit() did not)
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass

            self.config.set(args.key, value)
            self.config.flush()
            # Never return an API key in cleartext to the terminal/shell history
            if "api_key" in args.key.lower():
                shown = "*" * 12
            else:
                shown = value
            print(f"✓ Configuration updated: {args.key} = {shown}")
            return 0
        elif args.config_command == 'get':
            if not args.key:
                print("✗ You must specify a key", file=sys.stderr)
                return 1
            # A missing path and a real null value are different. Check the
            # stored structure before resolving environment/key-store values.
            stored = self.config.config
            for component in args.key.split('.'):
                if not isinstance(stored, dict) or component not in stored:
                    print(f"✗ Unknown configuration key: {args.key}", file=sys.stderr)
                    return 1
                stored = stored[component]
            components = args.key.split('.')
            if (len(components) == 4 and components[:2] == ['api', 'providers']
                    and components[3] == 'api_key'):
                value = self.config.get_api_key(components[2])
            else:
                value = self.config.get(args.key)
            # A section can contain API keys too. Redact before printing,
            # including effective credentials resolved from a vault or env.
            value = self._masked_config_value(value, args.key)
            print(f"{args.key}: {value}")
            return 0

    @classmethod
    def _masked_config_value(cls, value, key=''):
        if 'api_key' in key.lower() and value:
            return '*' * 12
        if isinstance(value, dict):
            return {name: cls._masked_config_value(item, str(name)) for name, item in value.items()}
        if isinstance(value, list):
            return [cls._masked_config_value(item) for item in value]
        return value

    def _print_config(self, config: Dict, indent: int = 0):
        """Show configuration in an organized way."""
        for key, value in config.items():
            if isinstance(value, dict):
                print("  " * indent + f"{key}:")
                self._print_config(value, indent + 1)
            else:
                # Hide API keys (fixed size: does not reveal the length)
                if 'api_key' in key.lower() and value:
                    value = "********"
                else:
                    value = self._masked_config_value(value)
                print("  " * indent + f"{key}: {value}")

    def handle_providers(self, args):
        """Process the providers command."""
        providers = self.ai_client.get_supported_providers()

        print("\n" + "=" * 50)
        print("AVAILABLE AI PROVIDERS")
        print("=" * 50 + "\n")

        # Get configuration
        api_config = self.config.get("api.providers", {})
        if not isinstance(api_config, dict):
            api_config = {}
        default_provider = self.config.get("api.default_provider", "openrouter")

        for provider in providers:
            config = api_config.get(provider, {})
            if not isinstance(config, dict):
                config = {}
            # Remote chat reads the provider map; only local settings resolve
            # a model override at the individual leaf as well.
            model = (self.config.get(f"api.providers.{provider}.model", config.get("model", ""))
                     if provider == 'local_llm' else config.get("model", "N/A"))
            is_default = provider == default_provider

            marker = "✓" if is_default else " "
            key_status, source = self._provider_key_status(provider)

            print(f"{marker} {provider:<20} Model: {model:<30} Key: {key_status} ({source})")

        print("\nKey reports the effective credential; no API request or key-store unlock was performed.")
        print("A present key is not proof that the provider accepts it. The leading ✓ marks the default provider.")
        print("\n" + "=" * 50 + "\n")
        return 0

    def _provider_key_status(self, provider):
        """Resolve credential presence without disclosing it or unlocking a vault."""
        if provider == 'local_llm':
            return 'N/A', 'not required'
        override = self.config.get_api_key_env_override(provider)
        if override is not None:
            present = bool(self.config.get_api_key(provider))
            source = f"environment: {override}"
            if not present:
                source += '; empty override blocks stored key'
            return ('✓' if present else '✗'), source
        from .credential_store import CredentialStoreError
        try:
            storage = self.config.get_api_key_storage(provider)
        except CredentialStoreError:
            return '✗', 'invalid credential storage'
        present = bool(self.config.get_api_key(provider))
        source = 'Secret Service' if storage == 'secret-service' else 'config'
        if not present:
            source += '; not set or unavailable'
        return ('✓' if present else '✗'), source

    def handle_tokens(self, args):
        """Process the tokens command."""
        if not args.tokens_command or args.tokens_command == 'usage':
            self._render_token_usage("TOKEN USAGE")
        elif args.tokens_command == 'reset':
            self.ai_client.reset_token_usage()
            print("✓ Token count reset")

    def _get_context_message(self, query='') -> Optional[Dict[str, str]]:
        return build_system_message(self.expert_mode, self.offline.distro, query, get_language(), getattr(self, 'system_context', None))

    def _save_history(self):
        store = self._store()
        for item in self._pending_exchanges:
            store.append(item['role'], item['content'], item['timestamp'])
        if not store.flush():
            raise OSError('Could not save conversation: {}'.format(store.last_error))
        store.set_diagnostic_state(self.offline.diagnostic_state())
        self._pending_exchanges = []



def main():
    """Main entry point for the CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    # File logging só no arranque real (não no import do pacote)
    from . import setup_file_logging
    setup_file_logging()
    app = None
    try:
        args = CLIApp.parse_args()
        # Recovery must remain available when normal initialization cannot read
        # a large, damaged or newer-format history. Do not load configuration,
        # instantiate a provider or interpret the unknown document first.
        if args.command == 'history' and args.history_action == 'recover':
            if not args.yes:
                print('history recover --yes is required. The complete original file will be preserved privately.', file=sys.stderr)
                sys.exit(1)
            backup = HistoryStore.recover_file(CLIApp._history_file(), confirmed=True)
            print(str(backup))
            sys.exit(0)
        app = CLIApp()
        code = app.run(args)
        sys.exit(code if isinstance(code, int) else 0)
    except KeyboardInterrupt:
        print("\n\n✓ Exiting...")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        if getattr(e, 'recovery_backup', None) is not None:
            print('Original history preserved at: {}'.format(e.recovery_backup))
        sys.exit(1)
    finally:
        if app is not None and getattr(app, 'actions', None) is not None:
            app.actions.close()


if __name__ == "__main__":
    main()
