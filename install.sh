#!/bin/bash
# LasTech Drive Wipe Station — Installer
# Run as root on the Mac Pro wipe station (192.168.10.50, Linux Mint)
# Usage: sudo bash install.sh

set -e

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
CYN='\033[0;36m'
NC='\033[0m'

echo -e "${CYN}"
echo "╔══════════════════════════════════════════════════╗"
echo "║      LasTech Drive Wipe Station — Installer      ║"
echo "║      Mac Pro | 192.168.10.50 | Linux Mint        ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Verify root ──────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}ERROR: Run this script as root.${NC}"
    echo "  sudo bash install.sh"
    exit 1
fi

# ── Verify we're on Linux Mint / Debian-based ────────────────────────────────
if ! command -v apt-get &>/dev/null; then
    echo -e "${RED}ERROR: apt-get not found. This installer requires a Debian/Ubuntu/Mint system.${NC}"
    exit 1
fi

# ── Verify all source files are present ──────────────────────────────────────
REQUIRED_FILES=(
    "lastech-wipe-gui.py"
    "lastech-wipe-trigger.sh"
    "99-lastech-wipe.rules"
    "config.env.template"
)
echo "[0/7] Checking source files..."
MISSING=0
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        echo -e "      ${RED}MISSING: $f${NC}"
        MISSING=1
    else
        echo "      OK: $f"
    fi
done
if [ "$MISSING" -eq 1 ]; then
    echo -e "${RED}ERROR: Missing files. Run install.sh from the repo directory.${NC}"
    exit 1
fi

# ── System dependencies ───────────────────────────────────────────────────────
echo ""
echo "[1/7] Installing system dependencies..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-tk \
    curl \
    util-linux \
    udev \
    pciutils \
    usbutils
echo -e "      ${GRN}Done.${NC}"

# ── sedutil-cli ───────────────────────────────────────────────────────────────
echo ""
echo "[2/7] Checking / installing sedutil-cli..."
if command -v sedutil-cli &>/dev/null; then
    echo -e "      ${GRN}sedutil-cli already installed: $(which sedutil-cli)${NC}"
else
    echo "      sedutil-cli not found — downloading static binary..."
    ARCH=$(uname -m)
    SEDUTIL_URL=""
    if [ "$ARCH" = "x86_64" ]; then
        SEDUTIL_URL="https://github.com/Drive-Trust-Alliance/exec/raw/master/sedutil-cli.amd64.debug.Linux"
    elif [ "$ARCH" = "i686" ] || [ "$ARCH" = "i386" ]; then
        SEDUTIL_URL="https://github.com/Drive-Trust-Alliance/exec/raw/master/sedutil-cli.x86.debug.Linux"
    else
        echo -e "      ${YLW}WARNING: Unknown arch ($ARCH). Cannot auto-download sedutil-cli.${NC}"
        echo "      Manually install from: https://github.com/Drive-Trust-Alliance/sedutil"
        echo "      Place binary at /usr/local/bin/sedutil-cli and chmod 755"
        SEDUTIL_URL=""
    fi

    # Known-good SHA256 checksums for Drive Trust Alliance static binaries.
    # *** REQUIRED: populate these before first deployment ***
    # To get the real values, run:
    #   curl -fsSL <SEDUTIL_URL> -o /tmp/sedutil-test && sha256sum /tmp/sedutil-test
    # Paste the output hash below for your architecture, then re-run install.sh.
    # If the hash below says REPLACE_WITH_REAL — the installer will abort safely.
    SEDUTIL_SHA256_AMD64="REPLACE_WITH_REAL_AMD64_SHA256"
    SEDUTIL_SHA256_X86="REPLACE_WITH_REAL_X86_SHA256"

    if [ "$ARCH" = "x86_64" ]; then
        EXPECTED_SHA256="$SEDUTIL_SHA256_AMD64"
    else
        EXPECTED_SHA256="$SEDUTIL_SHA256_X86"
    fi

    # Refuse to proceed with placeholder checksum
    if [[ "$EXPECTED_SHA256" == REPLACE_WITH_REAL* ]]; then
        echo -e "      ${RED}ERROR: sedutil-cli SHA256 checksum not set in install.sh.${NC}"
        echo "      Run this to get the real hash:"
        echo "        curl -fsSL $SEDUTIL_URL -o /tmp/sedutil-test && sha256sum /tmp/sedutil-test"
        echo "      Then update SEDUTIL_SHA256_AMD64 or SEDUTIL_SHA256_X86 in install.sh."
        echo "      sedutil-cli NOT installed. Fix checksums and re-run."
        exit 1
    fi

    if [ -n "$SEDUTIL_URL" ]; then
        TMPFILE=$(mktemp)
        echo "      Downloading sedutil-cli..."
        if curl -fsSL "$SEDUTIL_URL" -o "$TMPFILE"; then
            # Verify SHA256 checksum before installing
            ACTUAL_SHA256=$(sha256sum "$TMPFILE" | awk '{print $1}')
            if [ "$ACTUAL_SHA256" = "$EXPECTED_SHA256" ]; then
                mv "$TMPFILE" /usr/local/bin/sedutil-cli
                chmod 755 /usr/local/bin/sedutil-cli
                echo -e "      ${GRN}sedutil-cli installed and checksum verified.${NC}"
            else
                rm -f "$TMPFILE"
                echo -e "      ${RED}ERROR: sedutil-cli checksum FAILED — binary not installed.${NC}"
                echo "      Expected: $EXPECTED_SHA256"
                echo "      Got:      $ACTUAL_SHA256"
                echo "      Do not proceed until sedutil-cli is verified."
                echo "      Manually install from: https://github.com/Drive-Trust-Alliance/sedutil"
                echo "      Or update the SHA256 in install.sh if DTA released a new build."
            fi
        else
            rm -f "$TMPFILE"
            echo -e "      ${YLW}WARNING: Download failed. Install sedutil-cli manually.${NC}"
            echo "      https://github.com/Drive-Trust-Alliance/sedutil"
        fi
    fi
