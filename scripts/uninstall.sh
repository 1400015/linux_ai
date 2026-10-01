#!/bin/bash

# Uninstallation script for Linux AI Assistant

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

# Function to remove the virtual environment
remove_venv() {
    echo -e "${YELLOW}Removing the virtual environment...${NC}"
    
    VENV_DIR="$PROJECT_DIR/venv"
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        echo -e "${GREEN}Virtual environment removed.${NC}"
    else
        echo -e "${BLUE}Virtual environment not found.${NC}"
    fi
}

# Function to remove the application shortcut
remove_desktop_entry() {
    echo -e "${YELLOW}Removing the application shortcut...${NC}"
    
    DESKTOP_FILE="$HOME/.local/share/applications/linux-ai-assistant.desktop"
    if [ -f "$DESKTOP_FILE" ]; then
        rm "$DESKTOP_FILE"
        update-desktop-database "$HOME/.local/share/applications"
        echo -e "${GREEN}Shortcut removed.${NC}"
    else
        echo -e "${BLUE}Shortcut not found.${NC}"
    fi
}

# Function to remove the run script
remove_run_script() {
    echo -e "${YELLOW}Removing the run script...${NC}"
    
    RUN_SCRIPT="$PROJECT_DIR/run.sh"
    if [ -f "$RUN_SCRIPT" ]; then
        rm "$RUN_SCRIPT"
        echo -e "${GREEN}Run script removed.${NC}"
    else
        echo -e "${BLUE}Run script not found.${NC}"
    fi
}

# Function to remove the configuration files
remove_config() {
    echo -e "${YELLOW}Removing the configuration files...${NC}"
    
    CONFIG_DIR="$HOME/.config/linux_ai_assistant"
    if [ -d "$CONFIG_DIR" ]; then
        rm -rf "$CONFIG_DIR"
        echo -e "${GREEN}Configuration files removed.${NC}"
    else
        echo -e "${BLUE}Configuration files not found.${NC}"
    fi
}

# Function to remove the cache
remove_cache() {
    echo -e "${YELLOW}Removing the cache...${NC}"
    
    CACHE_DIR="$HOME/.cache/linux_ai_assistant"
    if [ -d "$CACHE_DIR" ]; then
        rm -rf "$CACHE_DIR"
        echo -e "${GREEN}Cache removed.${NC}"
    else
        echo -e "${BLUE}Cache not found.${NC}"
    fi
}

# Main function
main() {
    echo -e "${RED}"
    echo ""
    echo -e "      Linux AI Assistant - Uninstallation${NC}"
    echo ""
    
    # Ask for confirmation (an empty stdin/EOF cancels instead of aborting)
    read -p "Are you sure you want to uninstall Linux AI Assistant? [y/N] " -n 1 -r || REPLY=""
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Uninstallation cancelled.${NC}"
        exit 0
    fi
    
    # Remove components
    remove_venv
    remove_desktop_entry
    remove_run_script
    remove_config
    remove_cache
    
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Uninstallation completed!           ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
}

# Run
main "$@"
