# Contributing to Linux AI Assistant

Thanks for helping. This document is the short version: what must be green
before a pull request is ready, and how releases work. Environment setup
(venv with `--system-site-packages`, system GTK packages) is covered in the
[README](README.md#installation) — GTK bindings come from the distro, never
from pip.

## The gates

CI runs these on every push and pull request; run the same ones locally before
opening a PR.

```bash
# Full test suite (headless; GTK-dependent modules skip without a display)
python3 -m unittest discover -s tests -v

# Lint — same config as CI, defined in pyproject.toml
python3 -m ruff check src/ tests/ plugins/ scripts/run_gtk_tests.py

# Byte-compile everything CI byte-compiles
python3 -m py_compile src/*.py tests/*.py plugins/*.py scripts/run_gtk_tests.py

# Type gate — only the modules listed in [tool.mypy] are analyzed
python3 -m mypy --config-file pyproject.toml
```

GTK-touching tests (`tests/test_*gui*`, the capture and feature-interface
suites) need a display. Run them the way CI does:

```bash
xvfb-run -a python3 scripts/run_gtk_tests.py
```

Shell changes are checked with `shellcheck scripts/*.sh run.sh`.

## House style

- **Line length 110**, target Python 3.8. Ruff enables `E`, `F`, `W`, `B`
  (bugbear), `G` (logging format) and `RUF013`. `E501` and `E722` are
  deliberately off — don't re-enable them in a drive-by change.
- **Logging uses lazy formatting** (`logger.info("value: %s", value)`), enforced
  by `G`.
- **Every user-visible string needs a Portuguese entry.** Pass new text through
  `_()` and add the key to `TRANSLATIONS["pt"]` in `src/i18n.py`;
  `tests/test_i18n.py` fails on missing keys, duplicates, or placeholder
  mismatches between a key and its translation.
- **The version lives in one file**: `src/_version.py`. `pyproject.toml` reads
  it via setuptools' `attr:`; never bump a version anywhere else.
- Prefer extracting GTK-free modules over growing `main_window.py` — anything
  that can be tested headless should be. See `docs/analise-2026-10-01.md` for
  the roadmap of remaining extractions.

## Pull requests

1. Fork and branch (`git checkout -b feature/what-it-does`).
2. Keep changes focused; note any pre-existing behaviour you deliberately left
   alone.
3. Add or update tests for behaviour changes — bug fixes get a regression test
   that fails without the fix.
4. Update `CHANGELOG.md` under `[Unreleased]` (Keep a Changelog format:
   `Added` / `Changed` / `Fixed`).

## Releases

Releases are cut from a tag; no manual upload steps.

1. Move the `[Unreleased]` entries in `CHANGELOG.md` to a new
   `## [X.Y.Z] - YYYY-MM-DD` section.
2. Bump `src/_version.py` to `X.Y.Z` — that is the only version edit.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The `Release` workflow then builds the sdist and wheel, verifies the tag,
`src/_version.py` and the built wheel all agree, checks the wheel ships runtime
data and console scripts (`scripts/check_wheel.py`), extracts this version's
CHANGELOG entry as the release notes, and attaches both artifacts to the
GitHub Release.
