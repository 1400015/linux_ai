#!/bin/bash

# Refresh this checkout's application-menu entry and bundled icon only.
# This does not install dependencies, edit configuration or enable autostart.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

python3 - "$PROJECT_DIR" <<'PY'
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ICON_NAME = "io.github.linux_ai_assistant"
project = Path(sys.argv[1])
source_icon = project / "assets" / (ICON_NAME + ".svg")
launcher = project / "run.sh"
data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
if not data_home.is_absolute():
    raise SystemExit("XDG_DATA_HOME must be an absolute path.")
if not source_icon.is_file() or not launcher.is_file():
    raise SystemExit("The bundled icon or run.sh is missing; extract the complete download first.")
bash = shutil.which("bash")
if bash is None:
    raise SystemExit("bash is required to launch this checkout.")


def exec_argument(value):
    # Desktop Entry escaping happens twice: the string value is unescaped,
    # then the quoted Exec argument is parsed. Percent signs are field codes.
    if any(ord(character) < 32 for character in value):
        raise SystemExit("The checkout path contains unsupported control characters.")
    quoted = "".join("\\" + character if character in '\\"`$' else
                     "%%" if character == "%" else character for character in value)
    return '"' + quoted.replace("\\", "\\\\") + '"'


def atomic_write(destination, content, mode):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".linux-ai-", dir=str(destination.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(destination))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


command = exec_argument(str(Path(bash).resolve())) + " " + exec_argument(str(launcher)) + " --show"
desktop = "\n".join((
    "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=Linux AI Assistant",
    "Comment=Permanent AI assistant for Linux", "Exec=" + command, "Icon=" + ICON_NAME,
    "Terminal=false", "Categories=Utility;System;", "StartupWMClass=linux-ai-assistant", "",
))
icon_root = data_home / "icons" / "hicolor"
icon_file = icon_root / "scalable" / "apps" / (ICON_NAME + ".svg")
desktop_file = data_home / "applications" / "linux-ai-assistant.desktop"
atomic_write(icon_file, source_icon.read_bytes(), 0o644)
atomic_write(desktop_file, desktop.encode("utf-8"), 0o755)

for executable, arguments in (
    ("gtk-update-icon-cache", ["-f", "-t", str(icon_root)]),
    ("update-desktop-database", [str(desktop_file.parent)]),
):
    tool = shutil.which(executable)
    if tool is not None:
        subprocess.run([tool] + arguments, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("Application shortcut updated: " + str(desktop_file))
print("Application icon installed: " + str(icon_file))
PY
