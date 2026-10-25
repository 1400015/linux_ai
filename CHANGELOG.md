# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of the version is `src/_version.py`; `pyproject.toml` reads it
via setuptools' `attr:` directive, so a release only bumps that one file.

## [Unreleased]

### Added

- Local REST API (P3.3): `src/local_api.py` is a loopback-only HTTP API
  (binds to `127.0.0.1` only; refuses any non-loopback address) exposing
  12 JSON endpoints: `GET /api/health` (public), `GET /api/stats`,
  `GET /api/sessions`, `GET /api/config`, `GET|POST /api/a11y/contrast`,
  `GET|POST /api/pomodoro`, `POST /api/sync/preview`,
  `POST /api/sync/import`, `GET|POST /api/benchmarks`. All non-public
  routes require a bearer token (`Authorization: Bearer <token>` or
  `X-Linux-AI-Token` header) checked with `secrets.compare_digest`.
  Request bodies are capped at 1 MiB; responses carry
  `X-Content-Type-Options: nosniff` and no CORS headers. Wrong-method
  requests return JSON 405 (PUT/PATCH/DELETE are handled explicitly).
  `LocalAPIServer` supports ephemeral ports (`port=0`) and background
  threads for tests. New CLI command `serve [--host 127.0.0.1]
  [--port N] [--token T]` starts the server and blocks; the token comes
  from `--token`, config `local_api.token`, or is generated with
  `secrets.token_urlsafe(32)` and printed once. New `local_api` config
  section (`enabled`, `port`, `token`) in `ConfigManager`. 63 unit tests
  in `tests/test_local_api.py` cover routing, auth, body limits, security
  headers and every endpoint against a live loopback server.
- Docker image for the headless CLI (P3.2): `docker/Dockerfile` is a
  multi-stage build on `python:3.12-slim` that produces a lean, non-root
  (`assistant`, uid 1000) image running `linux-ai-assistant-cli`. The GTK
  GUI is intentionally excluded (it belongs on the host). The build copies
  only `src/`, `plugins/`, `themes/` and packaging metadata; a
  `docker/.dockerignore` keeps `.git`, venvs, caches, tests and docs out of
  the context. Optional `INSTALL_TESSERACT=1` build arg adds OCR.
  `LINUX_AI_COMMAND_SANDBOX=0` is set because systemd user units are not
  available in a typical container. `docker/README.md` documents build, run,
  volume mounts (config/history persistence), local-LLM usage (Ollama/LM
  Studio via `host.docker.internal`), sync bundles and security notes.
  `docker/docker-compose.yml` provides a ready-made CLI + Ollama stack.
  `scripts/check_docker_image.py` is a headless smoke test (compile all
  sources, `cli --help`, `cli config list`, `cli benchmarks`) run both on
  the host and inside the container by the new `.github/workflows/docker.yml`
  CI workflow (build job + host smoke job, triggered on changes to `src/`,
  `docker/`, `plugins/`, `themes/` or packaging files).
- `src/benchmarks.py` (P3.1): GTK-free micro-benchmark framework for the
  hot pure-Python paths. `run_benchmark()` measures a zero-arg callable
  with `time.perf_counter` using best-of-N repeats (standard noise
  filtering); `run_suite()` collects results into a `BenchmarkSuite`;
  `BenchmarkResult.status` flags benchmarks that exceed an advisory
  per-call threshold as `slow` (never raises). Built-in registry covers
  10 critical paths: `chat_controller.build_request_messages`,
  `StreamCoalescer.add`, `safe_paths.is_within`, `a11y.check_contrast`,
  `a11y.color_name`, `sync_bundle.plan_import`, `sync_bundle.serialize_bundle`,
  `stats_aggregator.aggregate_usage`, `provider_modes.validate_local_url`,
  `provider_modes.model_is_installed`. New `benchmarks [--scale N] [--json]`
  CLI command; exit code 1 when any benchmark is slow, so CI can gate on
  it. 25 new unit tests.
