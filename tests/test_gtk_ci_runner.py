"""A dependency/import skip must fail the GTK job, including class skips."""

from types import SimpleNamespace
import unittest

from scripts.run_gtk_tests import EXPECTED_SKIPS, unexpected_skips


class TestGtkCiSkips(unittest.TestCase):
    @staticmethod
    def result(identifier, reason):
        test = SimpleNamespace(id=lambda: identifier)
        return SimpleNamespace(skipped=[(test, reason)])

    def test_gui_class_dependency_skip_is_rejected(self):
        result = self.result('test_trial_dialog.TestTrialDialog',
                             "GTK unavailable: No module named 'yaml'")
        self.assertEqual(len(unexpected_skips(result)), 1)

    def test_only_the_expected_inverse_tests_are_allowed(self):
        for identifier in EXPECTED_SKIPS:
            self.assertEqual(unexpected_skips(self.result(
                identifier, 'GTK is available in this environment')), [])
            self.assertEqual(len(unexpected_skips(self.result(
                identifier, "No module named 'gi'"))), 1)


if __name__ == '__main__':
    unittest.main()
