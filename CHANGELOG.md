# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of the version is `src/_version.py`; `pyproject.toml` reads it
via setuptools' `attr:` directive, so a release only bumps that one file.

## [Unreleased]

### Added

- `src/chat_controller.py`: GTK-free chat orchestration extracted from
  `main_window.py` — message building (`build_request_messages`,
  `build_context_message`, `build_full_history`), stream coalescing
  (`StreamCoalescer`), routing (`ChatRoute`, `select_route`,
  `should_use_offline`) and credential-shadow detection
  (`check_credential_shadow`). 45 new unit tests. `main_window.py` now
  delegates to these helpers, shrinking by ~90 lines.
- `src/safe_paths.py`: centralized, GTK-free path validation (expand/realpath,
  `commonpath` containment, sensitive-path detection, `safe_join`). Replaces
  the duplicated containment checks in `file_actions`, `system_utils` and
  `inspection_actions` with one tested owner. 45 new unit tests.
- Mypy coverage extended from 4 to 9 modules: `command_policy`, `bounded_http`,
  `history_store`, `ai_client` and `safe_paths` now pass the type gate with
  full type annotations.
- Command sandbox (`src/command_sandbox.py`): confirmed catalog commands run
  inside a transient systemd user unit with `PrivateTmp=yes` and a
  `RuntimeMaxSec` bound, keeping stdio, environment and working directory.
  Without a systemd user session (Void/runit, containers, CI) the canary
  probe fails once and commands run exactly as before; a systemd-run
  startup failure after a successful probe disables the wrapper and
  retries bare once, since nothing had run. Privileged commands are never
  wrapped — they already go through pkexec.
- An `ocr` optional extra: `pytesseract` is no longer a hard runtime
  dependency. Without it the OCR path calls the system `tesseract` binary
  directly, which was already the fallback.
- `RequestState` (`src/request_state.py`): the per-request lifecycle — id
  sequence, cancel events and the `is_loading`/`streaming` flags — extracted
  from `MainWindow`. The transitions are GTK-free functions over the five-name
  contract, so they run on a real window and on the `SimpleNamespace` stand-ins
  the GUI tests bind them onto. Fifteen headless tests pin the invariants.
- `theme_store.py`: the single owner of theme names, paths, writes and
  deletion, replacing the copy that had drifted between the window and
  `ConfigManager`. Tests pin the behaviour both depended on, including that a
  bundled theme added later stays undeletable.
- A catalog contract test (`tests/test_i18n.py`): every text passed to `_()`
  needs a Portuguese entry, no key may be defined twice or left unreferenced,
  and `{placeholders}` must match between a key and its translation in every
  language.
- A `package` CI job that builds the sdist and wheel and asserts the wheel
  ships runtime data and both console scripts (`scripts/check_wheel.py`).
- A non-blocking `audit` CI job running `pip-audit` against `requirements.txt`,
  and Dependabot configuration for pip and GitHub Actions.

### Changed

- `src/ai_client.py`: the two halves of the provider request contract now
  have a single owner each. `_openai_style_payload`/`_openai_style_headers`
  replace the body and auth headers copied across the eight OpenAI-compatible
  `_chat_*`/`_stream_*` methods, `_stream_sse` replaces the identical
  bounded-iterate/close/usage/error frame copied across five streaming
  methods, and the Cohere and Anthropic request bodies are shared between
  their streaming and non-streaming paths. The exact per-provider headers
  and the source-level contracts the tests pin are unchanged.
- `src/i18n.py`: the five trailing `TRANSLATIONS["pt"].update(...)` fragments
  are merged into the main catalog literal. The Portuguese catalog is now
  complete (414 keys); Spanish, French and German remain partial with an
  English fallback. Eight dead keys were removed from every catalog.
- `pyproject.toml` enables the ruff `B` (bugbear), `G` (logging format) and
  `RUF013` rule sets, after the existing occurrences were corrected.

