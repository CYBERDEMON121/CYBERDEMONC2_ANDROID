#!/bin/bash
# CYBERDEMONS C2 Framework - Automated Installer
# For authorized security testing and educational purposes only.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;35m'
NC='\033[0m'
BOLD='\033[1m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAVA_HOME="/opt/jdk-17.0.2"
ANDROID_HOME="/opt/android-sdk"
BUILD_TOOLS="34.0.0"
PLATFORM="android-34"
JDK_URL="https://download.java.net/java/GA/jdk17.0.2/dfd4a8d0985749f896bed50d7138ee7f/8/GPL/openjdk-17.0.2_linux-x64_bin.tar.gz"
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"

print_banner() {
    echo -e "${CYAN}"
    echo "  ██████╗██╗   ██╗██████╗ ███████╗██████╗ ██████╗ ███████╗███╗   ███╗ ██████╗ ███╗   ██╗███████╗"
    echo " ██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔════╝████╗ ████║██╔═══██╗████╗  ██║██╔════╝"
    echo " ██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║  ██║█████╗  ██╔████╔██║██║   ██║██╔██╗ ██║███████╗"
    echo " ██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗██║  ██║██╔══╝  ██║╚██╔╝██║██║   ██║██║╚██╗██║╚════██║"
    echo " ╚██████╗   ██║   ██████╔╝███████╗██║  ██║██████╔╝███████╗██║ ╚═╝ ██║╚██████╔╝██║ ╚████║███████║"
    echo "  ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝"
    echo -e "${NC}"
    echo -e "${MAGENTA}  C2 FRAMEWORK v2.0 — Automated Installer${NC}"
    echo ""
}

log_info()    { echo -e "${CYAN}[*]${NC} $1"; }
log_ok()      { echo -e "${GREEN}[+]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
log_error()   { echo -e "${RED}[-]${NC} $1"; }
log_step()    { echo -e "\n${BOLD}${MAGENTA}==> $1${NC}"; }

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "Run as root: sudo ./install.sh"
        exit 1
    fi
}

check_os() {
    if ! grep -qiE "kali|debian|ubuntu|parrot" /etc/os-release 2>/dev/null; then
        log_warn "Detected non-Debian system. Some packages may differ."
    fi
}

# ============================================================
# 1. System Packages
# ============================================================
install_system_packages() {
    log_step "Installing system packages"

    apt-get update -qq

    log_info "Installing build essentials..."
    apt-get install -y -qq \
        curl wget unzip git \
        gcc g++ make \
        python3 python3-pip python3-venv \
        netcat-openbsd \
        libx11-dev \
        libcrypt-dev \
        imagemagick \
        2>/dev/null

    log_ok "System packages installed"
}

# ============================================================
# 2. Python Dependencies
# ============================================================
install_python_deps() {
    log_step "Installing Python dependencies"

    pip3 install flask --break-system-packages 2>/dev/null || pip3 install flask

    log_ok "Flask installed"
}

# ============================================================
# 3. MinGW (Windows Cross-Compilation)
# ============================================================
install_mingw() {
    log_step "Installing MinGW-w64 (Windows cross-compiler)"

    apt-get install -y -qq \
        mingw-w64 \
        binutils-mingw-w64 \
        x86_64-w64-mingw32-g++ \
        2>/dev/null || {
            log_warn "MinGW package names differ on this distro, trying alternatives..."
            apt-get install -y -qq mingw-w64 2>/dev/null || log_warn "Install mingw-w64 manually for Windows payload support"
        }

    if command -v x86_64-w64-mingw32-g++ &>/dev/null; then
        log_ok "MinGW-w64 installed"
    else
        log_warn "MinGW not found — Windows payload building unavailable"
    fi
}

