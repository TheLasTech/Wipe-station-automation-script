#!/bin/bash
# LasTech Drive Wipe Station — udev trigger wrapper
# File: /usr/local/bin/lastech-wipe-trigger.sh
#
# udev runs in a minimal environment with no DISPLAY or login session.
# This wrapper finds the active X11 user safely, then launches the GUI.

DEVICE="$1"
LOG="/var/log/lastech-wipe/wipe.log"
GUI="/usr/local/bin/lastech-wipe-gui.py"

mkdir -p "$(dirname "$LOG")"
timestamp=$(date '+%Y-%m-%d %H:%M:%S')

# Brief settle delay — lets kernel finish enumerating the device
sleep 2

# Find the user currently logged into the physical display (:0)
# 'who' output: username tty date (display) — filter for :0 session
DISPLAY_USER=$(who | awk '/:0\)/ || /\(:0\)/ {print $1; exit}')

if [ -z "$DISPLAY_USER" ]; then
    echo "[$timestamp] [ERROR] udev trigger: no active X session found on :0 — cannot launch GUI" >> "$LOG"
    exit 1
fi

XAUTH_FILE="/home/${DISPLAY_USER}/.Xauthority"

if [ ! -f "$XAUTH_FILE" ]; then
    echo "[$timestamp] [ERROR] udev trigger: Xauthority not found at $XAUTH_FILE" >> "$LOG"
    exit 1
fi

echo "[$timestamp] [UDEV] Drive detected: $DEVICE — launching wipe GUI as root (display user: $DISPLAY_USER)" >> "$LOG"

DISPLAY=:0 XAUTHORITY="$XAUTH_FILE" /usr/bin/python3 "$GUI" "$DEVICE" &
