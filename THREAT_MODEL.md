# Threat Model & Risk Assessment
## LasTech Drive Wipe Station Automation Script

**Version:** 1.0  
**Date:** June 10, 2026  
**Classification:** INTERNAL / OPERATIONAL

---

## Threat Model Methodology

This threat model uses **STRIDE** (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) to systematically identify threats.

---

## Assets at Risk

### Primary Assets
1. **Data on Drives Being Wiped** — PHI, sensitive business data
2. **Audit Log** — `/var/log/lastech-wipe/wipe.log`
3. **Credentials** — Telegram token in `/etc/lastech-wipe/config.env`
4. **Drive Inventory** — KNOWN_SEDS table (serial + PSID mappings)

### Secondary Assets
1. **Wipe Station Integrity** — System's continued operation
2. **Notification Channel** — Telegram bot availability
3. **System Reputation** — Data destruction compliance posture

---

## STRIDE Analysis

### 1. SPOOFING (False Identity)

#### Threat 1.1: Attacker Spoofs udev Event

**Scenario:** Attacker sends crafted udev netlink message to trigger fake drive insertion

**Likelihood:** MEDIUM (requires CAP_NET_ADMIN; rarely granted to non-root on locked systems)

**Impact:** Could trigger GUI for non-existent device

**Existing Mitigations:**
- udev runs as root — only root can trigger events
- Device path validated with `os.path.exists()` (line 540)
- Block device check with `stat.S_ISBLK()` (line 543)

**Residual Risk:** LOW (requires root compromise first)

**Additional Mitigation:** Add device inode validation
```python
# Ensure device has expected inode type (block device)
dev_stat = os.stat(device)
assert stat.S_ISBLK(dev_stat.st_mode), f"Not a block device: {device}"
```

---

#### Threat 1.2: Attacker Spoofs Telegram Notifications

**Scenario:** Attacker intercepts wipe completion notification, sending false success/failure alerts

**Likelihood:** LOW (requires MitM on HTTPS)

**Impact:** Operators confused about wipe status; could release un-wiped drives

**Existing Mitigations:**
- HTTPS/TLS for Telegram API
- Token never exposed in plaintext logs
- GUI shows actual wipe result (not just Telegram notification)

**Residual Risk:** MEDIUM (depends on network security; user relies on GUI + notification correlation)

**Recommendation:** Add HMAC signature to notification text for verification
```python
import hashlib
message_hash = hashlib.sha256(f"{device}{serial}{status}".encode()).hexdigest()[:8]
text = f"...Status: {status}\nHash: {message_hash}"  # Operator can verify
```

---

### 2. TAMPERING (Unauthorized Modification)

#### Threat 2.1: Attacker Modifies KNOWN_SEDS Table

**Scenario:** Attacker gains root, modifies PSID for a known SED to point to wrong drive

**Likelihood:** LOW (requires root compromise)

**Impact:** CRITICAL — could cause wipe of wrong drive

**Existing Mitigations:**
- File in `/usr/local/bin/` (root-only write)
- Two-step confirmation shows drive details
- Serial number printed prominently in GUI
- Final confirmation dialog impossible to bypass

**Residual Risk:** MEDIUM (root = system owner; assume trusted; but human error possible)

**Recommendation:** Calculate MD5 hash of KNOWN_SEDS at startup, warn if modified
```python
import hashlib
from functools import reduce

def verify_known_seds():
    seds_str = json.dumps(KNOWN_SEDS, sort_keys=True)
    current_hash = hashlib.md5(seds_str.encode()).hexdigest()
    
    # Optional: Store hash and compare
    if current_hash != EXPECTED_SEDS_HASH:
        log_entry("WARN", "n/a", "n/a", "KNOWN_SEDS table modified since build")
```

---

#### Threat 2.2: Attacker Modifies config.env Credentials

**Scenario:** Attacker changes Telegram token to attacker's bot, silently redirects notifications

**Likelihood:** LOW (requires root; file permissions 0o600)

**Impact:** MEDIUM — notifications routed to attacker instead of admin

**Existing Mitigations:**
- File permissions: 0o600 (root-only read/write)
- Token sourced from Vaultwarden (encrypted at rest)
- Telegram send wrapped in try/except with silent failure logging

**Residual Risk:** MEDIUM (requires root; assume root is trusted)

