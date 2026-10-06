"""Exercise application-menu installation and Desktop Entry command parsing."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from gi.repository import Gio
except ImportError:
    Gio = None


ROOT = Path(__file__).resolve().parents[1]
ICON_NAME = "io.github.linux_ai_assistant"


class TestDesktopInstallation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="linux-ai-desktop-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.data_home = self.directory / "dados de aplicação"
        self.fake_home = self.directory / "user-home"
        self.fake_home.mkdir()
        self.environment = dict(os.environ, XDG_DATA_HOME=str(self.data_home), HOME=str(self.fake_home))

    def make_project(self, name):
        project = self.directory / name
        (project / "scripts").mkdir(parents=True)
        (project / "assets").mkdir()
        for name in ("install-desktop.sh", "autostart.sh"):
            shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
        shutil.copy2(ROOT / "assets" / (ICON_NAME + ".svg"), project / "assets" / (ICON_NAME + ".svg"))
        shutil.copy2(ROOT / "run.sh", project / "run.sh")
        return project

    def install(self, project):
        return subprocess.run(
            ["bash", str(project / "scripts" / "install-desktop.sh")],
            env=self.environment, capture_output=True, text=True,
        )

    @property
    def desktop(self):
        return self.data_home / "applications" / "linux-ai-assistant.desktop"

    def test_refreshes_only_shortcut_and_icon_preserving_configuration_and_autostart(self):
        configuration = self.fake_home / ".config" / "linux_ai_assistant" / "config.json"
        configuration.parent.mkdir(parents=True)
        configuration.write_text('{"saved": "configuration"}')
        autostart = self.fake_home / ".config" / "autostart" / "linux-ai-assistant.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("[Desktop Entry]\nHidden=true\n")
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text("[Desktop Entry]\nIcon=/missing/old/icon.png\n")
        unrelated = self.desktop.parent / "another-app.desktop"
        unrelated.write_text("another application")

        project = self.make_project("nova pasta Ubuntu ü")
        result = self.install(project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Icon=" + ICON_NAME, self.desktop.read_text())
        icon = self.data_home / "icons" / "hicolor" / "scalable" / "apps" / (ICON_NAME + ".svg")
        self.assertEqual(icon.read_bytes(), (ROOT / "assets" / (ICON_NAME + ".svg")).read_bytes())
        self.assertEqual(icon.stat().st_mode & 0o777, 0o644)
        self.assertEqual(configuration.read_text(), '{"saved": "configuration"}')
        self.assertEqual(autostart.read_text(), "[Desktop Entry]\nHidden=true\n")
        self.assertEqual(unrelated.read_text(), "another application")
        self.assertFalse((project / "venv").exists())
        self.assertEqual(sorted(configuration.parent.iterdir()), [configuration])

    def test_repeating_install_updates_existing_launcher_to_new_checkout(self):
        old = self.make_project("old checkout")
        new = self.make_project("updated checkout")
        self.assertEqual(self.install(old).returncode, 0)
        before = self.desktop.read_text()
        self.assertEqual(self.install(new).returncode, 0)
        after = self.desktop.read_text()
        self.assertIn(str(old / "run.sh"), before)
        self.assertIn(str(new / "run.sh"), after)
        self.assertNotIn(str(old / "run.sh"), after)
        self.assertEqual(list(self.desktop.parent.glob("*.desktop")), [self.desktop])

    def test_missing_bundled_icon_does_not_claim_success_or_replace_launcher(self):
        project = self.make_project("incomplete checkout")
        (project / "assets" / (ICON_NAME + ".svg")).unlink()
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text("existing launcher")
        result = self.install(project)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bundled icon", result.stderr)
        self.assertEqual(self.desktop.read_text(), "existing launcher")
        self.assertFalse((self.data_home / "icons").exists())

    def test_relative_data_home_and_control_character_path_are_rejected(self):
        for project_name, data_home in (("ordinary checkout", "relative/path"),
                                        ("invalid\ncheckout", str(self.data_home))):
            with self.subTest(project_name=project_name):
                project = self.make_project(project_name)
                self.environment["XDG_DATA_HOME"] = data_home
                result = self.install(project)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.desktop.exists())

    @unittest.skipUnless(Gio is not None, "Gio Desktop Entry parser unavailable")
    def test_launches_correct_checkout_with_spaces_unicode_quotes_and_shell_metacharacters(self):
        project = self.make_project('checkout ü "quotes" \\ $USER $(touch INJECTED) `touch BACKTICK` %f ; &')
        interpreter = project / "venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$PWD" "$@" > "${LINUX_AI_DESKTOP_TEST_RESULT}.pending"\n'
            'mv "${LINUX_AI_DESKTOP_TEST_RESULT}.pending" "$LINUX_AI_DESKTOP_TEST_RESULT"\n'
        )
        interpreter.chmod(0o755)
        result = self.install(project)
        self.assertEqual(result.returncode, 0, result.stderr)
        application = Gio.DesktopAppInfo.new_from_filename(str(self.desktop))
        self.assertIsNotNone(application)
        output = self.directory / "launched-arguments"
        with patch.dict(os.environ, {"LINUX_AI_DESKTOP_TEST_RESULT": str(output)}):
            context = Gio.AppLaunchContext()
            context.setenv("LINUX_AI_DESKTOP_TEST_RESULT", str(output))
            self.assertTrue(application.launch([], context))
        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(output.exists(), "Desktop Entry did not launch the bundled run.sh")
        self.assertEqual(output.read_text().splitlines(), [str(project), "-m", "src.app", "--show"])
        self.assertFalse((ROOT / "INJECTED").exists())
        self.assertFalse((ROOT / "BACKTICK").exists())
        self.assertFalse((project / "INJECTED").exists())
        self.assertFalse((project / "BACKTICK").exists())

    def test_autostart_requires_explicit_enable_and_reuses_installed_icon(self):
        project = self.make_project('autostart ü "quotes" $USER %f')
        script = str(project / "scripts" / "autostart.sh")
        autostart = self.fake_home / ".config" / "autostart" / "linux-ai-assistant.desktop"
        result = subprocess.run(["bash", script, "check"], env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(autostart.exists())
        self.assertFalse(self.desktop.exists())
        result = subprocess.run(["bash", script, "enable"], env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        content = autostart.read_text()
        self.assertIn("Icon=" + ICON_NAME, content)
        desktop_exec = next(line for line in self.desktop.read_text().splitlines() if line.startswith("Exec="))
        self.assertIn(desktop_exec[:-len(" --show")], content.splitlines())
        self.assertNotIn(" --show", content)

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "Desktop Entry validator unavailable")
    def test_generated_native_wheel_and_flatpak_desktop_entries_validate(self):
        project = self.make_project('validate ü "quotes" $USER %f')
        result = self.install(project)
        self.assertEqual(result.returncode, 0, result.stderr)
        for desktop in (self.desktop, ROOT / "scripts/linux-ai-assistant.desktop",
                        ROOT / "flatpak/io.github.linux_ai_assistant.desktop"):
            with self.subTest(desktop=desktop):
                result = subprocess.run(["desktop-file-validate", str(desktop)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