fi

# ── Log directory ─────────────────────────────────────────────────────────────
echo ""
echo "[3/7] Creating log directory..."
mkdir -p /var/log/lastech-wipe
chmod 700 /var/log/lastech-wipe
echo -e "      ${GRN}Log dir: /var/log/lastech-wipe/wipe.log (chmod 700 — root only)${NC}"

# ── Install GUI script ────────────────────────────────────────────────────────
echo ""
echo "[4/7] Installing wipe GUI..."
cp lastech-wipe-gui.py /usr/local/bin/lastech-wipe-gui.py
chmod 755 /usr/local/bin/lastech-wipe-gui.py
# Verify python3 can at least parse it
if python3 -c "import ast; ast.parse(open('/usr/local/bin/lastech-wipe-gui.py').read())"; then
    echo -e "      ${GRN}Installed and syntax-verified: /usr/local/bin/lastech-wipe-gui.py${NC}"
else
    echo -e "      ${RED}ERROR: GUI script failed syntax check. Installation aborted.${NC}"
    exit 1
fi

# ── Install trigger script ────────────────────────────────────────────────────
echo ""
echo "[5/7] Installing udev trigger..."
cp lastech-wipe-trigger.sh /usr/local/bin/lastech-wipe-trigger.sh
chmod 755 /usr/local/bin/lastech-wipe-trigger.sh
cp 99-lastech-wipe.rules /etc/udev/rules.d/99-lastech-wipe.rules
chmod 644 /etc/udev/rules.d/99-lastech-wipe.rules
udevadm control --reload-rules
udevadm trigger
echo -e "      ${GRN}udev rule installed and reloaded.${NC}"

# ── Config file ───────────────────────────────────────────────────────────────
echo ""
echo "[6/7] Setting up credentials config..."
mkdir -p /etc/lastech-wipe
chmod 700 /etc/lastech-wipe
if [ ! -f /etc/lastech-wipe/config.env ]; then
    cp config.env.template /etc/lastech-wipe/config.env
    chown root:root /etc/lastech-wipe/config.env
    chmod 600 /etc/lastech-wipe/config.env
    echo -e "      ${GRN}Config template written to /etc/lastech-wipe/config.env${NC}"
    echo -e "      ${YLW}*** ACTION REQUIRED: populate credentials from Vaultwarden ***${NC}"
else
    echo "      Config already exists — not overwriting."
    echo "      Location: /etc/lastech-wipe/config.env"
fi

# ── Verify tkinter is importable ─────────────────────────────────────────────
echo ""
echo "[7/7] Verifying Python dependencies..."
if python3 -c "import tkinter" 2>/dev/null; then
    echo -e "      ${GRN}tkinter: OK${NC}"
else
    echo -e "      ${YLW}WARNING: tkinter import failed. Trying to fix...${NC}"
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-tk
    if python3 -c "import tkinter" 2>/dev/null; then
        echo -e "      ${GRN}tkinter: OK (installed)${NC}"
    else
        echo -e "      ${RED}ERROR: tkinter still not importable. GUI will not work.${NC}"
        echo "      Try: sudo apt-get install python3-tk"
    fi
fi

if python3 -c "import subprocess, sys, os, datetime, threading, urllib.request, urllib.parse, json" 2>/dev/null; then
    echo -e "      ${GRN}stdlib modules: OK${NC}"
else
    echo -e "      ${RED}ERROR: One or more stdlib modules missing. Something is very wrong with this Python install.${NC}"
    exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}╔══════════════════════════════════════════════════╗"
echo    "║           Installation Complete ✓                ║"
echo -e "╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YLW}REQUIRED NEXT STEPS:${NC}"
echo ""
echo "  1. Add Telegram credentials from Vaultwarden:"
echo "       sudo nano /etc/lastech-wipe/config.env"
echo ""
echo "  2. Get serial numbers for your known SEDs (insert each drive, then run):"
echo "       lsblk -o NAME,SIZE,MODEL,SERIAL"
echo ""
echo "  3. Update KNOWN_SEDS in the GUI script with real serials:"
echo "       sudo nano /usr/local/bin/lastech-wipe-gui.py"
echo "       (replace SERIAL_MICRON_M550 and SERIAL_KINGSTON_KC300)"
echo ""
echo "  4. Test manually before relying on udev:"
echo "       sudo python3 /usr/local/bin/lastech-wipe-gui.py /dev/sdb"
echo ""
echo "  5. Insert a drive to verify udev auto-launch works."
echo ""