### Fixed

- Two test modules imported the theme sanitisers from `src.main_window`,
  which stopped re-exporting them in an earlier refactor: with GTK present
  the imports failed and became unexpected skips, failing the CI `gtk` job.
  They now import from `src.theme_style`, and the sanitisation tests run
  headless instead of silently skipping without GTK.
- `test_id_like_is_used_as_fallback` exercised no fallback: `linuxmint` is
  mapped directly and the assertion accepted either of two distro managers.
  It now pins an unknown ID resolving through `ID_LIKE`, plus a case where a
  known ID wins over `ID_LIKE`.
- `cli config set` without a key and value printed an error but exited 0,
  so `config set && ...` chains treated a failed write as a success; the
  command now exits 1 (the last open case of P1 #16, CLI exit codes).
- The "Manage Themes" button jumped to the wrong settings page after an
  Assistance page was inserted; it now resolves the page by title instead of a
  hard-coded index.
- The tray icon no longer skips building its menu when the icon image fails to
  load.
- Sixteen credential-storage strings had no Portuguese translation and fell
  back to English in the settings dialog.

## [1.4.2] - 2026-10-06

### Fixed

- Guard credential warnings against an empty `.env` override that shadows a
  stored key.
- Surface stored keys disabled by an empty `.env` variable directly in chat.
- Ship the desktop icon and correct Ubuntu diagnostics and shutdown handling.
- Apply online credentials and select models from the settings dialog.
- Capture Wayland screenshots through the desktop portal.
- Make GTK controls readable and show the document dialog contents.

## [1.4.1] - 2026-10-05

### Fixed

- Recover GUI conversation history from a private backup instead of losing it.
- Supervise approved file operations so a cancelled request cannot leave a
  half-written file.

## [1.4.0] - 2026-10-05

### Added

- Reviewed conversations: browse, rename, archive, export and import sessions.
- Image attachment for the next question, with an explicit review step before
  any pixels leave the machine.
- Global shortcuts to open the assistant (X11 and the desktop portal).
- Local document context: index files you choose and review matching excerpts
  before they are sent to a model.

## [1.3.3] - 2026-10-04

### Fixed

- Safe retries when writing token-usage statistics, so a busy or read-only file
  no longer loses the count.

## [1.3.2] - 2026-10-03

### Fixed

- Trial provenance recording, so an exported case carries the build it was
  run against.
- Safe cleanup of exited child processes, compatible with Python 3.8.

## [1.3.1] - 2026-10-02

### Changed

- Confirmed catalog actions now run after the reply is shown, including when a
  model is online, instead of only in offline mode.

## [1.3.0] - 2026-10-02

### Added

- Local diagnostic reports: describe a symptom, collect bounded local read
  checks and export the result. Reports stay on the machine.
- Guarded file recovery, with a review step and a recovery copy before any
  write.

## [1.2.0] - 2026-10-02

### Added

- Offline guides per distribution, with review dates and applicability
  metadata.
- Named conversation sessions, so a long-running thread can be resumed.
- Local AI modes, including a strict loopback server and local models.

## [1.1.0] - 2026-10-01

### Security

- Command allowlist hardening: actions are validated against a per-action
  policy rather than by name.
- Streaming fixes so a cancelled request cannot keep writing to the chat.
- Typed provider errors, so a failure is reported by cause instead of by
  string-matching an HTTP body.
- Structural refactors that made the invariants above testable.

[Unreleased]: https://github.com/1400015/linux_ai/compare/v1.4.2...HEAD
[1.4.2]: https://github.com/1400015/linux_ai/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/1400015/linux_ai/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/1400015/linux_ai/compare/v1.3.3...v1.4.0
[1.3.3]: https://github.com/1400015/linux_ai/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/1400015/linux_ai/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/1400015/linux_ai/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/1400015/linux_ai/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/1400015/linux_ai/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/1400015/linux_ai/releases/tag/v1.1.0
