"""Bounded local evidence analysis and shareable, redacted reports.

Rules describe evidence and possible causes. They never execute text from logs,
make network requests, or turn a finding into an approved repair.
"""

from datetime import datetime, timezone
import ipaddress
import json
import re
import shutil

from .local_knowledge import PROCEDURE_BY_ID, localized

MAX_INPUT_BYTES = 65536
MAX_OBSERVATION_CHARS = 12000
PROBES = {
    "links": ("ip", "link", "show"),
    "addresses": ("ip", "addr", "show"),
    "routes": ("ip", "route"),
    "routes6": ("ip", "-6", "route"),
    "disk": ("df", "-h"),
    "inodes": ("df", "-i"),
    "memory": ("free", "-h"),
}


def redact(text):
    """Best-effort redaction, also applied to symptoms and finding excerpts.

    Unknown secrets can still require manual removal before sharing a report.
    """
    text = re.sub(r"-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\n]*PRIVATE KEY-----|\Z)",
                  "[private key removed]", text)
    text = re.sub(r"(?im)^(.*?\b(?:authorization|proxy-authorization)\s*[:=]\s*).*$", r"\1[redacted]", text)
    text = re.sub(r"(?i)(\b(?:password|passwd|pwd|token|api[_-]?key|secret|access[_-]?key)\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
                  r"\1[redacted]", text)
    text = re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)\b", "[token]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[credentials]@", text)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", text)
    text = re.sub(r"/(?:home|Users)/[^/\s]+", "/home/[user]", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IPv4]", text)
    text = re.sub(r"(?i)(?<!\w)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?!\w)", "[MAC]", text)
    def hide_ipv6(match):
        try:
            ipaddress.IPv6Address(match[0].split('%')[0])
        except ValueError:
            return match[0]
        return '[IPv6]'
    text = re.sub(r"(?i)(?<![\w:])(?:[0-9a-f]*:){2,}[0-9a-f]*(?:%[\w.-]+)?(?![\w:])", hide_ipv6, text)
    # Do not let terminal escape/control sequences affect the displayed report.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32)


