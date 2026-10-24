"""Adapters keep the action contract honest: read vs change, user vs pkexec.

These are the two adapters that map a conversational request onto a real
backend call, so their `prepare`/`execute` boundary is what decides whether a
model-supplied payload can reach a privileged service at all.
"""

import unittest
from unittest.mock import Mock

from src.action_adapters import DisplayAdapter, PackageAdapter
from src.display_actions import DisplayMode
from src.package_actions import PackageCandidate


def mode_dict(**overrides):
    mode = {
        "output": "HDMI-1",
        "width": 1920,
        "height": 1080,
        "refresh": 60.0,
        "identifier": "1920x1080@60.000000",
    }
    mode.update(overrides)
    return mode


class PackageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()

    def test_search_is_declared_read_only_and_user_scoped(self):
        adapter = PackageAdapter(self.service, install=False)
        self.assertEqual(adapter.definition.identifier, "packages.search")
        self.assertFalse(adapter.definition.changes_system)
        self.assertEqual(adapter.definition.risk, "read")
        self.assertEqual(adapter.definition.privilege, "user")

    def test_search_accepts_exactly_one_plain_query(self):
        adapter = PackageAdapter(self.service, install=False)
        parameters, resource, risk = adapter.prepare({"query": "vim"})
        self.assertEqual(parameters, {"query": "vim"})
        self.assertEqual(resource, "configured repositories")
        self.assertEqual(risk, "read")

    def test_search_rejects_shape_and_shell_text(self):
        adapter = PackageAdapter(self.service, install=False)
        for parameters in (
            {},
            {"query": "vim", "extra": "1"},
            {"query": ""},
            {"query": "-rf"},
            {"query": "vim; rm -rf /"},
            {"query": "a" * 200},
            {"query": ["vim"]},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                adapter.prepare(parameters)
        self.service.search.assert_not_called()

    def test_search_reports_backend_failure_without_claiming_verification(self):
        adapter = PackageAdapter(self.service, install=False)
        self.service.search.return_value = ([], "apt unavailable")
        result = adapter.execute({"query": "vim"}, is_current=lambda: True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.text, "apt unavailable")
        self.assertFalse(result.verified)
        self.assertFalse(result.ok)

    def test_search_success_carries_the_candidates_as_data(self):
        candidates = [PackageCandidate(name="vim", version="1.0", source="debian")]
        adapter = PackageAdapter(self.service, install=False)
        self.service.search.return_value = (candidates, "")
        result = adapter.execute({"query": "vim"}, is_current=lambda: True)
        self.assertEqual(result.status, "done")
        self.assertEqual(result.data, candidates)
        self.assertTrue(result.verified)
        self.assertTrue(result.ok)

    def test_install_requires_an_exact_repository_candidate(self):
        adapter = PackageAdapter(self.service, install=True)
        for parameters in (
            {"name": "vim"},
            {"name": "vim", "version": "1.0"},
            {"name": "vim", "version": "1.0", "source": "debian", "summary": "x", "extra": "1"},
            {},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                adapter.prepare(parameters)
        self.service.install.assert_not_called()

    def test_install_is_declared_a_change_and_names_the_target(self):
        adapter = PackageAdapter(self.service, install=True)
        parameters = PackageCandidate(name="vim", version="1.0", source="debian").to_dict()
        prepared, resource, risk = adapter.prepare(parameters)
        self.assertEqual(prepared, parameters)
        self.assertEqual(resource, "vim=1.0")
        self.assertEqual(risk, "change")
        self.assertTrue(adapter.definition.changes_system)
        self.assertEqual(adapter.definition.privilege, "pkexec")
        self.assertEqual(adapter.definition.risk, "change")

    def test_install_result_is_verified_only_when_the_transaction_succeeded(self):
        adapter = PackageAdapter(self.service, install=True)
        parameters = PackageCandidate(name="vim", version="1.0", source="debian").to_dict()
        self.service.install.return_value = (True, "installed")
        done = adapter.execute(parameters, is_current=lambda: True)
        self.assertEqual((done.status, done.verified), ("done", True))
        self.service.install.return_value = (False, "dpkg interrupted")
        failed = adapter.execute(parameters, is_current=lambda: True)
        self.assertEqual((failed.status, failed.verified, failed.text),
                         ("failed", False, "dpkg interrupted"))


class DisplayAdapterTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()

    def test_discovery_is_read_only_and_rejects_arguments(self):
        adapter = DisplayAdapter(self.service, apply=False)
        self.assertEqual(adapter.definition.identifier, "display.list_modes")
        self.assertFalse(adapter.definition.changes_system)
        self.assertEqual(adapter.prepare({}), ({}, "current display session", "read"))
        for parameters in ({"output": "HDMI-1"}, {"force": True}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                adapter.prepare(parameters)

    def test_apply_preparation_formats_the_exact_change(self):
        adapter = DisplayAdapter(self.service, apply=True)
        parameters = mode_dict()
        prepared, resource, risk = adapter.prepare(parameters)
        # The prepared payload is the normalised mode, defaults included.
        self.assertEqual(prepared, dict(parameters, scale=1.0, current=False))
        self.assertEqual(resource, "HDMI-1 1920x1080@60")
        self.assertEqual(risk, "change")
        self.assertTrue(adapter.definition.changes_system)
        self.assertEqual(adapter.definition.privilege, "user")
        self.assertIn("15-second", adapter.definition.recovery)

    def test_apply_rejects_an_invalid_mode_before_any_service_call(self):
        adapter = DisplayAdapter(self.service, apply=True)
        for parameters in (
            {},
            {"output": "HDMI-1", "width": 0, "height": 1080, "refresh": 60.0,
             "identifier": "1920x1080@60.000000"},
            {"output": "../../etc", "width": 1920, "height": 1080, "refresh": 60.0,
             "identifier": "1920x1080@60.000000"},
            dict(mode_dict(), width="1920"),
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                adapter.prepare(parameters)
        self.service.apply_mode.assert_not_called()

    def test_apply_result_is_pending_until_visual_confirmation(self):
        adapter = DisplayAdapter(self.service, apply=True)
        change = DisplayMode(**mode_dict())
        self.service.apply_mode.return_value = (change, "")
        pending = adapter.execute(mode_dict(), is_current=lambda: False)
        self.assertEqual(pending.status, "pending")
        self.assertEqual(pending.change, change)
        self.assertFalse(pending.verified)
        self.assertTrue(pending.ok)

        self.service.apply_mode.return_value = (None, "output disconnected")
        failed = adapter.execute(mode_dict(), is_current=lambda: False)
        self.assertEqual((failed.status, failed.ok), ("failed", False))
        self.assertEqual(failed.text, "output disconnected")

    def test_mode_listing_failure_is_not_reported_as_success(self):
        adapter = DisplayAdapter(self.service, apply=False)
        self.service.list_modes.return_value = ([], "xrandr unavailable")
        result = adapter.execute({}, is_current=lambda: True)
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.verified)
        self.service.list_modes.return_value = ([mode_dict()], "")
        listed = adapter.execute({}, is_current=lambda: True)
        self.assertEqual(listed.status, "done")
        self.assertTrue(listed.verified)


if __name__ == "__main__":
    unittest.main()
