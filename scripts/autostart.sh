#!/bin/bash

# Script to configure automatic startup

set -e

# Output colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Function to add to automatic startup
add_to_autostart() {
    echo -e "${YELLOW}Configuring automatic startup...${NC}"

    # This is the explicit enable path: ensure the supplied icon is installed
    # and reuse the correctly escaped command for this checkout.
    bash "$SCRIPT_DIR/install-desktop.sh"
    DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/linux-ai-assistant.desktop"
    AUTOSTART_EXEC=""
    while IFS= read -r line; do
        if [[ "$line" == Exec=* ]]; then
            AUTOSTART_EXEC="${line#Exec=}"
            AUTOSTART_EXEC="${AUTOSTART_EXEC% --show}"
            break
        fi
    done < "$DESKTOP_FILE"
    if [ -z "$AUTOSTART_EXEC" ]; then
        echo "Could not read the application startup command." >&2
        return 1
    fi
    
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
Exec=$AUTOSTART_EXEC
Icon=io.github.linux_ai_assistant
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
            # `check_autostart` returns 1 when not configured; that must not
            # abort the script under `set -e` nor fail the help path.
            check_autostart || true
            if [ "$#" -eq 0 ]; then
                # No arguments means help: show the usage and succeed
                exit 0
            fi
            # An explicitly unknown argument is a usage error
            exit 1
            ;;
    esac
}

# Run
main "$@"
