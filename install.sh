#!/usr/bin/env bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PYTHON_VERSION="3.11.13"
VENV_DIR=".venv"

# Helper functions
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    else
        print_error "Unsupported OS: $OSTYPE"
        exit 1
    fi
    print_info "Detected OS: $OS"
}

# Install pyenv
install_pyenv() {
    if command -v pyenv &> /dev/null; then
        print_success "pyenv is already installed"
        return 0
    fi

    print_info "Installing pyenv..."

    if [[ "$OS" == "macos" ]]; then
        if ! command -v brew &> /dev/null; then
            print_error "Homebrew is not installed. Please install it from https://brew.sh"
            exit 1
        fi
        brew update
        brew install pyenv
    elif [[ "$OS" == "linux" ]]; then
        curl https://pyenv.run | bash

        # Add pyenv to PATH for current session
        export PYENV_ROOT="$HOME/.pyenv"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init -)"

        # Add to shell config
        SHELL_CONFIG=""
        if [[ -f "$HOME/.bashrc" ]]; then
            SHELL_CONFIG="$HOME/.bashrc"
        elif [[ -f "$HOME/.zshrc" ]]; then
            SHELL_CONFIG="$HOME/.zshrc"
        fi

        if [[ -n "$SHELL_CONFIG" ]]; then
            if ! grep -q 'PYENV_ROOT' "$SHELL_CONFIG"; then
                echo '' >> "$SHELL_CONFIG"
                echo 'export PYENV_ROOT="$HOME/.pyenv"' >> "$SHELL_CONFIG"
                echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> "$SHELL_CONFIG"
                echo 'eval "$(pyenv init -)"' >> "$SHELL_CONFIG"
                print_warning "Added pyenv to $SHELL_CONFIG. Please restart your shell or run: source $SHELL_CONFIG"
            fi
        fi
    fi

    print_success "pyenv installed successfully"
}

# Install system dependencies
install_system_deps() {
    print_info "Installing system dependencies..."

    if [[ "$OS" == "macos" ]]; then
        if ! command -v brew &> /dev/null; then
            print_error "Homebrew is required. Install it from https://brew.sh"
            exit 1
        fi
        brew install libxml2 libxslt ffmpeg
        print_success "Installed libxml2, libxslt, and ffmpeg via Homebrew"
    elif [[ "$OS" == "linux" ]]; then
        if command -v apt-get &> /dev/null; then
            sudo apt-get update
            sudo apt-get install -y libxml2-dev libxslt-dev ffmpeg build-essential libssl-dev zlib1g-dev \
                libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
                libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev
            print_success "Installed dependencies via apt-get"
        elif command -v yum &> /dev/null; then
            sudo yum install -y libxml2-devel libxslt-devel ffmpeg gcc zlib-devel bzip2 bzip2-devel \
                readline-devel sqlite sqlite-devel openssl-devel tk-devel libffi-devel xz-devel
            print_success "Installed dependencies via yum"
        else
            print_error "No supported package manager found (apt-get or yum)"
            exit 1
        fi
    fi
}

# Install Python version
install_python() {
    # Initialize pyenv for current session
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)" 2>/dev/null || true

    if pyenv versions --bare | grep -q "^${PYTHON_VERSION}$"; then
        print_success "Python $PYTHON_VERSION is already installed"
        return 0
    fi

    print_info "Installing Python $PYTHON_VERSION via pyenv..."
    pyenv install "$PYTHON_VERSION"
    print_success "Python $PYTHON_VERSION installed successfully"
}

# Create virtual environment
create_venv() {
    # Initialize pyenv for current session
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)" 2>/dev/null || true

    if [[ -d "$VENV_DIR" ]]; then
        print_warning "Virtual environment already exists at $VENV_DIR"
        read -p "Do you want to recreate it? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$VENV_DIR"
        else
            print_info "Using existing virtual environment"
            return 0
        fi
    fi

    print_info "Creating virtual environment with Python $PYTHON_VERSION..."

    # Use pyenv's Python to create venv
    PYTHON_PATH="$HOME/.pyenv/versions/$PYTHON_VERSION/bin/python"

    if [[ ! -f "$PYTHON_PATH" ]]; then
        print_error "Python $PYTHON_VERSION not found at $PYTHON_PATH"
        exit 1
    fi

    "$PYTHON_PATH" -m venv "$VENV_DIR"
    print_success "Virtual environment created at $VENV_DIR"
}

# Install tepub
install_tepub() {
    print_info "Installing TEPUB in development mode..."

    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    pip install --upgrade pip

    # Install tepub with dev dependencies
    pip install -e ".[dev]"

    print_success "TEPUB installed successfully"
}

# Verify installation
verify_installation() {
    print_info "Verifying installation..."

    source "$VENV_DIR/bin/activate"

    if command -v tepub &> /dev/null; then
        print_success "tepub command is available"
        tepub --version || true
    else
        print_error "tepub command not found"
        exit 1
    fi
}

# Print next steps
print_next_steps() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  TEPUB Installation Complete!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}Next Steps:${NC}"
    echo ""
    echo "1. Activate the virtual environment:"
    echo -e "   ${BLUE}source $VENV_DIR/bin/activate${NC}"
    echo ""
    echo "2. Set up your API key (choose one):"
    echo -e "   ${BLUE}export OPENAI_API_KEY=\"sk-...\"${NC}"
    echo -e "   ${BLUE}export ANTHROPIC_API_KEY=\"sk-ant-...\"${NC}"
    echo -e "   ${BLUE}export GEMINI_API_KEY=\"...\"${NC}"
    echo ""
    echo "   Or create a .env file:"
    echo -e "   ${BLUE}echo 'OPENAI_API_KEY=sk-...' > .env${NC}"
    echo ""
    echo "3. (Optional) Create global config:"
    echo -e "   ${BLUE}mkdir -p ~/.tepub${NC}"
    echo -e "   ${BLUE}cp config.example.yaml ~/.tepub/config.yaml${NC}"
    echo ""
    echo "4. Test TEPUB:"
    echo -e "   ${BLUE}tepub --help${NC}"
    echo ""
    echo "5. Quick start:"
    echo -e "   ${BLUE}tepub extract book.epub${NC}"
    echo -e "   ${BLUE}tepub translate book.epub --to \"Simplified Chinese\"${NC}"
    echo -e "   ${BLUE}tepub export book.epub --epub${NC}"
    echo ""
    echo -e "${GREEN}Documentation:${NC} README.md"
    echo ""
}

# Main installation flow
main() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  TEPUB Installation Script${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""

    detect_os
    install_system_deps
    install_pyenv
    install_python
    create_venv
    install_tepub
    verify_installation
    print_next_steps
}

# Run main function
main
