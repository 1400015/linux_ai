#!/bin/bash

# Script de instalação específico para Void Linux e d77void
# Uso: ./install_void.sh [--dev]

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Verificar se é root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Não execute este script como root!${NC}"
    echo "O script vai pedir privilégios sudo quando necessário."
    exit 1
fi

# Diretório do script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Modo de desenvolvimento
DEV_MODE=false
if [ "$1" == "--dev" ]; then
    DEV_MODE=true
    echo -e "${BLUE}Modo de desenvolvimento ativo${NC}"
fi

# Verificar se é Void Linux
if ! command -v xbps-install >/dev/null 2>&1; then
    echo -e "${RED}Este script é específico para Void Linux/d77void!${NC}"
    echo "Use ./install.sh para outras distribuições."
    exit 1
fi

echo -e "${GREEN}"
echo "  _    _      _ _       __        __         _   _"
echo " | |  | |    | | |       \ \      / /        | | | |"
echo " | |__| | ___| | | ___    \ \ /\ / /__  _ __ | |_| |__   ___  _ __"
echo " |  __  |/ _ \ | |/ _ \    \ V  V / _ \ | '_ \| __| '_ \ / _ \| '_ \"  
echo " | |  | |  __/ | | (_) |    | |\_/ (_) || | | | |_| | | | (_) | | | |"
echo " |_|  |_|\___|_|_|\___/      | |_|\___/ |_| |_|\__|_| |_|\___/|_| |_|"
echo ""
echo -e "      Linux AI Assistant - Instalação para Void Linux/d77void${NC}"
echo ""

# Função para verificar se um pacote está instalado
package_installed() {
    xbps-query -x "$1" >/dev/null 2>&1
}

# Função para instalar pacotes no Void
install_void_packages() {
    echo -e "${YELLOW}Verificar dependências do sistema para Void Linux...${NC}"
    
    # Pacotes necessários para Void Linux
    REQUIRED_PACKAGES=(
        "python3"
        "python3-pip"
        "python3-venv"
        "git"
        "scrot"
        "tesseract-ocr"
        "tesseract-ocr-por"
        "tesseract-ocr-eng"
        "libgtk-3"
        "libgtk-3-devel"
        "py3-gobject"
        "py3-cairo"
        "gobject-introspection"
        "libappindicator-gtk3"
        "ImageMagick"  # Para criar ícone se necessário
    )
    
    # Verificar e instalar pacotes em falta
    MISSING_PACKAGES=()
    for pkg in "${REQUIRED_PACKAGES[@]}"; do
        if ! package_installed "$pkg"; then
            MISSING_PACKAGES+=("$pkg")
        fi
    done
    
    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        echo -e "${YELLOW}Pacotes em falta: ${MISSING_PACKAGES[*]}${NC}"
        echo -e "${BLUE}A instalar pacotes...${NC}"
        
        # Atualizar repositórios
        sudo xbps-install -Su
        
        # Instalar pacotes um por um (xbps não tem suporte nativo para instalar múltiplos)
        for pkg in "${MISSING_PACKAGES[@]}"; do
            echo -e "${BLUE}  A instalar: $pkg${NC}"
            sudo xbps-install -Sy "$pkg"
        done
        
        echo -e "${GREEN}Pacotes instalados com sucesso!${NC}"
    else
        echo -e "${GREEN}Todos os pacotes necessários já estão instalados!${NC}"
    fi
}

# Função para criar ambiente virtual
create_venv() {
    echo -e "${YELLOW}A criar ambiente virtual Python...${NC}"
    
    # Criar diretório para o ambiente virtual
    VENV_DIR="$PROJECT_DIR/venv"
    if [ -d "$VENV_DIR" ] && [ "$DEV_MODE" = false ]; then
        echo -e "${BLUE}Ambiente virtual já existe. A removê-lo...${NC}"
        rm -rf "$VENV_DIR"
    fi
    
    # Criar ambiente virtual
    python3 -m venv "$VENV_DIR"
    
    # Ativar ambiente virtual e instalar dependências
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install -r "$PROJECT_DIR/requirements.txt"
    
    echo -e "${GREEN}Ambiente virtual criado com sucesso!${NC}"
}

# Função para criar atalho de aplicação
create_desktop_entry() {
    echo -e "${YELLOW}A criar atalho de aplicação...${NC}"
    
    # Diretório para ficheiros .desktop
    DESKTOP_DIR="$HOME/.local/share/applications"
    mkdir -p "$DESKTOP_DIR"
    
    # Criar ficheiro .desktop
    DESKTOP_FILE="$DESKTOP_DIR/linux-ai-assistant.desktop"
    
    cat > "$DESKTOP_FILE" <<EOL
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
EOL
    
    # Tornar executável
    chmod +x "$DESKTOP_FILE"
    
    # Atualizar base de dados de aplicações
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR"
    fi
    
    echo -e "${GREEN}Atalho criado em $DESKTOP_FILE${NC}"
}

# Função para criar script de execução
create_run_script() {
    echo -e "${YELLOW}A criar script de execução...${NC}"
    
    RUN_SCRIPT="$PROJECT_DIR/run.sh"
    
    cat > "$RUN_SCRIPT" <<EOL
#!/bin/bash

# Script de execução para Linux AI Assistant

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

# Ativar ambiente virtual
source "$VENV_DIR/bin/activate"

# Executar aplicação
python "$PROJECT_DIR/src/app.py"
EOL
    
    chmod +x "$RUN_SCRIPT"
    
    echo -e "${GREEN}Script de execução criado em $RUN_SCRIPT${NC}"
}

