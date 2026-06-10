#!/usr/bin/env python3
"""
LasTech Drive Wipe Station — GUI Confirmation & Wipe Orchestrator
Mac Pro Wipe Station | 192.168.10.50 | Linux Mint
Version: 1.1

Triggered by udev on drive insertion. Identifies the drive, classifies it
(known SED / unknown / spinning), presents a GUI confirmation dialog, then
executes the appropriate wipe command with live progress output.

Credentials: /etc/lastech-wipe/config.env  (root:root, chmod 600)
Log:         /var/log/lastech-wipe/wipe.log

Security hardened — Haiku 4.5 audits rounds 1, 2 & 3, June 2026
v1.1 — full dark theme, versioned release
"""

import subprocess
import sys
import os
import re
import stat
import time
import fcntl
import datetime
import tkinter as tk
from tkinter import ttk
import threading
import urllib.request
import urllib.parse

VERSION = "1.1"

# ─── Paths ────────────────────────────────────────────────────────────────────
CONFIG_FILE   = "/etc/lastech-wipe/config.env"
LOG_FILE      = "/var/log/lastech-wipe/wipe.log"
LOG_DIR       = "/var/log/lastech-wipe"
SEDUTIL_BIN   = "/usr/local/bin/sedutil-cli"
COOLDOWN_FILE = "/tmp/lastech-wipe.cooldown"
COOLDOWN_SECS = 30

# ─── Theme ────────────────────────────────────────────────────────────────────
T = {
    "bg":       "#1a1a2e",
    "card":     "#16213e",
    "accent":   "#e94560",
    "safe":     "#00b894",
    "warn":     "#fdcb6e",
    "text":     "#dfe6e9",
    "muted":    "#b2bec3",
    "dim":      "#636e72",
    "dark2":    "#2d3436",
    "term_bg":  "#0d0d1a",
    "term_fg":  "#00cec9",
    "btn_cancel_hover": "#4a5568",
}

# ─── Known SED PSID Table ─────────────────────────────────────────────────────
# Key: drive serial number (exactly as reported by lsblk)
# Value: dict with label, psid, and optional note shown in GUI
#
# TO ADD A DRIVE:
#   1. Insert drive, run: lsblk -o NAME,SIZE,MODEL,SERIAL
#   2. Copy serial exactly as shown
#   3. Get PSID from physical label on the drive
#   4. Add entry below — no restart needed
KNOWN_SEDS = {
    # Replace SERIAL_MICRON_M550 with real serial from lsblk
    "SERIAL_MICRON_M550": {
        "label": "Micron M550 256GB",
        "psid":  "558AFE26-8BF3-0EE3-E100-000089C981EC",
        "note":  "Bob's personal drive — confirm before wiping"
    },
    # Replace SERIAL_KINGSTON_KC300 with real serial from lsblk
    "SERIAL_KINGSTON_KC300": {
        "label": "Kingston KC300 240GB",
        "psid":  "50026B725304A43CB98FD99F4EC5F023",
        "note":  ""
    },
}

# ─── PSID validation ──────────────────────────────────────────────────────────
_PSID_UUID    = re.compile(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$')
_PSID_COMPACT = re.compile(r'^[0-9A-F]{32}$')

def validate_psid(psid):
    p = psid.upper().strip()
    return bool(_PSID_UUID.match(p) or _PSID_COMPACT.match(p))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def safe_str(val):
    return str(val).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def ensure_log_dir():
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    os.chmod(LOG_DIR, 0o700)


def log_entry(status, serial, model, message):
    ensure_log_dir()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (f"[{timestamp}] [{safe_str(status)}] "
            f"serial={safe_str(serial)} "
            f"model={safe_str(model)} "
            f"{safe_str(message)}\n")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception as e:
        print(f"WARNING: Could not write to log: {e}", file=sys.stderr)


def load_config():
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
    if not config.get("TELEGRAM_BOT_TOKEN") or not config.get("TELEGRAM_CHAT_ID"):
        log_entry("WARN", "n/a", "n/a",
                  "Telegram credentials missing — notifications disabled")
    return config


def send_telegram(token, chat_id, text):
    if not token or not chat_id:
        return
    def _send():
        try:
            url  = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "HTML"
            }).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
        except Exception as e:
            log_entry("WARN", "n/a", "n/a", f"Telegram send failed: {e}")
    threading.Thread(target=_send, daemon=True).start()


