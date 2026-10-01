"""Regression tests for installation, configuration and headless startup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.config_manager import ConfigManager
from src.ai_client import AIClient
from src.system_utils import SystemUtils

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for script in [ROOT / "run.sh", *sorted((ROOT / "scripts").glob("*.sh"))]:
            with self.subTest(script=script.name):
                subprocess.run(["bash", "-n", str(script)], check=True)

    def test_generated_launchers_resolve_the_project_at_runtime(self):
        for name in ("install.sh", "install_void.sh"):
            with self.subTest(installer=name), tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "checkout with spaces"
                interpreter = project / "venv" / "bin" / "python"
                interpreter.parent.mkdir(parents=True)
                interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
                interpreter.chmod(0o755)
                text = (ROOT / "scripts" / name).read_text()
                function = text.split("create_run_script() {", 1)[1].split("\n}", 1)[0]
                subprocess.run(
                    ["bash", "-c", 'PROJECT_DIR="$1"\ncreate_run_script() {' + function +
                     '\n}\ncreate_run_script', "test", str(project)],
                    check=True, capture_output=True, text=True,
                )
                result = subprocess.run(
                    [str(project / "run.sh"), "argument with spaces"],
                    cwd="/tmp", check=True, capture_output=True, text=True,
                )
                self.assertEqual(result.stdout.splitlines(),
                                 [str(project), "-m", "src.app", "argument with spaces"])

    def test_package_import_does_not_load_gui(self):
        result = subprocess.run(
            [sys.executable, "-c", "import src,sys; assert 'src.main_window' not in sys.modules"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_documented_dotenv_key_reaches_api_client(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config_path = Path(directory) / "config.json"
            (config_path.parent / ".env").write_text("OPENROUTER_API_KEY=test-key\n")
            config = ConfigManager(str(config_path))
            client = AIClient(config)
            self.assertEqual(client._get_api_key("openrouter"), "test-key")
            client.session.close()

    def test_existing_environment_takes_precedence_over_dotenv(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "process-key"}, clear=True
        ):
            (Path(directory) / ".env").write_text("OPENROUTER_API_KEY=file-key\n")
            config = ConfigManager(str(Path(directory) / "config.json"))
            self.assertEqual(config.get_api_key("openrouter"), "process-key")

    def test_system_utils_without_a_controlling_terminal(self):
        with patch("os.getlogin", side_effect=OSError("no terminal")), patch(
            "getpass.getuser", return_value="desktop-user"
        ):
            utilities = SystemUtils(type("Config", (), {"get": lambda self, key, default: default})())
            self.assertEqual(utilities.username, "desktop-user")


if __name__ == "__main__":
    unittest.main()
