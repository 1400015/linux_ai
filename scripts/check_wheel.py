"""Assert a built wheel ships everything the application reads at runtime.

setuptools drops `package-data` and `data-files` silently when they are
misconfigured, and no other step of the pipeline installs the wheel, so a
regression here would only surface on a user's machine (missing themes, no
desktop entry, an empty package).

    python scripts/check_wheel.py dist/*.whl

Exits non-zero and lists every missing piece.
"""

import glob
import re
import sys
import zipfile
from pathlib import Path

# Modules and data the code opens by path at runtime.
REQUIRED_MODULES = (
    "src/app.py",
    "src/cli.py",
    "src/main_window.py",
    "src/theme_style.py",
    "src/theme_store.py",
    "src/app_settings_dialogs.py",
    "src/request_preparation.py",
    "src/knowledge_data/linux-core.yaml",
    "src/knowledge_data/schema/",
    "plugins/example_provider.py",
)

# Paths written by [tool.setuptools.data-files]; the wheel prefixes them with
# "<name>-<version>.data/data/", so they are matched by suffix.
REQUIRED_DATA_SUFFIXES = (
    "/share/linux-ai-assistant/themes/dark.json",
    "/share/linux-ai-assistant/themes/light.json",
    "/share/linux-ai-assistant/themes/dracula.json",
    "/share/linux-ai-assistant/themes/solarized-dark.json",
    "/share/applications/linux-ai-assistant.desktop",
    "/share/icons/hicolor/scalable/apps/io.github.linux_ai_assistant.svg",
)

REQUIRED_CONSOLE_SCRIPTS = (
    "linux-ai-assistant = src.app:main",
    "linux-ai-assistant-cli = src.cli:main",
)

MINIMUM_SOURCES = 70


def expected_version():
    """Version from src/_version.py, read by regex to avoid importing src."""
    module = Path(__file__).resolve().parents[1] / "src" / "_version.py"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', module.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def check(wheel_path):
    problems = []
    name = Path(wheel_path).name

    if not glob.glob(wheel_path):
        return ["{}: wheel not found".format(wheel_path)]
    if name.startswith("UNKNOWN-"):
        # pip < 23 with a stale egg-info builds this; it carries no package.
        return ["{}: wheel was built without project metadata".format(name)]

    archive = zipfile.ZipFile(wheel_path)
    names = archive.namelist()

    sources = [entry for entry in names if entry.startswith("src/") and entry.endswith(".py")]
    if len(sources) < MINIMUM_SOURCES:
        problems.append("only {} src modules inside (expected at least {})"
                        .format(len(sources), MINIMUM_SOURCES))

    for required in REQUIRED_MODULES:
        if not any(entry.startswith(required) for entry in names):
            problems.append("missing from the wheel: {}".format(required))

    for suffix in REQUIRED_DATA_SUFFIXES:
        if not any(entry.endswith(suffix) for entry in names):
            problems.append("missing data file: *{}".format(suffix))

    entries = [entry for entry in names if entry.endswith("entry_points.txt")]
    if not entries:
        problems.append("no dist-info/entry_points.txt: the console scripts are missing")
    else:
        declared = archive.read(entries[0]).decode("utf-8")
        for script in REQUIRED_CONSOLE_SCRIPTS:
            if script not in declared:
                problems.append("console script not declared: {}".format(script))

    version = expected_version()
    if version:
        expected = "linux_ai_assistant-{}".format(version)
        if not any(entry.startswith(expected + ".dist-info/") for entry in names):
            built = sorted({entry.split("/")[0] for entry in names
                            if entry.endswith(".dist-info/METADATA")})
            problems.append("wheel version does not match src/_version.py ({}): {}"
                            .format(version, built or "no dist-info"))

    return problems


def main(argv):
    targets = argv[1:]
    if not targets:
        print("usage: python scripts/check_wheel.py WHEEL [WHEEL ...]", file=sys.stderr)
        return 2

    failed = False
    for target in targets:
        problems = check(target)
        if problems:
            failed = True
            print("{}: FAILED".format(target), file=sys.stderr)
            for problem in problems:
                print("  - {}".format(problem), file=sys.stderr)
        else:
            print("{}: ok".format(target))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
