#!/bin/bash

# --- Helper functions ---
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

get_node_version() {
    node -v | sed 's/^v//'
}

version_ge() {
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

print_error() {
    echo -e "\033[31mERROR:\033[0m $*" >&2
}

# --- Configuration ---
MIN_NODE_VERSION="18.0.0"

if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
elif [[ "$OSTYPE" == "linux"* ]]; then
    OS="Linux"
else
    OS="Other"
fi

if command_exists brew; then
    PACKAGE_MANAGER="brew"
else
    PACKAGE_MANAGER="unknown"
fi

# --- Main function ---
install_nodejs() {
    echo "Installing Node.js and npm"

    if command_exists node && command_exists npm; then
        local current_version
        current_version=$(get_node_version)
        if version_ge "$current_version" "$MIN_NODE_VERSION"; then
            echo "Node.js $current_version already installed"
            return 0
        else
            echo "Upgrading Node.js from $current_version to latest"
        fi
    fi

    if [ "$OS" = "macOS" ] && [ "$PACKAGE_MANAGER" = "brew" ]; then
        echo "Installing Node.js via Homebrew (macOS)"
        brew install node
        if command_exists node && command_exists npm; then
            local installed_version
            installed_version=$(get_node_version)
            if version_ge "$installed_version" "$MIN_NODE_VERSION"; then
                echo "Node.js $installed_version installed via Homebrew"
                return 0
            fi
        fi
    fi

    echo "Installing Node.js via nvm (Node Version Manager)"

    if ! command_exists nvm; then
        echo "Installing nvm..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

        local primary_shell=""
        if [ -n "$ZSH_VERSION" ]; then
            primary_shell="$HOME/.zshrc"
        elif [ -n "$BASH_VERSION" ]; then
            primary_shell="$HOME/.bashrc"
        else
            primary_shell="$HOME/.profile"
        fi

        if [ -f "$primary_shell" ] && ! grep -q "NVM_DIR" "$primary_shell" 2>/dev/null; then
            echo 'export NVM_DIR="$HOME/.nvm"' >> "$primary_shell"
            echo '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"' >> "$primary_shell"
            echo '[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"' >> "$primary_shell"
        fi
    fi

    if ! command_exists nvm; then
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        if ! command_exists nvm; then
            print_error "nvm installation failed"
            return 1
        fi
    fi

    echo "Installing Node.js LTS via nvm"
    nvm install --lts
    nvm use --lts
    nvm alias default lts/*

    if command_exists node && command_exists npm; then
        local installed_version
        installed_version=$(get_node_version)
        if version_ge "$installed_version" "$MIN_NODE_VERSION"; then
            echo "Node.js $installed_version and npm installed successfully"
            echo "nvm current: $(nvm current 2>/dev/null || echo 'N/A')"
            return 0
        else
            print_error "Node.js version $installed_version is below required $MIN_NODE_VERSION"
            return 1
        fi
    else
        print_error "Node.js installation verification failed"
        return 1
    fi
}

main() {
    install_nodejs
}

main "$@"
