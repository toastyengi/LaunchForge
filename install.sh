#!/bin/bash
# ============================================================================
#  LaunchPad Controller - Arch Linux Installer
#  For Novation Launchpad Mini Mk2
# ============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║       LAUNCHPAD CONTROLLER INSTALLER      ║"
echo "  ║     Novation Launchpad Mini Mk2 Tool      ║"
echo "  ╚═══════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running on Arch
if [ ! -f /etc/arch-release ]; then
    echo -e "${YELLOW}Warning: This installer is designed for Arch Linux.${NC}"
    echo -e "Continuing anyway, but some packages may need manual installation."
    echo ""
fi

# ---- System Dependencies ----
echo -e "${BOLD}[1/5] Installing system dependencies...${NC}"

PACKAGES=(
    python
    python-pip
    python-pyqt5
    python-numpy
    alsa-utils
    alsa-lib
    rtmidi
    ffmpeg
    portaudio
)

# Check which packages are not installed
TO_INSTALL=()
for pkg in "${PACKAGES[@]}"; do
    if ! pacman -Qi "$pkg" &>/dev/null; then
        TO_INSTALL+=("$pkg")
    fi
done

if [ ${#TO_INSTALL[@]} -gt 0 ]; then
    echo -e "  Installing: ${TO_INSTALL[*]}"
    sudo pacman -S --needed --noconfirm "${TO_INSTALL[@]}"
else
    echo -e "  ${GREEN}All system packages already installed.${NC}"
fi

# ---- Python Dependencies ----
echo ""
echo -e "${BOLD}[2/5] Installing Python dependencies...${NC}"

pip install --user --break-system-packages \
    mido \
    python-rtmidi \
    sounddevice \
    soundfile \
    pydub \
    2>&1 | tail -5

echo -e "  ${GREEN}Python packages installed.${NC}"

# ---- Install the application ----
echo ""
echo -e "${BOLD}[3/5] Installing LaunchPad Controller...${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

pip install --user --break-system-packages -e . 2>&1 | tail -3

echo -e "  ${GREEN}Application installed.${NC}"

# ---- MIDI/Audio Permissions ----
echo ""
echo -e "${BOLD}[4/5] Setting up permissions...${NC}"

# Add user to audio group if not already
if ! groups | grep -q audio; then
    echo -e "  Adding user to 'audio' group..."
    sudo usermod -aG audio "$USER"
    echo -e "  ${YELLOW}Note: You may need to log out and back in for group changes.${NC}"
else
    echo -e "  ${GREEN}User already in 'audio' group.${NC}"
fi

# udev rule for Launchpad (so it's accessible without root)
UDEV_RULE='SUBSYSTEM=="usb", ATTR{idVendor}=="1235", ATTR{idProduct}=="0036", MODE="0666", GROUP="audio"'
UDEV_FILE="/etc/udev/rules.d/99-launchpad.rules"

if [ ! -f "$UDEV_FILE" ]; then
    echo -e "  Creating udev rule for Launchpad..."
    echo "$UDEV_RULE" | sudo tee "$UDEV_FILE" > /dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo -e "  ${GREEN}udev rule created.${NC}"
else
    echo -e "  ${GREEN}udev rule already exists.${NC}"
fi

# ---- Create Desktop Entry ----
echo ""
echo -e "${BOLD}[5/5] Creating desktop shortcut...${NC}"

DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_DIR/launchpad-ctrl.desktop" << EOF
[Desktop Entry]
Name=LaunchPad Controller
Comment=MIDI Controller for Novation Launchpad Mini Mk2
Exec=$HOME/.local/bin/launchpad-ctrl
Icon=audio-midi
Terminal=false
Type=Application
Categories=Audio;Music;Midi;
Keywords=launchpad;midi;sequencer;soundboard;
EOF

echo -e "  ${GREEN}Desktop shortcut created.${NC}"

# ---- Done ----
echo ""
echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║         Installation Complete!             ║${NC}"
echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}To run:${NC}"
echo -e "    ${CYAN}launchpad-ctrl${NC}"
echo -e "  or:"
echo -e "    ${CYAN}python -m launchpad_ctrl${NC}"
echo ""
echo -e "  ${BOLD}Connect your Launchpad Mini Mk2 via USB${NC}"
echo -e "  The app will auto-detect it on startup."
echo ""
echo -e "  ${BOLD}Config directory:${NC} ~/.launchpad-ctrl/"
echo -e "  ${BOLD}Projects:${NC}         ~/.launchpad-ctrl/projects/"
echo -e "  ${BOLD}Recordings:${NC}       ~/.launchpad-ctrl/recordings/"
echo ""

if ! groups | grep -q audio; then
    echo -e "  ${YELLOW}⚠ Remember to log out and back in for audio group access!${NC}"
    echo ""
fi
