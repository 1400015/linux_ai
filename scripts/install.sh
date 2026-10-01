#!/bin/bash

# Installation script for Linux AI Assistant
# Usage: ./install.sh [--dev]

set -e

# Output colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check whether it is running as root
if [ "$(id -u)" -eq 0 ]; then
    echo -e "${RED}Do not run this script as root!${NC}"
    echo "The script will ask for sudo privileges when needed."
    exit 1
fi

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if command -v xbps-install >/dev/null 2>&1; then
    exec bash "$SCRIPT_DIR/install_void.sh" "$@"
fi

# Development mode
DEV_MODE=false
if [ "$1" == "--dev" ]; then
    DEV_MODE=true
    echo -e "${BLUE}Development mode enabled${NC}"
fi

# Function to check whether a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to install packages
install_packages() {
    echo -e "${YELLOW}Checking the system dependencies...${NC}"
    
    # Detect the distribution
    if command_exists apt-get; then
        PKG_MANAGER="apt-get"
    else
        echo "Automatic installation currently supports Debian/Ubuntu and Void Linux."
        echo "For other distributions, install the dependencies from README.md manually."
        exit 1
    fi
    
    echo -e "${BLUE}Detected package manager: $PKG_MANAGER${NC}"
    
    REQUIRED_PACKAGES=(
        python3 python3-pip python3-venv git scrot tesseract-ocr
        tesseract-ocr-por tesseract-ocr-eng libgtk-3-0 python3-gi
        python3-gi-cairo gir1.2-gtk-3.0 gir1.2-notify-0.7
        gir1.2-appindicator3-0.1
    )

    # Check for and install missing packages
    MISSING_PACKAGES=()
    for pkg in "${REQUIRED_PACKAGES[@]}"; do
        if [ "$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null)" != "install ok installed" ]; then
            MISSING_PACKAGES+=("$pkg")
        fi
    done
    
    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        echo -e "${YELLOW}Missing packages: ${MISSING_PACKAGES[*]}${NC}"
        echo -e "${BLUE}Installing packages...${NC}"
        
        # Update the cache
        sudo apt-get update
        
        sudo apt-get install -y "${MISSING_PACKAGES[@]}"

        echo -e "${GREEN}Packages installed successfully!${NC}"
    else
        echo -e "${GREEN}All the required packages are already installed!${NC}"
    fi
}

# Function to create the virtual environment
create_venv() {
    echo -e "${YELLOW}Creating the Python virtual environment...${NC}"
    
    # Create the directory for the virtual environment
    VENV_DIR="$PROJECT_DIR/venv"
    if [ -d "$VENV_DIR" ] && [ "$DEV_MODE" = false ]; then
        echo -e "${BLUE}The virtual environment already exists. Removing it...${NC}"
        rm -rf "$VENV_DIR"
    fi
    
    # Create the virtual environment
    python3 -m venv --system-site-packages "$VENV_DIR"
    
    # Activate the virtual environment and install the dependencies
    # shellcheck disable=SC1091  # gerado por python3 -m venv
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install -r "$PROJECT_DIR/requirements.txt"
    
    echo -e "${GREEN}Virtual environment created successfully!${NC}"
}

# Function to create the application shortcut
create_desktop_entry() {
    echo -e "${YELLOW}Creating the application shortcut...${NC}"
    
    # Directory for the .desktop files
    DESKTOP_DIR="$HOME/.local/share/applications"
    mkdir -p "$DESKTOP_DIR"
    
    # Create the .desktop file
    DESKTOP_FILE="$DESKTOP_DIR/linux-ai-assistant.desktop"
    
    cat > "$DESKTOP_FILE" <<EOL
[Desktop Entry]
Version=1.0
Type=Application
Name=Linux AI Assistant
Comment=Permanent AI assistant for Linux
Exec=bash "$PROJECT_DIR/run.sh"
Icon=$PROJECT_DIR/assets/icon.png
Terminal=false
Categories=Utility;System;
StartupWMClass=linux-ai-assistant
EOL
    
    # Make it executable
    chmod +x "$DESKTOP_FILE"
    
    # Update the application database
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR"
    fi
    
    echo -e "${GREEN}Shortcut created at $DESKTOP_FILE${NC}"
}

