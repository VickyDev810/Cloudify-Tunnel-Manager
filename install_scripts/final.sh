#!/bin/bash
# Cloudify CLI Streamlined Installer

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

# System variables
OS=""
ARCH=""
PACKAGE_MANAGER=""
SERVICE_MANAGER=""
VERBOSE=false
SUDO_CMD=""

# Package mappings
declare -A PACKAGE_MAP
declare -A INSTALL_COMMANDS
declare -A UPDATE_COMMANDS
declare -A CHECK_COMMANDS

# Logging
log() { [ "$VERBOSE" = true ] && echo -e "${BLUE}→${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1" >&2; }
warning() { echo -e "${YELLOW}⚠${NC} $1"; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

command_exists() { command -v "$1" >/dev/null 2>&1; }
python_package_exists() { python3 -c "import $1" >/dev/null 2>&1; }

execute_cmd() {
    local cmd="$1"
    if [ "$VERBOSE" = true ]; then
        eval "$cmd"
    else
        eval "$cmd" >/dev/null 2>&1
    fi
}

# Banner
print_banner() {
    echo ""
    echo -e "${BOLD}${PURPLE}╔════════════════════════════════╗${NC}"
    echo -e "${BOLD}${PURPLE}║      Cloudify CLI Installer    ║${NC}"
    echo -e "${BOLD}${PURPLE}╚════════════════════════════════╝${NC}"
    echo ""
}

# Initialize package mappings
init_package_mappings() {
    local managers=("apt" "dnf" "yum" "pacman" "zypper" "apk" "brew")

    declare -A defaults=(
        [curl]="curl"
        [wget]="wget"
        [git]="git"
        [python3]="python3"
        [pip]="python3-pip"
        [systemctl]="systemd"
        [systemd]="systemd"
    )

    declare -A overrides
    overrides[apt_pip]="python3-pip"
    overrides[apt_venv]="python3-venv"

    overrides[pacman_python3]="python"
    overrides[pacman_pip]="python-pip"

    overrides[apk_pip]="py3-pip"
    overrides[apk_systemctl]="openrc"
    overrides[apk_systemd]="openrc"

    overrides[brew_python3]="python@3.11"

    # Populate PACKAGE_MAP
    for manager in "${managers[@]}"; do
        for pkg in "${!defaults[@]}"; do
            key="${manager}_${pkg}"
            PACKAGE_MAP[$key]="${overrides[$key]:-${defaults[$pkg]}}"
        done
    done

    # Install commands
    INSTALL_COMMANDS[apt]="apt install -y"
    INSTALL_COMMANDS[dnf]="dnf install -y"
    INSTALL_COMMANDS[yum]="yum install -y"
    INSTALL_COMMANDS[pacman]="pacman -S --noconfirm"
    INSTALL_COMMANDS[zypper]="zypper install -y"
    INSTALL_COMMANDS[apk]="apk add"
    INSTALL_COMMANDS[brew]="brew install"

    # Update commands
    UPDATE_COMMANDS[apt]="apt update -qq"
    UPDATE_COMMANDS[dnf]="dnf makecache -q"
    UPDATE_COMMANDS[yum]="yum makecache -q"
    UPDATE_COMMANDS[pacman]="pacman -Sy --noconfirm"
    UPDATE_COMMANDS[zypper]="zypper ref"
    UPDATE_COMMANDS[apk]="apk update"
    UPDATE_COMMANDS[brew]="brew update"

    # Check commands
    CHECK_COMMANDS[apt]="dpkg -l"
    CHECK_COMMANDS[dnf]="rpm -q"
    CHECK_COMMANDS[yum]="rpm -q"
    CHECK_COMMANDS[pacman]="pacman -Qs"
    CHECK_COMMANDS[zypper]="zypper se -i"
    CHECK_COMMANDS[apk]="apk info -e"
    CHECK_COMMANDS[brew]="brew list"
}

# Check if package installed
package_installed() {
    local package="$1"
    local cmd="${CHECK_COMMANDS[$PACKAGE_MANAGER]}"
    [ -z "$cmd" ] && return 1
    $cmd "$package" >/dev/null 2>&1
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

# Install a system package
install_system_package() {
    local pkg="$1"
    local actual="${PACKAGE_MAP[${PACKAGE_MANAGER}_$pkg]:-$pkg}"

    if package_installed "$actual"; then
        success "$actual already installed"
        return
    fi

    log "Installing $actual..."
    execute_cmd "$SUDO_CMD ${INSTALL_COMMANDS[$PACKAGE_MANAGER]} $actual"
    success "$actual installed"
}

# Install cloudflared
install_cloudflared() {
    echo "Installing cloudflared"
    
    if command_exists cloudflared; then
        local current_version=$(cloudflared version 2>/dev/null | head -n1 | awk '{print $3}' || echo "unknown")
        echo "cloudflared is already installed (version: $current_version)"
        return 0
    fi
    
    local cloudflared_url=""
    local filename=""
    
    # Determine download URL based on OS and architecture
    case "$OS" in
        "Linux")
            case "$ARCH" in
                "amd64")    filename="cloudflared-linux-amd64";;
                "arm64")    filename="cloudflared-linux-arm64";;
                "arm")      filename="cloudflared-linux-arm";;
                "386")      filename="cloudflared-linux-386";;
                *)          echo "Unsupported Linux architecture: $ARCH"; return 1;;
            esac
            cloudflared_url="https://github.com/cloudflare/cloudflared/releases/latest/download/$filename"
            ;;
        "macOS")
            case "$ARCH" in
                "amd64")    filename="cloudflared-darwin-amd64.tgz";;
                "arm64")    filename="cloudflared-darwin-amd64.tgz";;  # Use amd64 for compatibility
                *)          echo "Unsupported macOS architecture: $ARCH"; return 1;;
            esac
            cloudflared_url="https://github.com/cloudflare/cloudflared/releases/latest/download/$filename"
            ;;
        *)
            echo "Unsupported OS: $OS"
            return 1
            ;;
    esac
    
    echo "Downloading cloudflared from GitHub releases"
    
    # Create temporary directory for download
    local temp_cf_dir="/tmp/cloudflared-install"
    mkdir -p "$temp_cf_dir"
    
    # Download cloudflared
    if command_exists curl; then
        curl -fsSL -o "$temp_cf_dir/cloudflared" "$cloudflared_url"
    elif command_exists wget; then
        wget -q -O "$temp_cf_dir/cloudflared" "$cloudflared_url"
    else
        echo "Neither curl nor wget available for download"
        return 1
    fi
    
    # Handle macOS tarball
    if [ "$OS" = "macOS" ] && [[ "$filename" == *.tgz ]]; then
        cd "$temp_cf_dir"
        tar -xzf cloudflared
        # The binary should be extracted as 'cloudflared'
        if [ ! -f "cloudflared" ]; then
            echo "cloudflared binary not found after extraction"
            return 1
        fi
    fi
    
    # Make executable
    chmod +x "$temp_cf_dir/cloudflared"
    
    # Install to system location
    if [ "$EUID" -eq 0 ]; then
        # Running as root
        mv "$temp_cf_dir/cloudflared" /usr/local/bin/
    else
        # Use sudo
        sudo mv "$temp_cf_dir/cloudflared" /usr/local/bin/
    fi
    
    # Verify installation
    if command_exists cloudflared; then
        local installed_version=$(cloudflared version 2>/dev/null | head -n1 | awk '{print $3}' || echo "unknown")
        echo "cloudflared installed successfully (version: $installed_version)"
    else
        echo "cloudflared installation verification failed"
        return 1
    fi
    
    # Cleanup
    rm -rf "$temp_cf_dir"
}

