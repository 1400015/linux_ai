#!/bin/bash

# Script de desinstalação para Linux AI Assistant

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Diretório do script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Função para remover ambiente virtual
remove_venv() {
    echo -e "${YELLOW}A remover ambiente virtual...${NC}"
    
    VENV_DIR="$PROJECT_DIR/venv"
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        echo -e "${GREEN}Ambiente virtual removido.${NC}"
    else
        echo -e "${BLUE}Ambiente virtual não encontrado.${NC}"
    fi
}

# Função para remover atalho de aplicação
remove_desktop_entry() {
    echo -e "${YELLOW}A remover atalho de aplicação...${NC}"
    
    DESKTOP_FILE="$HOME/.local/share/applications/linux-ai-assistant.desktop"
    if [ -f "$DESKTOP_FILE" ]; then
        rm "$DESKTOP_FILE"
        update-desktop-database "$HOME/.local/share/applications"
        echo -e "${GREEN}Atalho removido.${NC}"
    else
        echo -e "${BLUE}Atalho não encontrado.${NC}"
    fi
}

# Função para remover script de execução
remove_run_script() {
    echo -e "${YELLOW}A remover script de execução...${NC}"
    
    RUN_SCRIPT="$PROJECT_DIR/run.sh"
    if [ -f "$RUN_SCRIPT" ]; then
        rm "$RUN_SCRIPT"
        echo -e "${GREEN}Script de execução removido.${NC}"
    else
        echo -e "${BLUE}Script de execução não encontrado.${NC}"
    fi
}

# Função para remover ficheiros de configuração
remove_config() {
    echo -e "${YELLOW}A remover ficheiros de configuração...${NC}"
    
    CONFIG_DIR="$HOME/.config/linux_ai_assistant"
    if [ -d "$CONFIG_DIR" ]; then
        rm -rf "$CONFIG_DIR"
        echo -e "${GREEN}Ficheiros de configuração removidos.${NC}"
    else
        echo -e "${BLUE}Ficheiros de configuração não encontrados.${NC}"
    fi
}

# Função para remover cache
remove_cache() {
    echo -e "${YELLOW}A remover cache...${NC}"
    
    CACHE_DIR="$HOME/.cache/linux_ai_assistant"
    if [ -d "$CACHE_DIR" ]; then
        rm -rf "$CACHE_DIR"
        echo -e "${GREEN}Cache removido.${NC}"
    else
        echo -e "${BLUE}Cache não encontrado.${NC}"
    fi
}

# Função principal
main() {
    echo -e "${RED}"
    echo ""
    echo -e "      Linux AI Assistant - Desinstalação${NC}"
    echo ""
    
    # Pedir confirmação
    read -p "Tem a certeza que quer desinstalar o Linux AI Assistant? [s/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Ss]$ ]]; then
        echo -e "${BLUE}Desinstalação cancelada.${NC}"
        exit 0
    fi
    
    # Remover componentes
    remove_venv
    remove_desktop_entry
    remove_run_script
    remove_config
    remove_cache
    
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Desinstalação concluída!           ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
}

# Executar
main "$@"
