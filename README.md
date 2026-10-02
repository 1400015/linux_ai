# Linux AI Assistant

A permanent AI assistant for Linux with a floating interface, integration with several AI APIs, screen capture, expert mode and more.

[![Void Linux](https://img.shields.io/badge/Void%20Linux-Compatible-green)](https://voidlinux.org)
[![d77void](https://img.shields.io/badge/d77void-Supported-blue)](https://d77void.sourceforge.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

- ✅ **Transparent floating interface** - Permanent window with black backgrounds and configurable transparency
- ✅ **Multi-API integration** - Support for OpenRouter, Google AI Studio and local models
- ✅ **Screen capture with OCR** - Captures the screen and extracts text automatically
- ✅ **Expert Mode** - Assistant specialized in Linux systems
- ✅ **File editing** - Edit configuration files with authorization
- ✅ **Command execution** - Run system commands with controlled permissions
- ✅ **Conversation sessions** - Independent context, resume, archive, search and Markdown/JSON export
- ✅ **Code highlighting** - Fenced blocks and inline `code` are highlighted in the chat
- ✅ **System tray icon** - Quick access through the taskbar icon
- ✅ **Automatic startup** - Configurable to start with the system
- ✅ **Docked mode** - Pin the window to a screen edge and reserve workspace (`_NET_WM_STRUT_PARTIAL` / gtk-layer-shell)
- ✅ **Floating button** - Permanent floating button to show/hide the main window
- ✅ **Multilingual** - UI translated via `src/i18n.py`; English is used when a language is not available
- ✅ **Offline guides** - Twenty searchable Portuguese/English procedures and guided local diagnostics without an AI service
- ✅ **Assistance modes** - Choose bundled guides, a local model, a remote provider, or automatic fallback

## Local guides, models and conversations

In Settings → Assistance, select the mode:

- **Local guides:** no model requests; bundled information and allowed local read probes are available without Internet access.
- **Local AI model:** use the configured loopback server and an already installed model. Connection testing lists installed models and distinguishes an unavailable server from a missing model. Ollama and OpenAI-compatible servers are supported.
- **Remote AI provider:** use the remote provider selected in the API tab.
- **Automatic:** preserve the selected provider and use local guides when it is unavailable.

Provider failure falls back to bundled guides, never to another AI provider. Explicit local mode rejects non-loopback URLs, redirects, environment proxies and known cloud models; Ollama's advertised remote aliases are checked before inference. An arbitrary local server remains trusted software: the client cannot audit whether a custom OpenAI-compatible server forwards requests elsewhere. Model downloads are not part of connection testing. Installing or updating Linux packages can still require Internet access.

The bundled procedures cover network links, IP addresses, routes, DNS, Wi-Fi, disk space/inodes, memory, permissions, mounts, services and APT/XBPS errors. Search only retrieves documentation. Starting a guide may run a separately allowlisted local read probe; source examples never authorize execution. Service/log access remains manual where the existing policy does not allow it. Sources have review dates and applicability metadata; documentation review is not a claim of on-device validation for every distribution version. Portuguese and English guide content is included; other guide languages currently fall back to English.

Try these messages:

```text
pesquisar conhecimento DNS
guia network-interface
e depois?
cancelar
```

The conversation selector and New conversation button isolate context. The History menu opens conversation management, including rename, archive/restore, search, export and JSON import. Existing flat history is retained in one legacy conversation. Diagnostic progress is scoped to a session, including CLI restarts; imported conversations never restore an active diagnostic. Exports do not replace existing files, and imports accept only a bounded, versioned user/assistant message format.

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
python -m src.cli sessions import conversation.json
dmesg | python -m src.cli chat --stdin 'Explain this error'
python -m src.cli chat --input error.log 'Explain this error'
python -m src.cli mode local --check
python -m src.cli local-models
```

Text-file/stdin input is limited to 64 KiB. `--no-history` avoids saving the new exchange; `history --clear` clears only the selected conversation. Relevant bundled guides and detected distribution facts also inform model responses, with the response language following the application language.

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
replacing existing process environment variables. Empty API key entries fall
back to the keys saved through the settings dialog. Canonical environment names
such as `LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY` take precedence over the
legacy `OPENROUTER_API_KEY` name. When an environment variable is active, the
**Settings → API** tab warns about it and offers **Reload saved keys** and
**Copy effective key to config**; see [Manual QA: API settings tab](#manual-qa-api-settings-tab).

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

### Supported Providers

| Provider | Base URL | Default Model |
|----------|----------|---------------|
| OpenRouter | https://openrouter.ai/api/v1 | google/gemini-2.5-flash |
| Google AI Studio | https://generativelanguage.googleapis.com/v1 | gemini-2.5-flash |
| Anthropic | https://api.anthropic.com/v1 | claude-3-5-haiku-latest |
| Mistral | https://api.mistral.ai/v1 | mistral-small-latest |
| Groq | https://api.groq.com/openai/v1 | llama-3.1-8b-instant |
| Cohere | https://api.cohere.ai/v1 | command-r |
| Local Model | http://localhost:11434/v1 | llama3.2 |

Model IDs change over time: check your provider's documentation if a
default model stops being available, and update `api.providers.<name>.model`
in `config.json`. Keys are read from `~/.config/linux_ai_assistant/.env`
(copy it from `config/.env.example`).

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
- **Changes** are never run silently: they are shown and executed through
  `pkexec` only after you confirm the dialog (GUI) or run them yourself
  (CLI). Commands are built as argument lists, so user input cannot inject a
  shell.

Offline mode is automatic. The CLI (`python -m src.cli chat "..."`) falls
back to it the same way and prints the suggested commands for you to run.

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

### Expert Mode

When expert mode is active:

- The AI responds as a Linux systems specialist
- It can help with system configuration
- It can suggest specific commands
- It can edit configuration files (with authorization)

### Screen Capture

1. Click the 📷 button
2. The screen will be captured automatically
3. Text will be extracted using OCR
4. The text will be added to the conversation

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

```
linux_ai_assistant/
├── src/
│   ├── __init__.py
│   ├── app.py              # Main application
│   ├── ai_client.py        # AI APIs client
│   ├── config_manager.py   # Configuration manager
│   ├── dock.py             # Docked mode (struts/layer-shell)
│   ├── file_actions.py     # File writing with diff confirmation
│   ├── i18n.py             # Translations (English fallback)
│   ├── main_window.py      # Main window
│   ├── offline_assistant.py # Offline answers and local tasks
│   ├── render_core.py      # Markup rendering (GTK-free)
│   ├── system_utils.py     # System utilities
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
preserved; a new privileged configuration is created with mode `0600`.

GUI and CLI history and token statistics use shared locks and atomic JSON
transactions. Shutdown drains queued history writes. Failed, cancelled or empty
streams do not leave a partial GUI answer saved as a completed response; provider
failures use the offline assistant. Cohere uses its v1 NDJSON chat contract.
Local OpenAI-compatible servers receive no `stream_options` by default; set
`api.providers.local_llm.stream_include_usage` to `true` if the server supports it.

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

### Manual QA: API settings tab

The **Settings → API** tab manages the default provider and its key. Since
environment variables override `config.json`, check both the normal flow and
the override flow. Open the dialog from `Menu → Settings` and use the helper
below to inspect what is on disk at any point:

```bash
python - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".config" / "linux_ai_assistant" / "config.json"
d = json.loads(p.read_text())
print("default:", d["api"]["default_provider"])
print({k: v.get("api_key") for k, v in d["api"]["providers"].items()})
PY
```

1. **The key loads for the current provider.** The *AI Provider* combo shows
   `api.default_provider` and the key field is pre-filled with that provider's
   key.
2. **Switching provider reloads the key.** Save a distinct key on two
   providers, then switch between them in the combo: the field must show each
   provider's own key (never the previous one).
3. **Saving writes to the right provider.** Select provider B, type a new key
   and click **OK**. `api.default_provider` becomes B and only B's key changes;
   the other provider's key must stay untouched.
4. **Env overrides are announced.** With `OPENROUTER_API_KEY` (or the canonical
   `LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY`) exported, open Settings and
   select OpenRouter. The warning *“the … environment variable overrides this
   key”* appears together with two buttons that are hidden otherwise:
   - **Reload saved keys** fills the field with the value stored in
     `config.json`, ignoring the env override.
   - **Copy effective key to config** writes the effective (env) value into
     `config.json` and notifies *“Key copied to config.json”*.
5. **The app picks changes up live.** After saving, send a message without
   restarting the app: `chat()`/`stream_chat()` read `api.default_provider` on
   every request.

Steps 2, 3 and 4 are also covered by the automated regression tests:

```bash
python -m unittest tests.test_regressions.TestStoredApiKeyIgnoresEnvOverride -v
python -m unittest tests.test_regressions.TestCopyEffectiveKeyToConfig -v
```

For native Void packaging, see [xbps-src/README.md](xbps-src/README.md).
For local diagnostic reports, offline log interpretation and recovery of approved file writes, see [the second-phase guide](docs/segunda-fase-2026-10-02.md).
The GUI must run inside a graphical user session; use
`./scripts/autostart.sh enable` for session startup rather than a root runit service.
