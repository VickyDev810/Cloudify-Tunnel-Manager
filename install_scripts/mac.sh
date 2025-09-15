#!/bin/bash

# Cloudify Tunnel Manager - macOS Installation Script
# This script automates the installation process for Cloudify Tunnel Manager

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Error tracking
ERROR_LOG="/tmp/cloudify_install_errors.log"
HAS_CRITICAL_ERRORS=false

# Initialize error log
echo "Cloudify Tunnel Manager Installation Error Log" > "$ERROR_LOG"
echo "Date: $(date)" >> "$ERROR_LOG"
echo "===========================================" >> "$ERROR_LOG"

# Function to log errors
log_error() {
    local error_msg="$1"
    local is_critical="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $error_msg" >> "$ERROR_LOG"
    if [ "$is_critical" = "true" ]; then
        HAS_CRITICAL_ERRORS=true
        echo "[CRITICAL] $error_msg" >> "$ERROR_LOG"
    fi
}

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to install Homebrew if not present
install_homebrew() {
    if ! command_exists brew; then
        print_status "Installing Homebrew..."
        if /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" 2>>"$ERROR_LOG"; then
            # Add Homebrew to PATH for Apple Silicon Macs
            if [[ $(uname -m) == "arm64" ]]; then
                echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile 2>>"$ERROR_LOG" || log_error "Failed to update .zprofile for Apple Silicon" false
                eval "$(/opt/homebrew/bin/brew shellenv)" 2>>"$ERROR_LOG" || log_error "Failed to eval homebrew shellenv for Apple Silicon" false
            else
                echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile 2>>"$ERROR_LOG" || log_error "Failed to update .zprofile for Intel" false
                eval "$(/usr/local/bin/brew shellenv)" 2>>"$ERROR_LOG" || log_error "Failed to eval homebrew shellenv for Intel" false
            fi
            print_success "Homebrew installed successfully"
        else
            log_error "Homebrew installation failed" true
            print_error "Homebrew installation had issues (logged)"
        fi
    else
        print_status "Homebrew is already installed"
    fi
}

# Function to install pipx
install_pipx() {
    if ! command_exists pipx; then
        print_status "Installing pipx..."
        brew install pipx 2>>"$ERROR_LOG" || log_error "pipx brew install command failed" false
        
        # Try to fix Python linking issues silently
        brew link --overwrite python@3.13 2>>"$ERROR_LOG" || log_error "Python linking failed (non-critical)" false
        
        pipx ensurepath 2>>"$ERROR_LOG" || log_error "pipx ensurepath failed" false
        
        if command_exists pipx; then
            print_success "pipx installed successfully"
        else
            log_error "pipx not available after installation" true
            print_error "pipx installation had critical issues (logged)"
        fi
    else
        print_status "pipx is already installed"
        pipx ensurepath 2>>"$ERROR_LOG" || log_error "pipx ensurepath failed for existing installation" false
    fi
}

# Function to install cloudflared
install_cloudflared() {
    if ! command_exists cloudflared; then
        print_status "Installing cloudflared..."
        if brew install cloudflared 2>>"$ERROR_LOG"; then
            print_success "cloudflared installed successfully"
        else
            log_error "cloudflared installation failed" false
            print_warning "cloudflared installation had issues (logged, but not critical)"
        fi
    else
        print_status "cloudflared is already installed"
    fi
}

# Function to clone and install the tool
install_cloudify_tunnel_manager() {
    local repo_url="https://github.com/VickyDev810/Cloudify-Tunnel-Manager.git"
    local temp_dir="/tmp/cloudify-tunnel-manager"
    
    print_status "Cloning Cloudify Tunnel Manager repository..."
    
    # Remove existing temp directory if it exists
    rm -rf "$temp_dir" 2>>"$ERROR_LOG" || log_error "Failed to remove existing temp directory" false
    
    # Clone the repository
    if git clone "$repo_url" "$temp_dir" 2>>"$ERROR_LOG"; then
        print_status "Repository cloned successfully"
    else
        log_error "Git clone failed for Cloudify Tunnel Manager repository" true
        print_error "Failed to clone repository (logged)"
        return 1
    fi
    
    # Change to the repository directory
    cd "$temp_dir" || {
        log_error "Failed to change to repository directory" true
        print_error "Failed to access repository directory (logged)"
        return 1
    }
    
    print_status "Installing Cloudify Tunnel Manager..."
    
    # Ensure pipx is in PATH for this session
    export PATH="$HOME/.local/bin:$PATH"
    
    # Install with pipx
    if pipx install . --force 2>>"$ERROR_LOG"; then
        print_success "Cloudify Tunnel Manager installed with pipx"
    else
        log_error "pipx install command failed for Cloudify Tunnel Manager" true
        print_error "pipx installation failed (logged)"
        cd - > /dev/null 2>&1
        return 1
    fi
    
    # Ensure pipx path is in shell profile
    pipx ensurepath 2>>"$ERROR_LOG" || log_error "pipx ensurepath failed after tool installation" false
    
    # Clean up temporary directory
    cd - > /dev/null 2>&1
    rm -rf "$temp_dir" 2>>"$ERROR_LOG" || log_error "Failed to cleanup temporary directory" false
}