# (id, pattern, procedure, Portuguese explanation, English explanation)
RULES = (
    ("dns", r"temporary failure in name resolution|could not resolve|name or service not known|falha.*resolu[çc][aã]o",
     "network-dns", "A resolução do nome falhou. Confirma DNS e ligação local; isto não prova uma falha do servidor remoto.",
     "Name resolution failed. Check DNS and the local connection; this does not prove the remote server failed."),
    ("route", r"network is unreachable|no route to host|rede.*inalcan[çc][aá]vel",
     "network-route", "O destino não foi alcançado. Verifica rotas IPv4/IPv6 e a interface; o erro também pode resultar de filtragem.",
     "The destination was not reached. Check IPv4/IPv6 routes and the interface; filtering can also cause this error."),
    ("refused", r"connection refused|conex[aã]o recusada|liga[çc][aã]o recusada",
     "service-port", "A ligação foi recusada. Confirma endereço, porta e serviço em escuta; não atribuas automaticamente a causa ao DNS.",
     "The connection was refused. Confirm address, port and listening service; do not automatically attribute this to DNS."),
    ("port", r"address already in use|eaddrinuse|endere[çc]o.*em uso",
     "service-port", "O endereço/porta já está em uso. Identifica o processo e a configuração antes de terminar serviços.",
     "The address/port is already in use. Identify the process and configuration before stopping services."),
    ("permission", r"permission denied|permiss[aã]o negada",
     "file-permissions", "Há uma recusa de acesso. Confirma o utilizador do processo e as permissões do caminho; sudo não é uma conclusão automática.",
     "Access was denied. Check the process user and path permissions; sudo is not an automatic conclusion."),
    ("space", r"no space left on device|enospc|sem espa[çc]o.*dispositivo",
     "disk-space", "Uma escrita ficou sem espaço. Compara bytes e inodes na montagem afetada antes de limpar dados.",
     "A write ran out of space. Compare bytes and inodes on the affected mount before cleaning data."),
    ("readonly", r"read-only file system|read-only filesystem|erofs|sistema.*s[oó].*leitura",
     "disk-read-only", "A escrita encontrou uma montagem só de leitura. Confirma se é intencional e procura erros do dispositivo antes de remontar.",
     "The write encountered a read-only mount. Check whether this is intentional and look for device errors before remounting."),
    ("io", r"input/output error|\bi/o error\b|buffer i/o|erro de entrada/sa[ií]da",
     "disk-read-only", "Foi reportado um erro de entrada/saída. Preserva dados e verifica dispositivo/montagem; o excerto não identifica sozinho a causa física.",
     "An input/output error was reported. Preserve data and check device/mount; the excerpt alone does not identify a physical cause."),
    ("oom", r"out of memory|oom-kill:|oom_kill\s+[1-9]\d*|killed process \d+|mem[oó]ria insuficiente",
     "memory-pressure", "Há indícios de falta de memória ou OOM. Confirma eventos e limites de contentores; não uses apenas a coluna free.",
     "There is evidence of memory shortage or OOM. Confirm events and container limits; do not rely only on the free column."),
    ("tls", r"certificate verify failed|certificate.*(?:expired|not yet valid)|certificado.*(?:expirado|inv[aá]lido)",
     "tls-clock", "A validação do certificado falhou. Confirma relógio, cadeia de confiança e destino; não desatives a validação TLS.",
     "Certificate validation failed. Check clock, trust chain and destination; do not disable TLS validation."),
    ("exec", r"203/exec|status=203|failed at step exec",
     "service-executable", "systemd não conseguiu executar o programa. Verifica ExecStart, existência, permissões e interpretador.",
     "systemd could not execute the program. Check ExecStart, existence, permissions and interpreter."),
    ("unit", r"unit .+ could not be found|unit .+ not found|loaded: not-found",
     "service-systemd", "A unidade não foi encontrada. Confirma o nome e a instalação antes de tentar reiniciar.",
     "The unit was not found. Confirm its name and installation before trying to restart it."),
    ("runit", r"(?m)^down: .+: \d+s|unable to open supervise/ok|runsv not running",
     "service-runit", "O serviço runit está parado ou sem supervisor acessível. Confirma o diretório supervisionado e o script run; down pode ser intencional.",
     "The runit service is down or its supervisor is inaccessible. Check the supervised directory and run script; down can be intentional."),
    ("apt-lock", r"could not get lock|unable to acquire.*lock|n[aã]o.*obter.*bloqueio",
     "apt-lock", "O gestor encontrou um bloqueio. Confirma se outra transação está ativa; não apagues ficheiros de lock.",
     "The manager encountered a lock. Check for another active transaction; do not delete lock files."),
    ("dpkg", r"dpkg was interrupted|dpkg foi interrompido|half-configured|half-installed",
     "apt-interrupted", "A configuração de pacotes está incompleta. Inspeciona o estado e a primeira falha antes de autorizar uma recuperação.",
     "Package configuration is incomplete. Inspect state and the first failure before authorizing recovery."),
    ("xbps-shlibs", r"unresolvable shlib|unresolved shlibs",
     "xbps-shlibs", "XBPS reportou bibliotecas não resolvidas. Pode haver pacotes antigos ou repositórios em transição; confirma o estado antes de remover pacotes.",
     "XBPS reported unresolved libraries. Packages may be outdated or repositories transitioning; confirm state before removing packages."),
    ("xbps-repo", r"\[reposync\].*(?:not found|operation not permitted)",
     "xbps-errors", "A sincronização XBPS falhou. Not Found sugere repositório incorreto; Operation not permitted também pode resultar do relógio. Confirma o erro completo.",
     "XBPS synchronization failed. Not Found suggests a wrong repository; Operation not permitted can also result from the clock. Confirm the full error."),
)


