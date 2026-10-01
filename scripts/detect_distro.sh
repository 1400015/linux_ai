#!/bin/bash

# Script para detetar a distribuição Linux e fornecer informações de compatibilidade
# Uso: ./detect_distro.sh

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo ""
echo -e "      Linux AI Assistant - Deteção de Distribuição${NC}"
echo ""

# Função para detetar distribuição
DetectDistribution() {
    if [ -f /etc/os-release ]; then
        # Ler informações do /etc/os-release
        . /etc/os-release
        
        # Verificar se é d77void
        if [ -n "$D77VOID_VERSION" ] || grep -qi "d77void" /etc/os-release 2>/dev/null; then
            echo "Distribuição: d77void"
            echo "Tipo: Void Linux based"
            echo "Init System: runit"
            echo "Package Manager: xbps"
            echo "Compatível: SIM"
            return 0
        fi
        
        # Verificar se é Void Linux
        if [ "$ID" = "void" ]; then
            echo "Distribuição: Void Linux"
            echo "Tipo: Independent"
            echo "Init System: runit"
            echo "Package Manager: xbps"
            echo "Compatível: SIM"
            return 0
        fi
        
        # Verificar outras distribuições
        if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
            echo "Distribuição: $PRETTY_NAME"
            echo "Tipo: Debian based"
            echo "Init System: systemd"
            echo "Package Manager: apt"
            echo "Compatível: SIM"
            return 0
        fi
        
        if [ "$ID" = "fedora" ]; then
            echo "Distribuição: $PRETTY_NAME"
            echo "Tipo: RedHat based"
            echo "Init System: systemd"
            echo "Package Manager: dnf"
            echo "Compatível: SIM"
            return 0
        fi
        
        if [ "$ID" = "arch" ] || [ "$ID" = "manjaro" ]; then
            echo "Distribuição: $PRETTY_NAME"
            echo "Tipo: Arch based"
            echo "Init System: systemd"
            echo "Package Manager: pacman"
            echo "Compatível: SIM"
            return 0
        fi
        
        if [ "$ID" = "opensuse" ] || [ "$ID" = "suse" ]; then
            echo "Distribuição: $PRETTY_NAME"
            echo "Tipo: SUSE based"
            echo "Init System: systemd"
            echo "Package Manager: zypper"
            echo "Compatível: SIM"
            return 0
        fi
        
        # Se não for nenhuma das conhecidas, mostrar informações genéricas
        echo "Distribuição: $PRETTY_NAME"
        echo "ID: $ID"
        echo "Compatível: Possivelmente (será testado durante a instalação)"
        return 1
    else
        echo -e "${RED}Não foi possível detetar a distribuição!${NC}"
        echo "Verifique se /etc/os-release existe."
        return 1
    fi
}

# Função para verificar gestor de pacotes
CheckPackageManager() {
    echo ""
    echo -e "${YELLOW}Verificar gestor de pacotes:${NC}"
    
    if command -v apt-get >/dev/null 2>&1; then
        echo "  ✓ apt-get (Debian/Ubuntu)"
        echo "  Comando de instalação: sudo apt-get install -y"
    fi
    
    if command -v dnf >/dev/null 2>&1; then
        echo "  ✓ dnf (Fedora)"
        echo "  Comando de instalação: sudo dnf install -y"
    fi
    
    if command -v yum >/dev/null 2>&1; then
        echo "  ✓ yum (CentOS/RHEL)"
        echo "  Comando de instalação: sudo yum install -y"
    fi
    
    if command -v pacman >/dev/null 2>&1; then
        echo "  ✓ pacman (Arch)"
        echo "  Comando de instalação: sudo pacman -S"
    fi
    
    if command -v zypper >/dev/null 2>&1; then
        echo "  ✓ zypper (openSUSE)"
        echo "  Comando de instalação: sudo zypper install -y"
    fi
    
    if command -v xbps-install >/dev/null 2>&1; then
        echo "  ✓ xbps (Void Linux/d77void)"
        echo "  Comando de instalação: sudo xbps-install -Sy"
    fi
}

# Função para verificar dependências
CheckDependencies() {
    echo ""
    echo -e "${YELLOW}Verificar dependências principais:${NC}"
    
    DEPENDENCIES=("python3" "python3-pip" "git" "scrot" "tesseract-ocr")
    
    for dep in "${DEPENDENCIES[@]}"; do
        if command -v "$dep" >/dev/null 2>&1; then
            echo "  ✓ $dep"
        else
            echo "  ✗ $dep (em falta)"
        fi
    done
}

# Função para mostrar recomendações
ShowRecommendations() {
    echo ""
    echo -e "${BLUE}Recomendações:${NC}"
    
    if command -v xbps-install >/dev/null 2>&1; then
        echo "  Para Void Linux/d77void:"
        echo "    ./scripts/install_void.sh"
        echo ""
    fi
    
    echo "  Para outras distribuições:"
    echo "    ./scripts/install.sh"
    echo ""
    echo "  Para instalação manual:"
    echo "    1. Instale as dependências listadas acima"
    echo "    2. python3 -m venv venv"
    echo "    3. source venv/bin/activate"
    echo "    4. pip install -r requirements.txt"
    echo "    5. ./run.sh"
}

# Função principal
main() {
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Deteção de Distribuição Linux      ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    
    # Detetar distribuição
    if DetectDistribution; then
        echo -e "${GREEN}✓ Distribuição detetada com sucesso!${NC}"
    else
        echo -e "${YELLOW}⚠ Distribuição não reconhecida, mas pode ser compatível${NC}"
    fi
    
    # Verificar gestor de pacotes
    CheckPackageManager
    
    # Verificar dependências
    CheckDependencies
    
    # Mostrar recomendações
    ShowRecommendations
    
    echo ""
    echo -e "${GREEN}=========================================${NC}"
}

# Executar
main "$@"
