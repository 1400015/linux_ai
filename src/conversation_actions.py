"""Local conversational tasks with session choices and scoped user authorization."""

from dataclasses import dataclass
import re
import threading
import time
from typing import Optional

from .action_adapters import DisplayAdapter, PackageAdapter
from .action_audit import ActionAudit
from .action_contract import ActionExecutor, ActionResult

from .display_actions import DisplayMode, DisplayService
from .local_knowledge import normalize
from .offline_assistant import _HYPOTHETICAL_RE, _is_refusal
from .package_actions import PackageCandidate, PackageService, SUPPORTED_MANAGERS
from .task_state import MAX_TASK_OPTIONS, TASK_TTL_SECONDS
from .inspection_actions import ChecksumAdapter, StorageAdapter
from .service_actions import ServiceAdapter, ServiceService


@dataclass(frozen=True)
class ActionSpec:
    identifier: str
    changes_system: bool


ACTION_CATALOG = {
    spec.identifier: spec for spec in (
        ActionSpec("packages.search", False), ActionSpec("packages.install", True),
        ActionSpec("display.list_modes", False), ActionSpec("display.apply_mode", True),
        ActionSpec("services.list", False), ActionSpec("services.status", False),
        ActionSpec("services.control", True), ActionSpec("files.checksum", False), ActionSpec("storage.list", False),
    )
}


@dataclass
class ActionReply:
    text: str
    status: str = "done"
    operation: str = ""
    change: Optional[object] = None
    executed: bool = False
    operation_id: str = ''

    @property
    def exit_code(self):
        return 1 if self.status == "failed" else 0


def _say(lang, portuguese, english):
    return portuguese if lang == "pt" else english


_CANCEL = {"cancel", "cancelar", "cancela", "cancel task", "cancelar tarefa"}
_KEEP = {"manter", "mantem", "mantem esta", "keep", "keep it", "confirmar", "confirm"}
_REVERT = {"reverter", "reverte", "voltar", "revert", "undo"}
_CHANGE = re.compile(r"^(?:aplica|aplicar|coloca|colocar|poe|mete|usa|usar|muda|mudar|altera|alterar|set|apply|use|change)\b")
_INSTALL = re.compile(r"^(?:instala|instalar|install)\b")
_SELECT_PREFIX = re.compile(
    r"^(?:(?:instala|instalar|install|aplica|aplicar|coloca|colocar|poe|mete|usa|usar|set|apply|use)\s+)?"
    r"(?:(?:em|para|to|a|o|the)\s+)?"
)
_ORDINALS = {"primeira": 0, "primeiro": 0, "first": 0, "segunda": 1, "segundo": 1,
             "second": 1, "terceira": 2, "terceiro": 2, "third": 2, "quarta": 3, "fourth": 3}
_RESOLUTION = re.compile(
    r"(?<!\d)(\d{2,5})\s*(?:x|×|por|by)\s*(\d{2,5})(?!\d)"
    r"(?:\s*(?:@|a|at)\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:hz)?)?"
)


