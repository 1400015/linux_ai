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
from .ai_client import AIClient, AIProviderError
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

logger = logging.getLogger(__name__)


class _ActionResult(str):
    """Keep action text compatible with callers while carrying its outcome.

    No action (including a non-interactive proposal) and user cancellation
    are not execution failures. Only an attempted operation that fails gives
    the chat command a nonzero exit code.
    """

    def __new__(cls, text="", status="none"):
        result = super().__new__(cls, text)
        result.status = status
        return result

    @property
    def exit_code(self):
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
        self.actions = ConversationActions(self._store(), self.offline.distro.pkg_manager, context=self.system_context)

    def parse_args(self):
        """Parse the command-line arguments."""
        parser = argparse.ArgumentParser(
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
        imported = actions.add_parser('import', help='Import a versioned JSON conversation')
        imported.add_argument('path', type=Path)
        mode = subparsers.add_parser('mode', help='Show or select assistance mode')
        mode.add_argument('value', nargs='?', choices=('auto', 'offline', 'local', 'remote'))
        mode.add_argument('--check', action='store_true', help='Test the local model connection')
        subparsers.add_parser('local-models', help='List models installed on the configured local server')
        knowledge = subparsers.add_parser('knowledge', help='Search bundled guides without any AI service')
        knowledge.add_argument('query', nargs='+')
        subparsers.add_parser('knowledge-verify', help='Validate bundled YAML schemas and references offline')
        audit_parser = subparsers.add_parser('actions', help='Review operations in the current conversation')
        audit_actions = audit_parser.add_subparsers(dest='audit_action', required=True)
        audit_actions.add_parser('list')
        audit_actions.add_parser('show').add_argument('id')

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
        change_actions.add_parser('list')
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
        history_parser.add_argument('--limit', '-n', type=int, default=20,
                                     help='Number of messages to show')
        history_parser.add_argument('--clear', action='store_true',
                                     help='Clear history')

        # Command: stats
        stats_parser = subparsers.add_parser('stats', help='Usage statistics')
        stats_parser.add_argument('--reset', action='store_true',
                                   help='Reset statistics')

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

        return parser.parse_args()

    def run(self):
        try:
            return self._run_command()
        finally:
            if self.history_store is not None:
                self.history_store.close()
            self.config.flush()
            self.ai_client.flush_usage()

    def _run_command(self):
        """Run the CLI application.

        Devolve o exit code do comando (0 = sucesso): scripts que encadeiam
        o CLI (`capture --ocr && ...`) precisam de detetar falhas — antes,
        TODAS as falhas esperadas saíam com 0.
        """
        args = self.parse_args()

        if not args.command:
            self.print_help()
            return 0

        if args.command == 'sessions':
            return self.handle_sessions(args)
        if args.command == 'diagnose':
            return self.handle_diagnose(args)
        if args.command == 'changes':
            return self.handle_changes(args)
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
  mode [value]              - Show/select assistance mode
  local-models              - List installed models on the local server
  stats [options]           - Usage statistics
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

    def handle_changes(self, args):
        journal = ChangeJournal()
        def allowed():
            return self.config.get('permissions.allowed_edit_dirs', [])
        if args.change_action == 'list':
            print(json.dumps(journal.list_changes(), ensure_ascii=False, indent=2))
            return 0
        if args.change_action == 'show':
            record = next((item for item in journal.list_changes() if item['id'] == args.id), None)
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
            print(store.import_session(read_conversation(args.path))['id'])
        return 0

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

    def _run_offline(self, message: str) -> str:
        """Answer from local knowledge and, on a terminal, offer to run the action."""
        reply = self.offline.handle(message, get_language())
        print(f"\n[OFFLINE]\n{reply.text}\n")
        extra = self._offer_action(reply)
        if extra:
            return _ActionResult(reply.text + "\n\n" + extra, extra.status)
        return _ActionResult(reply.text, extra.status)

    def _offer_model_action(self, message: str) -> str:
        """Offer a catalog action for the user's sentence, not for model text."""
        propose = getattr(self.offline, "propose", None)
        if not callable(propose):
            return _ActionResult()
        try:
            reply = propose(message, get_language())
        except Exception as exc:
            logger.error("Could not prepare a confirmed action: %s", exc, exc_info=True)
            return _ActionResult()
        interaction, commands = split_offer(reply)
        if not interaction and not commands:
            return _ActionResult()
        if getattr(reply, "text", ""):
            print(f"\n{reply.text}\n")
        return self._offer_action(reply)

    def _offer_action(self, reply) -> str:
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

    def _confirm_and_run(self, commands) -> str:
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

    def _choose_wifi(self) -> str:
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

    def _choose_printer(self, commands) -> str:
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

    def _choose_scanner(self, commands) -> str:
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

    def handle_chat(self, args):
        """Process the chat command. Devolve exit code (0 sucesso, 1 falha)."""
        self._prepare_conversation(args)
        message = self._message_from_args(args)

        if args.expert:
            self.expert_mode = True

        action_code = self._run_conversational_action(message, args)
        if action_code is not None:
            return action_code

        full_history = self._build_request_messages(message)

        # Get the response (or answer offline when the provider is unusable)
        answered_offline = False
        action_result = _ActionResult()
        if not self.ai_client.provider_ready(args.provider):
            response_text = self._run_offline(message)
            action_result = response_text
            answered_offline = True
        elif args.stream:
            print("\n[AI] ", end="", flush=True)
            response_text = ""
            try:
                for chunk in self.ai_client.stream_chat(
                    full_history,
                    provider=args.provider,
                    model=args.model
                ):
                    print(chunk, end="", flush=True)
                    response_text += chunk
                print("\n")
            except AIProviderError as e:
                # Falha estruturada (rede/HTTP/config): não é conteúdo do
                # modelo — não persistir como resposta, cair para offline.
                print(f"\n[!] {e}\n")
                response_text = self._run_offline(message)
                action_result = response_text
                answered_offline = True
        else:
            response_text = self.ai_client.chat(
                full_history,
                provider=args.provider,
                model=args.model
            )
            if response_text:
                print(f"\n[AI]\n{response_text}\n")
            else:
                response_text = self._run_offline(message)
                action_result = response_text
                answered_offline = True

        if response_text and not answered_offline:
            extra = self._offer_model_action(message)
            action_result = extra
            if extra:
                response_text = response_text + "\n\n" + extra

        # Save to history
        if not args.no_history:
            self._record_exchange(message, response_text)
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

    def handle_config(self, args):
        """Process the config command."""
        if not args.config_command:
            # List configuration
            config = self.config.config
            self._print_config(config)
            return

        if args.config_command == 'list':
            config = self.config.config
            self._print_config(config)
        elif args.config_command == 'set':
            if not args.key or not args.value:
                print("✗ You must specify both key and value")
                return

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
        elif args.config_command == 'get':
            if not args.key:
                print("✗ You must specify a key")
                return
            value = self.config.get(args.key)
            # get() decrypts api_key values - never print a live secret
            if "api_key" in args.key.lower() and value:
                value = "*" * 12
            print(f"{args.key}: {value}")

    def _print_config(self, config: Dict, indent: int = 0):
        """Show configuration in an organized way."""
        for key, value in config.items():
            if isinstance(value, dict):
                print("  " * indent + f"{key}:")
                self._print_config(value, indent + 1)
            else:
                # Hide API keys (fixed size: does not reveal the length)
                if 'api_key' in key and value:
                    value = "********"
                print("  " * indent + f"{key}: {value}")

    def handle_providers(self, args):
        """Process the providers command."""
        providers = self.ai_client.get_supported_providers()

        print("\n" + "=" * 50)
        print("AVAILABLE AI PROVIDERS")
        print("=" * 50 + "\n")

        # Get configuration
        api_config = self.config.get("api.providers", {})
        default_provider = self.config.get("api.default_provider", "openrouter")

        for provider in providers:
            config = api_config.get(provider, {})
            model = config.get("model", "N/A")
            has_key = bool(config.get("api_key"))
            is_default = provider == default_provider

            marker = "✓" if is_default else " "
            key_status = "✓" if has_key else "✗"

            print(f"{marker} {provider:<20} Model: {model:<30} Key: {key_status}")

        print("\n" + "=" * 50 + "\n")

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
        app = CLIApp()
        code = app.run()
        sys.exit(code if isinstance(code, int) else 0)
    except KeyboardInterrupt:
        print("\n\n✓ Exiting...")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)
    finally:
        if app is not None and getattr(app, 'actions', None) is not None:
            app.actions.close()


if __name__ == "__main__":
    main()