def analyze(text, distro=None, lang="en", probe_key=""):
    """Return bounded, redacted findings from a supplied observation only."""
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("Diagnostic input exceeds 64 KiB")
    findings = []
    for identifier, pattern, guide_id, pt, en in RULES:
        guide = PROCEDURE_BY_ID[guide_id]
        if distro is not None and not guide.applies_to(distro):
            continue
        match = re.search(pattern, text, re.I)
        if match:
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            evidence = text[start:end if end >= 0 else len(text)]
            findings.append({"id": identifier, "evidence": redact(evidence)[:300],
                             "interpretation": pt if lang == "pt" else en, "guide": guide_id})
    if probe_key in ("disk", "inodes"):
        for line in text.splitlines():
            values = re.findall(r"(?<!\d)(\d{1,3})%", line)
            if any(90 <= int(value) <= 100 for value in values):
                findings.append({"id": probe_key + "-usage", "evidence": redact(line)[:300],
                                 "interpretation": ("Ocupação de pelo menos 90%; confirma a montagem afetada." if lang == "pt" else
                                                    "Usage is at least 90%; confirm the affected mount."),
                                 "guide": "disk-inodes" if probe_key == "inodes" else "disk-space"})
                break
    if probe_key == "addresses" and re.search(r"\b169\.254\.", text):
        findings.append({"id": "link-local", "evidence": "169.254.x.x",
                         "interpretation": "Endereço link-local; confirma DHCP." if lang == "pt" else "Link-local address; check DHCP.",
                         "guide": "network-address"})
    if probe_key == "memory":
        memory = re.search(r"(?m)^Mem:\s+(.+)$", text)
        if memory and len(memory.group(1).split()) == 6:
            values = memory.group(1).split()
            def quantity(value):
                match = re.fullmatch(r"(\d+(?:[.,]\d+)?)([kKmMgGtTpP]?)(?:iB?|B)?", value)
                if match is None:
                    return None
                return float(match[1].replace(',', '.')) * 1024 ** ('kmgtp'.find(match[2].lower()) + 1 if match[2] else 0)
            total, available = quantity(values[0]), quantity(values[5])
            if total and available is not None and available / total < 0.1:
                findings.append({"id": "memory-available", "evidence": redact(memory[0])[:300],
                                 "interpretation": ("Memória available inferior a 10% nesta amostra. Observa processos e limites; este valor não confirma OOM." if lang == "pt" else
                                                    "Available memory is below 10% in this sample. Check processes and limits; this value does not confirm OOM."),
                                 "guide": "memory-pressure"})
    # An empty or failed probe must never be interpreted as a missing route.
    if probe_key in ("routes", "routes6") and text.strip() and not re.search(r"(?m)^default\s", text):
        findings.append({"id": probe_key + "-default", "evidence": "default: —",
                         "interpretation": ("Sem rota default nesta tabela; verifica também a outra família IP e rotas específicas." if lang == "pt" else
                                            "No default in this table; also check the other IP family and specific routes."),
                         "guide": "network-route"})
    return findings


def render_findings(findings, lang="en"):
    if not findings:
        return ("Sem indício reconhecido nestas regras. Não confirma ausência de problemas." if lang == "pt" else
                "No evidence recognized by these rules. This does not confirm the absence of problems.")
    lines = []
    for finding in findings:
        guide = PROCEDURE_BY_ID[finding["guide"]]
        lines.extend(("• " + finding["interpretation"], "  " + finding["evidence"],
                      "  [" + guide.id + "] " + localized(guide.title, lang)))
    return "\n".join(lines)