# Install pipx as user
install_pipx() {
    command_exists pipx && { success "pipx already installed"; return; }
    log "Installing pipx as current user..."
    if command_exists pipx; then
        print_success "pipx is already installed"
        return 0
    fi
    
    case "$PACKAGE_MANAGER" in
        apt)
            # Try package manager first, fallback to pip
            if apt-cache show pipx >/dev/null 2>&1; then
                sudo apt install -y pipx
            else
                python3 -m pip install --user pipx
            fi
            ;;
        dnf|yum)
            if sudo $PACKAGE_MANAGER list pipx >/dev/null 2>&1; then
                sudo $PACKAGE_MANAGER install -y pipx
            else
                python3 -m pip install --user pipx
            fi
            ;;
        pacman)
            if pacman -Si python-pipx >/dev/null 2>&1; then
                sudo pacman -S --noconfirm python-pipx
            else
                python3 -m pip install --user pipx
            fi
            ;;
        brew)
            brew install pipx
            ;;
        *)
            # Universal fallback
            python3 -m pip install --user pipx
            ;;
    esac
    success "pipx installed"
}

# Install the CLI tool as user
install_tool() {
    install_pipx

    if command_exists "$TOOL_NAME"; then
        warning "$TOOL_NAME already installed, reinstalling..."
    fi

    rm -rf "$TEMP_DIR"
    git clone --depth 1 "$REPO_URL" "$TEMP_DIR"
    cd "$TEMP_DIR"
    pipx install . --force || python3 -m pip install --user .
    export PATH="$HOME/.local/bin:$PATH"

    # Ensure PATH in shell profile
    for profile in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$profile" ] && ! grep -q "$HOME/.local/bin" "$profile"; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$profile"
        fi
    done

    success "$TOOL_NAME installed"
}

# Detect system
detect_system() {
    log "Detecting system..."
    case "$(uname -s)" in
        Linux*) OS="Linux";;
        Darwin*) OS="macOS";;
        *) error "Unsupported OS"; exit 1;;
    esac
    case "$(uname -m)" in
        x86_64|amd64) ARCH="amd64";;
        aarch64|arm64) ARCH="arm64";;
        armv7l) ARCH="arm";;
        i386|i686) ARCH="386";;
        *) ARCH="amd64";;
    esac

    if [ "$OS" = "Linux" ]; then
        for pm in apt dnf yum pacman zypper apk; do
            command_exists "$pm" && PACKAGE_MANAGER="$pm" && break
        done
        [ -z "$PACKAGE_MANAGER" ] && { error "No supported package manager"; exit 1; }
        SERVICE_MANAGER=$(command_exists systemctl && echo "systemd" || echo "none")
    else
        PACKAGE_MANAGER="brew"
        SERVICE_MANAGER="launchd"
        command_exists brew || { SUDO_CMD=""; execute_cmd "/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""; }
    fi

    success "OS=$OS ARCH=$ARCH PM=$PACKAGE_MANAGER SERVICE=$SERVICE_MANAGER"
}

# Install required packages
install_packages() {
    log "Installing system dependencies..."
    init_package_mappings

    local pkgs=("curl" "wget" "git" "python3" "pip")
    # [ "$PACKAGE_MANAGER" = "apt" ] 
    for pkg in "${pkgs[@]}"; do
        install_system_package "$pkg"
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

    detect_system
    check_privileges
    install_packages

    success "Installation completed!"
    info "Next steps:"
    echo "  • Restart terminal or source shell profile"
    echo "  • Configure: $TOOL_NAME setup"
    echo "  • Authenticate: cloudflared tunnel login"
    echo "  • Start: $TOOL_NAME serve"
}

trap 'error "Installation failed"; exit 1' ERR
trap 'warning "Installation interrupted"; exit 130' INT TERM

main "$@"