class ConversationActions:
    """Each new operation originates in the user's sentence, never model text.

    Stored state contains discovered data only. Installations and mode changes
    are revalidated by their backend immediately before execution.
    """

    def __init__(self, store, manager, packages=None, displays=None, clock=time.time, *, context=None, audit=None, services=None):
        self.store = store
        self.manager = manager
        self.packages = packages if packages is not None else PackageService(manager)
        self.displays = displays if displays is not None else DisplayService()
        self.clock = clock
        from .offline_assistant import DistroInfo
        from .system_context import detect_system_context
        self.context = context or detect_system_context(DistroInfo(pkg_manager=manager))
        self.audit = audit or ActionAudit(store.path.with_name('actions.json'))
        self.executor = ActionExecutor(self.audit)
        for adapter in (PackageAdapter(self.packages), PackageAdapter(self.packages, True),
                        DisplayAdapter(self.displays), DisplayAdapter(self.displays, True)):
            self.executor.register(adapter)
        self.services = services or ServiceService()
        for operation in ('list', 'status', 'control'):
            self.executor.register(ServiceAdapter(self.services, operation))
        self.executor.register(ChecksumAdapter())
        self.executor.register(StorageAdapter())
        self._service_confirmations = {}
        self._changes = {}
        self._lock = threading.Lock()
        self._mutation_lock = threading.Lock()
        self._closed = False

    def _invoke(self, identifier, parameters, session_id, is_current, confirmed_resource=None):
        try:
            prepared = self.executor.prepare(identifier, parameters, session_id)
            grant = self.executor.authorize(prepared, session_id, explicit_request=True,
                confirmed_resource=confirmed_resource) if prepared.definition.changes_system else None
            return self.executor.execute(prepared, grant=grant, is_current=is_current)
        except (OSError, ValueError):
            return ActionResult('failed', 'Não foi possível validar ou registar a ação. / Cannot validate or record the action.')

    def _extended_request(self, message, text, session_id, lang, can_execute, is_current):
        if text in {'lista servicos', 'lista os servicos', 'mostra servicos', 'list services', 'show services'}:
            result = self._invoke('services.list', {}, session_id, is_current)
            names = result.data or []
            detail = '\n'.join(names[:80]) + ('\n… usa o nome exato do serviço / use the exact service name' if len(names) > 80 else '')
            return ActionReply(result.text or detail, 'done' if result.ok else 'failed', 'services.list', operation_id=result.operation_id)
        if text in {'lista discos', 'lista dispositivos de armazenamento', 'list disks', 'list storage'}:
            result = self._invoke('storage.list', {}, session_id, is_current)
            return ActionReply(result.text, 'done' if result.ok else 'failed', 'storage.list', operation_id=result.operation_id)
        checksum = re.fullmatch(r'(?:checksum|sha256|calcula checksum|calculate checksum)\s+(.+)', message.strip(), re.IGNORECASE)
        if checksum:
            result = self._invoke('files.checksum', {'path': checksum[1].strip()}, session_id, is_current)
            return ActionReply(result.text, 'done' if result.ok else 'failed', 'files.checksum', operation_id=result.operation_id)
        if re.match(r'^(?:grava|gravar|write|flash)\s+(?:a\s+|an?\s+)?(?:iso|imagem|image)\b', text):
            return ActionReply(_say(lang, 'A gravação de ISOs ainda não está disponível. A consulta «lista discos» é suportada; a escrita exige validação adicional e ensaios em VM.',
                'ISO writing is not available yet. "list disks" is supported; writing requires additional validation and VM trials.'), 'proposal')
        confirmation = re.fullmatch(r'(?:confirma|confirm)\s+([a-z]+)\s+([a-z0-9_.@:-]+)', text)
        if confirmation and session_id in self._service_confirmations:
            prepared, phrase, created = self._service_confirmations[session_id]
            if text != phrase or not 0 <= self.clock() - created <= TASK_TTL_SECONDS:
                if not 0 <= self.clock() - created <= TASK_TTL_SECONDS:
                    self._service_confirmations.pop(session_id, None)
                    self.executor.discard(prepared)
                return ActionReply(_say(lang, 'A confirmação não corresponde ao alvo ou expirou. Consulta novamente o serviço.',
                    'Confirmation does not match the target or has expired. Inspect the service again.'), 'needs_choice')
            if not can_execute:
                return ActionReply(_say(lang, 'Usa uma conversa ou terminal interativo para executar.', 'Use an interactive chat or terminal to execute.'), 'proposal')
            if not self._mutation_lock.acquire(blocking=False):
                return ActionReply(_say(lang, 'Há outra alteração em curso.', 'Another change is running.'), 'needs_choice')
            try:
                self._service_confirmations.pop(session_id, None)
                grant = self.executor.authorize(prepared, session_id, explicit_request=True, confirmed_resource=prepared.resource)
                result = self.executor.execute(prepared, grant=grant, is_current=is_current)
            except (OSError, ValueError):
                return ActionReply('Não foi possível autorizar a ação. / Cannot authorize this action.', 'failed')
            finally:
                self._mutation_lock.release()
            return ActionReply(result.text, 'done' if result.ok else 'failed', 'services.control', executed=True, operation_id=result.operation_id)
        request = re.fullmatch(r'(estado|status|inicia|iniciar|start|para|parar|stop|reinicia|reiniciar|restart|ativa|ativar|enable|desativa|desativar|disable)\s+(?:(?:do|o|the)\s+)?(?:servico|service)\s+([a-z0-9_.@:-]+)', text)
        if request is None:
            return None
        verb, name = request.groups()
        result = self._invoke('services.status', {'name': name}, session_id, is_current)
        if not result.ok or result.data is None:
            return ActionReply(result.text, 'failed', 'services.status')
        if verb in {'estado', 'status'}:
            return ActionReply(result.text, operation='services.status', operation_id=result.operation_id)
        verb = {'inicia': 'start', 'iniciar': 'start', 'para': 'stop', 'parar': 'stop', 'reinicia': 'restart',
                'reiniciar': 'restart', 'ativa': 'enable', 'ativar': 'enable', 'desativa': 'disable', 'desativar': 'disable'}.get(verb, verb)
        try:
            prepared = self.executor.prepare('services.control', {'candidate': result.data.to_dict(), 'verb': verb}, session_id)
        except (OSError, ValueError):
            return ActionReply('Não foi possível preparar esta ação. / Cannot prepare this action.', 'failed')
        confirmation_verb = {'start': 'iniciar', 'stop': 'parar', 'restart': 'reiniciar', 'enable': 'ativar', 'disable': 'desativar'}.get(verb, verb) if lang == 'pt' else verb
        phrase = ('confirma' if lang == 'pt' else 'confirm') + ' ' + confirmation_verb + ' ' + result.data.name.lower()
        previous = self._service_confirmations.pop(session_id, None)
        if previous:
            self.executor.discard(previous[0])
        self._service_confirmations[session_id] = (prepared, phrase, self.clock())
        effects = _say(lang, 'No runit, ativar/desativar supervisão também pode iniciar/parar o serviço agora.',
            'With runit, enabling/disabling supervision can also start/stop the service now.') if result.data.manager == 'runit' else _say(lang,
            'Ativar/desativar altera o arranque; iniciar/parar/reiniciar altera a execução atual.',
            'enable/disable change boot activation; start/stop/restart change the current running state.')
        return ActionReply(result.text + '\n' + effects + '\n' + _say(lang, 'Esta ação pode interromper processos ou a ligação. Para executar, responde «{}».',
            'This action can interrupt processes or your connection. To execute, reply "{}".').format(phrase),
            'needs_choice', 'services.control', operation_id=prepared.operation_id)

    def _save(self, session_id, kind, operation, query, options, persist):
        state = {"version": 1, "kind": kind, "operation": operation,
                 "created_at": self.clock(), "query": query,
                 "options": [item.to_dict() for item in options[:MAX_TASK_OPTIONS]]}
        self.store.set_task_state(state if persist else None, session_id)

    def _clear(self, session_id):
        self.store.set_task_state(None, session_id)

    @staticmethod
    def _selector(text, options):
        body = _SELECT_PREFIX.sub("", text).strip()
        body = re.sub(r"^(?:opcao|option)\s+", "", body)
        if body in _ORDINALS:
            return _ORDINALS[body]
        if re.fullmatch(r"\d{1,2}", body):
            return int(body) - 1
        if body in {"essa", "esse", "esta", "este", "isso", "this", "that", "it"} and len(options) == 1:
            return 0
        matches = [index for index, item in enumerate(options)
                   if item.get("name", "").casefold() == body.casefold() and body]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _package_request(text):
        match = re.fullmatch(
            r"(?:(?:configura|configurar|configure)\s+(?:e|and)\s+)?"
            r"(instala|instalar|install|procura|procurar|pesquisa|pesquisar|search|find)\s+"
            r"(?:(?:e|and)\s+(instala|instalar|install)\s+)?"
            r"(?:(?:por|for)\s+)?(?:(?:o|a|um|uma|the|an)\s+)?"
            r"(?:(?:programa|aplicacao|software|pacote|program|application|package|app)\s+)?(.+)", text,
        )
        if not match:
            return None
        query = re.sub(r"\s+(?:por favor|please)$", "", match[3]).strip()
        operation = "install" if match[1] in {"instala", "instalar", "install"} or match[2] else "inspect"
        return operation, query

    @staticmethod
    def _display_request(text):
        words = re.search(r"\b(?:monitores?|monitors?|ecra|ecras|screen|screens|display|displays|resolucoes|resolucao|resolutions?|resolution)\b", text)
        query = re.match(r"^(?:lista|listar|mostra|mostrar|indica|indicar|quais|list|show|what|which)\b", text)
        change = _CHANGE.search(text) or re.match(r"^(?:configura|configurar|configure)\b", text)
        if (words and (query or change)) or (change and _RESOLUTION.search(text)):
            return "set_mode" if change else "inspect"
        return None

    @staticmethod
    def _is_resolution_selection(text):
        if _CHANGE.search(text) or re.match(r'^(?:configura|configurar|configure)\b', text):
            return True
        body = re.sub(r'^(?:resolucao|resolution)\s+', '', text)
        return re.fullmatch(_RESOLUTION.pattern + r'(?:\s+(?:no monitor|on monitor|output)\s+[a-z0-9_.:-]+)?', body) is not None

    @staticmethod
    def _resolution_indices(text, options):
        found = list(_RESOLUTION.finditer(text))
        if not found:
            return None
        if len(found) != 1 or re.search(r'\b(?:ou|or)\b', text):
            return []
        match = found[0]
        width, height = int(match[1]), int(match[2])
        rate = float(match[3].replace(",", ".")) if match[3] else None
        indices = [index for index, mode in enumerate(options)
                   if mode["width"] == width and mode["height"] == height
                   and (rate is None or abs(mode["refresh"] - rate) < 0.1)]
        output = re.search(r"\b(?:no monitor|on monitor|output)\s+([a-z0-9_.:-]+)\b", text)
        if output:
            indices = [index for index in indices if options[index]["output"].casefold() == output[1].casefold()]
        return indices

    def _package_choices(self, session_id, operation, query, lang, persist, is_current):
        if is_current():
            self._clear(session_id)
        result = self._invoke('packages.search', {'query': query}, session_id, is_current)
        choices, error = result.data or [], result.text
        if not is_current():
            return ActionReply(_say(lang, "Tarefa cancelada.", "Task cancelled."), "cancelled")
        if error:
            return ActionReply(error, "failed", "packages.search")
        if not choices:
            self._clear(session_id)
            return ActionReply(_say(lang, "Não encontrei esse programa nos repositórios configurados.",
                                    "No matching software was found in configured repositories."), operation="packages.search")
        self._save(session_id, "packages", operation, query, choices, persist)
        return choices

    def _render_packages(self, choices, operation, lang):
        lines = [_say(lang, "Programas encontrados nos repositórios configurados:", "Software found in configured repositories:")]
        for index, candidate in enumerate(choices[:MAX_TASK_OPTIONS], 1):
            lines.append(f"{index}. {candidate.name} — {candidate.version} — {candidate.source}\n   {candidate.summary}")
        lines.append(_say(lang, "Escolhe «opção 2» para instalar o programa pedido." if operation == "install"
                         else "Para instalar, responde «instala a opção 2».",
                         "Choose 'option 2' to install the requested software." if operation == "install"
                         else "To install, reply 'install option 2'."))
        return ActionReply("\n".join(lines), "needs_choice", "packages.search")

    def _render_display(self, modes, lang):
        lines = [_say(lang, "Modos disponíveis dos monitores:", "Available monitor modes:")]
        for index, mode in enumerate(modes[:MAX_TASK_OPTIONS], 1):
            current = _say(lang, " (atual)", " (current)") if mode.current else ""
            lines.append(f"{index}. {mode.output}: {mode.width}×{mode.height} @ {mode.refresh:g} Hz; "
                         f"{_say(lang, 'escala', 'scale')} {mode.scale:g}{current}")
        if len(modes) > MAX_TASK_OPTIONS:
            lines.append(_say(lang, 'A lista mostra as primeiras 30 opções. Pede uma resolução e frequência específicas para filtrar os restantes modos.',
                             'This list shows the first 30 options. Request a specific resolution and refresh rate to filter the remaining modes.'))
        lines.append(_say(lang, "Responde «aplica a opção 2» ou «coloca em 1920 por 1080 a 60 Hz».",
                         "Reply 'apply option 2' or 'set 1920 by 1080 at 60 Hz'."))
        return ActionReply("\n".join(lines), "needs_choice", "display.list_modes")

    def _install(self, session_id, candidate, lang, can_execute, is_current):
        if not can_execute:
            return ActionReply(_say(lang, f"Instalação proposta: {candidate.name} {candidate.version}. Usa um terminal interativo para executar.",
                                    f"Proposed installation: {candidate.name} {candidate.version}. Use an interactive terminal to execute."),
                               "proposal", "packages.install")
        if not is_current():
            return ActionReply(_say(lang, "Tarefa cancelada.", "Task cancelled."), "cancelled")
        if not self._mutation_lock.acquire(blocking=False):
            return ActionReply(_say(lang, 'Há outra alteração em curso. Aguarda o resultado.',
                                    'Another change is running. Wait for its result.'), 'needs_choice')
        try:
            self._clear(session_id)  # A completed/failed attempt cannot replay a choice.
            result = self._invoke('packages.install', candidate.to_dict(), session_id, is_current)
            ok, text = result.ok, result.text
        finally:
            self._mutation_lock.release()
        return ActionReply(text, "done" if ok else "failed", "packages.install", executed=True, operation_id=result.operation_id)

    def _apply_display(self, session_id, mode, lang, can_execute, is_current):
        if not can_execute:
            return ActionReply(_say(lang, f"Alteração proposta: {mode.output}, {mode.width}×{mode.height} @ {mode.refresh:g} Hz. Usa um terminal interativo para aplicar.",
                                    f"Proposed change: {mode.output}, {mode.width}×{mode.height} @ {mode.refresh:g} Hz. Use an interactive terminal to apply."),
                               "proposal", "display.apply_mode")
        if not is_current():
            return ActionReply(_say(lang, "Tarefa cancelada.", "Task cancelled."), "cancelled")
        if not self._mutation_lock.acquire(blocking=False):
            return ActionReply(_say(lang, 'Há outra alteração em curso. Aguarda o resultado.',
                                    'Another change is running. Wait for its result.'), 'needs_choice')
        try:
            self._clear(session_id)
            result = self._invoke('display.apply_mode', mode.to_dict(), session_id, is_current)
            change, error = result.change, result.text
        finally:
            self._mutation_lock.release()
        if change is None:
            return ActionReply(error, "failed", "display.apply_mode", executed=True)
        with self._lock:
            self._changes[session_id] = change
        return ActionReply(_say(lang, "Configuração aplicada temporariamente. Mantém a alteração dentro de 15 segundos; caso contrário será revertida.",
                               "Configuration applied temporarily. Keep the change within 15 seconds or it will be reverted."),
                           "needs_keep", "display.apply_mode", change=change, executed=True, operation_id=result.operation_id)

    def handle(self, message, session_id, lang="en", can_execute=True, is_current=None, persist_choices=True):
        """Return None for ordinary chat; never route pasted multi-line data."""
        requested_current = is_current if is_current is not None else lambda: True

        def is_current():
            return not self._closed and requested_current()
        if self._closed:
            return ActionReply(_say(lang, 'Tarefa cancelada.', 'Task cancelled.'), 'cancelled')
        if not isinstance(message, str) or not message.strip() or len(message) > 500 or "\n" in message or "\r" in message:
            return None
        text = normalize(message).strip().rstrip(".!?")
        if text in {'mostra acoes', 'mostra as acoes', 'lista acoes', 'show actions', 'list actions'}:
            try:
                return ActionReply(self.audit.render(session_id), operation='audit.list')
            except (OSError, ValueError):
                return ActionReply('Não foi possível ler o registo. / Cannot read the audit.', 'failed')
        detail = re.fullmatch(r'(?:detalhes|details)\s+([a-f0-9]{32})', text)
        if detail:
            try:
                return ActionReply(self.audit.render(session_id, detail[1]), operation='audit.show')
            except (OSError, ValueError):
                return ActionReply('Não foi possível ler o registo. / Cannot read the audit.', 'failed')
        if re.match(r'^(?:pesquisar|procurar|search)\s+(?:conhecimento|knowledge)\b', text):
            return None
        if text in _CANCEL:
            state = self.store.get_task_state(session_id)
            with self._lock:
                change = self._changes.get(session_id)
            pending_service = self._service_confirmations.pop(session_id, None)
            if pending_service:
                self.executor.discard(pending_service[0])
            if state is None and change is None and pending_service is None:
                return None
            self._clear(session_id)
            if change is not None and change.status == "pending":
                ok, detail = change.revert()
                return ActionReply(detail, "done" if ok else "failed", "display.apply_mode", executed=True)
            return ActionReply(_say(lang, "Tarefa cancelada.", "Task cancelled."), "cancelled")
        with self._lock:
            change = self._changes.get(session_id)
        if change is not None and text in (_KEEP | _REVERT):
            ok, detail = change.confirm() if text in _KEEP else change.revert()
            return ActionReply(detail, "done" if ok else "failed", "display.apply_mode", executed=True)
        refusal_text = re.sub(r'\bno monitor\s+[a-z0-9_.:-]+\b', '', normalize(message))
        if _HYPOTHETICAL_RE.search(message) or _is_refusal(refusal_text):
            return None
        extended = self._extended_request(message, text, session_id, lang, can_execute, is_current)
        if extended is not None:
            return extended
        if re.match(r'^(?:configura|configurar|configure)\s+(?:e|and)\s+', text):
            return ActionReply(_say(lang,
                'A instalação já é suportada; a configuração automática de aplicações precisa de um perfil específico. Pede «instala programa» para instalar.',
                'Installation is supported; automatic application configuration requires a specific profile. Request "install application" to install.'), 'needs_choice')
        state = self.store.get_task_state(session_id)
        if state and not 0 <= self.clock() - state["created_at"] <= TASK_TTL_SECONDS:
            was_selection = self._selector(text, state["options"]) is not None
            if state["kind"] == "display" and self._is_resolution_selection(text):
                was_selection = was_selection or self._resolution_indices(text, state["options"]) is not None
            self._clear(session_id)
            state = None
            if was_selection:
                return ActionReply(_say(lang, "As opções expiraram. Pede uma nova pesquisa ou listagem de monitores.",
                                        "These choices expired. Request a new search or monitor listing."), "needs_choice")
        if state is not None:
            selection = self._selector(text, state["options"])
            wrong_kind = ((state['kind'] == 'display' and _INSTALL.search(text))
                          or (state['kind'] == 'packages' and _CHANGE.search(text)))
            if wrong_kind and (selection is not None or (state['kind'] == 'display' and _RESOLUTION.search(text))):
                return ActionReply(_say(lang, 'A escolha não corresponde ao tipo de tarefa pendente. Pede a pesquisa ou listagem pretendida.',
                                        'This choice does not match the pending task. Request the intended search or listing.'), 'needs_choice')
            if state["kind"] == "display" and self._is_resolution_selection(text):
                matches = self._resolution_indices(text, state["options"])
                if matches is not None:
                    if not matches and _CHANGE.search(text) and len(list(_RESOLUTION.finditer(text))) == 1 and not re.search(r'\b(?:ou|or)\b', text):
                        # A direct request may name a mode beyond the bounded list.
                        state = None
                        selection = None
                    elif len(matches) != 1:
                        return ActionReply(_say(lang, "A resolução não identifica um único modo. Escolhe o número da opção, incluindo monitor e frequência.",
                                                "The resolution does not identify one mode. Choose its option number, including monitor and refresh rate."),
                                           "needs_choice", "display.list_modes")
                    else:
                        selection = matches[0]
            if selection is not None:
                if not 0 <= selection < len(state["options"]):
                    return ActionReply(_say(lang, "Esse número não existe nas opções apresentadas.", "That number is not among the listed choices."), "needs_choice")
                if state["kind"] == "packages":
                    candidate = PackageCandidate.from_dict(state["options"][selection])
                    if state["operation"] == "install" or _INSTALL.search(text):
                        return self._install(session_id, candidate, lang, can_execute, is_current)
                    self._save(session_id, "packages", "inspect", state["query"], [candidate], persist_choices)
                    return self._render_packages([candidate], "inspect", lang)
                mode = DisplayMode.from_dict(state["options"][selection])
                if state["operation"] == "set_mode" or _CHANGE.search(text):
                    return self._apply_display(session_id, mode, lang, can_execute, is_current)
                self._save(session_id, "display", "inspect", state["query"], [mode], persist_choices)
                return self._render_display([mode], lang)
        request = self._package_request(text)
        if request is not None and self.manager in SUPPORTED_MANAGERS:
            operation, query = request
            choices = self._package_choices(session_id, operation, query, lang, persist_choices, is_current)
            if isinstance(choices, ActionReply):
                return choices
            exact = [item for item in choices if item.name.casefold() == query.casefold()]
            if operation == "install" and len(exact) == 1:
                return self._install(session_id, exact[0], lang, can_execute, is_current)
            return self._render_packages(choices, operation, lang)
        display_request = self._display_request(text)
        if display_request is not None:
            if is_current():
                self._clear(session_id)
            result = self._invoke('display.list_modes', {}, session_id, is_current)
            modes, error = result.data or [], result.text
            if not is_current():
                return ActionReply(_say(lang, "Tarefa cancelada.", "Task cancelled."), "cancelled")
            if error:
                return ActionReply(error, "failed", "display.list_modes")
            if not modes:
                return ActionReply(_say(lang, "Não foram encontrados modos de monitor.", "No monitor modes were found."), operation="display.list_modes")
            matches = self._resolution_indices(text, [mode.to_dict() for mode in modes])
            if matches:
                modes = [modes[index] for index in matches]
            self._save(session_id, "display", display_request, "monitors", modes, persist_choices)
            if display_request == "set_mode" and matches is not None and len(matches) == 1:
                return self._apply_display(session_id, modes[0], lang, can_execute, is_current)
            return self._render_display(modes, lang)
        return None

    def close(self):
        self._closed = True
        self.displays.close()
        self.executor.close()
        self._service_confirmations.clear()
