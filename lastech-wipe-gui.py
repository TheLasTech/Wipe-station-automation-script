#!/usr/bin/env python3
"""
LasTech Drive Wipe Station — GUI Confirmation & Wipe Orchestrator
Mac Pro Wipe Station | 192.168.10.50 | Linux Mint

Triggered by udev on drive insertion. Identifies the drive, classifies it
(known SED / unknown / spinning), presents a GUI confirmation dialog, then
executes the appropriate wipe command with live progress output.

Credentials: /etc/lastech-wipe/config.env  (root:root, chmod 600)
Log:         /var/log/lastech-wipe/wipe.log
"""

import subprocess
import sys
import os
import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import configparser
import urllib.request
import urllib.parse
import json

# ─── Paths ────────────────────────────────────────────────────────────────────
CONFIG_FILE  = "/etc/lastech-wipe/config.env"
LOG_FILE     = "/var/log/lastech-wipe/wipe.log"
SEDUTIL_BIN  = "/usr/local/bin/sedutil-cli"

# ─── Known SED PSID Table ─────────────────────────────────────────────────────
# Key: drive serial number (as reported by lsblk)
# Value: dict with label and PSID
KNOWN_SEDS = {
    # Micron M550 256GB — staying with Bob, but tracked for safety
    "SERIAL_MICRON_M550": {
        "label": "Micron M550 256GB",
        "psid":  "558AFE26-8BF3-0EE3-E100-000089C981EC",
        "note":  "Bob's personal drive — confirm before wiping"
    },
    # Kingston KC300 240GB
    "SERIAL_KINGSTON_KC300": {
        "label": "Kingston KC300 240GB",
        "psid":  "50026B725304A43CB98FD99F4EC5F023",
        "note":  ""
    },
}
# NOTE: Replace SERIAL_MICRON_M550 and SERIAL_KINGSTON_KC300 above with the
# actual serial numbers from: lsblk -o NAME,SIZE,MODEL,SERIAL
# Run that command on the wipe station with each drive inserted to get serials.

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_config():
    """Load Telegram credentials from config.env (ini-style, no section header)."""
    config = {}
    try:
        with open(CONFIG_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    config[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        log_entry("ERROR", "n/a", "n/a", f"Config file not found: {CONFIG_FILE}")
    return config


def log_entry(status, serial, model, message):
    """Append a timestamped line to the wipe log."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{status}] serial={serial} model={model} {message}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception as e:
        print(f"WARNING: Could not write to log: {e}", file=sys.stderr)


def send_telegram(token, chat_id, text):
    """Send a Telegram message. Silently logs on failure."""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text":    text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log_entry("WARN", "n/a", "n/a", f"Telegram send failed: {e}")


def get_drive_info(device):
    """
    Run lsblk on the device and return a dict with name, size, model, serial.
    Cardinal rule: identify by serial before ANY wipe command.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,SIZE,MODEL,SERIAL", "-d", "-n", device],
            capture_output=True, text=True, check=True
        )
        parts = result.stdout.strip().split(None, 3)
        return {
            "device": device,
            "name":   parts[0] if len(parts) > 0 else "unknown",
            "size":   parts[1] if len(parts) > 1 else "unknown",
            "model":  parts[2] if len(parts) > 2 else "unknown",
            "serial": parts[3].strip() if len(parts) > 3 else "UNKNOWN"
        }
    except subprocess.CalledProcessError as e:
        return {
            "device": device,
            "name":   "unknown",
            "size":   "unknown",
            "model":  "unknown",
            "serial": "UNKNOWN"
        }


def classify_drive(serial):
    """
    Returns ("SED", psid_entry) if serial is in KNOWN_SEDS,
    ("UNKNOWN", None) if not recognized — script halts for manual classification.
    """
    if serial in KNOWN_SEDS:
        return ("SED", KNOWN_SEDS[serial])
    return ("UNKNOWN", None)


# ─── Wipe Execution ───────────────────────────────────────────────────────────

def run_sed_wipe(device, psid, output_callback):
    """Execute sedutil-cli PSID revert. Streams output via callback."""
    output_callback(f"[SED] Initiating PSID revert on {device}...\n")
    output_callback(f"[SED] PSID: {psid}\n\n")
    cmd = [SEDUTIL_BIN, "--PSIDrevert", psid, device]
    return _run_command(cmd, output_callback)


def run_shred_wipe(device, output_callback):
    """Execute shred with 1 pass + zero pass. Streams output via callback."""
    output_callback(f"[SHRED] Starting shred on {device} (1 pass + zero)...\n\n")
    cmd = ["shred", "-v", "-n", "1", "-z", device]
    return _run_command(cmd, output_callback)


def _run_command(cmd, output_callback):
    """Run a command, stream stderr+stdout, return (success:bool, output:str)."""
    full_output = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        for line in proc.stdout:
            full_output.append(line)
            output_callback(line)
        proc.wait()
        success = proc.returncode == 0
        return success, "".join(full_output)
    except FileNotFoundError:
        msg = f"ERROR: Command not found: {cmd[0]}\n"
        output_callback(msg)
        return False, msg
    except Exception as e:
        msg = f"ERROR: {e}\n"
        output_callback(msg)
        return False, msg


# ─── GUI ──────────────────────────────────────────────────────────────────────

class WipeApp(tk.Tk):
    def __init__(self, device):
        super().__init__()
        self.device = device
        self.config_data = load_config()

        self.title("LasTech Drive Wipe Station")
        self.resizable(False, False)
        self.configure(bg="#1a1a2e")
        self._set_icon()

        # ── Gather drive info immediately ──
        self.drive = get_drive_info(device)
        self.drive_class, self.sed_entry = classify_drive(self.drive["serial"])

        self._build_ui()
        self.center_window()

    def _set_icon(self):
        # Simple colored title bar — no external icon file needed
        pass

    def center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        PAD = 18
        BG      = "#1a1a2e"
        CARD_BG = "#16213e"
        ACCENT  = "#e94560"
        SAFE    = "#00b894"
        WARN    = "#fdcb6e"
        TEXT    = "#dfe6e9"
        MONO    = ("Courier New", 10)
        LABEL   = ("Helvetica", 10)
        HEADING = ("Helvetica", 13, "bold")

        # ── Header ──
        header = tk.Frame(self, bg=ACCENT, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="⚠  LasTech Drive Wipe Station",
                 font=("Helvetica", 15, "bold"),
                 bg=ACCENT, fg="white").pack()
        tk.Label(header, text="PHI-Compliant Wipe — Confirm Before Proceeding",
                 font=("Helvetica", 9), bg=ACCENT, fg="white").pack()

        # ── Drive Info Card ──
        card = tk.Frame(self, bg=CARD_BG, padx=PAD, pady=PAD)
        card.pack(fill="x", padx=PAD, pady=(PAD, 0))

        tk.Label(card, text="DRIVE IDENTIFIED", font=HEADING,
                 bg=CARD_BG, fg=ACCENT).grid(row=0, column=0, columnspan=2,
                                              sticky="w", pady=(0, 8))

        fields = [
            ("Device",  self.drive["device"]),
            ("Model",   self.drive["model"]),
            ("Size",    self.drive["size"]),
            ("Serial",  self.drive["serial"]),
        ]
        for i, (label, value) in enumerate(fields, start=1):
            tk.Label(card, text=f"{label}:", font=LABEL,
                     bg=CARD_BG, fg="#b2bec3", width=8,
                     anchor="w").grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(card, text=value, font=MONO,
                     bg=CARD_BG, fg=TEXT).grid(row=i, column=1,
                                               sticky="w", padx=(8, 0), pady=2)

        # ── Classification Banner ──
        class_frame = tk.Frame(self, bg=BG, pady=8)
        class_frame.pack(fill="x", padx=PAD)

        if self.drive_class == "SED":
            class_color = WARN
            class_text  = f"🔒  SELF-ENCRYPTING DRIVE (SED)"
            method_text = f"Method: sedutil-cli --PSIDrevert"
            psid_text   = f"PSID: {self.sed_entry['psid']}"
            note        = self.sed_entry.get("note", "")
        else:
            class_color = ACCENT
            class_text  = "⛔  DRIVE NOT IN KNOWN SED TABLE"
            method_text = "Cannot proceed — manual classification required"
            psid_text   = "Halt: add serial to KNOWN_SEDS or confirm as spinning drive"
            note        = ""

        tk.Label(class_frame, text=class_text,
                 font=("Helvetica", 11, "bold"),
                 bg=BG, fg=class_color).pack(anchor="w")
        tk.Label(class_frame, text=method_text,
                 font=LABEL, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(class_frame, text=psid_text,
                 font=MONO, bg=BG, fg="#b2bec3").pack(anchor="w")
        if note:
            tk.Label(class_frame, text=f"⚠  Note: {note}",
                     font=LABEL, bg=BG, fg=WARN).pack(anchor="w", pady=(4, 0))

        # ── Spinning drive option (only shown when UNKNOWN) ──
        self.force_shred_var = tk.BooleanVar(value=False)
        if self.drive_class == "UNKNOWN":
            shred_frame = tk.Frame(self, bg=CARD_BG, padx=PAD, pady=10)
            shred_frame.pack(fill="x", padx=PAD, pady=(0, 4))
            tk.Label(shred_frame,
                     text="If you have confirmed this is a standard (non-SED) spinning or SATA drive,\n"
                          "you may authorize shred below. Do NOT use this for SEDs.",
                     font=("Helvetica", 9), bg=CARD_BG, fg=WARN,
                     justify="left").pack(anchor="w")
            tk.Checkbutton(shred_frame,
                           text="I confirm this is NOT a self-encrypting drive — use shred",
                           variable=self.force_shred_var,
                           font=LABEL, bg=CARD_BG, fg=TEXT,
                           selectcolor="#2d3436",
                           activebackground=CARD_BG,
                           command=self._toggle_shred_confirm).pack(anchor="w", pady=(6, 0))

        # ── Progress output ──
        out_frame = tk.Frame(self, bg=BG, padx=PAD)
        out_frame.pack(fill="both", expand=True, pady=(8, 0), padx=PAD)
        tk.Label(out_frame, text="OUTPUT", font=("Helvetica", 9, "bold"),
                 bg=BG, fg="#636e72").pack(anchor="w")
        self.output_text = tk.Text(out_frame, height=12, width=72,
                                   bg="#0d0d1a", fg="#00cec9",
                                   font=MONO, relief="flat",
                                   state="disabled", wrap="word")
        scroll = ttk.Scrollbar(out_frame, command=self.output_text.yview)
        self.output_text.configure(yscrollcommand=scroll.set)
        self.output_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ── Buttons ──
        btn_frame = tk.Frame(self, bg=BG, pady=PAD)
        btn_frame.pack(fill="x", padx=PAD)

        self.wipe_btn = tk.Button(
            btn_frame,
            text="CONFIRM & WIPE",
            font=("Helvetica", 12, "bold"),
            bg=ACCENT, fg="white",
            activebackground="#c0392b",
            relief="flat", padx=20, pady=10,
            command=self._confirm_and_wipe,
            state="normal" if self.drive_class == "SED" else "disabled"
        )
        self.wipe_btn.pack(side="left")

        tk.Button(
            btn_frame,
            text="CANCEL",
            font=("Helvetica", 12),
            bg="#2d3436", fg=TEXT,
            activebackground="#636e72",
            relief="flat", padx=20, pady=10,
            command=self._cancel
        ).pack(side="right")

        self.status_label = tk.Label(self, text="",
                                     font=("Helvetica", 10, "bold"),
                                     bg=BG, fg=SAFE)
        self.status_label.pack(pady=(0, PAD))

        # If unknown and not shred-authorized, show a holding message
        if self.drive_class == "UNKNOWN":
            self._append_output(
                "HALT — Drive serial not found in known SED table.\n\n"
                "Options:\n"
                "  1. Add this serial + PSID to KNOWN_SEDS in the script if it is an SED.\n"
                "  2. Check the checkbox below if you have confirmed it is NOT an SED,\n"
                "     then click CONFIRM & WIPE to run shred.\n\n"
                f"Serial detected: {self.drive['serial']}\n"
                f"Model detected:  {self.drive['model']}\n"
            )

    def _toggle_shred_confirm(self):
        if self.force_shred_var.get():
            self.wipe_btn.configure(state="normal")
        else:
            self.wipe_btn.configure(state="disabled")

    def _append_output(self, text):
        self.output_text.configure(state="normal")
        self.output_text.insert("end", text)
        self.output_text.see("end")
        self.output_text.configure(state="disabled")

    def _confirm_and_wipe(self):
        """Final confirmation dialog before any destructive action."""
        serial = self.drive["serial"]
        model  = self.drive["model"]
        size   = self.drive["size"]
        device = self.drive["device"]

        if self.drive_class == "SED":
            method = f"sedutil-cli PSID revert\nPSID: {self.sed_entry['psid']}"
        else:
            method = "shred -v -n 1 -z (1 pass + zero)"

        confirmed = messagebox.askyesno(
            "Final Confirmation — This Cannot Be Undone",
            f"You are about to permanently destroy all data on:\n\n"
            f"  Device : {device}\n"
            f"  Model  : {model}\n"
            f"  Size   : {size}\n"
            f"  Serial : {serial}\n\n"
            f"  Method : {method}\n\n"
            f"This action is IRREVERSIBLE.\n\nProceed?",
            icon="warning"
        )

        if not confirmed:
            self._append_output("\n[CANCELLED] Wipe aborted by user.\n")
            return

        self.wipe_btn.configure(state="disabled", text="WIPING...")
        self.status_label.configure(text="Wipe in progress — do not remove drive...",
                                    fg="#fdcb6e")

        # Run wipe in background thread so GUI stays responsive
        thread = threading.Thread(target=self._execute_wipe, daemon=True)
        thread.start()

    def _execute_wipe(self):
        serial = self.drive["serial"]
        model  = self.drive["model"]
        device = self.drive["device"]
        token  = self.config_data.get("TELEGRAM_BOT_TOKEN", "")
        chat   = self.config_data.get("TELEGRAM_CHAT_ID", "")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        log_entry("START", serial, model,
                  f"Wipe started | device={device} | method={'SED' if self.drive_class == 'SED' else 'shred'}")

        send_telegram(token, chat,
            f"🔴 <b>LasTech Wipe Station</b>\n"
            f"Wipe <b>STARTED</b>\n\n"
            f"Device: <code>{device}</code>\n"
            f"Model:  <code>{model}</code>\n"
            f"Serial: <code>{serial}</code>\n"
            f"Method: {'SED PSID revert' if self.drive_class == 'SED' else 'shred'}\n"
            f"Time:   {timestamp}"
        )

        if self.drive_class == "SED":
            success, output = run_sed_wipe(
                device, self.sed_entry["psid"], self._append_output
            )
        else:
            success, output = run_shred_wipe(device, self._append_output)

        self._wipe_complete(success, serial, model, device, token, chat)

    def _wipe_complete(self, success, serial, model, device, token, chat):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "SUCCESS" if success else "FAILED"
        method = "SED PSID revert" if self.drive_class == "SED" else "shred -v -n 1 -z"

        log_entry(status, serial, model,
                  f"Wipe {status} | device={device} | method={method}")

        icon = "✅" if success else "❌"
        send_telegram(token, chat,
            f"{icon} <b>LasTech Wipe Station</b>\n"
            f"Wipe <b>{status}</b>\n\n"
            f"Device: <code>{device}</code>\n"
            f"Model:  <code>{model}</code>\n"
            f"Serial: <code>{serial}</code>\n"
            f"Method: {method}\n"
            f"Time:   {timestamp}"
        )

        # Update GUI on main thread
        self.after(0, self._update_ui_complete, success)

    def _update_ui_complete(self, success):
        if success:
            self.status_label.configure(
                text="✅ Wipe complete — drive is safe to release.", fg="#00b894"
            )
            self.wipe_btn.configure(text="DONE", bg="#00b894")
        else:
            self.status_label.configure(
                text="❌ Wipe FAILED — check output above and do not release drive.", fg="#e94560"
            )
            self.wipe_btn.configure(text="FAILED", bg="#636e72")

    def _cancel(self):
        log_entry("CANCELLED", self.drive["serial"], self.drive["model"],
                  f"User cancelled before wipe — device={self.device}")
        self.destroy()


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    if os.geteuid() != 0:
        print("ERROR: This script must run as root.", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: lastech-wipe-gui.py <device>  e.g.  /dev/sdb", file=sys.stderr)
        sys.exit(1)

    device = sys.argv[1]
    if not os.path.exists(device):
        print(f"ERROR: Device {device} not found.", file=sys.stderr)
        sys.exit(1)

    app = WipeApp(device)
    app.mainloop()


if __name__ == "__main__":
    main()
