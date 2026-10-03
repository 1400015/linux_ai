"""Run every test with GTK; unavailable GUI classes must not make CI green."""

from pathlib import Path
import sys
import unittest


# These tests intentionally exercise installations without GTK, so the GTK job
# cannot execute them. All other environment/import skips are errors here.
EXPECTED_SKIPS = {
    'test_regressions.TestGracefulGtkFailure.test_app_reports_missing_gtk_clearly',
    'test_regressions.TestGracefulGtkFailure.test_main_window_is_not_imported_without_gtk',
}


def unexpected_skips(result):
    return [(test.id(), reason) for test, reason in result.skipped
            if test.id() not in EXPECTED_SKIPS or reason != 'GTK is available in this environment']


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        if not Gtk.init_check()[0]:
            raise RuntimeError('GTK cannot open the test display')
        # Import failures must fail preflight instead of becoming class skips.
        from src import main_window, trial_dialog, device_dialogs, dock  # noqa: F401
    except (ImportError, ValueError, RuntimeError) as error:
        print('GTK preflight failed: ' + str(error), file=sys.stderr)
        return 1
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    skipped = unexpected_skips(result)
    for identifier, reason in skipped:
        print('Unexpected test skip: {}: {}'.format(identifier, reason), file=sys.stderr)
    return 0 if result.wasSuccessful() and not skipped else 1


if __name__ == '__main__':
    sys.exit(main())
