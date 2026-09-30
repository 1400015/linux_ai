#!/bin/bash

# Script para configurar inicialização automática

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

# Função para adicionar à inicialização automática
add_to_autostart() {
    echo -e "${YELLOW}A configurar inicialização automática...${NC}"
    
    # Diretório de autostart
    AUTOSTART_DIR="$HOME/.config/autostart"
    mkdir -p "$AUTOSTART_DIR"
    
    # Criar ficheiro .desktop para autostart
    AUTOSTART_FILE="$AUTOSTART_DIR/linux-ai-assistant.desktop"
    
    cat > "$AUTOSTART_FILE" <<EOL
[Desktop Entry]
Version=1.0
Type=Application
Name=Linux AI Assistant
Comment=Assistente de IA permanente para Linux
Exec=$PROJECT_DIR/run.sh
Icon=$PROJECT_DIR/assets/icon.png
Terminal=false
Categories=Utility;System;
StartupWMClass=linux-ai-assistant
Hidden=false
EOL
    
    chmod +x "$AUTOSTART_FILE"
    
    echo -e "${GREEN}Inicialização automática configurada!${NC}"
    echo "A aplicação irá iniciar automaticamente na próxima vez que fazer login."
}

# Função para remover da inicialização automática
remove_from_autostart() {
    echo -e "${YELLOW}A remover da inicialização automática...${NC}"
    
    AUTOSTART_FILE="$HOME/.config/autostart/linux-ai-assistant.desktop"
    
    if [ -f "$AUTOSTART_FILE" ]; then
        rm "$AUTOSTART_FILE"
        echo -e "${GREEN}Inicialização automática removida!${NC}"
    else
        echo -e "${BLUE}A aplicação já não está configurada para iniciar automaticamente.${NC}"
    fi
}

# Função para verificar estado
check_autostart() {
    AUTOSTART_FILE="$HOME/.config/autostart/linux-ai-assistant.desktop"
    
    if [ -f "$AUTOSTART_FILE" ]; then
        echo -e "${GREEN}A aplicação está configurada para iniciar automaticamente.${NC}"
        return 0
    else
        echo -e "${BLUE}A aplicação NÃO está configurada para iniciar automaticamente.${NC}"
        return 1
    fi
}

# Função principal
main() {
    echo -e "${GREEN}"
    echo "  _    _      _ _       __        __         _   _"
    echo " | |  | |    | | |       \ \      / /        | | | |"
    echo " | |__| | ___| | | ___    \ \ /\ / /__  _ __ | |_| |__   ___  _ __"
    echo " |  __  |/ _ \ | |/ _ \    \ V  V / _ \ | '_ \| __| '_ \ / _ \| '_ \"
    echo " | |  | |  __/ | | (_) |    | |\_/ (_) || | | | |_| | | | (_) | | | |"
    echo " |_|  |_|\___|_|_|\___/      | |_|\___/ |_| |_|\__|_| |_|\___/|_| |_|"
    echo ""
    echo -e "      Linux AI Assistant - Configuração de Inicialização${NC}"
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
            echo "Uso: $0 [enable|disable|check]"
            echo ""
            echo "Comandos:"
            echo "  enable   - Configurar inicialização automática"
            echo "  disable  - Remover da inicialização automática"
            echo "  check    - Verificar estado da inicialização automática"
            echo ""
            check_autostart
            ;;
    esac
}

# Executar
main "$@"
