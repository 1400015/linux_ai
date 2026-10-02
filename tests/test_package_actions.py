"""Repository candidates and confirmed software transactions, without installs."""

import os
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.package_actions import MAX_OUTPUT_BYTES, PackageCandidate, PackageService, _run


def which(name):
    return os.path.abspath(os.path.join(os.sep, "usr", "bin", name))


class RepositoryRunner:
    """Small package database with realistic CLI output and separate install state."""

    def __init__(self, manager="apt"):
        self.manager = manager
        self.records = {
            "nano": {"version": "8.3-1" if manager == "apt" else "8.3_1",
                     "source": "https://deb.example/debian stable/main amd64" if manager == "apt"
                     else "https://repo.example/current", "summary": "Small, friendly text editor"},
        }
        self.search_text = "nano - Small, friendly text editor\n" if manager == "apt" else "[-] nano-8.3_1 Small, friendly text editor\n"
        self.installed = {}
        self.calls = []
        self.install_ok = True
        self.verify_install = True
        self.install_status = "install ok installed"
        self.on_lookup = None

    def __call__(self, argv, timeout=20):
        self.calls.append((argv, timeout))
        tool = os.path.basename(argv[0])
        if tool == "pkexec":
            if not self.install_ok:
                return False, "Authentication cancelled"
            selected = argv[-1]
            if self.manager == "apt":
                name, version = selected.split("=", 1)
            else:
                name, version = selected.rsplit("-", 1)
            if self.verify_install:
                self.installed[name] = version
            return True, "Package transaction completed"
        if "search" in argv or "-s" in argv:
            return True, self.search_text
        name = argv[-1].split("=", 1)[0]
        if tool == "dpkg-query":
            if name not in self.installed:
                return False, "No packages found matching " + name
            return True, f"{name}\t{self.install_status}\t{self.installed[name]}\n"
        repository = tool == "apt-cache" or "-R" in argv
        record = self.records.get(name)
        if not record:
            return False, "Package not found"
        if repository and self.on_lookup:
            self.on_lookup()
        if tool == "apt-cache" and "policy" in argv:
            return True, (f"{name}:\n  Installed: (none)\n  Candidate: {record['version']}\n"
                          f"  Version table:\n     {record['version']} 500\n"
                          f"        500 {record['source']} Packages\n")
        if tool == "apt-cache":
            return True, (f"Package: {name}\nVersion: {record['version']}\n"
                          f"Description: {record['summary']}\n Extended description\n")
        version = record["version"] if repository else self.installed.get(name)
        if not version:
            return False, "Package not installed"
        return True, (f"pkgver: {name}-{version}\nrepository: {record['source']}\n"
                      f"short_desc: {record['summary']}\n"
                      + ("state: installed\n" if not repository else ""))

    def service(self):
        return PackageService(self.manager, runner=self, which=which)

    def mutations(self):
        return [argv for argv, _ in self.calls if os.path.basename(argv[0]) == "pkexec"]