- `src/sync_bundle.py` (P2.5): GTK-free sync bundle for machine-to-machine
  transfer of settings + history. A bundle is a versioned JSON document
  carrying a **whitelisted** subset of config (theme, language, assistance
  mode, pomodoro, context budgets, UI colors, local_llm connection — never
  credentials, encryption keys or plugin lists) and portable conversation
  sessions (messages only; `task`/`diagnostic` runtime state is dropped).
  `build_bundle()` / `serialize_bundle()` export; `parse_bundle()` validates
  (format, version, size cap 8 MiB, forbidden-key rejection);
  `plan_import()` previews changes; `apply_settings()` merges settings
  in-place; `merge_sessions()` merges sessions by ID (update-or-add).
  New `sync export` / `sync import [--preview] [--yes]` CLI commands.
  `HistoryStore.replace_sessions()` applies a merged session list with full
  validation. Import is merge-only and never touches destination credentials.
  57 new unit tests.
- Native local LLM backends (P2.4): `local_llm` now supports **Ollama native
  API** (`ollama-native`: `/api/chat`, `/api/tags`) and **LM Studio**
  (`lmstudio`: `/api/v0/chat`, `/api/v0/models`) alongside the existing
  OpenAI-compatible shims (`ollama`, `openai`). New `src/local_backends.py`
  module owns the native protocol details: URL normalization (native
  backends keep the bare server root, stripping a pasted `/v1`), discovery
  paths, chat paths, payload builders, NDJSON stream parsing with the same
  bounded limits and cancellation as SSE, and usage extraction (Ollama
  `prompt_eval_count`/`eval_count`, LM Studio OpenAI-style `usage`).
  `AIClient._chat_local_llm` / `_stream_local_llm` dispatch to the native
  path; `provider_modes.discover_local_models` delegates discovery;
  `config_manager` validates the new backend identifiers; the Settings UI
  combo lists all four backends. 43 unit tests in `tests/test_local_backends.py`
  + 12 integration tests in `tests/test_provider_modes.py` against real
  loopback HTTP fixtures speaking the native protocols.
- `docs/plugins-api.md`: full plugin API reference — trust model (opt-in via
  `plugins.enabled`), the `register_provider(ai_client)` contract, exact chat
  and stream function signatures, mandatory error handling via
  `AIProviderError` exceptions, image-request support, logging/privacy rules
  (`redact_url`), config integration, a publication checklist and a minimal
  working example. README and `plugins/__init__.py` now point to it.
- `src/a11y.py`: GTK-free accessibility helpers — WCAG 2.1 contrast ratio
  (`contrast_ratio`, `check_contrast` with AA/AAA normal/large levels),
  touch-target sizing (`check_touch_target` against WCAG 2.5.5/2.5.8),
  human-readable color names (`color_name`, e.g. "dark green") for
  tooltips and screen readers, theme color audits (`audit_theme_colors`),
  and accessible-foreground suggestions. 59 new unit tests.
- `src/pomodoro.py`: GTK-free Pomodoro focus timer. `PomodoroTimer` is a
  state machine driven by an injected clock (no threads, no GTK) with
  configurable work/short/long-break durations and cycles before a long
  break. Supports start/pause/resume/skip/reset, phase-transition
  callbacks, JSON state persistence (`save_state`/`load_state`), and
  display helpers (`format_duration`, `format_phase_label`,
  `session_summary`). New `pomodoro` section in the default config
  (disabled by default). 45 new unit tests.
- `src/stats_aggregator.py`: GTK-free usage statistics aggregation.
  `aggregate_usage()` builds a `UsageSummary` from per-provider token data;
  `format_summary_text()` / `format_summary_markdown()` render it for CLI or
  export; `estimate_cost()` projects spend from per-1M-token pricing;
  `daily_breakdown()` produces per-day stats. The Statistics dialog in
  `main_window.py` now uses `aggregate_usage()` and shows a summary line
  (total tokens, provider count, most-used provider with share). 31 new
  unit tests.
- `src/error_presenter.py`: user-friendly, GTK-free error presentation.
  Maps structured exceptions (AI provider, network, HTTP, file, validation)
  to localized, actionable messages via `present_error()`. Never exposes raw
  exception text, URLs or credentials. Includes `PresentedError` dataclass,
  `ErrorCategory` enum, `present_credential_shadow()` and `error_summary()`.
  41 new unit tests.
- `src/i18n_extended.py`: extended ES/FR/DE translations (~140 new keys per
  language, up from 94). Coverage improved from 23% to 44% for each of
  Spanish, French and German. Remaining keys fall back to English.
  Translations are merged at import time via `apply_extended_translations()`.
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
