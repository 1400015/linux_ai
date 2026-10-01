"""Tests for the offline knowledge base (wikis/manuals per distribution)."""

import unittest

from src.knowledge_base import KNOWLEDGE_BASE, knowledge_for
from src.offline_assistant import PKG_MANAGERS, OfflineAssistant


def assistant(distro_id="ubuntu", pretty=None, id_like=""):
    """Deterministic fixture (same contract as test_offline.assistant):
    pinned probes so the host environment (systemd running or not) cannot
    change the detected service manager."""
    return OfflineAssistant(
        None,
        os_release={
            "ID": distro_id,
            "PRETTY_NAME": pretty or distro_id.title(),
            "ID_LIKE": id_like,
        },
        which=lambda name: None,
        is_systemd_running=False,
    )


class TestKnowledgeBaseConsistency(unittest.TestCase):
    """A fact base that ships offline must be internally consistent."""

    def test_every_family_has_official_documentation(self):
        for family, kb in KNOWLEDGE_BASE.items():
            with self.subTest(family=family):
                self.assertTrue(kb.wiki_name, family)
                if family != "generic":
                    self.assertTrue(kb.wiki_url.startswith("https://"), family)
                self.assertTrue(kb.summary, family)

    def test_known_families_map_to_a_supported_package_manager(self):
        # A KB não guarda o gestor de pacotes (isso é do offline_assistant),
        # mas cada família tem de ser compatível com um dos templates.
        family_pkg = {
            "void": "xbps", "debian": "apt", "ubuntu": "apt", "mint": "apt",
            "arch": "pacman", "manjaro": "pacman", "fedora": "dnf",
            "rhel": "dnf", "opensuse": "zypper", "alpine": "apk",
            "gentoo": None,  # portage: sem templates de comando, KB só factos
        }
        for family, pkg in family_pkg.items():
            if pkg is not None:
                self.assertIn(pkg, PKG_MANAGERS, family)

    def test_known_families_have_config_locations(self):
        for family in ("void", "debian", "ubuntu", "arch", "fedora", "alpine"):
            kb = KNOWLEDGE_BASE[family]
            with self.subTest(family=family):
                self.assertTrue(kb.repositories, family)
                self.assertTrue(kb.network, family)
                self.assertTrue(kb.logs, family)
                self.assertTrue(kb.distinct, family)

    def test_urls_are_https(self):
        for family, kb in KNOWLEDGE_BASE.items():
            for url in (kb.wiki_url, *kb.docs_urls):
                if url:
                    self.assertTrue(url.startswith("https://"), url)


class TestKnowledgeResolution(unittest.TestCase):
    def test_exact_ids(self):
        self.assertEqual(knowledge_for("void").family, "void")
        self.assertEqual(knowledge_for("ubuntu").family, "ubuntu")
        self.assertEqual(knowledge_for("linuxmint").family, "mint")
        self.assertEqual(knowledge_for("rocky").family, "rhel")
        self.assertEqual(knowledge_for("opensuse-tumbleweed").family, "opensuse")

    def test_id_like_fallback(self):
        # "asahi" is unknown but declares arch ancestry
        self.assertEqual(knowledge_for("asahi", ("arch",)).family, "arch")
        self.assertEqual(knowledge_for("proxmox", ("debian",)).family, "debian")

    def test_unknown_without_ancestry_is_generic(self):
        self.assertEqual(knowledge_for("unknown").family, "generic")
        self.assertEqual(knowledge_for("").family, "generic")

    def test_named_unknown_has_no_profile(self):
        # A real distro we have no profile for: no facts invented.
        self.assertIsNone(knowledge_for("nixos", ()))

    def test_case_insensitive(self):
        self.assertEqual(knowledge_for("Void").family, "void")