def check_and_set_cooldown():
    try:
        fd = os.open(COOLDOWN_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            content = os.read(fd, 32).decode().strip()
            now = time.time()
            if content:
                try:
                    if now - float(content) < COOLDOWN_SECS:
                        log_entry("SKIPPED", "n/a", "n/a",
                                  f"Cooldown active ({COOLDOWN_SECS}s window)")
                        return True
                except ValueError:
                    pass
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(now).encode())
            os.ftruncate(fd, len(str(now)))
            return False
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except BlockingIOError:
        log_entry("SKIPPED", "n/a", "n/a", "Cooldown lock held — ignoring insertion")
        return True
    except Exception as e:
        log_entry("WARN", "n/a", "n/a", f"Cooldown check failed: {e} — proceeding")
        return False


def get_drive_info(device):
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
    except subprocess.CalledProcessError:
        return {"device": device, "name": "unknown",
                "size": "unknown", "model": "unknown", "serial": "UNKNOWN"}


def classify_drive(serial):
    if serial in KNOWN_SEDS:
        return ("SED", KNOWN_SEDS[serial])
    return ("UNKNOWN", None)


# ─── Wipe Execution ───────────────────────────────────────────────────────────

def run_sed_wipe(device, psid, cb):
    if not validate_psid(psid):
        msg = f"ERROR: PSID format invalid: {psid} — aborting\n"
        cb(msg)
        log_entry("ERROR", "n/a", "n/a", msg.strip())
        return False, msg
    cb(f"[SED] Initiating PSID revert on {device}...\n")
    cb(f"[SED] PSID: {psid}\n\n")
    return _run_command([SEDUTIL_BIN, "--PSIDrevert", psid, device], cb)


def run_shred_wipe(device, cb):
    cb(f"[SHRED] Starting shred on {device} (1 pass + zero)...\n\n")
    return _run_command(["shred", "-v", "-n", "1", "-z", device], cb)


def _run_command(cmd, cb):
    full = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            full.append(line)
            cb(line)
        proc.wait()
        return proc.returncode == 0, "".join(full)
    except FileNotFoundError:
        msg = f"ERROR: Command not found: {cmd[0]}\n"
        cb(msg)
        return False, msg
    except Exception as e:
        msg = f"ERROR: {e}\n"
        cb(msg)
        return False, msg


# ─── Dark Dialog (replaces system messagebox) ─────────────────────────────────