# ============================================================
# 4. JDK 17
# ============================================================
install_jdk() {
    log_step "Installing JDK 17"

    if [ -d "$JAVA_HOME" ] && [ -f "$JAVA_HOME/bin/javac" ]; then
        log_ok "JDK 17 already installed at $JAVA_HOME"
        return
    fi

    log_info "Downloading JDK 17..."
    local tmpfile=$(mktemp /tmp/jdk17.XXXXXX.tar.gz)
    wget -q --show-progress -O "$tmpfile" "$JDK_URL"

    log_info "Extracting to /opt/..."
    mkdir -p /opt
    tar -xzf "$tmpfile" -C /opt/
    rm -f "$tmpfile"

    if [ -f "$JAVA_HOME/bin/javac" ]; then
        log_ok "JDK 17 installed at $JAVA_HOME"
    else
        log_error "JDK 17 installation failed"
        log_warn "Expected at: $JAVA_HOME"
    fi
}

# ============================================================
# 5. Android SDK
# ============================================================
install_android_sdk() {
    log_step "Installing Android SDK"

    if [ -d "$ANDROID_HOME/platforms/$PLATFORM" ] && [ -d "$ANDROID_HOME/build-tools/$BUILD_TOOLS" ]; then
        log_ok "Android SDK already installed with platform $PLATFORM and build-tools $BUILD_TOOLS"
        return
    fi

    mkdir -p "$ANDROID_HOME"

    # Download command-line tools
    if [ ! -d "$ANDROID_HOME/cmdline-tools/latest" ]; then
        log_info "Downloading Android command-line tools..."
        local tmpfile=$(mktemp /tmp/cmdtools.XXXXXX.zip)
        wget -q --show-progress -O "$tmpfile" "$CMDLINE_TOOLS_URL"

        log_info "Extracting..."
        mkdir -p "$ANDROID_HOME/cmdline-tools"
        unzip -q -o "$tmpfile" -d "$ANDROID_HOME/cmdline-tools/"
        mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" 2>/dev/null || true
        rm -f "$tmpfile"
    fi

    export JAVA_HOME="$JAVA_HOME"
    export ANDROID_HOME="$ANDROID_HOME"
    export PATH="$PATH:$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin"

    # Accept licenses
    log_info "Accepting SDK licenses..."
    yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses --sdk_root="$ANDROID_HOME" >/dev/null 2>&1 || true

    # Install platform and build tools
    log_info "Installing platform $PLATFORM..."
    "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$ANDROID_HOME" "platforms;$PLATFORM" 2>/dev/null

    log_info "Installing build-tools $BUILD_TOOLS..."
    "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$ANDROID_HOME" "build-tools;$BUILD_TOOLS" 2>/dev/null

    if [ -d "$ANDROID_HOME/platforms/$PLATFORM" ] && [ -d "$ANDROID_HOME/build-tools/$BUILD_TOOLS" ]; then
        log_ok "Android SDK installed successfully"
    else
        log_error "Android SDK installation may have failed"
        log_warn "Platform: $ANDROID_HOME/platforms/$PLATFORM"
        log_warn "Build-tools: $ANDROID_HOME/build-tools/$BUILD_TOOLS"
    fi
}

# ============================================================
# 6. Environment Variables
# ============================================================
setup_environment() {
    log_step "Setting up environment variables"

    local env_file="/etc/profile.d/cyberdemon.sh"
    cat > "$env_file" << 'ENVEOF'
# CYBERDEMONS C2 Framework
export JAVA_HOME=/opt/jdk-17.0.2
export ANDROID_HOME=/opt/android-sdk
export PATH=$PATH:$JAVA_HOME/bin:$ANDROID_HOME/build-tools/34.0.0:$ANDROID_HOME/cmdline-tools/latest/bin
ENVEOF
    chmod 644 "$env_file"

    # Also add to current shell
    export JAVA_HOME="$JAVA_HOME"
    export ANDROID_HOME="$ANDROID_HOME"
    export PATH="$PATH:$JAVA_HOME/bin:$ANDROID_HOME/build-tools/$BUILD_TOOLS:$ANDROID_HOME/cmdline-tools/latest/bin"

    log_ok "Environment variables set in $env_file"
}

