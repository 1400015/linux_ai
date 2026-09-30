# Linux AI Assistant

A permanent AI assistant for Linux with a floating interface, integration with several AI APIs, screen capture, expert mode and more.

![Linux AI Assistant](assets/screenshot.png)

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
- ✅ **Conversation history** - Keeps conversation context
- ✅ **System tray icon** - Quick access through the taskbar icon
- ✅ **Automatic startup** - Configurable to start with the system
- ✅ **Docked mode** - Pin the window to a screen edge and reserve workspace (`_NET_WM_STRUT_PARTIAL` / gtk-layer-shell)
- ✅ **Floating button** - Permanent floating button to show/hide the main window
- ✅ **Multilingual** - UI translated via `src/i18n.py`; English is used when a language is not available

## Requirements

### System

- Linux (tested on Ubuntu, Fedora, Debian, Arch, **Void Linux**, **d77void**)
- Python 3.8+
- GTK 3.0+

### System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip python3-venv git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng libgtk-3-0 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-appindicator3-0.1

# Fedora
sudo dnf install python3 python3-pip git scrot tesseract tesseract-langpack-por tesseract-langpack-eng gtk3 python3-gobject

# Arch
sudo pacman -S python python-pip git scrot tesseract tesseract-data-por tesseract-data-eng gtk3 python-gobject
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
python3 -m venv venv
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
python src/app.py
```

### For Void Linux and d77void

```bash
# Install dependencies with xbps
git clone https://github.com/1400015/linux_ai.git
cd linux_ai

# Install system dependencies
sudo xbps-install -Su
sudo xbps-install -Sy python3 python3-pip python3-venv git scrot tesseract-ocr tesseract-ocr-por tesseract-ocr-eng libgtk-3 libgtk-3-devel py3-gobject py3-cairo gobject-introspection libappindicator-gtk3

# Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
./run.sh
```

## Configuration

### API Keys

Edit the file `~/.config/linux_ai_assistant/.env` and add your API keys:

```ini
OPENROUTER_API_KEY=your_openrouter_api_key_here
GOOGLE_AI_STUDIO_KEY=your_google_ai_studio_key_here
```

You can get free API keys at:

- [OpenRouter](https://openrouter.ai/) - $0.001 per 1K tokens (free to test)
- [Google AI Studio](https://aistudio.google.com/) - Free with generous quotas

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
- **Language** (`app.language`; empty = system locale, `en` = English)

### Supported Providers

| Provider | Base URL | Default Model |
|----------|----------|---------------|
| OpenRouter | https://openrouter.ai/api/v1 | google/gemini-flash-1.5 |
| Google AI Studio | https://generativelanguage.googleapis.com/v1beta | gemini-1.5-flash |
| Local Model | http://localhost:11434/v1 | llama3.2 |

## Usage

### Running the Application

```bash
# Using the run script
./run.sh

# Or directly
source venv/bin/activate
python src/app.py
```

### Shortcuts

| Action | Shortcut |
|--------|----------|
| Send message | Enter |
| Capture screen | 📷 button |
| Expert Mode | 🧠 button |
| Close window | X button |

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

The UI language is controlled by the `app.language` key in `config.json`
(e.g. `"pt"`, `"es"`, `"fr"`, `"de"`). If empty, the system locale is used.
Texts are authored in English; if a language (or a specific text) has no
translation, the English string is used. To add a new language, add a
catalog to `TRANSLATIONS` in `src/i18n.py`.

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
├── assets/
│   └── icon.png            # Application icon
├── requirements.txt        # Python dependencies
└── README.md              # Documentation
```

## d77void and Void Linux Support

The Linux AI Assistant has full support for **Void Linux** and **d77void**:

### 🎯 Specific Features

- ✅ **Native XBPS support** - Void Linux package manager
- ✅ **runit integration** - Void's init system (instead of systemd)
- ✅ **Dedicated installation script** - `install_void.sh` optimized for Void/d77void
- ✅ **XBPS package** - Template available in `xbps-src/` to build a native package
- ✅ **Automatic detection** - Recognizes Void Linux and d77void automatically

### 📦 Installation on d77void

d77void is a distribution based on Void Linux with several pre-configured window managers. The Linux AI Assistant works perfectly on all d77void variants:

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

### 🔧 runit Service (Optional)

To integrate the Linux AI Assistant with Void Linux's **runit** init system:

```bash
# Create service directory
mkdir -p ~/.local/service/linux-ai-assistant

# Create run file
cat > ~/.local/service/linux-ai-assistant/run <<EOL
#!/bin/sh
exec /path/to/linux_ai/run.sh
EOL
chmod +x ~/.local/service/linux-ai-assistant/run

# Enable service (requires sudo)
sudo ln -s ~/.local/service/linux-ai-assistant /etc/sv/linux-ai-assistant
sudo ln -s /etc/sv/linux-ai-assistant /var/service/

# Manage service
sv up linux-ai-assistant    # Start
sv down linux-ai-assistant  # Stop
sv restart linux-ai-assistant  # Restart
```

### 📦 Building an XBPS Package

To build a native package for Void Linux:

```bash
# Copy template to srcpkgs
sudo cp -r xbps-src/linux-ai-assistant /var/db/xbps/srcpkgs/

# Update repositories
sudo xbps-install -Su

# Install package
sudo xbps-install -S linux-ai-assistant
```

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

### Adding New Allowed Commands

Edit `~/.config/linux_ai_assistant/config.json`:

```json
"permissions": {
    "require_sudo": true,
    "allowed_commands": [
        "ls", "cat", "grep", "new_command"
    ],
    "allowed_edit_dirs": [
        "/etc", "/home", "/usr/local", "/new/directory"
    ]
}
```

## Troubleshooting

### Problem: Window does not appear

- Check that GTK is installed correctly
- Try running with: `GTK_DEBUG=interactive python src/app.py`

### Problem: OCR does not work

- Install Tesseract: `sudo apt-get install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng`
- Install pytesseract: `pip install pytesseract`

### Problem: API does not respond

- Check your API key
- Check your quota
- Test the API manually with curl

### Problem: Missing permissions

- Run with `sudo` if needed
- Check the permissions in `config.json`

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
