"""Normal GUI shutdown must retain supervision of a recovery preview helper."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    GTK_AVAILABLE = Gtk.init_check()[0]
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK is unavailable')
class TestRecoveryPreviewShutdown(unittest.TestCase):
    def test_normal_exit_reaps_the_bounded_preview_helper_after_dialog_close(self):
        # Use a fresh interpreter so its normal non-daemon-thread shutdown is
        # real. The only child is a local Python sleeper; no authentication or
        # installed privileged helper is involved.
        source = textwrap.dedent('''
            from pathlib import Path
            import sys
            import time
            from types import SimpleNamespace
            from unittest.mock import patch

            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import GLib, Gtk
            from src import file_actions
            from src.change_dialog import ChangeDialog

            assert Gtk.init_check()[0]
            marker = Path(sys.argv[1])
            helper = ('import os, pathlib, sys, time; '
                      'pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); '
                      'time.sleep(2)')
            # _inspect_privileged still uses the production bounded runner.
            patch.object(file_actions, 'file_helper_command',
                         return_value=[sys.executable, '-c', helper, str(marker)]).start()
            patch.object(file_actions, 'PRIVILEGED_TIMEOUT', .3).start()

            record = {'id': 'change', 'path': '/fixture/target', 'status': 'applied',
                      'created_at': '2026-10-05T00:00:00', 'session_id': 'original'}
            def preview(identifier, allowed):
                return file_actions._inspect_privileged('/fixture/target', 'a' * 64, (1, 2))

            window = Gtk.Window()
            window.history_store = SimpleNamespace(active_session_id='original')
            window.config = SimpleNamespace(get=lambda key, default=None: ['/fixture'])
            window.change_journal = SimpleNamespace(
                list_changes=lambda *args, **kwargs: [record], preview_restore=preview)
            dialog = ChangeDialog(window)
            def pump_until(predicate):
                deadline = time.monotonic() + 4
                context = GLib.MainContext.default()
                while not predicate():
                    assert time.monotonic() < deadline, 'Preview helper did not start'
                    while context.pending():
                        context.iteration(False)
                    time.sleep(.002)
            pump_until(lambda: not dialog._busy)
            dialog._restore(None)
            pump_until(marker.exists)
            dialog.destroy()
            window.destroy()
            # Do not join or pump GTK: returning from main must itself retain
            # the watchdog until its deadline, group cleanup and child reap.
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'helper.pid'
            result = subprocess.run([sys.executable, '-c', source, str(marker)],
                                    cwd=str(Path(__file__).resolve().parents[1]),
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            helper_pid = int(marker.read_text())
            self.assertFalse(Path('/proc/{}/stat'.format(helper_pid)).exists(),
                             'The GUI exited while its preview helper was still running')


if __name__ == '__main__':
    unittest.main()
