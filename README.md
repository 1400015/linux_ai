# Linux AI Assistant

A permanent AI assistant for Linux with a floating interface, integration with several AI APIs, screen capture, expert mode and more.

[![Void Linux](https://img.shields.io/badge/Void%20Linux-Compatible-green)](https://voidlinux.org)
[![d77void](https://img.shields.io/badge/d77void-Supported-blue)](https://d77void.sourceforge.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

- ✅ **Transparent floating interface** - Permanent window with black backgrounds and configurable transparency
- ✅ **Multi-API integration** - Support for OpenRouter, Google AI Studio and local models
- ✅ **Credential storage** - Stored-key editing, explicit environment-key copying and optional Linux Secret Service storage
- ✅ **Remote model selection** - Manual model IDs and explicit model listing on supported provider endpoints
- ✅ **Screen capture with OCR** - Captures the screen and extracts text automatically
- ✅ **Expert Mode** - Assistant specialized in Linux systems
- ✅ **File editing** - Edit configuration files with authorization
- ✅ **Command execution** - Run system commands with controlled permissions
- ✅ **Conversation sessions** - Independent context, resume, archive, search, Markdown/JSON export and reviewed import
- ✅ **Image questions** - Review an image before sending it with one question to a supported OpenRouter model
- ✅ **Global shortcut** - Optional desktop shortcut to show or focus the assistant
- ✅ **Local document search** - Search explicitly selected text files and use reviewed excerpts in a model request
- ✅ **Code highlighting** - Fenced blocks and inline `code` are highlighted in the chat
- ✅ **System tray icon** - Quick access through the taskbar icon
- ✅ **Automatic startup** - Configurable to start with the system
- ✅ **Docked mode** - Pin the window to a screen edge and reserve workspace (`_NET_WM_STRUT_PARTIAL` / gtk-layer-shell)
- ✅ **Floating button** - Permanent floating button to show/hide the main window
- ✅ **Multilingual** - UI translated via `src/i18n.py`; English is used when a language is not available
- ✅ **Offline guides** - Twenty searchable Portuguese/English procedures and guided local diagnostics without an AI service
- ✅ **Assistance modes** - Choose bundled guides, a local model, a remote provider, or automatic fallback
- ✅ **Local test recorder** - Record cases through Trials / Debug or the CLI, review scoped evidence and export a private ZIP

## Local guides, models and conversations

In Settings → Assistance, select the mode:

- **Local guides:** no model requests; bundled information and allowed local read probes are available without Internet access.
- **Local AI model:** use the configured loopback server and an already installed model. Connection testing lists installed models and distinguishes an unavailable server from a missing model. Ollama and OpenAI-compatible servers are supported.
- **Remote AI provider:** use the remote provider selected in the API tab.
- **Automatic:** preserve the selected provider and use local guides when it is unavailable.

Text-only provider failure falls back to bundled guides, never to another AI provider. Image requests report failures explicitly. Explicit local mode rejects non-loopback URLs, redirects, environment proxies and known cloud models; Ollama's advertised remote aliases are checked before inference. An arbitrary local server remains trusted software: the client cannot audit whether a custom OpenAI-compatible server forwards requests elsewhere. Model downloads are not part of connection testing. Installing or updating Linux packages can still require Internet access.

The bundled procedures cover network links, IP addresses, routes, DNS, Wi-Fi, disk space/inodes, memory, permissions, mounts, services and APT/XBPS errors. Search only retrieves documentation. Starting a guide may run a separately allowlisted local read probe; source examples never authorize execution. Service/log access remains manual where the existing policy does not allow it. Sources have review dates and applicability metadata; documentation review is not a claim of on-device validation for every distribution version. Portuguese and English guide content is included; other guide languages currently fall back to English.

Try these messages:

```text
pesquisar conhecimento DNS
guia network-interface
e depois?
cancelar
```

The conversation selector and New conversation button isolate context. The History menu opens conversation management, including rename, archive/restore, search, export and reviewed JSON/Markdown import. Existing flat history is retained in one legacy conversation. Diagnostic progress is scoped to a session, including CLI restarts; imported conversations never restore an active diagnostic. Exports do not replace existing files, and imports accept only a bounded, versioned user/assistant message format. Markdown import reads the application's framed export format, rather than guessing message roles from an arbitrary Markdown document.

CLI examples:

```bash
python -m src.cli mode offline
python -m src.cli knowledge 'DNS'
python -m src.cli sessions new 'Network diagnosis'
python -m src.cli chat 'guia network-interface'
python -m src.cli chat 'e depois?'
python -m src.cli sessions list
python -m src.cli chat --session SESSION_ID 'continue this conversation'
python -m src.cli sessions export --format markdown --output conversation.md
python -m src.cli sessions export --format json --output conversation.json
python -m src.cli sessions import conversation.json --preview
python -m src.cli sessions import conversation.md --preview
python -m src.cli sessions import conversation.md --yes
dmesg | python -m src.cli chat --stdin 'Explain this error'
python -m src.cli chat --input error.log 'Explain this error'
python -m src.cli mode local --check
python -m src.cli local-models
```

Text-file/stdin input is limited to 64 KiB. `--no-history` avoids saving the new exchange; `history --clear` clears only the selected conversation. Relevant bundled guides and detected distribution facts also inform model responses, with the response language following the application language.

Model responses have a local limit of 131072 characters. Cancel interrupts supported response reads and retry waits; it does not guarantee the provider stops computing or undo a local operation already started. A completed local operation is retained in its original conversation even when cancellation arrives during execution.

Images and document excerpts require a separate, explicit choice for each request. Local document search needs neither a model nor Internet access; using its excerpts with a remote model sends those excerpts to that provider. See the [conversation, image and document guide](docs/conversas-imagens-documentos.md) for import review, attachment limits, source citations and the optional desktop shortcut.

Explicit Wi-Fi, printer and scanner setup requests can open a device chooser or offer missing support packages, both offline and after a model answer. Hypothetical questions and refusals do not start setup. Printer confirmation shows the command for the edited queue name; changing Wi-Fi networks clears the password. Scanner support installs the package providing `scanimage` explicitly, alongside the AirScan backend when the tool is missing.

Legacy CLI action offers require an interactive terminal and confirmation. Pipes show proposals without device discovery or execution. The chat command returns exit code `1` when an attempted action fails; success, cancellation and proposals without execution return `0`. The action result remains part of the conversation unless `--no-history` is used.

The Wayland chat dock requires layer-shell protocol version 4 or newer for keyboard focus on demand. Older compositors or libraries use an ordinary window so the chat remains usable without taking exclusive keyboard focus. The helper button can still use layer-shell because it does not need keyboard input.

## Conversational software and monitor actions

Local task handling works in GTK and the CLI, with or without an AI provider. Software search and installation use APT/XBPS repositories already configured on the machine. Monitor mode changes support active outputs through `xrandr` on X11 and native Sway on Wayland. An explicit installation or mode-change request authorizes that bounded task; search or listing alone only presents choices. Administrator authentication still uses the system's `pkexec` prompt.

Portuguese examples, as successive messages in the same conversation:

```text
procura o programa VLC
instala a opção 2
lista as resoluções dos monitores
aplica a opção 2
```

English equivalents:

```text
search the program VLC
install option 2
show monitor resolutions
apply option 2
```

Use an option number from the actual results. `instala VLC` / `install VLC` can install a unique exact package match directly; otherwise the assistant asks for a choice. A listed resolution can also be selected with `coloca em 1920 por 1080 a 60 Hz` / `set 1920 by 1080 at 60 Hz` when it identifies one monitor mode. Confirm a temporary display change within 15 seconds to keep it; otherwise the program attempts to restore the previous configuration. Failure to prepare a complete previous configuration blocks the change.

CLI conversations keep choices between invocations for up to 15 minutes:

```bash
python -m src.cli chat 'search the program VLC'
python -m src.cli chat 'install option 2'
python -m src.cli chat 'show monitor resolutions'
python -m src.cli chat 'apply option 2'
```

The CLI requires an interactive terminal to execute changes; pipes and `--stdin`/`--input` produce proposals. `--no-history` does not retain choices for a later message. Choices belong to one session, expire after 15 minutes and are omitted from conversation import/export. Package/version and monitor mode are rechecked before changing the system. Commands come from the local action catalog, never from model-generated shell text.

Package search uses existing indexes without refreshing them silently and returns at most 12 candidates. Monitor lists show up to 30 choices; request a specific resolution/frequency to filter longer lists. By default, the rollback watchdog runs independently of GTK inside the application process and stops if that process is killed. The experimental `app.display_independent_watchdog: true` option uses a separate worker that can survive the frontend PID being killed and attempt recovery in the same graphical session. It is disabled by default pending real X11/Sway trials; worker, cgroup or graphical server termination can prevent recovery. See the [independent recovery guide](docs/recuperacao-ecra-watchdog.md) for its conditions. This phase does not add repositories, run Web installers, configure arbitrary applications, activate disabled monitors, change display layout/scale, or configure GNOME/KDE. Hardware and distribution coverage require local validation. See the [Portuguese roadmap](docs/roadmap-acoes-conversacionais.md) for the next capabilities and acceptance criteria.

## History and file recovery

Version 1.4.1 validates history before opening the main window. An invalid, unsupported, oversized or excessively nested history shows a recovery dialog. **Back up and start new history** preserves all original bytes in a private adjacent backup before creating a fresh history. Cancelling leaves the original unchanged and stops startup. Unknown formats are not converted.

If saving fails during a session, a persistent warning and **Recover history** in the application menu offer the same explicit recovery. New chat requests wait until recovery succeeds; messages already queued for saving are retained in the new history. Keep the backup path shown after recovery for manual inspection. The CLI recovery command remains available when the GUI cannot start:

```bash
python -m src.cli history recover --yes
```

Approved file changes keep a journal and adjacent backups. In version 1.4.1, file previews, writes, change listing and recovery run in workers so GTK remains responsive while waiting for authentication. Review and confirmation still happen in the interface, and the journal transaction lock remains held during each write. Closing a window or cancelling further offers does not undo an approved write already started; its actual outcome remains in the journal.

Archive completed records to free journal capacity, then review a change before approving recovery:

```bash
python -m src.cli changes archive --yes
python -m src.cli changes list --archived
python -m src.cli changes show CHANGE_ID
python -m src.cli changes restore CHANGE_ID --yes
```

Archiving preserves change IDs and backup references; show/restore also find archived records. Recovery checks the current file, backup hashes and allowed paths again. A timeout or durability error requires checking the actual target before repeating the operation. The [security and recovery guide](docs/correcoes-seguranca-robustez.md) describes limits and uncertain outcomes.

## Local test recording

Open **Trials / Debug** beside the conversation selector or from the menu. Start a trial, begin a case, exercise the main program, verify the actual result, and record the outcome. The panel remains usable alongside the main window; closing it preserves an open case. The same local recorder is available without the GUI:

```bash
python -m src.cli trials start --title 'Offline smoke test' --environment ENV-01 --type vm
python -m src.cli trials begin KNW-01
# Exercise and independently verify the case before recording its result.
python -m src.cli trials end --result PASS --notes 'Expected local guide verified'
python -m src.cli trials finish
python -m src.cli trials preview --output trial-preview-new.md
# Read the complete preview and inspect attachments before confirming review.
python -m src.cli trials export --output trial-reviewed-new.zip --reviewed
```

Collection reads observed context, new operation events for the case's conversation and file-change metadata. App log excerpts require an explicit opt-in and may include other conversations. Text redaction is partial; selected PNG/JPEG images retain their pixels and metadata. Collection works offline, does not execute system commands and does not copy whole conversations/configurations or upload data. Results remain operator reports requiring independent verification.

Use `trials attach`, `exclude`, `status`, `list` and `use` to manage evidence and trials. Storage is private under `~/.local/share/linux_ai_assistant/trials/`; preview/export refuse existing destinations. Supply a verified build reference with `trials start --build` when needed. Without it, trial startup observes the commit of the checkout containing the running code, marks local changes or unknown worktree state, and retains `unknown` when no checkout is identified. This uses bounded local Git reads only at startup; case collection and export do not execute commands. See the [Portuguese recorder guide](docs/ferramenta-ensaios.md) for GUI steps, CLI options, limits and review rules, and the [real-world testing protocol](docs/protocolo-ensaios-reais.md) for environments, cases and bug reporting.

## Requirements

### System

- Linux; automatic installers are provided for Debian/Ubuntu and Void-based systems.
- Other distributions require manual dependency installation; compatibility must be verified locally.
- Python 3.8+
- GTK 3.0+

### System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip python3-venv git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng libgtk-3-0 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-notify-0.7 gir1.2-appindicator3-0.1

# Fedora
sudo dnf install python3 python3-pip git scrot tesseract tesseract-langpack-por tesseract-langpack-eng gtk3 python3-gobject libnotify

# Arch
sudo pacman -S python python-pip git scrot tesseract tesseract-data-por tesseract-data-eng gtk3 python-gobject libnotify
```

### Python Dependencies

See [requirements.txt](requirements.txt)

## Installation

### Method 1: Automatic Installation

```bash
# Clone the repository
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Make scripts executable
chmod +x scripts/*.sh

# Run installation (for most distributions)
./scripts/install.sh

# For Void Linux and d77void specifically
./scripts/install_void.sh
```

System file operations and runit activation use optional, dedicated polkit helpers. From a reviewed checkout under your control, install them separately:

```bash
sudo bash scripts/install-privileged-helpers.sh
```

This installs root-owned helpers and application-specific authentication prompts. These operations refuse unsafe or missing helper installations; they do not run privileged Python from the user checkout. Reinstall the helpers when their code changes. Home-directory file operations do not require this system installation. See the [helper installation requirements](docs/correcoes-seguranca-robustez.md#helpers-privilegiados-e-polkit); the current Flatpak does not provide host-helper integration.

### Method 2: Manual Installation

```bash
# Clone the repository
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Create virtual environment
python3 -m venv --system-site-packages venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create configuration file
mkdir -p ~/.config/linux_ai_assistant
cp config/config.json ~/.config/linux_ai_assistant/
cp config/.env.example ~/.config/linux_ai_assistant/.env

# Edit configuration
nano ~/.config/linux_ai_assistant/.env

# Run
python -m src.app
```

### For Void Linux and d77void

```bash
# Install dependencies with xbps
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Install system dependencies
sudo xbps-install -Su
sudo xbps-install -y python3 python3-pip git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng gtk+3 python3-gobject python3-cairo libnotify

# Create virtual environment and install
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt

# Run
bash run.sh
```

### Flatpak

A manifest is provided in `flatpak/`:

```bash
flatpak-builder --user --install build flatpak/io.github.linux_ai_assistant.json
flatpak run io.github.linux_ai_assistant
```

The dependencies are installed under `/app/lib/python3.*/site-packages`, so the
sandbox starts the app through `scripts/flatpak-launch.sh`, which puts that
directory on `PYTHONPATH` before running `python -m src.app`. Note that OCR
needs the `tesseract` binary, which the current runtime does not provide.

## Configuration

### API Keys

Edit the file `~/.config/linux_ai_assistant/.env` and add your API keys:

```ini
OPENROUTER_API_KEY=
GOOGLE_AI_STUDIO_KEY=
```

The `.env` file is loaded from the same directory as `config.json` without
replacing existing process environment variables. A canonical name such as
`LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY` takes precedence even when its
value is empty. Otherwise a nonempty legacy variable overrides the selected
credential store; an empty legacy variable falls back to that store.

**Settings → API** shows the stored key, keeps edits for each provider until
**OK**, and identifies an active environment override. Copying the effective
environment key and moving a stored key require their separate buttons. Optional
Linux Secret Service storage uses an existing default desktop collection and
requires an explicit unlock before writing; install it with
`python -m pip install '.[secret-service]'` from the checkout. Optional JSON
encryption uses `python -m pip install '.[encryption]'`. See the
[credential, model and manual QA guide](docs/credenciais-modelos.md) for
precedence, verified migration, encryption recovery and safe inspection.

GTK bindings come from the system packages; use `--system-site-packages` so the
virtual environment can import them. Do not install the unrelated PyPI `gi`
package.

You can get API keys at:

- [OpenRouter](https://openrouter.ai/) - Availability and pricing depend on the model.
- [Google AI Studio](https://aistudio.google.com/) - Check the provider for current quotas.

### Application Configuration

Edit the file `~/.config/linux_ai_assistant/config.json` to customize:

- **Window dimensions and position**
- **Opacity** (0.1 - 1.0)
- **Default AI provider**
- **AI model**
- **Enabled features**
- **Permissions**
- **Theme colors**
- **Docked mode and dock edge** (`app.dock_mode`, `app.dock_edge`)
- **Expert mode state** (`app.expert_mode`; `features.expert_mode` only shows/hides the button)
- **Context budget** (`context.max_messages`, `context.max_chars`) - how much of the conversation is sent on each request
- **Language** (`app.language`; empty = system locale, `en` = English)
- **Optional global shortcut** (`app.global_shortcut_enabled`, `app.global_shortcut`; disabled by default)

The shipped configuration template includes both shortcut settings, with
`false` and `<Ctrl><Alt>space` as their defaults.

### Supported Providers

| Provider | Base URL | Default Model |
|----------|----------|---------------|
| OpenRouter | https://openrouter.ai/api/v1 | google/gemini-2.5-flash |
| Google AI Studio | https://generativelanguage.googleapis.com/v1 | gemini-3.5-flash-lite |
| Anthropic | https://api.anthropic.com/v1 | claude-haiku-4-5-20251001 |
| Mistral | https://api.mistral.ai/v1 | mistral-small-latest |
| Groq | https://api.groq.com/openai/v1 | openai/gpt-oss-20b |
| Cohere | https://api.cohere.ai/v1 | command-r-08-2024 |
| Local Model | http://localhost:11434/v1 | llama3.2 |

These defaults apply to new configurations and missing fields; existing model
choices are preserved. Model availability depends on the provider and account.
In **Settings → API**, enter a model ID or explicitly click **List models**;
listing runs in the background and changes no saved model until **OK**. Remote
listing uses supported standard HTTPS endpoints and is blocked in local/offline
modes. Direct Google and OpenRouter model IDs are independent. See the
[model selection guide](docs/credenciais-modelos.md#modelos-remotos).

## Offline Mode

When no API key is configured, or the selected provider cannot be reached
(no internet/DNS), the assistant answers from a local knowledge base built
from `src/offline_assistant.py` and `src/knowledge_base.py`. Nothing leaves
the machine and no extra configuration is needed.

It detects the distribution from `/etc/os-release` (package manager and
service manager) and can help with:

- **Package management** - update, install, remove and search packages with
  the right tool (`xbps`, `apt`, `dnf`, `pacman`, `zypper`, `apk`).
- **Services and startup** - enable/disable/start/restart services
  (`systemd`, `runit`, `openrc`).
- **Timezone, locale and hostname** - including ready-to-run commands.
- **Network and firewall** - diagnostics (addresses, routes, DNS, ping) and
  the firewall commands that apply to the system (`ufw`, `firewalld`,
  `nftables`... per distribution).
- **Disks and cleanup** - free space, biggest consumers and safe cleanup.
- **Shell and environment** - default shell and aliases.
- **Distro knowledge base** - where configuration files live (repositories,
  network, logs, hostname, locale, services), how logs are collected
  (journald vs syslog/socklog), where repositories are configured, which
  firewall tool is in use, and what makes the distribution different - all
  curated per family from the official wikis/handbooks, with a link to the
  official documentation in every answer.

Examples: `how do I update the system?`, `install htop`,
`enable service chronyd`, `set timezone to Europe/Lisbon`,
`set hostname to laptop`, `quanto espaço em disco tenho?`,
`where are the configuration files?`, `onde estão os registos?`,
`show repositories`, `documentation about ufw`.

The knowledge base covers the Void, Debian, Ubuntu, Linux Mint, Arch,
Manjaro, Fedora, RHEL (CentOS/Rocky/Alma), openSUSE, Alpine and Gentoo
families (resolved by `ID` first, then `ID_LIKE` ancestry; unknown systems
get generic Linux facts). Sources: Void Handbook, Debian Wiki, Ubuntu Server
Documentation, Arch Wiki, Manjaro Wiki, Fedora Docs, Red Hat Docs, openSUSE
Wiki, Alpine Wiki, Gentoo Wiki.

Safety model:

- **Diagnostics** (e.g. `df -h`, `free -h`) are run through the sandboxed
  `SystemUtils`, so only commands allowed in `permissions.allowed_commands`
  are executed.
- **Changes:** legacy action offers show commands and require confirmation in
  the GUI or an interactive CLI terminal. Structured software and monitor tasks
  follow the explicit request and choice flow described above. Noninteractive
  CLI input produces proposals. Administrator authentication uses `pkexec`;
  commands are built as argument lists.

Offline fallback also applies to the CLI (`python -m src.cli chat "..."`). An
interactive terminal can execute authorized actions; pipes and file/stdin input
show proposals without executing changes.

## Usage

### Running the Application

```bash
# Using the run script
bash run.sh

# Or directly
source venv/bin/activate
python -m src.app
```

### Shortcuts

| Action | Shortcut |
|--------|----------|
| Send message | Enter |
| Capture screen | Ctrl+S (or the 📷 button) |
| Expert Mode | Ctrl+E (or the 🧠 button) |
| Clear input | Esc |
| Quit | Ctrl+Q (or the X button) |
| Show or focus from another application | Ctrl+Alt+Space when the optional global shortcut is enabled |

The global shortcut uses X11 or the Wayland GlobalShortcuts portal, depending on
the graphical session. A desktop without a compatible portal can bind
`linux-ai-assistant --show` itself. Desktop activation requires a session D-Bus;
the command shows the existing window instead of starting a second instance.
For Flatpak, bind `flatpak run io.github.linux_ai_assistant --show`.
See the [shortcut guide](docs/conversas-imagens-documentos.md#abrir-o-assistente-com-um-atalho-global)
for approval, conflicts and session limitations.

### Expert Mode

When expert mode is active:

- The AI responds as a Linux systems specialist
- It can help with system configuration
- It can suggest specific commands
- It can edit configuration files (with authorization)

### Screen Capture

1. Click the 📷 button
2. On Wayland, choose or approve the screenshot in the desktop's screenshot dialog.
   On X11, the configured capture utility takes the screenshot directly.
3. Text will be extracted using OCR
4. The text will be added to the conversation

Wayland capture uses the XDG Screenshot portal, including on GNOME/Ubuntu where
`grim` cannot capture the desktop. Ubuntu needs `xdg-desktop-portal` and
`xdg-desktop-portal-gnome` running in the graphical session. Cancellation and
permission refusal stop the request; the application does not try another tool
after either. A wlroots/Sway desktop without this portal can still use `grim`.
The request has a 120-second limit, and its temporary image is removed after
OCR or image preparation. OCR also requires Tesseract and the chosen languages.

For an image question, use **Capture an image for AI** or choose an image file.
Review the normalized image and the selected OpenRouter model, then attach it
to the next question. PNG, JPEG and WebP are supported; pixels are sent only for
that request and are not stored in conversation history. See the
[image guide](docs/conversas-imagens-documentos.md#enviar-uma-imagem-com-uma-pergunta)
for supported models, limits and disclosure.

### File Editing

In expert mode, you can ask the AI to:

- Read configuration files
- Explain configuration options
- Suggest changes
- Edit files (with explicit authorization)

## Languages

Supported languages: **English, Portuguese, Spanish, French and German**
(`i18n.SUPPORTED_LANGUAGES`). The UI language comes from `app.language` in
`config.json`; if empty, the system locale is used. If the configured (or
system) language is **not supported, English is used automatically** - and
you can redefine it in **Settings > Appearance > Language** at any time.
Texts are authored in English; if a language (or a specific text) has no
translation, the English string is used. To add a new language, add a
catalog to `TRANSLATIONS` in `src/i18n.py` and list the code in
`SUPPORTED_LANGUAGES`.

## Automatic Startup

To configure the application to start automatically:

```bash
# Add to startup
./scripts/autostart.sh enable

# Remove from startup
./scripts/autostart.sh disable

# Check status
./scripts/autostart.sh check
```

## Uninstallation

```bash
./scripts/uninstall.sh
```

## Project Structure

Selected modules and directories:

```
linux_ai_assistant/
├── src/
│   ├── __init__.py
│   ├── app.py              # Main application
│   ├── ai_client.py        # AI APIs client
│   ├── config_manager.py   # Configuration manager
│   ├── credential_settings.py # Stored-key editing and explicit migration
│   ├── credential_store.py # Optional Linux Secret Service backend
│   ├── remote_model_settings.py # Model selection UI
│   ├── remote_models.py    # Explicit bounded remote model discovery
│   ├── action_contract.py  # Structured action requests and results
│   ├── conversation_actions.py # Session-scoped task choices
│   ├── conversation_markdown.py # Versioned, framed conversation export/import
│   ├── document_store.py    # Private snapshots and local lexical document search
│   ├── display_watchdog.py # Experimental separate-process display recovery
│   ├── dock.py             # Docked mode (struts/layer-shell)
│   ├── file_actions.py     # File writing with diff confirmation
│   ├── global_shortcuts.py # Desktop activation and optional global shortcut
│   ├── i18n.py             # Translations (English fallback)
│   ├── image_attachments.py # Bounded, immutable image normalization
│   ├── main_window.py      # Main window
│   ├── offline_assistant.py # Offline answers and local tasks
│   ├── render_core.py      # Markup rendering (GTK-free)
│   ├── storage.py          # Locked atomic JSON transactions
│   ├── system_utils.py     # System utilities
│   ├── trial_recorder.py   # Local trial evidence and reviewed ZIP export
│   ├── trial_dialog.py     # Trials / Debug UI
│   └── tray_icon.py        # System tray icon
├── config/
│   ├── config.json         # Default configuration
│   └── .env.example        # Environment variables example
├── scripts/
│   ├── install.sh          # Installation script
│   ├── uninstall.sh        # Uninstallation script
│   └── autostart.sh        # Startup configuration
├── assets/                 # generated by the installer (not in the repo)
│   └── icon.png            # Application icon
├── requirements.txt        # Python dependencies
└── README.md              # Documentation
```

## d77void and Void Linux Support

The project provides a Void/d77void installer and a native XBPS recipe.
Compatibility must be validated in the selected graphical session:

### 🎯 Specific Features

- ✅ **Native XBPS support** - Void Linux package manager
- ✅ **Session autostart** - Start the GUI in the graphical user session
- ✅ **Dedicated installation script** - `install_void.sh` optimized for Void/d77void
- ✅ **XBPS package** - Template available in `xbps-src/` to build a native package
- ✅ **Automatic detection** - Recognizes Void Linux and d77void automatically

### 📦 Installation on d77void

d77void is a distribution based on Void Linux with several pre-configured window managers. Tray, docking and screen capture must be checked separately for each window manager or compositor:

- **Awesome WM**
- **BSPWM**
- **DWM**
- **Fluxbox**
- **Hyprland**
- **i3**
- **JWM**
- **LabWC**
- **LeftWM**
- **MangoWC**
- **Niri**
- **Openbox**
- **Qtile**
- **River**
- **Sway**
- **Wayfire**
- **wmd77**
- **GNOME**
- **LXQt**
- **Plasma**
- **XFCE**

### Graphical session startup

Use `bash scripts/autostart.sh enable` or your window manager's own startup
configuration. The GUI needs the user's display and session environment and
must not be installed as a root system service.

### Building an XBPS package

Follow [xbps-src/README.md](xbps-src/README.md). Build the recipe with `xbps-src`
inside a `void-packages` checkout before installing the resulting binary from
`hostdir/binpkgs`; copying a template alone does not publish a package.

## Customization

### Adding New Providers

Edit `src/ai_client.py` and add a new method for the provider:

```python
def _chat_new_provider(self, messages, model, api_key, base_url, temperature, max_tokens, timeout):
    # Implement logic for the new provider
    pass
```

Then register the provider in the `chat`/`stream_chat` dispatch (methods are
looked up automatically as `_chat_<provider>` / `_stream_<provider>`).

**Plugins are opt-in** (v1.1.0): a plugin is arbitrary code that runs with
your user privileges at startup, so only files listed in the `plugins.enabled`
config key are loaded:

```json
"plugins": { "enabled": ["example_provider"] }
```

The bundled `plugins/example_provider.py` is inert until you add it there.
Failures surface as `AIProviderError` exceptions — never as chat text.

### Adding New Allowed Commands

Edit `~/.config/linux_ai_assistant/config.json`:

```json
"permissions": {
    "require_sudo": true,
    "allowed_commands": [
        "ls", "cat", "grep", "ps", "df", "du", "free", "uname",
        "new_command"
    ],
    "allowed_edit_dirs": [
        "/etc", "/home", "/usr/local", "/opt", "/new/directory"
    ]
}
```

Only commands in `allowed_commands` are executed, and file arguments are
checked against `allowed_edit_dirs`. Prefer read-only commands: anything
listed here can be run by the assistant without an extra confirmation.

Additional protections apply on top of the allowlist (v1.1.1):

- **Blocked flags**: some flags execute code or read files even with a
  diagnostic command name. `man`, `neofetch` and interactive pagers are
  refused even if manually added to the allowlist. Indirect input flags,
  attached values and abbreviations of blocked flags are also refused.
  The policy lives in `src/command_policy.py`.
- **Filename operands**: file-reading commands validate absolute, relative
  and bare filenames against `allowed_edit_dirs`, including filenames after
  `--`. Regex and formatting arguments remain usable. Unknown long options
  of file readers are refused, so abbreviations cannot change operand parsing.
- **Bounded diagnostics**: command output is capped at 1 MB while reading the
  pipes, and the deadline includes children holding those pipes open.

`allowed_edit_dirs` now defaults to an **empty list** (minimum privilege):
new installations have no editable system directories until you add them
here or in Settings. Existing configurations keep their list.

Expert file blocks still require a preview and confirmation. Privileged writes
use one `pkexec` invocation, exclusive temporary files and backups, and reject
changes to the destination since preview. Existing ownership and mode are
preserved; a new privileged configuration is created with mode `0600`. Sensitive
account and authorization files also include known adjacent backup variants,
such as `/etc/shadow-`, `/etc/sudoers.tmp` and editor backup names. Both the
client and the privileged helper refuse these targets.

GUI and CLI history and token statistics use shared locks and atomic JSON
transactions. Shutdown drains queued history writes. Failed, cancelled or empty
streams do not leave a partial GUI answer saved as a completed response; provider
failures use the offline assistant. Cohere uses its v1 NDJSON chat contract.
Local OpenAI-compatible servers receive no `stream_options` by default; set
`api.providers.local_llm.stream_include_usage` to `true` if the server supports it.

PyYAML remains a required dependency. If it is missing or bundled YAML knowledge
cannot be validated, the application starts with built-in Python references and
an explicit degraded-knowledge warning. Reinstall the dependencies to restore
the bundled procedures.

## Troubleshooting

### Problem: Window does not appear

- Check that GTK is installed correctly
- Try running with: `GTK_DEBUG=interactive python -m src.app`

### Problem: OCR does not work

- Install Tesseract: `sudo apt-get install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng`
- Install pytesseract: `pip install pytesseract`

### Problem: API does not respond

- Check your API key
- Check your quota
- Test the API manually with curl

### Problem: Missing permissions

- Check the permissions in `config.json` (`allowed_commands`,
  `allowed_edit_dirs`)
- Do **not** run the application as root/sudo: it talks to your session bus
  and writes to your own config directory. Fix the specific permission
  instead.

## Contributing

1. Fork the project
2. Create a branch for your feature (`git checkout -b feature/new-feature`)
3. Commit your changes (`git commit -m 'Add new feature'`)
4. Push to the branch (`git push origin feature/new-feature`)
5. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) for more details.

## Acknowledgments

- [OpenRouter](https://openrouter.ai/) - AI API
- [Google AI Studio](https://aistudio.google.com/) - AI API
- [GTK](https://www.gtk.org/) - Graphical interface
- [Tesseract](https://github.com/tesseract-ocr/tesseract) - OCR

---

**Made with ❤️ for the Linux community**

## Validation

```bash
python -m unittest discover -s tests -v
python -m src.cli --help
for script in run.sh scripts/*.sh; do bash -n "$script" || exit; done
```

The [credential and model guide](docs/credenciais-modelos.md#verificação-manual)
contains the API settings QA steps and a helper that prints credential presence
and origin without printing values. The
[maintenance and compatibility guide](docs/manutencao-compatibilidade.md)
describes the current Python/GTK support and the limited mypy CI gate.

For native Void packaging, see [xbps-src/README.md](xbps-src/README.md).
For reviewed conversation imports, one-request images, desktop activation and chosen local documents, see [the conversation, image and document guide](docs/conversas-imagens-documentos.md).
For structured actions, service control, private operation events, offline checksums and validated YAML knowledge, see [the infrastructure guide](docs/infraestrutura-acoes-conhecimento.md). Existing virtual environments need the updated `requirements.txt`.
For local diagnostic reports, offline log interpretation and recovery of approved file writes, see [the second-phase guide](docs/segunda-fase-2026-10-02.md).
For real-world test environments, scenarios, result and bug reports, see [the real-world testing protocol](docs/protocolo-ensaios-reais.md), with reusable report and CSV templates. The integrated Trials / Debug panel and CLI recorder are described in [the test recorder guide](docs/ferramenta-ensaios.md).
The GUI must run inside a graphical user session; use
`./scripts/autostart.sh enable` for session startup rather than a root runit service.