# Função para criar ícone
create_icon() {
    echo -e "${YELLOW}A criar ícone...${NC}"
    
    ICON_DIR="$PROJECT_DIR/assets"
    mkdir -p "$ICON_DIR"
    
    # Criar um ícone simples (se não existir)
    if [ ! -f "$ICON_DIR/icon.png" ]; then
        # Usar um ícone padrão do sistema
        if command -v convert >/dev/null 2>&1; then
            # Criar ícone com ImageMagick
            convert -size 64x64 xc:black -fill white -draw "circle 32,32 32,16" "$ICON_DIR/icon.png"
        else
            # Tentar copiar ícone padrão do Void
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
            
            # Se não encontrou nenhum, criar placeholder
            if [ ! -f "$ICON_DIR/icon.png" ]; then
                touch "$ICON_DIR/icon.png"
            fi
        fi
    fi
    
    echo -e "${GREEN}Ícone criado em $ICON_DIR/icon.png${NC}"
}

# Função para criar ficheiro de configuração
create_config() {
    echo -e "${YELLOW}A criar ficheiro de configuração...${NC}"
    
    CONFIG_DIR="$HOME/.config/linux_ai_assistant"
    mkdir -p "$CONFIG_DIR"
    
    # Copiar configuração default
    if [ ! -f "$CONFIG_DIR/config.json" ]; then
        cp "$PROJECT_DIR/config/config.json" "$CONFIG_DIR/config.json"
        echo -e "${GREEN}Configuração copiada para $CONFIG_DIR/config.json${NC}"
    else
        echo -e "${BLUE}Ficheiro de configuração já existe. A mantê-lo.${NC}"
    fi
    
    # Copiar .env.example
    if [ ! -f "$CONFIG_DIR/.env" ] && [ -f "$PROJECT_DIR/config/.env.example" ]; then
        cp "$PROJECT_DIR/config/.env.example" "$CONFIG_DIR/.env"
        echo -e "${GREEN}Ficheiro .env criado em $CONFIG_DIR/.env${NC}"
        echo "Por favor edite este ficheiro para adicionar as suas API Keys."
    fi
}

# Função para criar serviço runit (opcional para Void Linux)
create_runit_service() {
    echo -e "${YELLOW}A criar serviço runit (opcional)...${NC}"
    
    # Diretório para serviços de utilizador
    RUNIT_DIR="$HOME/.local/service"
    mkdir -p "$RUNIT_DIR"
    
    # Criar ficheiro run
    cat > "$RUNIT_DIR/linux-ai-assistant/run" <<EOL
#!/bin/sh
exec $PROJECT_DIR/run.sh
EOL
    
    chmod +x "$RUNIT_DIR/linux-ai-assistant/run"
    
    # Criar link simbólico para /etc/sv (requer sudo)
    echo -e "${BLUE}Para ativar o serviço runit, execute:${NC}"
    echo "  sudo ln -s $RUNIT_DIR/linux-ai-assistant /etc/sv/linux-ai-assistant"
    echo "  sudo ln -s /etc/sv/linux-ai-assistant /var/service/"
    echo ""
    echo -e "${BLUE}Para iniciar o serviço:${NC}"
    echo "  sv up linux-ai-assistant"
    echo ""
    echo -e "${BLUE}Para parar o serviço:${NC}"
    echo "  sv down linux-ai-assistant"
}

# Função para mostrar instruções finais
show_final_instructions() {
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Instalação concluída com sucesso!   ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    echo -e "${BLUE}Para executar a aplicação:${NC}"
    echo "  1. Edite o ficheiro de configuração:"
    echo "     nano ~/.config/linux_ai_assistant/.env"
    echo ""
    echo "  2. Adicione as suas API Keys (OpenRouter, Google AI Studio, etc.)"
    echo ""
    echo "  3. Execute a aplicação:"
    echo "     $PROJECT_DIR/run.sh"
    echo ""
    echo "  Ou use o atalho criado no menu de aplicações."
    echo ""
    echo -e "${BLUE}Para desinstalar:${NC}"
    echo "  Execute: ./scripts/uninstall.sh"
    echo ""
    echo -e "${YELLOW}Notas para Void Linux/d77void:${NC}"
    echo "  - O assistente será executado com privilégios normais."
    echo "  - Para funcionalidades que requerem sudo, será pedido password."
    echo "  - O modo especialista permite editar ficheiros de configuração."
    echo "  - Opcionalmente, pode configurar um serviço runit para iniciar automaticamente."
    echo "  - O d77void usa runit como init system em vez de systemd."
    echo ""
}

# Função principal
main() {
    # Instalar dependências do sistema
    install_void_packages
    
    # Criar ícone
    create_icon
    
    # Criar ambiente virtual
    create_venv
    
    # Criar script de execução
    create_run_script
    
    # Criar atalho de aplicação
    create_desktop_entry
    
    # Criar configuração
    create_config
    
    # Criar serviço runit (opcional)
    create_runit_service
    
    # Mostrar instruções finais
    show_final_instructions
}

# Executar
main "$@"