# ============================================================
# 7. Verify Installation
# ============================================================
verify_installation() {
    log_step "Verifying installation"

    local errors=0

    echo ""
    echo -e "${BOLD}Component Status:${NC}"
    echo "─────────────────────────────────────────"

    # Python
    if command -v python3 &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Python3       $(python3 --version 2>&1)"
    else
        echo -e "  ${RED}✗${NC} Python3       NOT FOUND"
        ((errors++))
    fi

    # Flask
    if python3 -c "import flask" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Flask         $(python3 -c 'import flask; print(flask.__version__)')"
    else
        echo -e "  ${RED}✗${NC} Flask         NOT FOUND"
        ((errors++))
    fi

    # MinGW
    if command -v x86_64-w64-mingw32-g++ &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} MinGW-w64     $(x86_64-w64-mingw32-g++ --version 2>&1 | head -1)"
    else
        echo -e "  ${YELLOW}!${NC} MinGW-w64     Not installed (Windows payloads unavailable)"
    fi

    # GCC
    if command -v gcc &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} GCC           $(gcc --version 2>&1 | head -1)"
    else
        echo -e "  ${RED}✗${NC} GCC           NOT FOUND"
        ((errors++))
    fi

    # JDK
    if [ -f "$JAVA_HOME/bin/javac" ]; then
        echo -e "  ${GREEN}✓${NC} JDK 17        $JAVA_HOME"
    else
        echo -e "  ${RED}✗${NC} JDK 17        NOT FOUND at $JAVA_HOME"
        ((errors++))
    fi

    # Android SDK
    if [ -d "$ANDROID_HOME/platforms/$PLATFORM" ]; then
        echo -e "  ${GREEN}✓${NC} Android SDK   Platform $PLATFORM"
    else
        echo -e "  ${RED}✗${NC} Android SDK   Platform $PLATFORM NOT FOUND"
        ((errors++))
    fi

    if [ -d "$ANDROID_HOME/build-tools/$BUILD_TOOLS" ]; then
        echo -e "  ${GREEN}✓${NC} Build Tools   $BUILD_TOOLS"
    else
        echo -e "  ${RED}✗${NC} Build Tools   $BUILD_TOOLS NOT FOUND"
        ((errors++))
    fi

    # Netcat
    if command -v nc &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Netcat        $(nc -h 2>&1 | head -1 || echo 'installed')"
    else
        echo -e "  ${YELLOW}!${NC} Netcat        Not installed (listener unavailable)"
    fi

    # APK Builder script
    if [ -f "$SCRIPT_DIR/apk_builder/generate_apk.py" ]; then
        echo -e "  ${GREEN}✓${NC} APK Builder   generate_apk.py"
    else
        echo -e "  ${RED}✗${NC} APK Builder   NOT FOUND"
        ((errors++))
    fi

    echo "─────────────────────────────────────────"
    echo ""

    if [ $errors -eq 0 ]; then
        echo -e "${GREEN}${BOLD}All core components installed successfully!${NC}"
    else
        echo -e "${YELLOW}${BOLD}$errors component(s) missing — check above.${NC}"
    fi

    echo ""
    echo -e "${CYAN}Quick start:${NC}"
    echo "  cd $SCRIPT_DIR"
    echo "  python3 web_listener.py"
    echo ""
    echo -e "${CYAN}Build Android APK:${NC}"
    echo "  cd $SCRIPT_DIR"
    echo "  python3 apk_builder/generate_apk.py --c2-host YOUR_IP --c2-port 8080 --app-name \"Chrome Update\""
    echo ""
}

# ============================================================
# Main
# ============================================================
main() {
    print_banner
    check_root
    check_os

    install_system_packages
    install_python_deps
    install_mingw
    install_jdk
    install_android_sdk
    setup_environment
    verify_installation
}

main "$@"
