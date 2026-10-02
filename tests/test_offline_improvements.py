"""Knowledge retrieval and diagnostic workflows without GTK, models or network."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from src.local_knowledge import (PROCEDURES, PROCEDURE_BY_ID, knowledge_context,
                                 render_procedure, search_procedures)
from src.offline_assistant import OfflineAssistant, detect_distro


class ReadUtils:
    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.calls = []

    def execute_command(self, command, timeout=10):
        self.calls.append((command, timeout))
        return self.outputs.get(command, (False, "not allowed"))


def assistant(utils=None, distro="ubuntu", tools=(), version="24.04"):
    return OfflineAssistant(utils, os_release={"ID": distro, "VERSION_ID": version},
                            which=lambda name: "/usr/bin/" + name if name in tools else None,
                            is_systemd_running=False)


class TestOfflineActions(unittest.TestCase):
    def test_negated_or_hypothetical_actions_never_offer_commands(self):
        for text in ("não quero remover nano", "do not install nano", "don't remove nano",
                     "nunca reiniciar serviço nginx", "no quiero desinstalar nano",
                     "se eu instalar nano", "what happens if I remove nano",
                     "ne pas désactiver service nginx", "no reiniciar servicio nginx"):
            with self.subTest(text=text):
                self.assertEqual(assistant().handle(text, "pt").commands, [])

    def test_problem_description_is_not_an_installation_request(self):
        self.assertEqual(assistant().handle("erro ao instalar nano", "pt").commands, [])

    def test_conflicting_actions_require_clarification(self):
        reply = assistant().handle("install htop and remove nano")
        self.assertEqual(reply.commands, [])
        self.assertIn("ambiguous", reply.text)

    def test_multiple_packages_are_one_explicit_argv_list(self):
        for text in ("instalar htop e nano", "install htop, nano", "install htop nano"):
            with self.subTest(text=text):
                commands = assistant().handle(text).commands
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0].argv, ["apt-get", "install", "-y", "htop", "nano"])

    def test_multiple_removals_preserve_each_package(self):
        commands = assistant().handle("remover htop e nano", "pt").commands
        self.assertEqual(commands[0].argv[-2:], ["htop", "nano"])

    def test_invalid_or_extra_text_is_never_silently_discarded(self):
        for text in ("install nano; rm -rf ~", "install nano on Ubuntu",
                     "remove nano --purge", "install nano-", "install nano+",
                     "install nano and update system", "instalar nano e",
                     "install a package", "install " + " ".join("pkg" + str(i) for i in range(21))):
            with self.subTest(text=text):
                self.assertEqual(assistant().handle(text).commands, [])

    def test_ambiguous_service_targets_are_not_ignored(self):
        for text in ("restart service nginx and apache", "ativar serviço nginx e apache",
                     "restart service nginx; rm -rf /tmp/test"):
            with self.subTest(text=text):
                self.assertEqual(assistant().handle(text).commands, [])

    def test_cleaning_does_not_remove_unused_packages_or_blindly_delete_cache(self):
        for distro in ("ubuntu", "void", "fedora"):
            with self.subTest(distro=distro):
                reply = assistant(distro=distro).handle("limpar cache", "pt")
                self.assertNotIn("rm -rf", reply.text)
                for command in reply.commands:
                    self.assertNotIn("autoremove", command.argv)
                    self.assertNotIn("-o", command.argv)


class TestLocalKnowledge(unittest.TestCase):
    def test_twenty_localized_procedures_with_review_metadata(self):
        self.assertEqual(len(PROCEDURES), 20)
        self.assertEqual(len(PROCEDURE_BY_ID), 20)
        for item in PROCEDURES:
            with self.subTest(procedure=item.id):
                self.assertEqual(item.verification, "documentation_review")
                self.assertEqual(item.tested_versions, ())
                self.assertEqual(item.reviewed_at, "2026-10-02")
                self.assertTrue(all(source.startswith("https://") for source in item.sources))
                self.assertEqual(len(item.title), 2)
                self.assertEqual(len(item.summary), 2)
                self.assertGreaterEqual(len(item.steps), 2)
                for step in item.steps:
                    self.assertTrue(all(step.instruction))
                    self.assertTrue(all(step.interpretation))

    def test_search_accepts_portuguese_accents_and_english_keywords(self):
        distro = assistant().distro
        for query in ("memória insuficiente", "out of memory", "DNS", "disco cheio"):
            with self.subTest(query=query):
                self.assertTrue(search_procedures(query, distro))

    def test_unsupported_distribution_does_not_get_apt_or_xbps_instructions(self):
        distro = assistant(distro="nixos").distro
        self.assertFalse(search_procedures("apt dependencies", distro))
        self.assertFalse(search_procedures("xbps repositories", distro))

    def test_void_gets_xbps_and_not_apt(self):
        distro = assistant(distro="void", version="").distro
        self.assertEqual(search_procedures("xbps", distro)[0].id, "xbps-errors")
        self.assertFalse(search_procedures("apt", distro))

    def test_version_restrictions_are_conservative(self):
        procedure = replace(PROCEDURE_BY_ID["apt-repositories"], versions=("24.04",))
        self.assertTrue(procedure.applies_to(assistant(version="24.04").distro))
        self.assertFalse(procedure.applies_to(assistant(version="20.04").distro))
        rendered = render_procedure(procedure, "pt", assistant(version="").distro)
        self.assertIn("Versão da distribuição desconhecida", rendered)

    def test_local_context_never_contacts_network_or_executes_a_probe(self):
        with patch("socket.socket", side_effect=AssertionError("no network")), \
                patch("subprocess.run", side_effect=AssertionError("no process")):
            content = knowledge_context("DNS", assistant().distro, "pt", 350)
        self.assertGreater(len(content), 0)
        self.assertLessEqual(len(content), 350)
        self.assertEqual(knowledge_context("unrelated question", assistant().distro), "")
        self.assertEqual(knowledge_context("DNS", assistant().distro, max_chars=0), "")

    def test_exact_guide_opens_without_privileged_commands(self):
        reply = assistant().handle("guia disk-space", "pt")
        self.assertIn("passo 1/2", reply.text)
        self.assertEqual(reply.commands, [])

    def test_search_only_does_not_execute_read_probes(self):
        utils = ReadUtils()
        bot = assistant(utils, tools=("ip",))
        reply = bot.handle("pesquisar conhecimento rede", "pt")
        self.assertIn("[network-interface]", reply.text)
        self.assertEqual(utils.calls, [])
        self.assertEqual(reply.commands, [])

    def test_search_results_can_be_opened_by_identifier(self):
        reply = assistant().handle("pesquisar conhecimento rede", "pt")
        self.assertIn("Rede", reply.text)
        reply = assistant().handle("guia local", "pt")
        self.assertIn("disk-space", reply.text)
        self.assertIn("apt-lock", reply.text)

    def test_portuguese_target_family_config_is_complete_prose(self):
        for distro in ("void", "debian", "ubuntu"):
            reply = assistant(distro=distro).handle("onde ficam os ficheiros de configuração?", "pt")
            self.assertNotIn("holds the defaults", reply.text)
            self.assertNotIn("NetworkManager or systemd-networkd are", reply.text)
            self.assertIn("Confirma", reply.text)


class TestGuidedDiagnostics(unittest.TestCase):
    def test_diagnostic_snapshot_is_minimal_and_independent(self):
        bot = assistant()
        self.assertIsNone(bot.diagnostic_state())
        bot.handle("guia network-interface", "pt")
        state = bot.diagnostic_state()
        self.assertEqual(state, {"id": "network-interface", "step": 0})
        state["step"] = 1
        self.assertEqual(bot.diagnostic_state()["step"], 0)

    def test_invalid_untrusted_state_clears_old_continuation_without_probes(self):
        utils = ReadUtils()
        bot = assistant(utils, tools=("ip",))
        invalid_states = (None, "network-interface", [], {},
                          {"id": "network-interface"},
                          {"id": "network-interface", "step": 0, "command": "rm -rf ~"},
                          {"id": "rm -rf ~", "step": 0},
                          {"id": ["network-interface"], "step": 0},
                          {"id": "network-interface", "step": True},
                          {"id": "network-interface", "step": "0"},
                          {"id": "network-interface", "step": 0.0},
                          {"id": "network-interface", "step": -1},
                          {"id": "network-interface", "step": 3})
        for state in invalid_states:
            with self.subTest(state=state):
                self.assertTrue(bot.restore_diagnostic({"id": "network-interface", "step": 0}))
                self.assertFalse(bot.restore_diagnostic(state))
                self.assertIsNone(bot.diagnostic_state())
        self.assertEqual(utils.calls, [])

    def test_restore_rejects_procedure_for_another_distribution(self):
        bot = assistant(distro="void")
        self.assertFalse(bot.restore_diagnostic({"id": "apt-lock", "step": 0}))
        self.assertIsNone(bot.diagnostic_state())

    def test_restoring_is_passive_and_next_request_advances(self):
        utils = ReadUtils({"ip addr show": (True, "inet 169.254.1.2/16")})
        bot = assistant(utils, tools=("ip",))
        state = {"id": "network-interface", "step": 0}
        self.assertTrue(bot.restore_diagnostic(state))
        state["step"] = 2
        self.assertEqual(utils.calls, [])
        reply = bot.handle("e depois?", "pt")
        self.assertIn("passo 2/3", reply.text)
        self.assertEqual(bot.diagnostic_state(), {"id": "network-interface", "step": 1})
        self.assertEqual(utils.calls, [("ip addr show", 8)])

    def test_network_continues_with_local_probes_and_no_outbound_checks(self):
        utils = ReadUtils({"ip link show": (True, "2: enp1s0 state DOWN"),
                           "ip addr show": (True, "inet 169.254.1.2/16"),
                           "ip route": (True, "192.168.1.0/24 dev enp1s0")})
        bot = assistant(utils, tools=("ip",))
        first = bot.handle("diagnóstico de rede", "pt")
        self.assertIn("DOWN", first.text)
        second = bot.handle("e depois?", "pt")
        self.assertIn("169.254", second.text)
        third = bot.handle("e depois?", "pt")
        self.assertIn("Não encontrei uma rota default", third.text)
        self.assertEqual(utils.calls, [("ip link show", 8), ("ip addr show", 8), ("ip route", 8)])
        self.assertFalse(first.commands or second.commands or third.commands)

    def test_permission_denial_uses_manual_guidance_without_bypass(self):
        utils = ReadUtils()
        reply = assistant(utils, tools=("ip",)).handle("rede", "pt")
        self.assertIn("não tem uma observação automática", reply.text)
        self.assertEqual(utils.calls, [("ip link show", 8)])
        self.assertEqual(reply.commands, [])

    def test_absent_component_skips_probe(self):
        utils = ReadUtils()
        reply = assistant(utils).handle("rede", "pt")
        self.assertEqual(utils.calls, [])
        self.assertIn("ip -brief link", reply.text)

    def test_pasted_observation_is_interpreted_and_next_step_is_presented(self):
        bot = assistant()
        bot.handle("guia disk-space", "pt")
        reply = bot.handle("/dev/sda1 100G 95G 5G 95% /", "pt")
        self.assertIn("igual ou superior a 90%", reply.text)
        self.assertIn("passo 2/2", reply.text)

    def test_reset_drops_state_and_unknown_followup_does_not_run_probes(self):
        utils = ReadUtils()
        bot = assistant(utils, tools=("ip",))
        bot.handle("rede", "pt")
        bot.reset_conversation()
        reply = bot.handle("e depois?", "pt")
        self.assertIn("modo offline", reply.text)
        self.assertEqual(len(utils.calls), 1)

    def test_cancel_and_topic_change_drop_old_continuation(self):
        bot = assistant()
        bot.handle("rede", "pt")
        self.assertIn("cancelado", bot.handle("cancelar", "pt").text)
        self.assertIn("modo offline", bot.handle("e depois?", "pt").text)
        bot.handle("rede", "pt")
        bot.handle("install nano")
        self.assertIn("modo offline", bot.handle("e depois?", "pt").text)

    def test_probes_retain_bounded_output(self):
        utils = ReadUtils({"ip link show": (True, "a" * 50000)})
        reply = assistant(utils, tools=("ip",)).handle("rede")
        self.assertLess(len(reply.text), 14000)

    def test_detection_records_version_and_installed_components_without_claiming_active(self):
        distro = detect_distro({"ID": "ubuntu", "VERSION_ID": "24.04"},
                              which=lambda name: "/usr/bin/" + name if name in {"apt-get", "nmcli", "systemctl"} else None,
                              is_systemd_running=False)
        self.assertEqual(distro.version_id, "24.04")
        self.assertTrue(distro.package_manager_verified)
        self.assertIn("nmcli", distro.available_tools)
        self.assertFalse(distro.service_manager_verified)


if __name__ == "__main__":
    unittest.main()