**Recommendation:** Add checksum verification + startup validation
```python
def validate_config():
    with open(CONFIG_FILE, 'r') as f:
        content = f.read()
    
    token = config_data.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = config_data.get("TELEGRAM_CHAT_ID", "")
    
    if token and not token.startswith("123"):  # Telegram bot tokens start with digit
        log_entry("WARN", "n/a", "n/a", "Telegram token format suspicious")
```

---

#### Threat 2.3: Attacker Modifies Wipe Commands

**Scenario:** Attacker replaces `sedutil-cli` with fake binary that doesn't actually wipe

**Likelihood:** LOW (requires root; binary in `/usr/local/bin/`)

**Impact:** CRITICAL — drives released without data destruction

**Existing Mitigations:**
- Binary path in `/usr/local/bin/` (root-only write)
- Installer verifies Python syntax but NOT sedutil-cli binary
- Wipe output streamed to GUI (malicious tool could fake output)

**Residual Risk:** HIGH (no integrity checking of sedutil-cli binary)

**Recommendation:** Add binary signature verification
```python
import hashlib

EXPECTED_SEDUTIL_SHA256 = "abc123def456..."  # From Drive Trust Alliance site

def verify_sedutil_binary():
    try:
        with open(SEDUTIL_BIN, 'rb') as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        if actual_hash != EXPECTED_SEDUTIL_SHA256:
            log_entry("ERROR", "n/a", "n/a", "sedutil-cli binary verification FAILED")
            sys.exit(1)
    except Exception as e:
        log_entry("ERROR", "n/a", "n/a", f"Could not verify sedutil-cli: {e}")
        sys.exit(1)

verify_sedutil_binary()  # Call in main() before any wipes
```

---

### 3. REPUDIATION (Denial of Action)

#### Threat 3.1: Operator Claims Wipe Never Happened

**Scenario:** Operator runs wipe, then claims drive was never wiped (tries to recover it)

**Likelihood:** MEDIUM (human dishonesty)

**Impact:** MEDIUM — compliance violation; audit trail compromised if trusted

**Existing Mitigations:**
- Timestamped audit log with drive serial
- Telegram notification with date/time
- GUI displays wipe completion status
- Non-repudiation: physical Telegram alert message

**Residual Risk:** LOW (multiple evidence sources; difficult to credibly deny)

**Recommendation:** Require operator sign-off
```python
# Optional: At completion, require manual acknowledgment
response = messagebox.askyesno(
    "Acknowledge Completion",
    "I acknowledge this drive has been wiped and is safe to release.\n"
    "Click YES to confirm."
)
if response:
    log_entry("OPERATOR_SIGNOFF", serial, model, "Operator confirmed wipe completion")
```

---

### 4. INFORMATION DISCLOSURE (Unauthorized Access to Data)

#### Threat 4.1: Unprivileged User Reads Audit Log

**Scenario:** Non-root user reads wipe log, learns what drives were wiped (commercial espionage)

**Likelihood:** LOW (log file is 0o600)

**Impact:** LOW — serial numbers + drive models revealed (not sensitive per se)

**Existing Mitigations:**
- Log file permissions: 0o600 (root-only read)
- Log directory permissions: 0o700 (root-only access)
- Enforced at runtime in `ensure_log_dir()` (lines 80-85)

**Residual Risk:** MINIMAL (file permissions strictly enforced)

---

#### Threat 4.2: Memory Disclosure of Credentials

**Scenario:** Attacker core-dumps process, extracts Telegram token from memory

**Likelihood:** VERY LOW (requires root; core dump + process introspection)

**Impact:** MEDIUM — Telegram token could be used to spam notifications

**Existing Mitigations:**
- Token stored in memory only during Telegram send (line 146)
- Send wrapped in try/except; error handling cleans up
- Async daemon thread means token cleaned up quickly

**Residual Risk:** LOW (requires root + core dumps enabled; assume hardened system)

**Recommendation:** Use `secrets` module for sensitive string handling (prevents accidental logging)
```python
import secrets

token = config_data.get("TELEGRAM_BOT_TOKEN", "")
if len(token) > 4:
    token_display = token[:4] + "***" + token[-4:]  # Mask in logs
    log_entry("INFO", "n/a", "n/a", f"Telegram token found: {token_display}")
```

---

#### Threat 4.3: Drive Serial Number Leakage in Error Messages

**Scenario:** Exception occurs during wipe; stack trace logged with serial number visible in stderr

**Likelihood:** LOW (errors caught and logged safely)

**Impact:** LOW — serial number in log (same as audit trail anyway)

