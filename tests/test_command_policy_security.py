"""Regressions for diagnostic commands which also expose mutation/execution."""

import shlex
import unittest
from unittest.mock import patch

from src.command_policy import validate_arguments
from src.system_utils import SystemUtils


class Config:
    def __init__(self, commands):
        self.commands = commands

    def get(self, key, default=None):
        if key == 'permissions.allowed_commands':
            return self.commands
        if key == 'permissions.allowed_edit_dirs':
            return ['/approved']
        return default


class TestDiagnosticCommandPolicy(unittest.TestCase):
    def allowed(self, command):
        return validate_arguments(shlex.split(command), lambda path: path.startswith('/approved/'))

    def test_ip_preserves_common_read_only_queries(self):
        commands = (
            'ip', 'ip help', 'ip -V', 'ip link', 'ip link show',
            'ip -brief link', 'ip -brief addr', 'ip -j -s address show dev eth0',
            'ip -6 route', 'ip -family inet6 route show table main',
            'ip a', 'ip r', 'ip l', 'ip addr show eth0',
            'ip link show master br0', 'ip route show 192.0.2.0/24',
            'ip route show table all', 'ip route get 192.0.2.1',
            'ip route get 2001:db8::1 from 2001:db8::2 oif eth0',
            'ip neigh show dev eth0', 'ip neighbor get 192.0.2.1 dev eth0',
            'ip rule show', 'ip rule list from 192.0.2.0/24',
            'ip maddr show dev eth0', 'ip netconf show dev eth0',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(self.allowed(command))

    def test_ip_blocks_execution_namespaces_and_batch_aliases(self):
        commands = (
            'ip netns exec test-ns bash -c true', 'ip netns e test-ns true',
            'ip netns add test-ns', 'ip -n test-ns link',
            'ip -netns test-ns link show', 'ip -b /approved/commands',
            'ip -batch /approved/commands', 'ip --batch=/approved/commands',
            'ip -ba /approved/commands', 'ip -force -batch /approved/commands',
            'ip vrf exec test-vrf true', 'ip -all netns exec test-ns true',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_ip_rejects_mutation_verbs_and_abbreviations(self):
        commands = (
            'ip link set eth0 down', 'ip link s eth0 down',
            'ip link add dummy0 type dummy', 'ip link a dummy0 type dummy',
            'ip link delete dummy0', 'ip link del dummy0',
            'ip addr add 192.0.2.1/24 dev eth0', 'ip a a 192.0.2.1/24 dev eth0',
            'ip address delete 192.0.2.1/24 dev eth0', 'ip addr flush dev eth0',
            'ip route add default via 192.0.2.1', 'ip r a default via 192.0.2.1',
            'ip route replace default via 192.0.2.1', 'ip route flush table main',
            'ip rule add from 192.0.2.0/24 table 100',
            'ip neigh replace 192.0.2.1 lladdr 00:11:22:33:44:55 dev eth0',
            'ip maddr add 00:11:22:33:44:55 dev eth0',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_ip_rejects_unknown_options_and_path_operands(self):
        for command in ('ip -unknown route', 'ip link nonsense',
                        'ip -family unknown addr', 'ip route get',
                        'ip route get /approved/input', 'ip link show /approved/input',
                        'ip route show table', 'ip route show table --batch',
                        'ip link show type ../../custom-plugin'):
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_ss_preserves_queries_and_filters(self):
        commands = (
            'ss', 'ss -tuln', 'ss -lntu', 'ss -ntap', 'ss --summary',
            'ss -4 -6 --numeric --listening --processes',
            'ss --family=inet', 'ss -f inet6', 'ss -tnfinet',
            'ss -A tcp,udp', 'ss --query=tcp,udp', 'ss --socket=unix',
            'ss -nt state established', 'ss -nt dst 192.0.2.1',
            'ss -x src /approved/socket', 'ss -n state listening --tcp',
            'ss --bpf-maps --inet-sockopt',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(self.allowed(command))

    def test_ss_blocks_socket_killing_in_clusters_and_after_filters(self):
        commands = (
            'ss -K', 'ss -ntK', 'ss -Knt', 'ss --kill', 'ss --kil',
            'ss --kill=all', 'ss -nt state established --kill',
            'ss -A tcp -K', 'ss -fK', 'ss --query=tcp,K',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_ss_blocks_indirect_io_and_namespace_switches(self):
        for command in ('ss -F /approved/input', 'ss --filter=/approved/input',
                        'ss -D /approved/output', 'ss --diag=/approved/output',
                        'ss -nD/approved/output', 'ss -N test-ns',
                        'ss --net=test-ns', 'ss --unknown', 'ss -f unknown',
                        'ss -A tcp,unknown', 'ss -x src /outside/socket'):
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_file_blocks_decompression_special_devices_and_sandbox_override(self):
        commands = (
            'file -z /approved/input', 'file -Z /approved/input',
            'file -bz /approved/input', 'file -bZ /approved/input',
            'file --uncompress /approved/input', 'file --uncompress-noreport /approved/input',
            'file --uncomp /approved/input', 'file -s /approved/input',
            'file -bs /approved/input', 'file --special-files /approved/input',
            'file --special /approved/input', 'file -S /approved/input',
            'file -bS /approved/input', 'file --no-sandbox /approved/input',
            'file --no-san /approved/input', 'file -C /approved/input',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_ifconfig_preserves_queries_but_rejects_network_changes(self):
        for command in ('ifconfig', 'ifconfig -a', 'ifconfig -s',
                        'ifconfig eth0', 'ifconfig -v eth0', 'ifconfig eth0 -s'):
            with self.subTest(command=command):
                self.assertTrue(self.allowed(command))
        for command in ('ifconfig eth0 down', 'ifconfig eth0 up',
                        'ifconfig eth0 192.0.2.1', 'ifconfig eth0 mtu 1500',
                        'ifconfig eth0 hw ether 00:11:22:33:44:55',
                        'ifconfig eth0 add 2001:db8::1/64', 'ifconfig -unknown'):
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))

    def test_file_keeps_apple_and_format_values_with_flag_letters(self):
        for command in ('file --apple /approved/input', 'file -bi /approved/input',
                        'file --mime-type /approved/input',
                        'file -Fz /approved/input', 'file -F -z /approved/input',
                        'file -e compress /approved/input'):
            with self.subTest(command=command):
                self.assertTrue(self.allowed(command))

    def test_execution_engines_cannot_be_added_to_a_diagnostic_allowlist(self):
        commands = (
            'bash -c true', 'sh -c true', 'fish -c true', 'busybox sh -c true',
            'python3.13 -c pass', 'python2.7 -c pass', 'python3.13t -c pass',
            'pypy3 -c pass', 'node -e true', 'lua5.4 -e true',
            'awk BEGIN{}', 'sed -e e /approved/input', 'find /approved -exec true ;',
            'xargs true', 'env bash -c true', 'sudo true', 'pkexec true',
            'timeout 1 bash -c true', 'git -c core.pager=true log',
            'systemd-run true', 'ssh example.invalid true', 'bwrap true',
            '/usr/bin/bash -c true', '/usr/bin/ip netns exec test-ns true',
            '/usr/bin/ss -K', '/usr/bin/file -z /approved/input', './bash -c true',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(self.allowed(command))
        self.assertFalse(validate_arguments([], lambda path: True))
        self.assertFalse(validate_arguments([''], lambda path: True))

    def test_executor_rejects_mutation_before_spawning_even_with_manual_allowlist(self):
        commands = (
            ['ip', 'netns', 'exec', 'test-ns', 'bash', '-c', 'true'],
            ['ip', 'link', 'set', 'eth0', 'down'], ['ss', '-ntK'],
            ['bash', '-c', 'true'], ['/usr/bin/ss', '-K'],
            ['file', '--uncompress', '/approved/input'],
            ['ifconfig', 'eth0', 'down'],
        )
        utils = SystemUtils(Config([argv[0] for argv in commands]))
        with patch('src.system_utils.run_bounded', return_value=(0, 'fixture', '')) as run:
            for command in commands:
                with self.subTest(command=command):
                    self.assertFalse(utils.execute_command(command)[0])
            run.assert_not_called()

    def test_executor_keeps_existing_diagnostic_recipes(self):
        commands = (
            ['ip', 'link', 'show'], ['ip', 'addr', 'show'],
            ['ip', 'route'], ['ip', '-6', 'route'], ['ss', '-tuln'],
        )
        utils = SystemUtils(Config(['ip', 'ss']))
        with patch('src.system_utils.run_bounded', return_value=(0, 'fixture', '')) as run:
            for command in commands:
                with self.subTest(command=command):
                    self.assertEqual(utils.execute_command(command), (True, 'fixture'))
            self.assertEqual(run.call_count, len(commands))


if __name__ == '__main__':
    unittest.main()