# Function to create the run script
create_run_script() {
    echo -e "${YELLOW}Creating the run script...${NC}"
    
    RUN_SCRIPT="$PROJECT_DIR/run.sh"
    
    # Keep the python3 fallback: if the venv is missing (e.g. the user
    # removed it) the launcher still starts the application.
    cat > "$RUN_SCRIPT" <<'EOL'
#!/bin/bash
set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    exec "$PROJECT_DIR/venv/bin/python" -m src.app "$@"
fi
exec python3 -m src.app "$@"
EOL
    
    chmod +x "$RUN_SCRIPT"
    
    echo -e "${GREEN}Run script created at $RUN_SCRIPT${NC}"
}

# Function to create the icon
create_icon() {
    echo -e "${YELLOW}Creating the icon...${NC}"
    
    ICON_DIR="$PROJECT_DIR/assets"
    mkdir -p "$ICON_DIR"
    
    # Create a simple icon (if it does not exist)
    if [ ! -f "$ICON_DIR/icon.png" ]; then
        # Use a default system icon
        if command_exists convert; then
            # Create the icon with ImageMagick
            convert -size 64x64 xc:black -fill white -draw "circle 32,32 32,16" "$ICON_DIR/icon.png"
        else
            # Copy the default icon
            if [ -f "/usr/share/icons/hicolor/64x64/apps/system-run.png" ]; then
                cp "/usr/share/icons/hicolor/64x64/apps/system-run.png" "$ICON_DIR/icon.png"
            fi
        fi
    fi
    
    echo -e "${GREEN}Icon created at $ICON_DIR/icon.png${NC}"
}

# Function to create the configuration file
create_config() {
    echo -e "${YELLOW}Creating the configuration file...${NC}"
    
    CONFIG_DIR="$HOME/.config/linux_ai_assistant"
    mkdir -p "$CONFIG_DIR"
    # O config guarda API keys: 0700 evita leitura por outros utilizadores
    chmod 700 "$CONFIG_DIR"
    
    # Copy the default configuration
    if [ ! -f "$CONFIG_DIR/config.json" ]; then
        cp "$PROJECT_DIR/config/config.json" "$CONFIG_DIR/config.json"
        echo -e "${GREEN}Configuration copied to $CONFIG_DIR/config.json${NC}"
    else
        echo -e "${BLUE}The configuration file already exists. Keeping it.${NC}"
    fi
    
    # Copy .env.example
    if [ ! -f "$CONFIG_DIR/.env" ] && [ -f "$PROJECT_DIR/config/.env.example" ]; then
        cp "$PROJECT_DIR/config/.env.example" "$CONFIG_DIR/.env"
        # O .env guarda chaves API: 0644 num $HOME 0755 era legível por todos
        chmod 600 "$CONFIG_DIR/.env"
        echo -e "${GREEN}.env file created at $CONFIG_DIR/.env${NC}"
        echo "Please edit this file to add your API keys."
    fi
}

# Function to show the final instructions
show_final_instructions() {
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Installation completed successfully!   ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    echo -e "${BLUE}To run the application:${NC}"
    echo "  1. Edit the configuration file:"
    echo "     nano ~/.config/linux_ai_assistant/.env"
    echo ""
    echo "  2. Add your API keys (OpenRouter, Google AI Studio, etc.)"
    echo ""
    echo "  3. Run the application:"
    echo "     $PROJECT_DIR/run.sh"
    echo ""
    echo "  Or use the shortcut created in the applications menu."
    echo ""
    echo -e "${BLUE}To uninstall:${NC}"
    echo "  Run: ./scripts/uninstall.sh"
    echo ""
    echo -e "${YELLOW}Notes:${NC}"
    echo "  - The assistant runs with normal privileges."
    echo "  - To run system commands, allow them in \"permissions.allowed_commands\" in ~/.config/linux_ai_assistant/config.json."
    echo "  - Expert mode allows editing the configuration files."
    echo ""
}

# Main function
main() {
    echo -e "${GREEN}"
    echo ""
    echo -e "      Linux AI Assistant - Installation${NC}"
    echo ""
    
    # Install the system dependencies
    install_packages
    
    # Create the icon
    create_icon
    
    # Create the virtual environment
    create_venv
    
    # Create the run script
    create_run_script
    
    # Create the application shortcut
    create_desktop_entry
    
    # Create the configuration
    create_config
    
    # Show the final instructions
    show_final_instructions
}

# Run
main "$@"
