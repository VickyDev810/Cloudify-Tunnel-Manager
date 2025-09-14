#!/bin/bash
# Cloudify CLI Installer for macOS

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'
BOLD='\033[1m'

# Configuration
REPO_URL="https://github.com/VickyDev810/Cloudify-Tunnel-Manager.git"
TOOL_NAME="cloudify"
TEMP_DIR="/tmp/cloudify-install"

# State
VERBOSE=false
SUDO_CMD=""

# Logging functions
log()      { [ "$VERBOSE" = true ] && echo -e "${BLUE}→${NC} $1"; }
success()  { echo -e "${GREEN}✓${NC} $1"; }
error()    { echo -e "${RED}✗${NC} $1" >&2; }
warning()  { echo -e "${YELLOW}⚠${NC} $1"; }
info()     { echo -e "${BLUE}ℹ${NC} $1"; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

# Banner
print_banner() {
    echo ""
    echo -e "${BOLD}${PURPLE}╔════════════════════════════════╗${NC}"
    echo -e "${BOLD}${PURPLE}║   Cloudify CLI (Mac Version)   ║${NC}"
    echo -e "${BOLD}${PURPLE}╚════════════════════════════════╝${NC}"
    echo ""
}

# Privilege check
check_privileges() {
    if [ "$EUID" -eq 0 ]; then
        log "Running as root"
        SUDO_CMD=""
    else
        command_exists sudo || { error "sudo required"; exit 1; }
        if ! sudo -n true 2>/dev/null; then
            info "Requesting sudo..."
            sudo -v || { error "Cannot obtain sudo"; exit 1; }
        fi
        SUDO_CMD="sudo"
    fi
}

# Install Homebrew if missing
install_homebrew() {
    if command_exists brew; then
        success "Homebrew is already installed"
    else
        info "Installing Homebrew..."
        NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
        success "Homebrew installed"
    fi
}

# Install a package via Homebrew
install_package() {
    local pkg="$1"
    if brew list --formula | grep -q "^$pkg\$"; then
        success "$pkg already installed"
    else
        log "Installing $pkg..."
        brew install "$pkg"
        success "$pkg installed"
    fi
}

# Install cloudflared
install_cloudflared() {
    if command_exists cloudflared; then
        local version
        version=$(cloudflared version 2>/dev/null | head -n1 | awk '{print $3}' || echo "unknown")
        success "cloudflared already installed (version: $version)"
        return
    fi

    info "Installing cloudflared via Homebrew..."
    brew install cloudflared
    success "cloudflared installed"
}

# Install pipx
install_pipx() {
    if command_exists pipx; then
        success "pipx already installed"
        return
    fi

    info "Installing pipx..."
    brew install pipx
    pipx ensurepath
    success "pipx installed"
}

# Install your CLI tool
install_tool() {
    install_pipx

    if command_exists "$TOOL_NAME"; then
        warning "$TOOL_NAME already installed — reinstalling..."
    fi

    rm -rf "$TEMP_DIR"
    git clone --depth 1 "$REPO_URL" "$TEMP_DIR"
    cd "$TEMP_DIR"
    pipx install . --force || python3 -m pip install --user .

    export PATH="$HOME/.local/bin:$PATH"

    for profile in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$profile" ] && ! grep -q "$HOME/.local/bin" "$profile"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$profile"
        fi
    done

    success "$TOOL_NAME installed"
}

# Install all dependencies
install_packages() {
    log "Ensuring required packages are installed..."
    install_homebrew

    local packages=("git" "python@3.11" "pipx" "wget" "curl")
    for pkg in "${packages[@]}"; do
        install_package "$pkg"
    done

    install_cloudflared
    install_tool
}


# Main
main() {
    print_banner
    printf "Verbose output? (y/N): "
    read -r choice
    [[ "$choice" =~ ^(y|Y|yes)$ ]] && VERBOSE=true

    check_privileges
    install_packages

    success "Installation completed!"
    info "Next steps:"
    echo "  • Restart terminal or source your shell profile"
    echo "  • Configure: $TOOL_NAME setup"
    echo "  • Authenticate: cloudflared tunnel login"
    echo "  • Start: $TOOL_NAME serve"

}

trap 'error "Installation failed"; exit 1' ERR
trap 'warning "Installation interrupted"; exit 130' INT TERM

main "$@"