def build_report(symptom, pasted, distro, lang="en", probe_keys=(), system_utils=None, which=shutil.which, session_id="", cancel_event=None):
    """Collect only explicitly selected, fixed read probes via current permissions."""
    if len((symptom + pasted).encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("Diagnostic input exceeds 64 KiB")
    keys = tuple(dict.fromkeys(probe_keys))
    if any(key not in PROBES for key in keys):
        raise ValueError("Unknown diagnostic probe")
    pt = lang == "pt"
    observations = []
    findings = []
    if pasted.strip():
        findings.extend(analyze(pasted, distro, lang))
        observations.append({"key": "pasted", "command": "", "status": "supplied",
                             "output": redact(pasted)[:MAX_OBSERVATION_CHARS],
                             "truncated": len(pasted) > MAX_OBSERVATION_CHARS})
    for key in keys:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('Report cancelled')
        argv = PROBES[key]
        status, output = "unavailable", ""
        if system_utils is not None and which(argv[0]):
            try:
                ok, output = system_utils.execute_command(list(argv), timeout=8)
                status = "ok" if ok else "failed"
            except Exception as error:
                status, output = "failed", str(error)
        output = str(output)
        if status == "ok":
            findings.extend(analyze(output[:MAX_OBSERVATION_CHARS], distro, lang, key))
        observations.append({"key": key, "command": " ".join(argv), "status": status,
                             "output": redact(output)[:MAX_OBSERVATION_CHARS],
                             "truncated": len(output) > MAX_OBSERVATION_CHARS})
    guides = list(dict.fromkeys(item["guide"] for item in findings))
    return {"format": "linux-ai-diagnostic", "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(), "language": lang,
            "session_id": session_id, "symptom": redact(symptom)[:2000],
            "system": {key: redact(str(getattr(distro, key, ""))) for key in
                       ("pretty_name", "distro_id", "version_id", "service_manager", "pkg_manager", "kernel")},
            "observations": observations, "findings": findings,
            "guides": [{"id": guide_id, "title": localized(PROCEDURE_BY_ID[guide_id].title, lang),
                        "steps": [localized(step.instruction, lang) + (" (" + step.command + ")" if step.command else "") + " " + localized(step.interpretation, lang)
                                  for step in PROCEDURE_BY_ID[guide_id].steps],
                        "sources": list(PROCEDURE_BY_ID[guide_id].sources),
                        "reviewed_at": PROCEDURE_BY_ID[guide_id].reviewed_at,
                        "verification": PROCEDURE_BY_ID[guide_id].verification} for guide_id in guides],
            "limitations": ("Interpretação por regras, sem confirmação da causa. Recolha só de leitura; sem reparações executadas. Ocultação de dados automática e parcial: revê antes de partilhar." if pt else
                            "Rule-based interpretation, without cause confirmation. Read-only collection; no repairs executed. Automatic redaction is partial: review before sharing.")}


def export_report(report, format="markdown"):
    if format == "json":
        return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if format != "markdown":
        raise ValueError("Unknown report format")
    pt = report["language"] == "pt"
    lines = ["# " + ("Diagnóstico Linux AI" if pt else "Linux AI diagnostic"), report["created_at"],
             report["limitations"], "\n## " + ("Sintoma" if pt else "Symptom"), report["symptom"],
             "\n## " + ("Sistema" if pt else "System")]
    lines.extend(key + ": " + value for key, value in report["system"].items())
    lines.extend(("\n## " + ("Indícios e hipóteses" if pt else "Evidence and hypotheses"),
                  render_findings(report["findings"], report["language"])))
    lines.append("\n## " + ("Observações" if pt else "Observations"))
    for item in report["observations"]:
        lines.append("\n" + item["key"] + " — " + item["status"] + " — " + item["command"])
        # Indented code prevents pasted fence delimiters from ending the block.
        lines.extend("    " + line for line in item["output"].splitlines())
        if item["truncated"]:
            lines.append("[truncated]")
    lines.append("\n## " + ("Próximas verificações e fontes" if pt else "Next checks and sources"))
    for guide in report["guides"]:
        lines.append("\n[" + guide["id"] + "] " + guide["title"])
        lines.extend(str(index) + ". " + step for index, step in enumerate(guide["steps"], 1))
        lines.append(guide["verification"] + " — " + guide["reviewed_at"])
        lines.extend(guide["sources"])
    return "\n".join(lines) + "\n"