# Function to update shell profile
update_shell_profile() {
    local shell_profile=""
    
    # Determine which shell profile to update
    if [ -n "$ZSH_VERSION" ]; then
        shell_profile="$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ]; then
        shell_profile="$HOME/.bash_profile"
    else
        shell_profile="$HOME/.zshrc"  # Default to zsh
    fi
    
    print_status "Updating shell profile: $shell_profile"
    
    # Ensure pipx path is added to shell profile
    if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$shell_profile" 2>/dev/null; then
        if echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_profile" 2>>"$ERROR_LOG"; then
            print_status "Added pipx path to $shell_profile"
        else
            log_error "Failed to update shell profile: $shell_profile" false
            print_warning "Could not update shell profile (logged)"
        fi
    fi
    
    # Source the profile to update current session
    source "$shell_profile" 2>>"$ERROR_LOG" || log_error "Failed to source shell profile" false
    
    # Also update current session PATH
    export PATH="$HOME/.local/bin:$PATH"
}

# Function to show error summary
show_error_summary() {
    if [ "$HAS_CRITICAL_ERRORS" = "true" ]; then
        echo
        print_error "CRITICAL ERRORS DETECTED!"
        print_error "Installation may not work properly."
        print_error "Error log saved to: $ERROR_LOG"
        echo
        print_status "Critical errors found in log:"
        grep "\[CRITICAL\]" "$ERROR_LOG" | while read -r line; do
            echo "  • ${line#*] }"
        done
        echo
        print_warning "Please check the full error log for details:"
        print_warning "cat $ERROR_LOG"
    elif [ -s "$ERROR_LOG" ] && [ "$(wc -l < "$ERROR_LOG")" -gt 3 ]; then
        print_warning "Some non-critical errors occurred during installation"
        print_warning "Error log saved to: $ERROR_LOG"
        print_status "You can review it with: cat $ERROR_LOG"
    else
        print_success "Installation completed without significant errors!"
        rm -f "$ERROR_LOG" 2>/dev/null
    fi
}

# Main installation function
main() {
    echo -e "${BLUE}"
    echo "=================================="
    echo "Cloudify Tunnel Manager Installer"
    echo "=================================="
    echo -e "${NC}"
    
    # Check if Git is installed
    if ! command_exists git; then
        log_error "Git is not installed - required for installation" true
        print_error "Git is not installed. Please install Git first:"
        print_error "Visit: https://git-scm.com/download/mac"
        show_error_summary
        exit 1
    fi
    
    # Check if running on macOS
    if [[ "$OSTYPE" != "darwin"* ]]; then
        log_error "Script run on non-macOS system: $OSTYPE" true
        print_error "This script is designed for macOS only"
        show_error_summary
        exit 1
    fi
    
    print_status "Starting installation process..."
    echo "Errors will be logged to: $ERROR_LOG"
    echo
    
    # Install dependencies (continue regardless of errors)
    install_homebrew
    install_pipx
    install_cloudflared
    
    # Install the main tool
    install_cloudify_tunnel_manager
    
    # Update shell profile
    update_shell_profile
    
    echo
    echo -e "${GREEN}"
    echo "=================================="
    echo "Installation Process Completed!"
    echo "=================================="
    echo -e "${NC}"
    
    # Test if cloudify command is available
    if command_exists cloudify; then
        print_success "Cloudify Tunnel Manager is ready to use!"
        print_status "Test it with: cloudify --help"
    else
        log_error "cloudify command not available after installation" true
        print_warning "Cloudify command not found. Try:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo "  source ~/.zshrc"
        echo "  cloudify --help"
    fi
    
    echo
    show_error_summary
    echo
    print_status "Installation complete!"
}

# Run the main function
main "$@"