#!/bin/bash

# Script to detect the Linux distribution and provide compatibility information
# Usage: ./detect_distro.sh

# Output colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo ""
echo -e "      Linux AI Assistant - Distribution Detection${NC}"
echo ""

# Function to detect the distribution
DetectDistribution() {
    if [ -f /etc/os-release ]; then
        # Read the information from /etc/os-release
        # shellcheck disable=SC1091  # so existe no sistema de destino
        . /etc/os-release
        
        # Check whether it is d77void
        if [ -n "$D77VOID_VERSION" ] || grep -qi "d77void" /etc/os-release 2>/dev/null; then
            echo "Distribution: d77void"
            echo "Type: Void Linux based"
            echo "Init System: runit"
            echo "Package Manager: xbps"
            echo "Compatible: YES"
            return 0
        fi
        
        # Check whether it is Void Linux
        if [ "$ID" = "void" ]; then
            echo "Distribution: Void Linux"
            echo "Type: Independent"
            echo "Init System: runit"
            echo "Package Manager: xbps"
            echo "Compatible: YES"
            return 0
        fi
        
        # Check the other distributions
        if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
            echo "Distribution: $PRETTY_NAME"
            echo "Type: Debian based"
            echo "Init System: systemd"
            echo "Package Manager: apt"
            echo "Compatible: YES"
            return 0
        fi
        
        if [ "$ID" = "fedora" ]; then
            echo "Distribution: $PRETTY_NAME"
            echo "Type: RedHat based"
            echo "Init System: systemd"
            echo "Package Manager: dnf"
            echo "Compatible: YES"
            return 0
        fi
        
        if [ "$ID" = "arch" ] || [ "$ID" = "manjaro" ]; then
            echo "Distribution: $PRETTY_NAME"
            echo "Type: Arch based"
            echo "Init System: systemd"
            echo "Package Manager: pacman"
            echo "Compatible: YES"
            return 0
        fi
        
        if [ "$ID" = "opensuse" ] || [ "$ID" = "suse" ]; then
            echo "Distribution: $PRETTY_NAME"
            echo "Type: SUSE based"
            echo "Init System: systemd"
            echo "Package Manager: zypper"
            echo "Compatible: YES"
            return 0
        fi
        
        # If it is none of the known ones, show generic information
        echo "Distribution: $PRETTY_NAME"
        echo "ID: $ID"
        echo "Compatible: Possibly (it will be tested during installation)"
        return 1
    else
        echo -e "${RED}Could not detect the distribution!${NC}"
        echo "Check whether /etc/os-release exists."
        return 1
    fi
}

# Function to check the package manager
CheckPackageManager() {
    echo ""
    echo -e "${YELLOW}Package manager check:${NC}"
    
    if command -v apt-get >/dev/null 2>&1; then
        echo "  ✓ apt-get (Debian/Ubuntu)"
        echo "  Install command: sudo apt-get install -y"
    fi
    
    if command -v dnf >/dev/null 2>&1; then
        echo "  ✓ dnf (Fedora)"
        echo "  Install command: sudo dnf install -y"
    fi
    
    if command -v yum >/dev/null 2>&1; then
        echo "  ✓ yum (CentOS/RHEL)"
        echo "  Install command: sudo yum install -y"
    fi
    
    if command -v pacman >/dev/null 2>&1; then
        echo "  ✓ pacman (Arch)"
        echo "  Install command: sudo pacman -S"
    fi
    
    if command -v zypper >/dev/null 2>&1; then
        echo "  ✓ zypper (openSUSE)"
        echo "  Install command: sudo zypper install -y"
    fi
    
    if command -v xbps-install >/dev/null 2>&1; then
        echo "  ✓ xbps (Void Linux/d77void)"
        echo "  Install command: sudo xbps-install -Sy"
    fi
}

# Function to check the dependencies
CheckDependencies() {
    echo ""
    echo -e "${YELLOW}Main dependencies check:${NC}"
    
    # command -v procura BINÁRIOS: "python3-pip"/"tesseract-ocr" são nomes
    # de PACOTES e falhavam sempre (relatório acusava "missing" com tudo
    # instalado). Os binários são pip3/tesseract.
    DEPENDENCIES=("python3" "pip3" "git" "scrot" "tesseract")

    for dep in "${DEPENDENCIES[@]}"; do
        if command -v "$dep" >/dev/null 2>&1; then
            echo "  ✓ $dep"
        else
            echo "  ✗ $dep (missing)"
        fi
    done
}

# Function to show the recommendations
ShowRecommendations() {
    echo ""
    echo -e "${BLUE}Recommendations:${NC}"
    
    if command -v xbps-install >/dev/null 2>&1; then
        echo "  For Void Linux/d77void:"
        echo "    ./scripts/install_void.sh"
        echo ""
    fi
    
    echo "  For other distributions:"
    echo "    ./scripts/install.sh"
    echo ""
    echo "  For a manual installation:"
    echo "    1. Install the dependencies listed above"
    echo "    2. python3 -m venv venv"
    echo "    3. source venv/bin/activate"
    echo "    4. pip install -r requirements.txt"
    echo "    5. ./run.sh"
}

# Main function
main() {
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Linux Distribution Detection      ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    
    # Detect the distribution
    if DetectDistribution; then
        echo -e "${GREEN}✓ Distribution detected successfully!${NC}"
    else
        echo -e "${YELLOW}⚠ Unrecognized distribution, but it may still be compatible${NC}"
    fi
    
    # Check the package manager
    CheckPackageManager
    
    # Check the dependencies
    CheckDependencies
    
    # Show the recommendations
    ShowRecommendations
    
    echo ""
    echo -e "${GREEN}=========================================${NC}"
}

# Run
main "$@"
