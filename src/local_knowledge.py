"""Bundled, searchable diagnostic procedures. No I/O or executable documents.

Sources were reviewed on 2026-10-02. This is a documentation review, not a
claim that every procedure was tested on each listed distribution version.
Only ``probe_key`` identifiers can select a separately allowlisted read probe;
commands shown in prose are examples and must never be executed from this data.
"""

from dataclasses import dataclass
import re
from typing import Tuple
import unicodedata

from .knowledge_base import knowledge_for


def normalize(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(char))


def localized(pair: Tuple[str, str], lang: str) -> str:
    return pair[0] if lang == "pt" else pair[1]


@dataclass(frozen=True)
class DiagnosticStep:
    instruction: Tuple[str, str]
    interpretation: Tuple[str, str]
    command: str = ""
    probe_key: str = ""


@dataclass(frozen=True)
class Procedure:
    id: str
    title: Tuple[str, str]
    summary: Tuple[str, str]
    keywords: Tuple[str, ...]
    steps: Tuple[DiagnosticStep, ...]
    sources: Tuple[str, ...]
    families: Tuple[str, ...] = ()
    component: str = ""
    versions: Tuple[str, ...] = ()
    reviewed_at: str = "2026-10-02"
    verification: str = "documentation_review"
    tested_versions: Tuple[str, ...] = ()
    version_scope: Tuple[str, str] = (
        "Orientação por componente; confirma diferenças da versão instalada antes de corrigir.",
        "Component-based guidance; confirm installed-release differences before fixing.",
    )
    effects: str = "read_only_guidance"
    recovery: Tuple[str, str] = (
        "Estes passos não alteram o sistema. Antes de corrigir, guarda a configuração e define como repor a alteração.",
        "These steps do not modify the system. Before fixing it, save the configuration and define how to revert the change.",
    )

    def applies_to(self, distro) -> bool:
        if distro is None:
            return not self.families
        profile = knowledge_for(getattr(distro, "distro_id", "unknown"),
                                getattr(distro, "id_like", ()))
        family = profile.family if profile else "generic"
        if self.families and family not in self.families:
            return False
        version = getattr(distro, "version_id", "")
        if self.versions and version and version not in self.versions:
            return False
        manager = getattr(distro, "service_manager", "unknown")
        if self.component in {"systemd", "runit", "openrc"} and manager != self.component:
            return False
        return True


def _step(pt, en, explain_pt, explain_en, command="", probe=""):
    return DiagnosticStep((pt, en), (explain_pt, explain_en), command, probe)


IP_ADDR = "https://man7.org/linux/man-pages/man8/ip-address.8.html"
IP_ROUTE = "https://man7.org/linux/man-pages/man8/ip-route.8.html"
VOID_NET = "https://docs.voidlinux.org/config/network/index.html"
VOID_SVC = "https://docs.voidlinux.org/config/services/index.html"
APT = "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html"
CORE = "https://manpages.debian.org/bookworm/coreutils/"
MEMORY = "https://www.kernel.org/doc/html/latest/admin-guide/mm/concepts.html"
SYSTEMD = "https://www.freedesktop.org/software/systemd/man/latest/systemctl.html"