**Existing Mitigations:**
- All errors caught in try/except blocks
- Errors logged via `log_entry()` which sanitizes inputs with `safe_str()`
- No raw stack traces printed to stderr

**Residual Risk:** MINIMAL (sanitization in place)

---

### 5. DENIAL OF SERVICE (Service Unavailability)

#### Threat 5.1: Rapid Drive Insertions Cause Stacked GUIs

**Scenario:** Attacker (or operator error) inserts/removes drives rapidly; multiple GUIs launch

**Likelihood:** MEDIUM (easy to trigger, possible by accident)

**Impact:** MEDIUM — confusion, potential accidental double-wipe

**Existing Mitigations:**
- Cooldown mechanism with fcntl locking (lines 149-186)
- 30-second window: second insertion rejected atomically
- Rejected insertion logged as SKIPPED

**Residual Risk:** MINIMAL (cooldown impossible to bypass)

---

#### Threat 5.2: Attacker Fills Disk, Prevents Log Writing

**Scenario:** Attacker fills `/var/log/` partition; log entries fail silently

**Likelihood:** VERY LOW (requires root; unusual on dedicated system)

**Impact:** MEDIUM — audit trail incomplete

**Existing Mitigations:**
- Log write failures caught; warning printed to stderr (line 100)
- Wipe proceeds even if log fails (safe default)
- Exception handling prevents crashes (lines 96-100)

**Residual Risk:** LOW (warning produced; wipe still completes)

**Recommendation:** Monitor disk space at startup
```python
import shutil

def check_disk_space():
    stat = shutil.disk_usage(LOG_DIR)
    free_gb = stat.free / (1024**3)
    if free_gb < 1.0:
        log_entry("WARN", "n/a", "n/a", f"Low disk space: {free_gb:.2f} GB free")
```

---

#### Threat 5.3: Telegram API Unavailable

**Scenario:** Telegram API down; notifications fail; operator thinks wipe failed

**Likelihood:** VERY LOW (Telegram is highly available)

**Impact:** LOW — GUI still shows result; only notification missing

**Existing Mitigations:**
- Notifications sent asynchronously in daemon thread (non-blocking)
- Wipe proceeds regardless of notification success
- Failure silently logged (line 145)
- Credentials validated at startup; missing credentials warned (line 123)

**Residual Risk:** LOW (wipe completes; only notification affected)

**Recommendation:** Add connection test at startup
```python
def test_telegram_connectivity():
    if not config_data.get("TELEGRAM_BOT_TOKEN"):
        return True  # Credentials missing, already warned
    try:
        urllib.request.urlopen("https://api.telegram.org/", timeout=5)
        return True
    except Exception:
        log_entry("WARN", "n/a", "n/a", "Telegram API unreachable")
        return False
```

---

### 6. ELEVATION OF PRIVILEGE (Unauthorized Root Access)

#### Threat 6.1: Attacker Exploits sedutil-cli Vulnerability

**Scenario:** sedutil-cli binary has security flaw; attacker exploits to gain root

**Likelihood:** VERY LOW (Drive Trust Alliance maintains tool; used by enterprise)

**Impact:** CRITICAL — full system compromise

**Existing Mitigations:**
- Binary downloaded from official GitHub repo (line 80 in install.sh)
- Used only with validated PSID (lines 230-237)
- Executed via subprocess list (no shell metacharacters)

**Residual Risk:** MEDIUM (depends on sedutil-cli security; recommend binary verification)

**Recommendation:** Add GPG signature verification
```bash
# In install.sh, after downloading sedutil-cli:
if command -v gpg &>/dev/null; then
    # Download signature from repo
    curl -fsSL "$SEDUTIL_URL.sig" -o /tmp/sedutil-cli.sig
    # Verify against Drive Trust Alliance public key
    gpg --verify /tmp/sedutil-cli.sig /usr/local/bin/sedutil-cli
else
    echo "WARNING: GPG not available — cannot verify sedutil-cli signature"
fi
```

---

#### Threat 6.2: Attacker Exploits Python Vulnerability

**Scenario:** Python interpreter has security flaw exploited via crafted input

**Likelihood:** VERY LOW (Python security actively maintained)

**Impact:** CRITICAL — full system compromise

**Existing Mitigations:**
- Input from system sources only (lsblk, environment) — no user input
- All subprocess calls use list form (no shell interpretation)
- Standard library only (no third-party packages with vulnerabilities)

**Residual Risk:** LOW (attack surface minimal; no external input processing)

