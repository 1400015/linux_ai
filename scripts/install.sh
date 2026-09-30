#!/bin/bash

# Script de instalação para Linux AI Assistant
# Uso: ./install.sh [--dev]

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

# Função para verificar se um comando existe
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Função para instalar pacotes
install_packages() {
    local packages=("$@")
    
    echo -e "${YELLOW}Verificar dependências do sistema...${NC}"
    
    # Detectar distribuidor
    if command_exists apt-get; then
        PKG_MANAGER="apt-get"
        UPDATE_CMD="sudo apt-get update"
        INSTALL_CMD="sudo apt-get install -y"
    elif command_exists dnf; then
        PKG_MANAGER="dnf"
        UPDATE_CMD="sudo dnf makecache"
        INSTALL_CMD="sudo dnf install -y"
    elif command_exists yum; then
        PKG_MANAGER="yum"
        UPDATE_CMD="sudo yum makecache"
        INSTALL_CMD="sudo yum install -y"
    elif command_exists pacman; then
        PKG_MANAGER="pacman"
        UPDATE_CMD="sudo pacman -Sy"
        INSTALL_CMD="sudo pacman -S --noconfirm"
    elif command_exists zypper; then
        PKG_MANAGER="zypper"
        UPDATE_CMD="sudo zypper refresh"
        INSTALL_CMD="sudo zypper install -y"
    else
        echo -e "${RED}Não foi possível detectar o gestor de pacotes!${NC}"
        exit 1
    fi
    
    echo -e "${BLUE}Detetado gestor de pacotes: $PKG_MANAGER${NC}"
    
    # Pacotes necessários
    REQUIRED_PACKAGES=(
        "python3"
        "python3-pip"
        "python3-venv"
        "git"
        "scrot"
        "tesseract-ocr"
        "tesseract-ocr-por"
        "tesseract-ocr-eng"
        "libgtk-3-0"
        "libgtk-3-dev"
        "python3-gi"
        "python3-gi-cairo"
        "gir1.2-gtk-3.0"
        "gir1.2-appindicator3-0.1"
    )
    
    # Verificar e instalar pacotes em falta
    MISSING_PACKAGES=()
    for pkg in "${REQUIRED_PACKAGES[@]}"; do
        if ! command_exists "$pkg" && ! dpkg -l "$pkg" >/dev/null 2>&1 && ! rpm -q "$pkg" >/dev/null 2>&1; then
            MISSING_PACKAGES+=("$pkg")
        fi
    done
    
    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        echo -e "${YELLOW}Pacotes em falta: ${MISSING_PACKAGES[*]}${NC}"
        echo -e "${BLUE}A instalar pacotes...${NC}"
        
        # Atualizar cache
        eval "$UPDATE_CMD"
        
        # Instalar pacotes
        eval "$INSTALL_CMD ${MISSING_PACKAGES[*]}"
        
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
    update-desktop-database "$DESKTOP_DIR"
    
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
        if command_exists convert; then
            # Criar ícone com ImageMagick
            convert -size 64x64 xc:black -fill white -draw "circle 32,32 32,16" "$ICON_DIR/icon.png"
        else
            # Copiar ícone padrão
            if [ -f "/usr/share/icons/hicolor/64x64/apps/system-run.png" ]; then
                cp "/usr/share/icons/hicolor/64x64/apps/system-run.png" "$ICON_DIR/icon.png"
            else
                # Criar ficheiro vazio como placeholder
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
    echo -e "${YELLOW}Notas:${NC}"
    echo "  - O assistente será executado com privilégios normais."
    echo "  - Para funcionalidades que requerem sudo, será pedido password."
    echo "  - O modo especialista permite editar ficheiros de configuração."
    echo ""
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
    echo -e "      Linux AI Assistant - Instalação${NC}"
    echo ""
    
    # Instalar dependências do sistema
    install_packages
    
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
    
    # Mostrar instruções finais
    show_final_instructions
}

# Executar
main "$@"
