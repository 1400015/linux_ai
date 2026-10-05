"""Offline knowledge base: distro-specific facts from official wikis/manuals.

The offline assistant uses this to answer "how does THIS distro do X"
questions without any network access. Everything here is curated, stable
knowledge (package manager commands, where configuration lives, log
locations, firewall tooling, service manager specifics) plus links to the
official wikis/handbooks so the user can dig deeper.

Content language: ENGLISH (source). User-facing templates are localized via
``src/i18n.py``; only factual strings (paths, commands, URLs) live here.
Entries are deliberately conservative: well-established facts only, since
they ship offline and cannot be refreshed at runtime.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class DistroKnowledge:
    """Curated facts for one distribution family."""

    family: str
    display_name: str
    # Official documentation
    wiki_name: str
    wiki_url: str
    # MediaWiki-style search URL with a `{query}` placeholder ("" = no search)
    wiki_search_url: str = ""
    docs_urls: Tuple[str, ...] = ()
    summary: str = ""
    # Where things live
    repositories: str = ""
    network: str = ""
    logs: str = ""
    hostname: str = ""
    locale: str = ""
    # Firewall tooling (argv tuples for display)
    firewall_tool: str = ""
    firewall_status: Tuple[Tuple[str, ...], ...] = ()
    firewall_allow: Tuple[str, ...] = ()  # example "allow" command
    # Service manager notes beyond offline_assistant's SERVICE_MANAGERS
    services_note: str = ""
    # What makes this distro different (English bullets)
    distinct: Tuple[str, ...] = ()


def _k(**kwargs) -> DistroKnowledge:
    return DistroKnowledge(**kwargs)


# ---------------------------------------------------------------------------
# Per-family entries (the "wiki, manuals and documents" base)
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE: Dict[str, DistroKnowledge] = {
    "ubuntu": _k(
        family="ubuntu",
        display_name="Ubuntu",
        wiki_name="Ubuntu Server Documentation",
        wiki_url="https://documentation.ubuntu.com/server/",
        wiki_search_url="https://discourse.ubuntu.com/search?q={query}",
        docs_urls=("https://help.ubuntu.com/community/CommunityHelpWiki",
                   "https://manpages.ubuntu.com/",
                   "https://discourse.ubuntu.com/"),
        summary=("Ubuntu is a Debian-based distribution with fixed releases; "
                 "LTS versions get five years of support."),
        repositories=("/etc/apt/sources.list and /etc/apt/sources.list.d/ "
                      "(24.04+ uses the deb822 'ubuntu.sources' file). "
                      "'sudo apt-get update' after edits."),
        network=("netplan: YAML in /etc/netplan/*.yaml, applied with "
                 "'netplan apply' (renderer networkd on servers, "
                 "NetworkManager on desktops). DNS may go through "
                 "systemd-resolved (stub at /etc/resolv.conf)."),
        logs=("systemd journal ('journalctl -xe', per-boot with -b) plus "
              "/var/log/syslog. Installer/cloud-init: /var/log/cloud-init*."),
        hostname="'hostnamectl set-hostname NAME' (writes /etc/hostname).",
        locale=("'localectl set-locale LANG=...' or /etc/default/locale."),
        firewall_tool="ufw (inactive by default)",
        firewall_status=(("ufw", "status", "verbose"),),
        firewall_allow="ufw allow 22/tcp   &&   ufw enable",
        services_note=("systemd: 'systemctl status NAME', units under "
                       "/etc/systemd/system. cloud-init handles first boot."),
        distinct=(
            "LTS releases (even years, April) supported for 5 years.",
            "snap preinstalled ('snap list'); debs via apt.",
            "netplan abstracts network config into YAML.",
            "unattended-upgrades installed by default on servers.",
        ),
    ),
    "mint": _k(
        family="mint",
        display_name="Linux Mint",
        wiki_name="Linux Mint Installation Guide",
        wiki_url="https://linuxmint-installation-guide.readthedocs.io/",
        wiki_search_url="https://forums.linuxmint.com/search.php?keywords={query}",
        docs_urls=("https://community.linuxmint.com/",
                   "https://forums.linuxmint.com/"),
        summary=("Linux Mint is an Ubuntu-LTS-based desktop distribution "
                 "with Cinnamon, MATE or Xfce editions."),
        repositories=("/etc/apt/sources.list.d/official-package-repositories."
                      "list (managed by 'Software Sources')."),
        network="NetworkManager (nmcli / nm-connection-editor).",
        logs=("/var/log/syslog plus the systemd journal ('journalctl -xe')."),
        hostname="'hostnamectl set-hostname NAME'.",
        locale="Language settings GUI, or /etc/default/locale.",
        firewall_tool="ufw (GUI: gufw)",
        firewall_status=(("ufw", "status", "verbose"),),
        firewall_allow="ufw allow 22/tcp",
        services_note="systemd, same as Ubuntu.",
        distinct=(
            "Update Manager with timeshift snapshots for system rollback.",
            "Software Manager GUI (flatpak included by default).",
            "Sticks to Ubuntu LTS bases for stability.",
        ),
    ),
    "arch": _k(
        family="arch",
        display_name="Arch Linux",
        wiki_name="Arch Wiki",
        wiki_url="https://wiki.archlinux.org/",
        wiki_search_url="https://wiki.archlinux.org/index.php?search={query}",
        docs_urls=("https://man.archlinux.org/", "https://archlinux.org/news/"),
        summary=("Arch is a minimalist rolling-release distribution: you "
                 "assemble the system yourself and maintain it."),
        repositories=("/etc/pacman.conf defines repos; mirrors in "
                      "/etc/pacman.d/mirrorlist. 'pacman -Syu' refreshes and "
                      "upgrades - avoid partial upgrades (-Sy alone)."),
        network=("Manual by design: NetworkManager on desktops, iwd or "
                 "wpa_supplicant for Wi-Fi, systemd-networkd for servers - "
                 "the Wiki has a page per tool."),
        logs=("systemd journal only ('journalctl -xe', 'journalctl -b'); no "
              "syslog by default."),
        hostname="/etc/hostname and /etc/hosts (or 'hostnamectl').",
        locale="Uncomment locales in /etc/locale.gen, run 'locale-gen', set "
               "/etc/locale.conf.",
        firewall_tool="none preinstalled (nftables/iptables/ufw via pacman)",
        firewall_status=(("nft", "list", "ruleset"), ("iptables", "-S")),
        firewall_allow="ufw allow 22/tcp   (after 'pacman -S ufw')",
        services_note="systemd; enable units with 'systemctl enable --now'.",
        distinct=(
            "Rolling release: 'pacman -Syu' regularly; read archlinux.org/news "
            "before upgrading.",
            "The Arch Wiki is the de-facto reference for Linux in general.",
            "AUR (Arch User Repository) - community PKGBUILDs, built locally.",
        ),
    ),
    "manjaro": _k(
        family="manjaro",
        display_name="Manjaro Linux",
        wiki_name="Manjaro Wiki",
        wiki_url="https://wiki.manjaro.org/",
        wiki_search_url="https://wiki.manjaro.org/index.php?search={query}",
        docs_urls=("https://forum.manjaro.org/",),
        summary=("Manjaro is an Arch-based distribution focused on being "
                 "usable out of the box, with its own repositories that hold "
                 "packages back briefly for stability testing."),
        repositories=("/etc/pacman.conf and /etc/pacman.d/mirrorlist (Manjaro "
                      "mirrors; 'sudo pacman-mirrors --fasttrack' reorders). "
                      "AUR needs an AUR helper (pamac GUI supports it)."),
        network="NetworkManager by default (nmcli, nmtui).",
        logs="systemd journal ('journalctl -xe').",
        hostname="'hostnamectl set-hostname NAME'.",
        locale="Manjaro Settings Manager > Locale, or /etc/locale.conf.",
        firewall_tool="ufw preconfigured (GUI: gufw or Manjaro Settings)",
        firewall_status=(("ufw", "status", "verbose"),),
        firewall_allow="ufw allow 22/tcp",
        services_note="systemd, same as Arch.",
        distinct=(
            "Holds Arch packages ~2 weeks for extra testing; independent "
            "mirrors (pacman-mirrors).",
            "mhwd auto-configures graphics drivers (free/proprietary).",
            "pamac: friendly GUI/CLI package manager (pacman-compatible).",
        ),
    ),
    "fedora": _k(
        family="fedora",
        display_name="Fedora Linux",
        wiki_name="Fedora Documentation",
        wiki_url="https://docs.fedoraproject.org/",
        wiki_search_url="https://discussion.fedoraproject.org/search?q={query}",
        docs_urls=("https://docs.fedoraproject.org/en-US/quick-docs/",
                   "https://ask.fedoraproject.org/"),
        summary=("Fedora is upstream of Red Hat Enterprise Linux, shipping "
                 "newer software on a ~6-month release cadence."),
        repositories=("/etc/yum.repos.d/*.repo (fedora.repo, "
                      "fedora-updates.repo, RPM Fusion if added)."),
        network="NetworkManager everywhere: 'nmcli', 'nmtui', or GNOME Settings.",
        logs="systemd journal ('journalctl -xe'); audit in /var/log/audit.",
        hostname="'hostnamectl set-hostname NAME'.",
        locale="'localectl set-locale LANG=en_US.UTF-8'.",
        firewall_tool="firewalld (enabled by default)",
        firewall_status=(("firewall-cmd", "--state"),
                         ("firewall-cmd", "--list-all")),
        firewall_allow=("firewall-cmd --permanent --add-service=http "
                        "&& firewall-cmd --reload"),
        services_note="systemd; SELinux runs enforcing by default.",
        distinct=(
            "SELinux enforcing by default - check 'getenforce' when something "
            "is blocked.",
            "firewalld zones instead of raw iptables rules.",
            "First to ship new desktop stacks (Wayland, PipeWire).",
        ),
    ),
    "rhel": _k(
        family="rhel",
        display_name="RHEL family (RHEL/CentOS/Rocky/Alma)",
        wiki_name="Red Hat Documentation",
        wiki_url="https://docs.redhat.com/",
        wiki_search_url="",
        docs_urls=("https://wiki.rockylinux.org/",
                   "https://wiki.almalinux.org/",
                   "https://access.redhat.com/documentation"),
        summary=("The enterprise family: RHEL and its rebuilds (Rocky, Alma, "
                 "CentOS Stream) share dnf, SELinux and firewalld."),
        repositories=("/etc/yum.repos.d/*.repo (Rocky/Alma ship their repos "
                      "there; RHEL uses subscription-manager)."),
        network="NetworkManager ('nmcli', 'nmtui'); cockpit for web admin.",
        logs=("systemd journal ('journalctl -xe') and /var/log/messages; "
              "SELinux denials: 'ausearch -m avc -ts recent'."),
        hostname="'hostnamectl set-hostname NAME'.",
        locale="'localectl set-locale'.",
        firewall_tool="firewalld (enabled by default)",
        firewall_status=(("firewall-cmd", "--list-all"),),
        firewall_allow=("firewall-cmd --permanent --add-port=8443/tcp "
                        "&& firewall-cmd --reload"),
        services_note=("systemd; SELinux enforcing - use 'restorecon' after "
                       "moving files, 'setenforce 0' only to diagnose."),
        distinct=(
            "10-year lifecycle; ABI stability within a major release.",
            "SELinux is central: never blindly disable it.",
            "dnf is yum's successor - both commands exist.",
        ),
    ),
    "opensuse": _k(
        family="opensuse",
        display_name="openSUSE (Leap/Tumbleweed)",
        wiki_name="openSUSE Wiki",
        wiki_url="https://en.opensuse.org/",
        wiki_search_url="https://en.opensuse.org/index.php?search={query}",
        docs_urls=("https://doc.opensuse.org/",),
        summary=("openSUSE ships as Leap (fixed, SLE-derived) and Tumbleweed "
                 "(rolling, tested by openQA), with YaST and snapper built in."),
        repositories=("/etc/zypp/repos.d/*.repo; manage with "
                      "'zypper ar/rr' or YaST > Software Repositories."),
        network=("NetworkManager on desktops, wicked on servers "
                 "(/etc/sysconfig/network/ifcfg-*)."),
        logs="systemd journal ('journalctl -xe'); supportconfig bundles logs.",
        hostname="'hostnamectl set-hostname NAME' (or YaST).",
        locale="'localectl set-locale' or YaST > System > Language.",
        firewall_tool="firewalld",
        firewall_status=(("firewall-cmd", "--list-all"),),
        firewall_allow="firewall-cmd --permanent --add-service=ssh && firewall-cmd --reload",
        services_note=("systemd; snapper snapshots before/after zypper "
                       "transactions on btrfs - roll back from the boot menu."),
        distinct=(
            "YaST: complete ncurses/GUI control panel for almost everything.",
            "snapper + btrfs: transactional rollbacks (Tumbleweed too).",
            "openQA: automated GUI testing of every release.",
        ),
    ),
    "alpine": _k(
        family="alpine",
        display_name="Alpine Linux",
        wiki_name="Alpine Wiki",
        wiki_url="https://wiki.alpinelinux.org/",
        wiki_search_url="https://wiki.alpinelinux.org/wiki/Special:Search?search={query}",
        docs_urls=("https://docs.alpinelinux.org/",),
        summary=("Alpine is a security-oriented, ultra-light distribution "
                 "built on musl libc and busybox - the container favourite."),
        repositories=("/etc/apk/repositories (one mirror line per repo, "
                      "e.g. main/community; enable community explicitly)."),
        network=("ifupdown-ng with /etc/network/interfaces; wpa_supplicant "
                 "for Wi-Fi (setup-interfaces helper exists)."),
        logs=("busybox syslogd writes /var/log/messages (enable 'rc-update "
              "add syslog'); no journald - use 'rc-service' logs per daemon."),
        hostname="/etc/hostname (or 'setup-hostname').",
        locale="musl is locale-simplified; busybox provides the basics.",
        firewall_tool="iptables (awall as policy layer)",
        firewall_status=(("iptables", "-S"), ("iptables", "-L", "-n")),
        firewall_allow="iptables -A INPUT -p tcp --dport 443 -j ACCEPT",
        services_note=("OpenRC: scripts in /etc/init.d/, per-service config "
                       "in /etc/conf.d/, enable with 'rc-update add NAME'."),
        distinct=(
            "musl instead of glibc - some binaries need gcompat.",
            "busybox provides coreutils; initramfs-based diskless mode.",
            "'setup-alpine' configures the whole system interactively.",
        ),
    ),
    "gentoo": _k(
        family="gentoo",
        display_name="Gentoo Linux",
        wiki_name="Gentoo Wiki",
        wiki_url="https://wiki.gentoo.org/",
        wiki_search_url="https://wiki.gentoo.org/index.php?search={query}",
        docs_urls=("https://www.gentoo.org/support/",),
        summary=("Gentoo is a source-based meta-distribution: packages are "
                 "compiled with your own USE-flag choices via Portage."),
        repositories=("/var/db/repos/gentoo (the ebuild tree); repo config in "
                      "/etc/portage/repos.conf/; global settings in "
                      "/etc/portage/make.conf."),
        network=("netifrc (/etc/conf.d/net) or NetworkManager - the Handbook "
                 "covers the choice during install."),
        logs="syslog-ng/metalog optional; journald possible with systemd.",
        hostname="/etc/hostname; 'hostname' also settable at runtime.",
        locale="/etc/locale.gen + 'locale-gen'; /etc/env.d/02locale.",
        firewall_tool="none preinstalled (iptables/nftables)",
        firewall_status=(("iptables", "-S"),),
        firewall_allow="iptables -A INPUT -p tcp --dport 22 -j ACCEPT",
        services_note=("OpenRC by default (/etc/init.d, rc-update); systemd "
                       "is a supported alternative profile."),
        distinct=(
            "Portage compiles everything: USE flags control features.",
            "/etc/portage/package.use per-package options.",
            "The Handbook is meant to be followed end-to-end per profile.",
        ),
    ),
    "generic": _k(
        family="generic",
        display_name="Linux",
        wiki_name="your distribution's official documentation",
        wiki_url="",
        wiki_search_url="",
        docs_urls=("https://www.kernel.org/doc/html/latest/",),
        summary=("Unknown distribution: generic Linux facts apply."),
        repositories="Depends on the package manager (dpkg/apt, rpm/dnf, pacman, ...).",
        network="NetworkManager or systemd-networkd are the common tools.",
        logs=("If systemd: 'journalctl -xe'. Otherwise /var/log/messages or "
              "/var/log/syslog."),
        hostname="/etc/hostname (+ /etc/hosts).",
        locale="/etc/locale.conf or /etc/default/locale.",
        firewall_tool="nftables or iptables",
        firewall_status=(("nft", "list", "ruleset"), ("iptables", "-S")),
        firewall_allow="nft add rule inet filter input tcp dport 22 accept",
        services_note="systemd (systemctl) unless the distro ships runit/OpenRC.",
        distinct=(
            "Check /etc/os-release (ID and ID_LIKE) to identify the family.",
        ),
    ),
}

from .knowledge_loader import available_bundled_modules, legacy_facts
for _module in available_bundled_modules():
    for _facts in _module["facts"]:
        KNOWLEDGE_BASE[_facts["family"]] = DistroKnowledge(**legacy_facts(_facts))

# os-release ID -> knowledge family (most specific wins)
_FAMILY_BY_ID: Dict[str, str] = {
    "void": "void",
    "debian": "debian",
    "ubuntu": "ubuntu",
    "linuxmint": "mint",
    "pop": "ubuntu", "elementary": "ubuntu",
    "raspbian": "debian", "kali": "debian",
    "arch": "arch",
    "manjaro": "manjaro",
    "artix": "arch",
    "fedora": "fedora",
    "rhel": "rhel", "centos": "rhel", "rocky": "rhel", "almalinux": "rhel",
    "ol": "rhel", "centos_stream": "rhel",
    "opensuse": "opensuse", "opensuse-leap": "opensuse",
    "opensuse-tumbleweed": "opensuse", "suse": "opensuse", "sles": "opensuse",
    "alpine": "alpine",
    "gentoo": "gentoo", "funtoo": "gentoo",
}

# The same family resolution supplies both documentation and command templates.
# This describes the distribution convention, not proof that a tool is installed.
PACKAGE_MANAGER_BY_FAMILY = {
    "void": "xbps", "debian": "apt", "ubuntu": "apt", "mint": "apt",
    "arch": "pacman", "manjaro": "pacman", "fedora": "dnf", "rhel": "dnf",
    "opensuse": "zypper", "alpine": "apk",
}


def package_manager_for(distro_id: str, id_like: Tuple[str, ...] = ()) -> str:
    """Return the distro convention; callers verify executable availability."""
    profile = knowledge_for(distro_id, id_like)
    return PACKAGE_MANAGER_BY_FAMILY.get(profile.family if profile else "", "unknown")


_PORTUGUESE_FACTS = {
    "void": {
        "repositories": "/usr/share/xbps.d/ contém os valores de base; /etc/xbps.d/ contém as substituições locais. Confirma a arquitetura e a edição glibc ou musl.",
        "network": "Identifica o gestor ativo: dhcpcd, NetworkManager, iwd ou wpa_supplicant podem fazer parte da configuração. Não deduzas o gestor apenas pela distribuição.",
        "logs": "runit não fornece o journal de systemd. A localização depende do serviço de logging configurado; socklog pode guardar registos em /var/log/socklog/.",
        "hostname": "/etc/hostname é usado no arranque pelos serviços de base do runit.",
        "locale": "/etc/locale.conf; consulta também a secção de locales do Handbook para a edição instalada.",
        "services_note": "Definições runit em /etc/sv/<nome>/; serviços ativados através de ligações em /var/service/. Confirma o gestor realmente em execução.",
        "distinct": ("Void utiliza normalmente runit; as definições de serviços são diretórios em /etc/sv.", "XBPS gere pacotes com xbps-install, xbps-remove e xbps-query.", "Void é uma distribuição de atualização contínua.", "Existem edições glibc e musl; os repositórios devem corresponder à edição instalada."),
    },
    "debian": {
        "repositories": "APT consulta /etc/apt/sources.list e /etc/apt/sources.list.d/; os ficheiros podem usar os formatos .list ou .sources.",
        "network": "A configuração depende do gestor ativo, como NetworkManager, systemd-networkd ou ifupdown (/etc/network/interfaces). Confirma qual controla a interface.",
        "logs": "Com systemd ativo, consulta o journal. /var/log/syslog pode existir se estiver configurado um serviço de syslog.",
        "hostname": "/etc/hostname e entradas relevantes em /etc/hosts.",
        "locale": "Normalmente /etc/default/locale; a geração de locales depende da configuração instalada.",
        "services_note": "systemd é habitual, mas a instalação pode utilizar outro init. Confirma o gestor em execução antes de escolher comandos.",
        "distinct": ("APT é o gestor habitual sobre a base de dados dpkg.", "Confirma a versão instalada antes de escolher repositórios.", "A instalação concreta determina os gestores de rede e serviços."),
    },
    "ubuntu": {
        "repositories": "APT consulta /etc/apt/sources.list e /etc/apt/sources.list.d/, incluindo ficheiros .sources nas versões que usam o formato deb822.",
        "network": "Netplan pode definir a configuração em /etc/netplan/ e delegar em NetworkManager ou systemd-networkd. Confirma o backend da instalação.",
        "logs": "Com systemd ativo, consulta o journal; /var/log/syslog depende do serviço de logging instalado.",
        "hostname": "/etc/hostname e entradas relevantes em /etc/hosts; hostnamectl apenas quando systemd for aplicável.",
        "locale": "Normalmente /etc/default/locale; confirma os locales instalados.",
        "services_note": "systemd é habitual. Contentores ou instalações personalizadas podem exigir comandos diferentes.",
        "distinct": ("Ubuntu utiliza normalmente APT e dpkg.", "Netplan pode configurar a rede; o backend depende da instalação.", "Confirma VERSION_ID para adaptar instruções à versão instalada."),
    },
    "generic": {
        "repositories": "Depende do gestor de pacotes efetivamente instalado.",
        "network": "Identifica o gestor ativo e a interface; não há um único gestor universal em Linux.",
        "logs": "Identifica o serviço de logging e o componente. Journal e ficheiros em /var/log dependem da instalação.",
        "hostname": "Normalmente /etc/hostname e /etc/hosts; confirma a configuração da distribuição.",
        "locale": "Depende da distribuição; podem existir /etc/locale.conf ou /etc/default/locale.",
        "services_note": "Confirma o init e o gestor de serviços em execução; a presença de systemctl ou sv não é prova suficiente.",
        "distinct": ("Consulta ID, ID_LIKE e VERSION_ID em /etc/os-release para identificar a distribuição.",),
    },
}


def knowledge_content(profile: DistroKnowledge, key: str, lang: str = "en"):
    """Localized prose for the first supported procedure families."""
    if lang == "pt":
        facts = _PORTUGUESE_FACTS.get(profile.family)
        if facts is not None and key in facts:
            return facts[key]
    return getattr(profile, key)

# ID_LIKE token -> family (fallback when the ID is unknown)
_FAMILY_BY_LIKE: Dict[str, str] = {
    "debian": "debian", "ubuntu": "ubuntu", "mint": "mint",
    "arch": "arch", "manjaro": "manjaro", "archarm": "arch",
    "fedora": "fedora", "rhel": "rhel", "centos": "rhel",
    "suse": "opensuse", "sled": "opensuse",
    "alpine": "alpine", "void": "void", "gentoo": "gentoo",
}


def knowledge_for(distro_id: str,
                  id_like: Tuple[str, ...] = ()) -> Optional[DistroKnowledge]:
    """Resolve the best-matching profile for an os-release ID/ID_LIKE.

    Exact ID wins over ID_LIKE; unknown distros with no recognizable
    ancestry return None. Empty IDs and "unknown" return the generic profile.
    """
    did = (distro_id or "").strip().lower()
    if did in _FAMILY_BY_ID:
        return KNOWLEDGE_BASE.get(_FAMILY_BY_ID[did])
    for token in id_like:
        family = _FAMILY_BY_LIKE.get((token or "").strip().lower())
        if family and family in KNOWLEDGE_BASE:
            return KNOWLEDGE_BASE[family]
    if did and did != "unknown":
        return None
    return KNOWLEDGE_BASE["generic"]