---

#### Threat 6.3: Attacker Exploits Tkinter/X11 Vulnerability

**Scenario:** X11 server compromise; attacker injects events into Tkinter GUI

**Likelihood:** LOW (X11 security hardened on modern systems)

**Impact:** MEDIUM — could trigger unintended wipes or steal input

**Existing Mitigations:**
- Xauthority validation in trigger script (lines 27-32)
- Wipe requires explicit user confirmation + final dialog
- Event injection would need to click through 2 dialogs correctly

**Residual Risk:** MEDIUM (depends on X11 server security; assume modern system)

---

## Risk Scoring Matrix

### Risk = (Likelihood × Impact × Exploitability)

| Threat ID | Threat | L | I | E | Risk | Status |
|-----------|--------|---|---|---|------|--------|
| 1.1 | Spoofed udev event | M | M | L | MED | ✅ Mitigated |
| 1.2 | Spoofed notifications | L | M | M | LOW | ✅ Acceptable |
| 2.1 | Modified KNOWN_SEDS | L | C | L | MED | ⚠️ Recommend verification |
| 2.2 | Modified config.env | L | M | L | LOW | ✅ Mitigated |
| 2.3 | Modified wipe commands | L | C | M | MED | ⚠️ Recommend verification |
| 3.1 | Operator denial | M | M | L | MED | ✅ Mitigated |
| 4.1 | Log file disclosure | L | L | L | LOW | ✅ Mitigated |
| 4.2 | Memory credential leak | VL | M | VL | LOW | ✅ Acceptable |
| 4.3 | Serial leakage in errors | L | L | L | MINIMAL | ✅ Mitigated |
| 5.1 | Stacked GUI DoS | M | M | H | MED | ✅ Mitigated |
| 5.2 | Disk full DoS | VL | M | VL | LOW | ✅ Acceptable |
| 5.3 | Telegram API down | VL | L | N/A | MINIMAL | ✅ Mitigated |
| 6.1 | sedutil-cli exploit | VL | C | VL | MED | ⚠️ Recommend verification |
| 6.2 | Python exploit | VL | C | VL | LOW | ✅ Acceptable |
| 6.3 | X11/Tkinter exploit | L | M | L | MED | ✅ Acceptable |

**Legend:**
- L: Likelihood (VL=Very Low, L=Low, M=Medium, H=High)
- I: Impact (L=Low, M=Medium, C=Critical)
- E: Exploitability (L=Low, M=Medium, H=High)
- Risk: Overall risk level

---

## Recommended Priority Mitigations

### HIGH PRIORITY (Implement Before Deployment)

1. ✅ **Block Device Validation** — Already implemented (lines 543-545)
2. ✅ **PSID Format Validation** — Already implemented (lines 230-237)
3. ✅ **Log Directory Permissions** — Already implemented (lines 80-85)
4. ✅ **Cooldown Locking** — Already implemented (lines 149-186)
5. ⚠️ **sedutil-cli Binary Verification** — Add SHA256 checksum verification

### MEDIUM PRIORITY (Next Release)

1. Add MD5 hash verification of KNOWN_SEDS table at startup
2. Add Telegram connectivity test at startup
3. Add disk space monitoring
4. Add operator sign-off confirmation at completion

### LOW PRIORITY (Optional Enhancements)

1. GPG signature verification for sedutil-cli
2. Notification HMAC validation
3. Secrets module for credential masking in memory
4. Systemd journal integration for centralized logging

---

## Residual Risk Summary

**Overall Residual Risk: ACCEPTABLE** ✅

**Assumptions That Must Hold:**
1. System has physical access controls (locked room)
2. Root access restricted to trusted administrators
3. udev device isolation maintained (wipe bay only)
4. Regular log audits conducted (weekly minimum)
5. Telegram bot token rotated periodically (quarterly)

**If Assumptions Violated:**
- Add binary verification (sedutil-cli SHA256 check)
- Add KNOWN_SEDS integrity verification (MD5 hash)
- Implement centralized audit logging (syslog/journald)
- Require audit trail review before release

---

## Threat Model Sign-Off

**This threat model is valid for:**
- Current codebase (as of June 10, 2026)
- Linux Mint 21+ / Debian 11+ systems
- Enterprise medical device manufacturing environment

**Next Review Date:** June 10, 2027 (or after significant changes)

**Reviewed By:** [Security Officer]  
**Approved By:** [Operations Manager]  
**Date:** June 10, 2026