class TestKnowledgeIntents(unittest.TestCase):
    """The offline assistant must answer per-distro, not generically."""

    def test_config_files_per_distro(self):
        ubuntu = assistant(distro_id="ubuntu").handle(
            "where are the configuration files?", "en")
        self.assertIn("/etc/netplan", ubuntu.text)
        void = assistant(distro_id="void").handle(
            "onde ficam os ficheiros de configuração?", "pt")
        self.assertIn("/etc/sv", void.text)  # runit service definitions
        self.assertIn("docs.voidlinux.org", void.text)  # reference footer

    def test_logs_per_distro(self):
        arch = assistant(distro_id="arch").handle("where are the logs?", "en")
        self.assertIn("journalctl", arch.text)
        alpine = assistant(distro_id="alpine").handle("where are the logs?", "en")
        self.assertIn("/var/log/messages", alpine.text)

    def test_repositories_per_distro(self):
        ubuntu = assistant(distro_id="ubuntu").handle(
            "show repositories", "en")
        self.assertIn("/etc/apt/sources.list", ubuntu.text)
        fedora = assistant(distro_id="fedora").handle(
            "where are the repositories configured?", "en")
        self.assertIn("/etc/yum.repos.d", fedora.text)

    def test_docs_with_search_query(self):
        reply = assistant(distro_id="arch").handle(
            "documentation about ufw", "en")
        self.assertIn("wiki.archlinux.org", reply.text)
        self.assertIn("ufw", reply.text)

    def test_docs_pt(self):
        reply = assistant(distro_id="void").handle(
            "documentação sobre xbps", "pt")
        self.assertIn("Void Handbook", reply.text)
        self.assertIn("xbps", reply.text)

    def test_firewall_per_distro(self):
        ubuntu = assistant(distro_id="ubuntu").handle("firewall", "en")
        self.assertIn("ufw", ubuntu.text)
        fedora = assistant(distro_id="fedora").handle("firewall", "en")
        self.assertIn("firewalld", fedora.text)
        void = assistant(distro_id="void").handle("firewall", "en")
        self.assertIn("none preinstalled", void.text)

    def test_distro_reply_includes_notes_and_reference(self):
        reply = assistant(distro_id="void").handle("que distro sou?", "pt")
        self.assertIn("runit", reply.text)
        self.assertIn("Notas sobre Void", reply.text)
        self.assertIn("docs.voidlinux.org", reply.text)

    def test_offline_texts_kb_keys_all_languages(self):
        from src.i18n import OFFLINE_TEXTS
        new_keys = {"reference", "config_files", "logs", "repos", "docs",
                    "docs_search", "firewall_kb", "distro_notes"}
        for lang in ("en", "pt", "es", "fr", "de"):
            with self.subTest(lang=lang):
                self.assertTrue(new_keys <= set(OFFLINE_TEXTS[lang]))


class TestLanguageFallback(unittest.TestCase):
    """Unsupported language -> English, redefinable afterwards."""

    def test_set_language_unsupported_falls_back_to_english(self):
        from src import i18n
        i18n.set_language("it")
        self.assertEqual(i18n.get_language(), "en")
        i18n.set_language("pt-PT")
        self.assertEqual(i18n.get_language(), "pt")
        i18n.set_language("en")

    def test_unsupported_config_falls_back_to_english(self):
        from src import i18n
        import tempfile
        import os
        from src.config_manager import ConfigManager
        d = tempfile.mkdtemp()
        config = ConfigManager(os.path.join(d, "config.json"))
        config.set("app.language", "jp")
        i18n.set_language_from_config(config)
        self.assertEqual(i18n.get_language(), "en")
        # ... e pode ser redefinida para uma suportada
        i18n.set_language("fr", config)
        self.assertEqual(i18n.get_language(), "fr")
        self.assertEqual(config.get("app.language"), "fr")
        i18n.set_language("en", config)

    def test_offline_reply_respects_language_and_falls_back(self):
        # es has only the new KB keys; existing keys fall back to English
        reply = assistant(distro_id="ubuntu").handle(
            "where are the configuration files?", "es")
        self.assertIn("/etc/netplan", reply.text)
        self.assertTrue(reply.text)


if __name__ == "__main__":
    unittest.main()