PROCEDURES = (
    Procedure("network-interface", ("Rede sem ligação", "Network link unavailable"),
        ("Começa pela interface local antes de testar destinos externos.", "Check the local interface before testing external destinations."),
        ("rede", "network", "internet", "interface", "sem ligacao", "no connection"), (
            _step("Verifica as interfaces.", "Check the interfaces.", "DOWN indica uma interface inativa; UNKNOWN pode ser normal em interfaces virtuais. Confirma cabo ou Wi-Fi.", "DOWN means an inactive interface; UNKNOWN can be normal for virtual interfaces. Check cable or Wi-Fi.", "ip -brief link", "links"),
            _step("Confirma os endereços da interface utilizada.", "Check addresses on the interface in use.", "Ignora lo. Sem endereço adequado, investiga o gestor da ligação ou DHCP antes de DNS.", "Ignore lo. Without a suitable address, investigate the connection manager or DHCP before DNS.", "ip -brief addr", "addresses"),
            _step("Verifica a rota e continua pelo guia de DNS se necessário.", "Check the route and continue with the DNS guide if needed.", "A ausência de rota default pode impedir acesso fora da rede local; uma rota presente não prova acesso à Internet.", "A missing default route may prevent access beyond the local network; its presence does not prove Internet access.", "ip route", "routes"),
        ), (IP_ADDR, IP_ROUTE)),
    Procedure("network-address", ("Interface sem endereço IP", "Interface without an IP address"),
        ("Distingue uma interface ativa de uma ligação com endereço utilizável.", "Distinguish an active interface from a connection with a usable address."),
        ("dhcp", "endereco", "address", "ip", "169.254"), (
            _step("Identifica a interface e o endereço.", "Identify the interface and address.", "Um endereço 169.254.x.x é link-local; não confirma que o DHCP da rede funcionou.", "A 169.254.x.x address is link-local; it does not confirm that network DHCP succeeded.", "ip -brief addr", "addresses"),
            _step("Identifica o gestor de rede realmente utilizado.", "Identify the network manager actually in use.", "NetworkManager, networkd e dhcpcd são alternativas. A existência do programa não prova que controla esta interface.", "NetworkManager, networkd and dhcpcd are alternatives. An installed program does not prove it controls this interface."),
        ), (IP_ADDR, VOID_NET)),
    Procedure("network-route", ("Gateway e rotas", "Gateway and routes"),
        ("Inspeciona a tabela local de encaminhamento.", "Inspect the local routing table."),
        ("gateway", "rota", "rotas", "route", "routing", "default"), (
            _step("Mostra as rotas IPv4.", "Show IPv4 routes.", "Compara default e a interface escolhida com a ligação desejada; VPNs podem instalar várias rotas.", "Compare default and the selected interface with the intended connection; VPNs may install several routes.", "ip route", "routes"),
            _step("Se a ligação for IPv6, verifica também a tabela IPv6.", "For IPv6 connections, also check the IPv6 table.", "A falta de default IPv4 não é suficiente para diagnosticar uma ligação exclusivamente IPv6.", "A missing IPv4 default alone does not diagnose an IPv6-only connection.", "ip -6 route"),
        ), (IP_ROUTE,)),
    Procedure("network-dns", ("Problemas de resolução DNS", "DNS resolution problems"),
        ("Verifica a configuração local sem enviar consultas DNS automaticamente.", "Check local configuration without automatically sending DNS queries."),
        ("dns", "resolve", "resolucao", "resolv.conf", "name resolution"), (
            _step("Identifica quem gere o DNS.", "Identify the DNS manager.", "Se systemd-resolved estiver ativo, usa resolvectl status. Noutros casos consulta /etc/resolv.conf com permissão de leitura.", "If systemd-resolved is active, use resolvectl status. Otherwise inspect /etc/resolv.conf with read permission.", "resolvectl status"),
            _step("Compara os servidores com a configuração da ligação ou VPN.", "Compare servers with the connection or VPN configuration.", "Não sobrescrevas resolv.conf se for gerado por outro serviço. Consultas externas exigem rede e são um teste separado.", "Do not overwrite resolv.conf if another service generates it. External lookups require a network and are a separate test."),
        ), ("https://www.freedesktop.org/software/systemd/man/latest/resolvectl.html", VOID_NET)),
    Procedure("network-wifi", ("Wi-Fi indisponível", "Wi-Fi unavailable"),
        ("Distingue bloqueio do rádio, falta de interface e falha de associação.", "Distinguish radio blocking, a missing interface and association failures."),
        ("wifi", "wi-fi", "wireless", "rfkill", "radio"), (
            _step("Verifica se existe uma interface sem fios.", "Check for a wireless interface.", "Se faltar a interface esperada, investiga hardware, driver e firmware antes das palavras-passe.", "If the expected interface is missing, investigate hardware, driver and firmware before passwords.", "ip -brief link", "links"),
            _step("Consulta o estado do bloqueio do rádio.", "Inspect radio blocking.", "Um bloqueio físico exige o interruptor ou configuração do equipamento; não é resolvido por mudar DNS.", "A hardware block requires a device switch or setting; changing DNS will not fix it.", "rfkill list"),
        ), (VOID_NET,)),
    Procedure("network-manager", ("Ligação no NetworkManager", "NetworkManager connection"),
        ("Consulta o estado dos dispositivos quando NetworkManager for o gestor ativo.", "Inspect device status when NetworkManager is the active manager."),
        ("networkmanager", "nmcli", "connection", "ligacao", "disconnected"), (
            _step("Verifica dispositivos e perfis ativos.", "Inspect devices and active profiles.", "Unmanaged significa que este dispositivo não é controlado pelo NetworkManager; identifica o outro gestor.", "Unmanaged means this device is not controlled by NetworkManager; identify the other manager.", "nmcli device status"),
            _step("Compara o perfil ativo com o dispositivo pretendido.", "Compare the active profile with the intended device.", "Não elimines perfis para diagnosticar. Guarda os parâmetros relevantes sem expor segredos.", "Do not delete profiles as a diagnostic step. Save relevant parameters without exposing secrets.", "nmcli connection show --active"),
        ), ("https://networkmanager.dev/docs/api/latest/nmcli.html",), component="nmcli"),
    Procedure("disk-space", ("Disco cheio", "Disk full"),
        ("Localiza o sistema de ficheiros sem espaço antes de apagar dados.", "Locate the full filesystem before deleting data."),
        ("disco", "disk", "espaco", "space", "storage", "no space", "cheio"), (
            _step("Verifica a ocupação dos sistemas de ficheiros.", "Check filesystem usage.", "Uma percentagem elevada exige identificar a montagem afetada; uma montagem separada pode estar cheia enquanto / tem espaço.", "High usage requires identifying the affected mount; a separate mount may be full while / has free space.", "df -h", "disk"),
            _step("Verifica também os inodes.", "Also check inodes.", "Inodes esgotados podem impedir novos ficheiros mesmo com bytes livres. Identifica os diretórios antes de limpar.", "Exhausted inodes can prevent new files despite free bytes. Identify directories before cleaning.", "df -i", "inodes"),
        ), (CORE,)),
    Procedure("disk-inodes", ("Inodes esgotados", "Inodes exhausted"),
        ("Investiga demasiados ficheiros pequenos.", "Investigate too many small files."),
        ("inode", "inodes", "small files", "ficheiros pequenos"), (
            _step("Compara inodes usados e livres.", "Compare used and free inodes.", "100% em IUse% sugere esgotamento de inodes nessa montagem; alguns tipos de sistema não apresentam este limite.", "100% IUse% suggests inode exhaustion on that mount; some filesystem types do not expose this limit.", "df -i", "inodes"),
            _step("Identifica a aplicação que cria os ficheiros.", "Identify the application creating files.", "Inspeciona apenas diretórios autorizados. Corrige retenção ou rotação antes de remover conjuntos de ficheiros.", "Inspect only authorized directories. Fix retention or rotation before removing groups of files."),
        ), (CORE,)),
    Procedure("disk-read-only", ("Sistema de ficheiros só de leitura", "Read-only filesystem"),
        ("Investiga a montagem e erros antes de tentar remontar.", "Investigate the mount and errors before trying to remount."),
        ("read-only", "readonly", "so leitura", "só de leitura", "erofs"), (
            _step("Consulta as opções da montagem afetada.", "Inspect affected mount options.", "ro pode ser intencional ou consequência de erros. Um contentor pode ter montagens só de leitura por desenho.", "ro can be intentional or caused by errors. A container may have read-only mounts by design.", "findmnt --output TARGET,SOURCE,FSTYPE,OPTIONS"),
            _step("Procura erros recentes do dispositivo nos registos disponíveis.", "Look for recent device errors in available logs.", "Não executes reparação de um sistema de ficheiros montado. Guarda dados importantes e segue o procedimento específico do sistema.", "Do not repair a mounted filesystem. Preserve important data and follow filesystem-specific recovery."),
        ), ("https://man7.org/linux/man-pages/man8/findmnt.8.html",)),
    Procedure("disk-mount", ("Montagem ou dispositivo ausente", "Missing mount or device"),
        ("Confirma a montagem utilizada pelo caminho problemático.", "Confirm the mount used by the problematic path."),
        ("mount", "montagem", "fstab", "device", "dispositivo"), (
            _step("Consulta as montagens atuais.", "Inspect current mounts.", "Uma pasta existir não prova que o dispositivo esperado está montado nela.", "A directory existing does not prove the expected device is mounted there.", "findmnt"),
            _step("Compara a origem com a configuração autorizada.", "Compare the source with authorized configuration.", "Antes de editar fstab, guarda uma cópia e confirma identificadores; alterações erradas podem afetar o arranque.", "Before editing fstab, save a copy and confirm identifiers; incorrect changes can affect boot."),
        ), ("https://man7.org/linux/man-pages/man8/findmnt.8.html",)),
    Procedure("file-permissions", ("Permissão negada", "Permission denied"),
        ("Verifica utilizador, propriedade e permissões do caminho.", "Check user, ownership and path permissions."),
        ("permission", "permissao", "permissão", "denied", "negada", "chmod", "ownership"), (
            _step("Identifica o utilizador e os atributos do ficheiro com acesso de leitura autorizado.", "Identify the user and file attributes with authorized read access.", "Confirma proprietário, grupo e permissão de travessia dos diretórios. Um erro não implica que seja necessário sudo.", "Check owner, group and directory traversal permissions. An error does not imply sudo is required.", "whoami"),
            _step("Investiga ACLs ou restrições da montagem quando as permissões parecem corretas.", "Investigate ACLs or mount restrictions when permissions look correct.", "Evita chmod 777 e mudanças recursivas de proprietário. Define a permissão mínima e guarda os atributos anteriores.", "Avoid chmod 777 and recursive ownership changes. Define the minimum permission and preserve previous attributes."),
        ), (CORE,)),
    Procedure("memory-pressure", ("Memória insuficiente", "Memory pressure"),
        ("Distingue cache recuperável de falta de memória disponível.", "Distinguish reclaimable cache from low available memory."),
        ("memory", "memoria", "memória", "ram", "oom", "swap", "out of memory"), (
            _step("Consulta memória disponível e swap.", "Inspect available memory and swap.", "Usa available em vez de apenas free: caches podem ser recuperáveis. Este valor isolado não identifica a aplicação responsável.", "Use available rather than free alone: caches may be reclaimable. This value alone does not identify the responsible application.", "free -h", "memory"),
            _step("Procura processos consumidores e eventos OOM.", "Look for consuming processes and OOM events.", "Confirma o processo e guarda o trabalho antes de terminar aplicações; contentores podem ter limites inferiores à RAM total.", "Confirm the process and save work before terminating applications; container limits may be below total RAM."),
        ), (MEMORY,)),
    Procedure("process-resources", ("Processo a consumir recursos", "Process consuming resources"),
        ("Observa os processos antes de decidir terminar algum.", "Observe processes before deciding whether to terminate one."),
        ("process", "processo", "cpu", "slow", "lento", "consumo", "resource"), (
            _step("Consulta os processos com PID e utilização de recursos.", "Inspect processes with PID and resource usage.", "Uma amostra não prova consumo persistente. Compara várias observações e identifica a aplicação.", "One sample does not prove persistent consumption. Compare observations and identify the application.", "ps aux"),
            _step("Escolhe uma ação específica depois de guardar o trabalho.", "Choose a specific action after saving work.", "Reiniciar ou terminar um processo é uma alteração; não faz parte deste guia de leitura.", "Restarting or terminating a process is a change; it is not part of this read-only guide."),
        ), ("https://man7.org/linux/man-pages/man1/ps.1.html",)),
    Procedure("service-systemd", ("Serviço systemd não arranca", "systemd service fails to start"),
        ("Confirma a unidade e o motivo da falha antes de reiniciar.", "Confirm the unit and failure reason before restarting."),
        ("servico", "serviço", "service", "systemd", "failed", "nao arranca", "not starting"), (
            _step("Consulta o estado da unidade identificada.", "Inspect the identified unit status.", "Usa systemctl --no-pager status <unidade>. Distingue not-found, inactive e failed; substitui o marcador por um nome confirmado.", "Use systemctl --no-pager status <unit>. Distinguish not-found, inactive and failed; replace the placeholder with a confirmed name."),
            _step("Consulta um trecho limitado dos registos da mesma unidade.", "Inspect a bounded excerpt of that unit's logs.", "Usa journalctl --no-pager -u <unidade> -n 50. Procura a primeira causa, como configuração inválida, porta ocupada ou permissões.", "Use journalctl --no-pager -u <unit> -n 50. Look for the first cause, such as invalid configuration, a busy port or permissions."),
        ), (SYSTEMD,), component="systemd"),
    Procedure("service-runit", ("Serviço runit não arranca", "runit service fails to start"),
        ("Inspeciona o serviço supervisionado e a sua configuração.", "Inspect the supervised service and its configuration."),
        ("runit", "sv", "servico", "serviço", "service", "nao arranca", "not starting"), (
            _step("Confirma se o serviço está ligado em /var/service.", "Confirm whether the service is linked in /var/service.", "As definições ficam em /etc/sv. Um diretório de definição não prova que o serviço esteja ativado.", "Definitions live in /etc/sv. A definition directory does not prove the service is enabled."),
            _step("Consulta sv status <serviço> e o registo específico.", "Inspect sv status <service> and its specific log.", "down pode ser intencional. Verifica o ficheiro down e a configuração com leitura autorizada antes de iniciar o serviço.", "down can be intentional. Check the down file and configuration with authorized reads before starting the service."),
        ), (VOID_SVC,), component="runit"),
    Procedure("service-logs", ("Encontrar registos de um erro", "Find logs for an error"),
        ("Escolhe registos do componente e intervalo relevantes.", "Choose logs for the relevant component and time interval."),
        ("logs", "log", "registos", "journal", "erro", "error", "logging"), (
            _step("Identifica o serviço e o gestor que realmente o executa.", "Identify the service and manager actually running it.", "systemd usa o journal; runit pode encaminhar saída para um serviço de logging separado. A localização depende da instalação.", "systemd uses the journal; runit may forward output to a separate logging service. Location depends on the installation."),
            _step("Seleciona as linhas da falha e remove segredos antes de partilhar.", "Select failure lines and remove secrets before sharing.", "Inclui a mensagem inicial e o momento da falha. A última linha nem sempre contém a causa.", "Include the initial message and failure time. The final line does not always contain the cause."),
        ), ("https://docs.voidlinux.org/config/services/logging.html", "https://www.freedesktop.org/software/systemd/man/latest/journalctl.html")),
    Procedure("apt-lock", ("APT bloqueado por outra operação", "APT locked by another operation"),
        ("Verifica operações em curso antes de intervir.", "Check running operations before intervening."),
        ("apt", "lock", "bloqueio", "dpkg", "another process", "outro processo"), (
            _step("Confirma se existe outra operação de pacotes em curso.", "Check whether another package operation is running.", "Atualizações automáticas também podem usar APT. Aguarda uma operação legítima; não apagues ficheiros de lock como primeiro passo.", "Automatic updates can also use APT. Wait for a legitimate operation; do not delete lock files as a first step.", "ps aux"),
            _step("Se uma operação terminou com erro, guarda a mensagem completa.", "If an operation ended with an error, preserve the full message.", "A recuperação depende da causa e pode alterar pacotes. Confirma o plano antes de reparar.", "Recovery depends on the cause and may modify packages. Confirm the plan before repairing."),
        ), ("https://wiki.debian.org/Teams/Dpkg/FAQ",), families=("debian", "ubuntu", "mint"), component="apt"),
    Procedure("apt-dependencies", ("Dependências APT inconsistentes", "Inconsistent APT dependencies"),
        ("Recolhe a mensagem e verifica o estado antes de reparar.", "Collect the message and inspect state before repairing."),
        ("apt", "dependencia", "dependência", "dependency", "dependencies", "broken", "unmet"), (
            _step("Guarda os nomes e versões mencionados no erro.", "Preserve names and versions mentioned in the error.", "Verifica se há pacotes retidos ou repositórios de versões diferentes.", "Check for held packages or repositories from different releases.", "apt-mark showhold"),
            _step("Avalia uma simulação de reparação antes de confirmar alterações.", "Evaluate a repair simulation before confirming changes.", "apt-get -s -f install mostra um plano. Revê remoções e não assumes que todas são desejadas.", "apt-get -s -f install shows a plan. Review removals and do not assume all are intended.", "apt-get -s -f install"),
        ), (APT,), families=("debian", "ubuntu", "mint"), component="apt"),
    Procedure("apt-repositories", ("Erro nos repositórios APT", "APT repository error"),
        ("Distingue falha de rede, assinatura e versão de distribuição.", "Distinguish network, signature and distribution-release failures."),
        ("apt", "repository", "repositories", "repositorio", "repositório", "sources.list", "404", "signature"), (
            _step("Identifica a origem e a mensagem exata.", "Identify the source and exact message.", "A configuração pode estar em sources.list ou sources.list.d, incluindo ficheiros .sources. Confirma a versão em os-release.", "Configuration may be in sources.list or sources.list.d, including .sources files. Confirm the release in os-release."),
            _step("Confirma a origem oficial antes de editar.", "Confirm the official source before editing.", "Não desatives verificação de assinaturas para contornar o erro. Guarda a configuração anterior e verifica o resultado.", "Do not disable signature verification to bypass the error. Save previous configuration and verify the result."),
        ), ("https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",), families=("debian", "ubuntu", "mint"), component="apt"),
    Procedure("xbps-errors", ("Erro de pacotes XBPS", "XBPS package error"),
        ("Recolhe o erro e confirma os repositórios da edição instalada.", "Collect the error and confirm repositories for the installed edition."),
        ("xbps", "void", "package", "pacote", "repository", "repositorio", "error", "erro"), (
            _step("Identifica a operação, o pacote e a mensagem de erro.", "Identify the operation, package and error message.", "Confirma glibc ou musl e a arquitetura antes de escolher repositórios ou pacotes.", "Confirm glibc or musl and the architecture before choosing repositories or packages."),
            _step("Compara a configuração local com o Handbook.", "Compare local configuration with the Handbook.", "Os valores de /etc/xbps.d podem substituir os de /usr/share/xbps.d. Não removas a base de dados para resolver um erro desconhecido.", "Values in /etc/xbps.d may override /usr/share/xbps.d. Do not remove the database to resolve an unknown error."),
        ), ("https://docs.voidlinux.org/xbps/troubleshooting/common-issues.html", "https://docs.voidlinux.org/xbps/repositories/index.html"), families=("void",), component="xbps"),
)

