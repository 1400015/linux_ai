#!/bin/bash

# Installation script specific to Void Linux and d77void
# Usage: ./install_void.sh [--dev]

set -e

# Output colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check whether it is running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Do not run this script as root!${NC}"
    echo "The script will ask for sudo privileges when needed."
    exit 1
fi

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Development mode
DEV_MODE=false
if [ "$1" == "--dev" ]; then
    DEV_MODE=true
    echo -e "${BLUE}Development mode enabled${NC}"
fi

# Check whether it is Void Linux
if ! command -v xbps-install >/dev/null 2>&1; then
    echo -e "${RED}This script is specific to Void Linux/d77void!${NC}"
    echo "Use ./install.sh for the other distributions."
    exit 1
fi

echo -e "${GREEN}"
echo ""
echo -e "      Linux AI Assistant - Installation for Void Linux/d77void${NC}"
echo ""

# Function to check whether a package is installed
package_installed() {
    xbps-query -p pkgver "$1" >/dev/null 2>&1
}

# Function to install the packages on Void
install_void_packages() {
    echo -e "${YELLOW}Checking the system dependencies for Void Linux...${NC}"
    
    # Packages required for Void Linux
    REQUIRED_PACKAGES=(
        "python3"
        "python3-pip"
        "git"
        "scrot"
        "tesseract-ocr"
        "tesseract-ocr-por"
        "tesseract-ocr-eng"
        "gtk+3"
        "python3-gobject"
        "python3-cairo"
        "libnotify"
        "ImageMagick"  # To create the icon if needed
    )
    
    # Check for and install the missing packages
    MISSING_PACKAGES=()
    for pkg in "${REQUIRED_PACKAGES[@]}"; do
        if ! package_installed "$pkg"; then
            MISSING_PACKAGES+=("$pkg")
        fi
    done
    
    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        echo -e "${YELLOW}Missing packages: ${MISSING_PACKAGES[*]}${NC}"
        echo -e "${BLUE}Installing packages...${NC}"
        
        # Update the repositories
        sudo xbps-install -Su
        
        sudo xbps-install -y "${MISSING_PACKAGES[@]}"
        
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
    
    cat > "$RUN_SCRIPT" <<'EOL'
#!/bin/bash
set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/venv/bin/python" -m src.app "$@"
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
        if command -v convert >/dev/null 2>&1; then
            # Create the icon with ImageMagick
            convert -size 64x64 xc:black -fill white -draw "circle 32,32 32,16" "$ICON_DIR/icon.png"
        else
            # Try to copy the default Void icon
            VOID_ICONS=(
                "/usr/share/icons/hicolor/64x64/apps/system-run.png"
                "/usr/share/pixmaps/system-run.png"
                "/usr/local/share/icons/hicolor/64x64/apps/system-run.png"
            )
            
            for icon in "${VOID_ICONS[@]}"; do
                if [ -f "$icon" ]; then
                    cp "$icon" "$ICON_DIR/icon.png"
                    break
                fi
            done
            
            # If none was found, create a placeholder
            if [ ! -f "$ICON_DIR/icon.png" ]; then
                touch "$ICON_DIR/icon.png"
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
    echo -e "${YELLOW}Notes for Void Linux/d77void:${NC}"
    echo "  - The assistant runs with normal privileges."
    echo "  - For features that require sudo, a password will be requested."
    echo "  - Expert mode allows editing the configuration files."
    echo "  - To start in the graphical session: ./scripts/autostart.sh enable"
    echo "  - d77void uses runit as the init system instead of systemd."
    echo ""
}

# Main function
main() {
    # Install the system dependencies
    install_void_packages
    
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
