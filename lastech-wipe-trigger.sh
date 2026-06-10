#!/bin/bash
# LasTech Drive Wipe Station — udev trigger wrapper
# File: /usr/local/bin/lastech-wipe-trigger.sh
#
# udev runs in a minimal environment with no DISPLAY set.
# This wrapper waits briefly for the device to settle, then
# launches the GUI on the physical console display as root.

DEVICE="$1"
LOG="/var/log/lastech-wipe/wipe.log"
GUI="/usr/local/bin/lastech-wipe-gui.py"

# Ensure log dir exists
mkdir -p "$(dirname "$LOG")"

# Brief settle delay — lets the kernel finish enumerating the device
sleep 2

# Detect the active console display and user session
# Works for Linux Mint Cinnamon (X11) with a logged-in user
DISPLAY_USER=$(who | grep -v "(:0)" | head -n1 | awk '{print $1}')
XDISPLAY=":0"

timestamp=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$timestamp] [UDEV] Drive detected: $DEVICE — launching wipe GUI" >> "$LOG"

# Launch GUI as root on the physical display
DISPLAY=$XDISPLAY XAUTHORITY=/home/$(logname)/.Xauthority \
    /usr/bin/python3 "$GUI" "$DEVICE" &
