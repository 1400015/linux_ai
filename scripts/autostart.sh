#!/bin/bash

# Script to configure automatic startup

set -e

# Output colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Function to add to automatic startup
add_to_autostart() {
    echo -e "${YELLOW}Configuring automatic startup...${NC}"
    
    # Autostart directory
    AUTOSTART_DIR="$HOME/.config/autostart"
    mkdir -p "$AUTOSTART_DIR"
    
    # Create the .desktop file for autostart
    AUTOSTART_FILE="$AUTOSTART_DIR/linux-ai-assistant.desktop"
    
    cat > "$AUTOSTART_FILE" <<EOL
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
Hidden=false
EOL
    
    chmod +x "$AUTOSTART_FILE"
    
    echo -e "${GREEN}Automatic startup configured!${NC}"
    echo "The application will start automatically the next time you log in."
}

# Function to remove from automatic startup
remove_from_autostart() {
    echo -e "${YELLOW}Removing from automatic startup...${NC}"
    
    AUTOSTART_FILE="$HOME/.config/autostart/linux-ai-assistant.desktop"
    
    if [ -f "$AUTOSTART_FILE" ]; then
        rm "$AUTOSTART_FILE"
        echo -e "${GREEN}Automatic startup removed!${NC}"
    else
        echo -e "${BLUE}The application is not configured to start automatically.${NC}"
    fi
}

# Function to check the current state
check_autostart() {
    AUTOSTART_FILE="$HOME/.config/autostart/linux-ai-assistant.desktop"
    
    if [ -f "$AUTOSTART_FILE" ]; then
        echo -e "${GREEN}The application is configured to start automatically.${NC}"
        return 0
    else
        echo -e "${BLUE}The application is NOT configured to start automatically.${NC}"
        return 1
    fi
}

# Main function
main() {
    echo -e "${GREEN}"
    echo ""
    echo -e "      Linux AI Assistant - Startup Configuration${NC}"
    echo ""
    
    ACTION="${1:-help}"
    
    case "$ACTION" in
        enable|add|on)
            add_to_autostart
            ;;
        disable|remove|off)
            remove_from_autostart
            ;;
        check|status)
            check_autostart
            ;;
        *)
            echo "Usage: $0 [enable|disable|check]"
            echo ""
            echo "Commands:"
            echo "  enable   - Configure automatic startup"
            echo "  disable  - Remove from automatic startup"
            echo "  check    - Check the automatic startup state"
            echo ""
            check_autostart
            ;;
    esac
}

# Run
main "$@"