class DarkDialog(tk.Toplevel):
    """
    Fully dark confirmation dialog — replaces tk.messagebox which
    inherits system theme and would show light chrome on Linux Mint.
    """
    def __init__(self, parent, title, message, is_warning=False):
        super().__init__(parent)
        self.result = False
        self.title(title)
        self.configure(bg=T["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Icon + message
        top = tk.Frame(self, bg=T["bg"], padx=28, pady=24)
        top.pack(fill="x")
        icon_color = T["accent"] if is_warning else T["warn"]
        tk.Label(top, text="⚠", font=("Helvetica", 32),
                 bg=T["bg"], fg=icon_color).pack(side="left", padx=(0, 16))
        tk.Label(top, text=message, font=("Helvetica", 10),
                 bg=T["bg"], fg=T["text"], justify="left",
                 wraplength=400).pack(side="left", anchor="nw")

        # Divider
        tk.Frame(self, bg=T["dark2"], height=1).pack(fill="x")

        # Buttons
        btn_frame = tk.Frame(self, bg=T["card"], pady=14, padx=20)
        btn_frame.pack(fill="x")

        def _yes():
            self.result = True
            self.destroy()

        def _no():
            self.result = False
            self.destroy()

        tk.Button(btn_frame, text="YES — WIPE NOW",
                  font=("Helvetica", 11, "bold"),
                  bg=T["accent"], fg="white", activebackground="#c0392b",
                  relief="flat", padx=18, pady=8,
                  command=_yes).pack(side="left")
        tk.Button(btn_frame, text="Cancel",
                  font=("Helvetica", 11),
                  bg=T["dark2"], fg=T["text"], activebackground=T["dim"],
                  relief="flat", padx=18, pady=8,
                  command=_no).pack(side="right")

        self._center(parent)
        self.wait_window()

    def _center(self, parent):
        self.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width()  // 2
        py = parent.winfo_y() + parent.winfo_height() // 2
        w  = self.winfo_width()
        h  = self.winfo_height()
        self.geometry(f"+{px - w // 2}+{py - h // 2}")


# ─── Dark ttk Style ───────────────────────────────────────────────────────────

def apply_dark_style(root):
    """Apply dark theme to all ttk widgets (scrollbars, etc.)."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Vertical.TScrollbar",
                    gripcount=0,
                    background=T["dark2"],
                    darkcolor=T["bg"],
                    lightcolor=T["card"],
                    troughcolor=T["bg"],
                    bordercolor=T["bg"],
                    arrowcolor=T["muted"],
                    relief="flat")
    style.map("Vertical.TScrollbar",
              background=[("active", T["dim"]), ("pressed", T["accent"])])


# ─── GUI ──────────────────────────────────────────────────────────────────────

class WipeApp(tk.Tk):
    def __init__(self, device):
        super().__init__()
        self.device      = device
        self.config_data = load_config()

        self.title(f"LasTech Drive Wipe Station  v{VERSION}")
        self.resizable(False, False)
        self.configure(bg=T["bg"])
        apply_dark_style(self)

        self.drive       = get_drive_info(device)
        self.drive_class, self.sed_entry = classify_drive(self.drive["serial"])

        self._build_ui()
        self.center_window()

    def center_window(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w  = self.winfo_width()
        h  = self.winfo_height()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_ui(self):
        PAD   = 18
        MONO  = ("Courier New", 10)
        LABEL = ("Helvetica", 10)

        # ── Header ──
        header = tk.Frame(self, bg=T["accent"], pady=10)
        header.pack(fill="x")
        tk.Label(header, text="⚠  LasTech Drive Wipe Station",
                 font=("Helvetica", 15, "bold"),
                 bg=T["accent"], fg="white").pack()
        tk.Label(header, text=f"PHI-Compliant Wipe  •  v{VERSION}",
                 font=("Helvetica", 9),
                 bg=T["accent"], fg="white").pack()

        # ── Drive Info Card ──
        card = tk.Frame(self, bg=T["card"], padx=PAD, pady=PAD)
        card.pack(fill="x", padx=PAD, pady=(PAD, 0))
        tk.Label(card, text="DRIVE IDENTIFIED",
                 font=("Helvetica", 13, "bold"),
                 bg=T["card"], fg=T["accent"]).grid(
                     row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for i, (lbl, val) in enumerate([
            ("Device",  self.drive["device"]),
            ("Model",   self.drive["model"]),
            ("Size",    self.drive["size"]),
            ("Serial",  self.drive["serial"]),
        ], start=1):
            tk.Label(card, text=f"{lbl}:", font=LABEL,
                     bg=T["card"], fg=T["muted"],
                     width=8, anchor="w").grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(card, text=val, font=MONO,
                     bg=T["card"], fg=T["text"]).grid(
                         row=i, column=1, sticky="w", padx=(8, 0), pady=2)

        # ── Classification Banner ──
        cf = tk.Frame(self, bg=T["bg"], pady=8)
        cf.pack(fill="x", padx=PAD)

        if self.drive_class == "SED":
            c_color  = T["warn"]
            c_text   = "🔒  SELF-ENCRYPTING DRIVE (SED)"
            m_text   = "Method: sedutil-cli --PSIDrevert"
            p_text   = f"PSID: {self.sed_entry['psid']}"
            note     = self.sed_entry.get("note", "")
        else:
            c_color  = T["accent"]
            c_text   = "⛔  DRIVE NOT IN KNOWN SED TABLE"
            m_text   = "Cannot proceed — manual classification required"
            p_text   = "Halt: confirm drive type before continuing"
            note     = ""

        tk.Label(cf, text=c_text, font=("Helvetica", 11, "bold"),
                 bg=T["bg"], fg=c_color).pack(anchor="w")
        tk.Label(cf, text=m_text, font=LABEL,
                 bg=T["bg"], fg=T["text"]).pack(anchor="w")
        tk.Label(cf, text=p_text, font=MONO,
                 bg=T["bg"], fg=T["muted"]).pack(anchor="w")
        if note:
            tk.Label(cf, text=f"⚠  Note: {note}", font=LABEL,
                     bg=T["bg"], fg=T["warn"]).pack(anchor="w", pady=(4, 0))

        # ── Shred override (UNKNOWN only) ──
        self.force_shred_var = tk.BooleanVar(value=False)
        if self.drive_class == "UNKNOWN":
            sf = tk.Frame(self, bg=T["card"], padx=PAD, pady=10)
            sf.pack(fill="x", padx=PAD, pady=(0, 4))
            tk.Label(sf,
                     text="If you have confirmed this is a standard (non-SED) drive,\n"
                          "you may authorize shred below. Do NOT use this for SEDs.",
                     font=("Helvetica", 9), bg=T["card"], fg=T["warn"],
                     justify="left").pack(anchor="w")
            tk.Checkbutton(sf,
                           text="I confirm this is NOT a self-encrypting drive — use shred",
                           variable=self.force_shred_var,
                           font=LABEL,
                           bg=T["card"], fg=T["text"],
                           activebackground=T["card"],
                           activeforeground=T["text"],
                           selectcolor=T["bg"],
                           highlightthickness=0,
                           command=self._toggle_shred_confirm).pack(anchor="w", pady=(6, 0))

        # ── Output pane ──
        of = tk.Frame(self, bg=T["bg"], padx=PAD)
        of.pack(fill="both", expand=True, pady=(8, 0), padx=PAD)
        tk.Label(of, text="OUTPUT", font=("Helvetica", 9, "bold"),
                 bg=T["bg"], fg=T["dim"]).pack(anchor="w")
        self.output_text = tk.Text(
            of, height=12, width=72,
            bg=T["term_bg"], fg=T["term_fg"],
            insertbackground=T["term_fg"],
            selectbackground=T["dim"],
            selectforeground=T["text"],
            font=MONO, relief="flat",
            state="disabled", wrap="word",
            highlightthickness=0,
            borderwidth=0
        )
        scroll = ttk.Scrollbar(of, orient="vertical",
                               command=self.output_text.yview,
                               style="Vertical.TScrollbar")
        self.output_text.configure(yscrollcommand=scroll.set)
        self.output_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ── Buttons ──
        bf = tk.Frame(self, bg=T["bg"], pady=PAD)
        bf.pack(fill="x", padx=PAD)
        self.wipe_btn = tk.Button(
            bf, text="CONFIRM & WIPE",
            font=("Helvetica", 12, "bold"),
            bg=T["accent"], fg="white",
            activebackground="#c0392b",
            activeforeground="white",
            relief="flat", padx=20, pady=10,
            cursor="hand2",
            command=self._confirm_and_wipe,
            state="normal" if self.drive_class == "SED" else "disabled"
        )
        self.wipe_btn.pack(side="left")
        tk.Button(bf, text="CANCEL",
                  font=("Helvetica", 12),
                  bg=T["dark2"], fg=T["text"],
                  activebackground=T["dim"],
                  activeforeground=T["text"],
                  relief="flat", padx=20, pady=10,
                  cursor="hand2",
                  command=self._cancel).pack(side="right")

        # ── Status ──
        self.status_label = tk.Label(self, text="",
                                     font=("Helvetica", 10, "bold"),
                                     bg=T["bg"], fg=T["safe"])
        self.status_label.pack(pady=(0, PAD))

        # ── Version footer ──
        tk.Label(self, text=f"LasTech Wipe Station v{VERSION}  •  192.168.10.50",
                 font=("Helvetica", 8),
                 bg=T["bg"], fg=T["dim"]).pack(pady=(0, 6))

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
        self.wipe_btn.configure(
            state="normal" if self.force_shred_var.get() else "disabled"
        )

    def _append_output(self, text):
        self.output_text.configure(state="normal")
        self.output_text.insert("end", text)
        self.output_text.see("end")
        self.output_text.configure(state="disabled")

    def _confirm_and_wipe(self):
        d = self.drive
        method = (f"sedutil-cli PSID revert\nPSID: {self.sed_entry['psid']}"
                  if self.drive_class == "SED"
                  else "shred -v -n 1 -z (1 pass + zero)")

        dlg = DarkDialog(self,
            "Final Confirmation — This Cannot Be Undone",
            f"You are about to permanently destroy all data on:\n\n"
            f"  Device : {d['device']}\n"
            f"  Model  : {d['model']}\n"
            f"  Size   : {d['size']}\n"
            f"  Serial : {d['serial']}\n\n"
            f"  Method : {method}\n\n"
            f"This action is IRREVERSIBLE.",
            is_warning=True
        )
        if not dlg.result:
            self._append_output("\n[CANCELLED] Wipe aborted by user.\n")
            return

        self.wipe_btn.configure(state="disabled", text="WIPING...")
        self.status_label.configure(
            text="Wipe in progress — do not remove drive...", fg=T["warn"]
        )
        threading.Thread(target=self._execute_wipe, daemon=True).start()

    def _execute_wipe(self):
        serial       = self.drive["serial"]
        model        = self.drive["model"]
        device       = self.drive["device"]
        token        = self.config_data.get("TELEGRAM_BOT_TOKEN", "")
        chat         = self.config_data.get("TELEGRAM_CHAT_ID", "")
        timestamp    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        method_label = "SED PSID revert" if self.drive_class == "SED" else "shred"

        log_entry("START", serial, model,
                  f"Wipe started | device={device} | method={method_label}")
        send_telegram(token, chat,
            f"🔴 <b>LasTech Wipe Station v{VERSION}</b>\n"
            f"Wipe <b>STARTED</b>\n\n"
            f"Device: <code>{device}</code>\n"
            f"Model:  <code>{model}</code>\n"
            f"Serial: <code>{serial}</code>\n"
            f"Method: {method_label}\n"
            f"Time:   {timestamp}"
        )

        if self.drive_class == "SED":
            success, _ = run_sed_wipe(device, self.sed_entry["psid"], self._append_output)
        else:
            success, _ = run_shred_wipe(device, self._append_output)

        self._wipe_complete(success, serial, model, device, token, chat)

    def _wipe_complete(self, success, serial, model, device, token, chat):
        timestamp    = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status       = "SUCCESS" if success else "FAILED"
        method_label = "SED PSID revert" if self.drive_class == "SED" else "shred -v -n 1 -z"

        log_entry(status, serial, model,
                  f"Wipe {status} | device={device} | method={method_label}")
        send_telegram(token, chat,
            f"{'✅' if success else '❌'} <b>LasTech Wipe Station v{VERSION}</b>\n"
            f"Wipe <b>{status}</b>\n\n"
            f"Device: <code>{device}</code>\n"
            f"Model:  <code>{model}</code>\n"
            f"Serial: <code>{serial}</code>\n"
            f"Method: {method_label}\n"
            f"Time:   {timestamp}"
        )
        self.after(0, self._update_ui_complete, success)

    def _update_ui_complete(self, success):
        if success:
            self.status_label.configure(
                text="✅ Wipe complete — drive is safe to release.", fg=T["safe"])
            self.wipe_btn.configure(text="DONE", bg=T["safe"],
                                    activebackground="#00a381")
        else:
            self.status_label.configure(
                text="❌ Wipe FAILED — check output and do not release drive.",
                fg=T["accent"])
            self.wipe_btn.configure(text="FAILED", bg=T["dim"])

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
        print(f"LasTech Wipe Station v{VERSION}", file=sys.stderr)
        print("Usage: lastech-wipe-gui.py <device>  e.g.  /dev/sdb", file=sys.stderr)
        sys.exit(1)

    device = sys.argv[1]

    if not os.path.exists(device):
        print(f"ERROR: Device {device} not found.", file=sys.stderr)
        sys.exit(1)
    if not stat.S_ISBLK(os.stat(device).st_mode):
        print(f"ERROR: {device} is not a block device.", file=sys.stderr)
        sys.exit(1)

    if check_and_set_cooldown():
        sys.exit(0)

    app = WipeApp(device)
    app.mainloop()


if __name__ == "__main__":
    main()