class TestPackageCandidates(unittest.TestCase):
    def test_serialization_rejects_unknown_fields_and_command_like_names(self):
        item = PackageCandidate("nano", "8.3-1", "https://repo.example/main", "Editor")
        self.assertEqual(PackageCandidate.from_dict(item.to_dict()), item)
        for value in ({"name": "nano", "argv": ["sh"]}, {"name": "-oAPT::X=1"},
                      {"name": "nano;id"}, {"name": "nano-"},
                      {"name": "nano", "source": "repo\nRun this"},
                      {"name": "nano", "version": None}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PackageCandidate.from_dict(value)

    def test_apt_search_reports_name_candidate_version_and_actual_repository(self):
        runner = RepositoryRunner()
        items, error = runner.service().search("text editor")
        self.assertEqual(error, "")
        self.assertEqual(items, [PackageCandidate("nano", "8.3-1",
                                                 "https://deb.example/debian stable/main amd64",
                                                 "Small, friendly text editor")])
        self.assertEqual(runner.calls[0][0], ["apt-cache", "search", "--", "text editor"])
        self.assertFalse(runner.mutations())

    def test_xbps_search_checks_repo_identity_instead_of_trusting_search_line(self):
        runner = RepositoryRunner("xbps")
        runner.search_text += "[*] nano-8.2_1 Duplicate\n[-] bogus malformed\n"
        items, error = runner.service().search("nano")
        self.assertEqual(error, "")
        self.assertEqual(items[0].to_dict(), {
            "name": "nano", "version": "8.3_1", "source": "https://repo.example/current",
            "summary": "Small, friendly text editor",
        })
        self.assertEqual(len(items), 1)
        self.assertFalse(runner.mutations())

    def test_literal_ere_escaping_and_unsafe_query_rejection(self):
        for manager in ("apt", "xbps"):
            runner = RepositoryRunner(manager)
            runner.search_text = ""
            service = runner.service()
            self.assertEqual(service.search("C++ editor [3]"), ([], ""))
            self.assertEqual(runner.calls[0][0][-1], r"C\+\+ editor \[3\]")
            for query in ("--allow-unauthenticated", "nano; id", "$(id)", "a\nb", "", "x" * 129):
                before = len(runner.calls)
                items, error = service.search(query)
                self.assertEqual(items, [])
                self.assertTrue(error)
                self.assertEqual(len(runner.calls), before)

    def test_exact_package_is_presented_before_similar_library(self):
        runner = RepositoryRunner()
        runner.records["libnano"] = dict(runner.records["nano"])
        runner.search_text = "libnano - Library\nnano - Editor\n"
        items, error = runner.service().search("nano")
        self.assertEqual(error, "")
        self.assertEqual([item.name for item in items], ["nano", "libnano"])

    def test_virtual_or_installed_only_packages_are_not_install_candidates(self):
        def runner(argv, timeout=20):
            if "search" in argv:
                return True, "virtual - Virtual package\nlocal - Installed local package\n"
            name = argv[-1]
            if name == "virtual":
                return True, "virtual:\n  Candidate: (none)\n  Version table:\n"
            return True, ("local:\n  Candidate: 1.0\n  Version table:\n"
                          " *** 1.0 100\n        100 /var/lib/dpkg/status\n")
        items, error = PackageService("apt", runner, which).search("package")
        self.assertEqual(items, [])
        self.assertIn("verified", error)

    def test_identity_mismatch_and_control_metadata_do_not_reach_installer(self):
        runner = RepositoryRunner("xbps")
        original = runner.__call__

        def malformed(argv, timeout=20):
            if "-R" in argv and "-s" not in argv:
                return True, "pkgver: different-1.0_1\nrepository: https://repo.example\n"
            return original(argv, timeout)

        items, error = PackageService("xbps", malformed, which).search("nano")
        self.assertEqual(items, [])
        self.assertTrue(error)
        self.assertFalse(runner.mutations())

    def test_metadata_lookup_is_limited_to_twelve_candidates(self):
        runner = RepositoryRunner()
        runner.records = {f"editor{index:02}": dict(runner.records["nano"]) for index in range(50)}
        runner.search_text = "\n".join(name + " - Editor" for name in runner.records)
        items, error = runner.service().search("editor")
        self.assertEqual(error, "")
        self.assertEqual(len(items), 12)
        self.assertEqual(sum("policy" in argv for argv, _ in runner.calls), 12)

    def test_missing_tool_and_unsupported_manager_do_not_run_commands(self):
        calls = []
        def runner(argv, timeout=20):
            calls.append(argv)
            return True, ""
        for service in (PackageService("dnf", runner, which), PackageService("apt", runner, lambda name: None)):
            items, error = service.search("nano")
            self.assertEqual(items, [])
            self.assertTrue(error)
        self.assertFalse(calls)

    def test_search_failures_and_oversized_output_are_explicit(self):
        for result in ((False, "Cache unavailable"), (True, "x" * (MAX_OUTPUT_BYTES + 1))):
            service = PackageService("apt", lambda argv, timeout=20: result, which)
            items, error = service.search("nano")
            self.assertEqual(items, [])
            self.assertTrue(error)


class TestPackageInstallation(unittest.TestCase):
    def test_package_with_plus_suffix_remains_a_pinned_repository_install(self):
        runner = RepositoryRunner()
        runner.records['g++'] = dict(runner.records['nano'])
        runner.search_text = 'g++ - GNU C++ compiler'
        choices, error = runner.service().search('g++')
        self.assertEqual(error, '')
        self.assertEqual(choices[0].name, 'g++')
        self.assertTrue(runner.service().install(choices[0])[0])
        self.assertEqual(runner.mutations()[0][-1], 'g++=' + choices[0].version)

    def candidate(self, runner):
        return runner.service().search("nano")[0][0]

    def test_exact_version_installation_is_verified_for_apt_and_xbps(self):
        for manager in ("apt", "xbps"):
            runner = RepositoryRunner(manager)
            candidate = self.candidate(runner)
            ok, text = runner.service().install(candidate)
            self.assertTrue(ok)
            self.assertIn("Installed and verified", text)
            argv = runner.mutations()[0]
            self.assertEqual(argv[:2], [which("pkexec"), which("apt-get" if manager == "apt" else "xbps-install")])
            self.assertEqual(argv[-1], "nano=8.3-1" if manager == "apt" else "nano-8.3_1")
            self.assertNotIn("-S", argv)
            if manager == "apt":
                self.assertIn("--no-remove", argv)
            self.assertEqual(len(runner.mutations()), 1)

    def test_stale_version_or_repository_never_executes_mutation(self):
        for manager in ("apt", "xbps"):
            for field in ("version", "source"):
                runner = RepositoryRunner(manager)
                candidate = self.candidate(runner)
                runner.records["nano"][field] = ("9.0-1" if manager == "apt" else "9.0_1") if field == "version" else "https://other.example/repo"
                ok, text = runner.service().install(candidate)
                self.assertFalse(ok)
                self.assertIn("changed", text)
                self.assertFalse(runner.mutations())

    def test_cancel_during_repository_lookup_prevents_installation(self):
        runner = RepositoryRunner()
        candidate = self.candidate(runner)
        active = [True]
        runner.on_lookup = lambda: active.__setitem__(0, False)
        ok, text = runner.service().install(candidate, is_current=lambda: active[0])
        self.assertFalse(ok)
        self.assertIn("cancelled", text)
        self.assertFalse(runner.mutations())

    def test_cancel_during_installed_probe_prevents_installation(self):
        runner = RepositoryRunner()
        candidate = self.candidate(runner)
        active = [True]
        original = runner.__call__

        def probe(argv, timeout=20):
            result = original(argv, timeout)
            if argv[0] == "dpkg-query":
                active[0] = False
            return result

        ok, text = PackageService("apt", probe, which).install(candidate, lambda: active[0])
        self.assertFalse(ok)
        self.assertIn("cancelled", text)
        self.assertFalse(runner.mutations())

    def test_success_exit_without_fully_installed_package_is_failure(self):
        for manager in ("apt", "xbps"):
            runner = RepositoryRunner(manager)
            candidate = self.candidate(runner)
            runner.verify_install = False
            ok, text = runner.service().install(candidate)
            self.assertFalse(ok)
            self.assertIn("not fully installed", text)

    def test_partial_dpkg_configuration_is_not_reported_as_installed(self):
        runner = RepositoryRunner()
        candidate = self.candidate(runner)
        runner.install_status = "install ok unpacked"
        ok, text = runner.service().install(candidate)
        self.assertFalse(ok)
        self.assertIn("not fully installed", text)

    def test_authentication_failure_preserves_failure_without_false_success(self):
        runner = RepositoryRunner()
        candidate = self.candidate(runner)
        runner.install_ok = False
        self.assertEqual(runner.service().install(candidate), (False, "Authentication cancelled"))

    def test_already_installed_exact_version_does_not_start_pkexec(self):
        runner = RepositoryRunner()
        candidate = self.candidate(runner)
        runner.installed["nano"] = candidate.version
        ok, text = runner.service().install(candidate)
        self.assertTrue(ok)
        self.assertIn("already installed", text)
        self.assertFalse(runner.mutations())

    def test_unverified_candidate_and_missing_auth_tool_are_rejected(self):
        runner = RepositoryRunner()
        self.assertFalse(runner.service().install(PackageCandidate("nano"))[0])
        candidate = self.candidate(runner)
        service = PackageService("apt", runner, lambda name: None if name == "pkexec" else which(name))
        self.assertFalse(service.install(candidate)[0])
        self.assertFalse(runner.mutations())


class TestBoundedPackageRunner(unittest.TestCase):
    def test_default_runner_forces_c_locale_no_shell_and_disables_stdin(self):
        captured = {}

        def run(argv, **kwargs):
            captured.update(kwargs)
            captured["argv"] = argv
            kwargs["stdout"].write(b"candidate output\n")
            return SimpleNamespace(returncode=0)

        with patch("src.package_actions.subprocess.run", side_effect=run):
            self.assertEqual(_run(["apt-cache", "search", "--", "nano"], 7), (True, "candidate output"))
        self.assertEqual(captured["env"]["LC_ALL"], "C")
        self.assertEqual(captured["env"]["NO_COLOR"], "1")
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["stdin"], subprocess.DEVNULL)
        self.assertNotIn("shell", captured)

    def test_default_runner_handles_timeout_and_output_limit(self):
        with patch("src.package_actions.subprocess.run", side_effect=subprocess.TimeoutExpired(["apt-cache"], 1)):
            ok, text = _run(["apt-cache"], 1)
            self.assertFalse(ok)
            self.assertIn("timed out", text)

        def noisy(argv, **kwargs):
            kwargs["stdout"].write(b"x" * (MAX_OUTPUT_BYTES + 1))
            return SimpleNamespace(returncode=0)

        with patch("src.package_actions.subprocess.run", side_effect=noisy):
            ok, text = _run(["apt-cache"], 1)
            self.assertFalse(ok)
            self.assertIn("size limit", text)


if __name__ == "__main__":
    unittest.main()