PROCEDURE_BY_ID = {item.id: item for item in PROCEDURES}
_STOPWORDS = {"a", "o", "as", "os", "de", "do", "da", "e", "em", "no", "na", "the", "a", "an", "and", "is", "for", "to", "how", "como", "sobre", "about", "guide", "guia", "local", "offline", "knowledge", "conhecimento", "base", "search", "pesquisar", "procurar", "documentacao", "documentation"}


def search_procedures(query: str, distro=None, limit: int = 5) -> Tuple[Procedure, ...]:
    """Deterministic lexical retrieval, without a model or network access."""
    normalized = normalize(query)
    words = set(re.findall(r"[\w-]+", normalized)) - _STOPWORDS
    matches = []
    for item in PROCEDURES:
        if not item.applies_to(distro):
            continue
        score = 0
        for keyword in item.keywords:
            term = normalize(keyword)
            if re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", normalized):
                score += 3 if " " in term else 2
        title_words = set(re.findall(r"[\w-]+", normalize(" ".join(item.title))))
        score += len(words & title_words)
        if score:
            matches.append((score, item.id, item))
    matches.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(entry[2] for entry in matches[:max(0, limit)])


def render_procedure(procedure: Procedure, lang: str = "en", distro=None) -> str:
    """Render all guidance locally; displayed examples are never executed."""
    pt = lang == "pt"
    lines = [f"[{procedure.id}] {localized(procedure.title, lang)}", localized(procedure.summary, lang)]
    for index, step in enumerate(procedure.steps, 1):
        lines.append(f"\n{index}. {localized(step.instruction, lang)}")
        if step.command:
            lines.append("   " + step.command)
        lines.append("   " + localized(step.interpretation, lang))
    lines.append("\n" + localized(procedure.recovery, lang))
    if procedure.component:
        lines.append(("Componente necessário: " if pt else "Required component: ") + procedure.component)
    lines.append(localized(procedure.version_scope, lang))
    if distro is not None and getattr(distro, "version_id", ""):
        lines.append(("Versão identificada: " if pt else "Identified release: ") + distro.version_id)
    if distro is not None and not getattr(distro, "version_id", ""):
        lines.append("Versão da distribuição desconhecida; confirma a aplicabilidade antes de alterar." if pt else "Distribution version is unknown; confirm applicability before making changes.")
    lines.append(("Revisão das fontes: " if pt else "Sources reviewed: ") + procedure.reviewed_at +
                 ("; sem validação nesta máquina." if pt else "; not validated on this machine."))
    lines.append(("Fontes: " if pt else "Sources: ") + " ".join(procedure.sources))
    return "\n".join(lines)


def knowledge_context(query: str, distro=None, lang: str = "en", max_chars: int = 3500) -> str:
    """Relevant bounded reference text for all providers. Never runs probes."""
    if max_chars <= 0:
        return ""
    matches = search_procedures(query, distro, limit=2)
    if not matches:
        return ""
    prefix = ("Referência local revista; exemplos não autorizam execução. Confirma componentes, versão e observações antes de propor alterações.\n" if lang == "pt" else
              "Reviewed local reference; examples do not authorize execution. Confirm components, version and observations before proposing changes.\n")
    return (prefix + "\n\n".join(render_procedure(item, lang, distro) for item in matches))[:max_chars]
