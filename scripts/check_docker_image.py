#!/usr/bin/env python3
"""Smoke-test the CLI without installing GTK: compile, import, and run --help.

Used by the Docker CI job to prove the image's entry point works before
pushing. Runs entirely headless — no display, no GTK bindings required.

Exit code 0 means the smoke test passed.
"""

import os
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict:
    """Minimal environment: PATH + HOME + PYTHONPATH only.

    PYTHONPATH preserves the caller's site-packages: on the CI runner,
    `pip install -r requirements.txt` may land in the user site dir
    (~/.local/lib/pythonX.Y/site-packages), which is NOT on sys.path when
    HOME is overridden. We forward it explicitly. HOME is a throwaway dir
    so the CLI writes its config there, not into a real home.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "LINUX_AI_COMMAND_SANDBOX": "0",
    }
    # Forward the user site-packages dir so pip-installed deps resolve.
    for entry in sys.path:
        if "site-packages" in entry and entry not in env.get("PYTHONPATH", ""):
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = entry if not existing else existing + os.pathsep + entry
    return env


def main() -> int:
    failures = []

    # 1. Byte-compile every runtime source (catches syntax errors early).
    sources = sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "plugins").glob("*.py"))
    for path in sources:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as error:
            failures.append("compile {}: {}".format(path.name, error))
    print("compiled {} source files".format(len(sources)))

    # 2. The CLI entry point must print help and exit 0.
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "--help"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        failures.append("cli --help exited {}: {}".format(result.returncode, result.stderr.strip()))
    elif "Available commands" not in result.stdout:
        failures.append("cli --help did not print the command list")
    else:
        print("cli --help OK")

    # 3. A headless subcommand must work end-to-end (no network, no GTK).
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "config", "list"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        env=_clean_env(),
    )
    if result.returncode != 0:
        failures.append("cli config list exited {}: {}".format(result.returncode, result.stderr.strip()))
    else:
        print("cli config list OK")

    # 4. The benchmark suite must run (fast scale) and complete. Timing
    # thresholds are advisory and hardware-dependent: a shared CI runner
    # can spike a micro-benchmark past its threshold, so this check asserts
    # that the suite RAN and printed a summary, not that every benchmark
    # was "ok". A real regression gate belongs in a dedicated perf job with
    # a stable runner, not in a smoke test.
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "benchmarks", "--scale", "0.05"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        env=_clean_env(),
    )
    if "benchmarks," not in result.stdout:
        failures.append("cli benchmarks did not print a summary (exit {}): {}".format(
            result.returncode, result.stderr.strip()[-500:]))
    else:
        slow_line = [l for l in result.stdout.splitlines() if "benchmarks," in l]
        print("cli benchmarks OK ({})".format(slow_line[0].strip() if slow_line else "no summary"))

    if failures:
        for failure in failures:
            print("FAIL: " + failure, file=sys.stderr)
        return 1
    print("Docker image smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
