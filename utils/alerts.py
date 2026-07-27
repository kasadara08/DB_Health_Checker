import os
import sys
import json
import smtplib
import datetime
try:
    import msgraph
    from msgraph import GraphServiceClient
except (ImportError, ModuleNotFoundError, Exception):
    msgraph = None
    GraphServiceClient = None


try:
    from dotenv import load_dotenv
    load_dotenv()
except (ImportError, ModuleNotFoundError, Exception):
    pass


# Resolve config directory dynamically for both script and PyInstaller EXE
if getattr(sys, 'frozen', False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_DIR = os.path.join(_base_dir, "config")
STATE_FILE  = os.path.join(CONFIG_DIR, "alert_state.json")
# Resolves a unique JSON configuration path based on the selected TXT file
def get_custom_config_path() -> str:
    try:
        from db_connection import get_txt_path
        txt_path = get_txt_path()
        if txt_path:
            # Replace backslas
            # hes for uniform processing
            normalized_path = txt_path.replace("\\", "/")
            
            # Split from the last '.' (rfind/rsplit) to remove extension
            if "." in normalized_path:
                path_no_ext = normalized_path.rsplit(".", 1)[0]
            else:
                path_no_ext = normalized_path
                
            # From the end, take up to the first '/' (rfind/split)
            if "/" in path_no_ext:
                base_name = path_no_ext.rsplit("/", 1)[1]
            else:
                base_name = path_no_ext
                
            json_name = base_name + ".json"
            return os.path.join(CONFIG_DIR, json_name)
    except Exception as e:
        print(f"[alerts] Error resolving custom config path: {e}")
    return os.path.join(CONFIG_DIR, "email_config.json")

# Helper to check if a specific alert type is enabled by the user
def is_alert_enabled_by_user(alert_type_key: str) -> bool:
    """
    Check if a specific alert type key is enabled in the user's notification preferences.
    """
    config_file = get_custom_config_path()
    if not os.path.exists(config_file):
        return False # Default to disabled if no config exists yet (new registry)
    try:
        with open(config_file, "r") as f:
            prefs = json.load(f)
        # Prefs are stored in the same JSON under a nested "preferences" key, or directly at root
        # We will look for a "preferences" dictionary
        user_prefs = prefs.get("preferences", {})
        return user_prefs.get(alert_type_key, False)
    except Exception:
        return False

#Reads your SMTP settings (server, port, from, to). If no file exists, returns empty defaults (so they are blank in UI)
def load_config() -> dict:
    default_config = {
        "smtp_server": "",
        "smtp_port": "",
        "smtp_username": "",
        "smtp_password": "",
        "from_email": "",
        "to_emails": [],
        "enabled": False
    }
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, exist_ok=True)
    
    config_file = get_custom_config_path()
    if not os.path.exists(config_file):
        return default_config
    try:
        with open(config_file, "r") as f:
            return json.load(f)
    except Exception:
        return default_config

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving alert state: {e}")

NOTIFICATION_FILE = os.path.join(CONFIG_DIR, "email_notifications.json")
HISTORY_FILE      = os.path.join(CONFIG_DIR, "email_history.json")


def _write_notification(ntype: str, subject: str, message: str):
    """Write a notification entry to a shared JSON file for the Streamlit UI to pick up."""
    try:
        notifications = []
        if os.path.exists(NOTIFICATION_FILE):
            try:
                with open(NOTIFICATION_FILE, "r") as f:
                    notifications = json.load(f)
            except Exception:
                notifications = []

        notifications.append({
            "type": ntype,  # "success" or "error"
            "subject": subject,
            "message": message,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "seen": False
        })
        # Keep only the last 20 notifications
        notifications = notifications[-20:]
        with open(NOTIFICATION_FILE, "w") as f:
            json.dump(notifications, f, indent=2)
    except Exception:
        pass


def _write_history(subject: str, body: str, to_emails: list, status: str, error: str = ""):
    """
    Append a structured record to email_history.json.
    Keeps the last 200 records. Each record contains:
      timestamp, subject, to, status (sent/failed/disabled), error, body_preview
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    history = json.load(f)
            except Exception:
                history = []

        # Extract alert type from subject for quick display
        alert_type = "General"
        for kw in ("Database Down", "Listener Down", "Tablespace", "Archive Log",
                   "Mount Point", "ORA Error", "Deadlock", "Blocking Session", "Test Alert"):
            if kw.lower() in subject.lower():
                alert_type = kw
                break

        # Extract DB name and server from body (first 2 lines that match)
        db_name = ""
        server  = ""
        for line in body.splitlines():
            line = line.strip()
            if line.lower().startswith("database name") and not db_name:
                db_name = line.split(":", 1)[-1].strip()
            if line.lower().startswith("server name") and not server:
                server  = line.split(":", 1)[-1].strip()
            if db_name and server:
                break

        record = {
            "timestamp":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "subject":      subject,
            "alert_type":   alert_type,
            "db_name":      db_name,
            "server":       server,
            "to":           to_emails,
            "status":       status,       # "sent" | "failed" | "disabled"
            "error":        error,
            "body_preview": body[:300].replace("\n", " | ")  # compact preview
        }
        history.append(record)
        history = history[-200:]  # keep last 200
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[history] write error: {e}")


def send_alert_email(subject: str, body: str, alert_type: str = None) -> dict:
    """
    Send an alert email using the user-configured SMTP settings.
    Respects user notification preferences — if alert_type is unchecked, no email is sent.
    """
    from email.message import EmailMessage
    import ssl

    config    = load_config()
    result    = {"success": False, "message": "", "error": ""}
    to_emails = config.get("to_emails", [])

    # ── Preference Filter: Check if user enabled emails for this alert type ───
    if alert_type and not is_alert_enabled_by_user(alert_type):
        msg = f"Notification for '{alert_type}' is disabled in user preferences. Email skipped."
        print(f"[alerts] {msg}")
        result["message"] = msg
        return result


    # ── Audit log (always) ────────────────────────────────────────────────────
    log_msg = (
        f"--- EMAIL ALERT ---\nDate: {datetime.datetime.now()}\n"
        f"Subject: {subject}\nBody:\n{body}\n-----------------------\n"
    )
    try:
        with open(os.path.join(_base_dir, "alerts_log.txt"), "a") as f:
            f.write(log_msg)
    except Exception:
        pass

    # ── Guard: alerts disabled ────────────────────────────────────────────────
    if not config.get("enabled"):
        msg = f"SMTP disabled. Alert logged only: {subject}"
        print(msg)
        result["message"] = msg
        _write_history(subject, body, to_emails, "disabled")
        return result

    # ── Read user-configured SMTP values (no hardcoded defaults) ─────────────
    smtp_server   = (config.get("smtp_server") or "").strip()
    smtp_port     = int(config.get("smtp_port") or 587)
    smtp_username = (config.get("smtp_username") or "").strip()
    smtp_password = (config.get("smtp_password") or "").strip()
    from_email    = (config.get("from_email") or "").strip()
    to_emails     = [e.strip() for e in config.get("to_emails", []) if str(e).strip()]

    # ── Validate required fields ──────────────────────────────────────────────
    if not smtp_server:
        msg = "SMTP Server is not configured. Please fill in the Email Alerts form."
        result["error"] = msg
        _write_notification("error", subject, msg)
        _write_history(subject, body, to_emails, "failed", msg)
        return result

    if not from_email:
        msg = "Sender Email is not configured. Please fill in the Email Alerts form."
        result["error"] = msg
        _write_notification("error", subject, msg)
        _write_history(subject, body, to_emails, "failed", msg)
        return result

    if not to_emails:
        msg = "Recipient email(s) are not configured. Please fill in the Email Alerts form."
        result["error"] = msg
        _write_notification("error", subject, msg)
        _write_history(subject, body, to_emails, "failed", msg)
        return result

    # ── Build the EmailMessage ────────────────────────────────────────────────
    email_msg = EmailMessage()
    email_msg["Subject"] = subject
    email_msg["From"]    = from_email
    email_msg["To"]      = ", ".join(to_emails)
    email_msg.set_content(body)

    # ── Send using the correct connection mode for the configured port ────────
    try:
        if smtp_port == 465:
            # SSL from the start (SMTP_SSL)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15, context=context) as smtp:
                smtp.ehlo()
                smtp.send_message(email_msg)

        else:
            # Port 587 (Office 365 / Gmail) and others: plain connect → STARTTLS
            # with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as smtp:
                
            #     print("login ", smpt_)
            #     smtp.ehlo()
            #     smtp.starttls()          # mandatory for Office 365
            #     smtp.ehlo()
            print("checking port 587")
            with smtplib.SMTP("smtp.office365.com",int("587")) as smtp:
                
                smtp.starttls()
                # smtp.login("mohanraj.krishnasamy@kasadara.com","Gthannasi12#")
                smtp.login("yuvarani.skannan@kasadara.com","Yuva@2004")
                print("login successful", smtp)

                smtp.send_message(email_msg)


        success_msg = f"Email sent successfully to: {', '.join(to_emails)}"
        print(f"[SMTP] Sent: {subject}")
        result["success"] = True
        result["message"] = success_msg
        _write_notification("success", subject, success_msg)
        _write_history(subject, body, to_emails, "sent")
        return result

    except smtplib.SMTPAuthenticationError as e:
        err_msg = f"SMTP Authentication Failed: Wrong username or password. Detail: {e}"
    except smtplib.SMTPConnectError as e:
        err_msg = f"SMTP Connection Failed: Cannot connect to {smtp_server}:{smtp_port}. Check server/port. Detail: {e}"
    except smtplib.SMTPRecipientsRefused as e:
        err_msg = f"Recipients Refused: Server rejected recipient(s) {to_emails}. Detail: {e}"
    except smtplib.SMTPSenderRefused as e:
        err_msg = f"Sender Refused: Server rejected sender '{from_email}'. Detail: {e}"
    except smtplib.SMTPDataError as e:
        err_msg = f"SMTP Data Error: Server rejected email content. Detail: {e}"
    except smtplib.SMTPServerDisconnected as e:
        err_msg = f"SMTP Server Disconnected unexpectedly. Detail: {e}"
    except ConnectionRefusedError:
        err_msg = f"Connection Refused: No SMTP server at {smtp_server}:{smtp_port}."
    except TimeoutError:
        err_msg = f"Connection Timeout: Could not reach {smtp_server}:{smtp_port} within 15 seconds."
    except OSError as e:
        err_msg = f"Network Error reaching {smtp_server}:{smtp_port}. Detail: {e}"
    except Exception as e:
        err_msg = f"Unexpected email error: {type(e).__name__}: {e}"

    print(f"[SMTP] Failed: {err_msg}")
    result["error"] = err_msg
    _write_notification("error", subject, err_msg)
    _write_history(subject, body, to_emails, "failed", err_msg)
    try:
        with open(os.path.join(_base_dir, "alerts_log.txt"), "a") as f:
            f.write(f"SMTP Send Error: {err_msg}\n\n")
    except Exception:
        pass
    return result

def check_and_trigger_alerts(server_name: str, db_name: str, host_name: str, stats: dict):
    """
    Checks the status details of a DB instance and fires email alerts on state transition.
    """
    state = load_state()
    db_key = f"{server_name}:{db_name}"
    if db_key not in state:
        state[db_key] = {
            "db_down":          False,
            "listener_down":    False,
            "tablespaces":      {},
            "arc_log_alert":    False,
            "mount_points":     {},
            "ora_errors_sent":  [],
            "deadlock_alert":   False,
            "blocking_alert":   False,
        }
    
    db_state = state[db_key]
    changed = False
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Database Down Check
    db_is_down = (stats.get("db") == "DOWN")
    if db_is_down:
        if not db_state.get("db_down"):
            subject = "Critical Alert - Database Down"
            body = (
                f"Server Name: {server_name}\n"
                f"Database Name: {db_name}\n"
                f"Host Name: {host_name}\n"
                f"Alert Type: Database Down\n"
                f"Severity: Critical\n"
                f"Status: DOWN\n"
                f"Date & Time: {now_str}\n\n"
                f"Problem: The Oracle Database instance '{db_name}' is DOWN or unreachable."
            )
            send_alert_email(subject, body, alert_type="db_down")
            db_state["db_down"] = True
            changed = True
    else:
        if db_state.get("db_down"):
            db_state["db_down"] = False
            changed = True

    # 2. Listener Down Check
    lsnr_is_down = (stats.get("listener") == "DOWN")
    if lsnr_is_down:
        if not db_state.get("listener_down"):
            subject = "Critical Alert - Listener Down"
            body = (
                f"Server Name: {server_name}\n"
                f"Database Name: {db_name}\n"
                f"Host Name: {host_name}\n"
                f"Alert Type: Listener Down\n"
                f"Severity: Critical\n"
                f"Status: DOWN\n"
                f"Date & Time: {now_str}\n\n"
                f"Problem: The Oracle Listener for DB '{db_name}' is DOWN."
            )
            send_alert_email(subject, body, alert_type="listener_down")
            db_state["listener_down"] = True
            changed = True
    else:
        if db_state.get("listener_down"):
            db_state["listener_down"] = False
            changed = True

    # 3. Tablespace Alert Check (Permanent Tablespaces >= 90%)
    balance_ts = stats.get("balance_ts", [])
    current_full_ts = {}
    for ts in balance_ts:
        pct = ts.get("pct", 0)
        ts_name = ts.get("name")
        if pct >= 90.0:
            current_full_ts[ts_name] = ts

    ts_state = db_state.setdefault("tablespaces", {})
    for ts_name, ts in current_full_ts.items():
        if not ts_state.get(ts_name):
            subject = "Critical Alert - Tablespace Nearly Full"
            body = (
                f"Server Name: {server_name}\n"
                f"Database Name: {db_name}\n"
                f"Host Name: {host_name}\n"
                f"Alert Type: Tablespace Alert\n"
                f"Severity: Critical\n"
                f"Status: Nearly Full\n"
                f"Date & Time: {now_str}\n\n"
                f"Tablespace Name: {ts_name}\n"
                f"Used MB: {ts.get('used_mb', 0):,.2f}\n"
                f"Free MB: {ts.get('free_mb', 0):,.2f}\n"
                f"Max Size MB: {ts.get('total_mb', 0):,.2f}\n"
                f"Percentage Used: {ts.get('pct', 0):.2f}%"
            )
            send_alert_email(subject, body, alert_type="tablespace_critical")
            ts_state[ts_name] = True
            changed = True

    for ts_name in list(ts_state.keys()):
        if ts_name not in current_full_ts:
            ts_state[ts_name] = False
            del ts_state[ts_name]
            changed = True

    # 4. Archive Log Alert Check (Used pct >= 90% or general failures)
    arc_pct = stats.get("arc_pct", 0)
    arc_configured = stats.get("arc_configured", False)
    arc_alert_triggered = False
    problem_desc = ""
    if arc_configured:
        if arc_pct >= 90.0:
            arc_alert_triggered = True
            problem_desc = f"Archive Log (FRA) destination is nearly full: {arc_pct:.2f}%."
    
    # Check alert log messages if any
    alert_log_errs = stats.get("alert_log_errors", [])
    for msg in alert_log_errs:
        lower_msg = msg.lower()
        if "ora-00257" in lower_msg or "archiver error" in lower_msg or "archive log" in lower_msg:
            arc_alert_triggered = True
            problem_desc = f"Archive log error found in alert log: {msg}"
            break

    if arc_alert_triggered:
        if not db_state.get("arc_log_alert"):
            subject = "Critical Alert - Archive Log Issue"
            body = (
                f"Server Name: {server_name}\n"
                f"Database Name: {db_name}\n"
                f"Host Name: {host_name}\n"
                f"Alert Type: Archive Log Alert\n"
                f"Severity: Critical\n"
                f"Status: Warning/Error\n"
                f"Date & Time: {now_str}\n\n"
                f"Problem: {problem_desc}"
            )
            send_alert_email(subject, body, alert_type="archive_log_full")
            db_state["arc_log_alert"] = True
            changed = True
    else:
        if db_state.get("arc_log_alert"):
            db_state["arc_log_alert"] = False
            changed = True

    # 5. Mount Point Alert Check (Usage >= 90%)
    drives = stats.get("drives", [])
    current_full_mounts = {}
    for dr in drives:
        pct = dr.get("pct", 0)
        mount = dr.get("mount_point", dr.get("drive"))
        # Exclude pseudo filesystems
        if dr.get("stype") == "Pseudo":
            continue
        if pct >= 90.0:
            current_full_mounts[mount] = dr

    mount_state = db_state.setdefault("mount_points", {})
    for mount, dr in current_full_mounts.items():
        if not mount_state.get(mount):
            subject = "Critical Alert - Mount Point Nearly Full"
            body = (
                f"Server Name: {server_name}\n"
                f"Database Name: {db_name}\n"
                f"Host Name: {host_name}\n"
                f"Alert Type: Mount Point Alert\n"
                f"Severity: Critical\n"
                f"Status: Nearly Full\n"
                f"Date & Time: {now_str}\n\n"
                f"Mount Point: {mount}\n"
                f"Total Space: {dr.get('total', 0):,.2f} GB\n"
                f"Used Space: {dr.get('used', 0):,.2f} GB\n"
                f"Free Space: {dr.get('free', 0):,.2f} GB\n"
                f"Usage %: {dr.get('pct', 0):.2f}%"
            )
            send_alert_email(subject, body, alert_type="mount_critical")
            mount_state[mount] = True
            changed = True

    for mount in list(mount_state.keys()):
        if mount not in current_full_mounts:
            mount_state[mount] = False
            del mount_state[mount]
            changed = True

    # 6. ORA Error Alert Check
    ora_errors = stats.get("ora_errors", [])
    sent_codes  = db_state.setdefault("ora_errors_sent", [])
    for ora_msg in ora_errors:
        ora_msg_str = str(ora_msg)
        # Extract ORA code (e.g. ORA-00257)
        import re as _re
        match = _re.search(r"ORA-\d+", ora_msg_str, _re.IGNORECASE)
        ora_code = match.group(0).upper() if match else ora_msg_str[:20]
        if ora_code not in sent_codes:
            subject = f"Critical Alert - ORA Error Detected"
            body = (
                f"Server Name  : {server_name}\n"
                f"Database Name: {db_name}\n"
                f"Host Name    : {host_name}\n"
                f"Alert Type   : ORA Error\n"
                f"Severity     : Critical\n"
                f"Date & Time  : {now_str}\n\n"
                f"ORA Error Code   : {ora_code}\n"
                f"Complete Message : {ora_msg_str}\n\n"
                f"Action: Please investigate immediately."
            )
            send_alert_email(subject, body, alert_type="ora_error")
            sent_codes.append(ora_code)
            changed = True
    # Trim sent_codes to last 50 to prevent unbounded growth
    db_state["ora_errors_sent"] = sent_codes[-50:]

    # 7. Deadlock Alert Check
    deadlock = stats.get("deadlock_detected", False)
    if deadlock and not db_state.get("deadlock_alert"):
        deadlock_info = "; ".join(str(e) for e in stats.get("alert_log_errors", []))[:500]
        blocking_details = stats.get("blocking_details", [])
        session_lines = ""
        for s in blocking_details[:5]:
            session_lines += (
                f"  SID={s.get('SID','?')} Serial={s.get('SERIAL#','?')} "
                f"User={s.get('USERNAME','?')} Blocking={s.get('BLOCKING_SESSION','?')}\n"
            )
        subject = "Critical Alert - Deadlock Detected"
        body = (
            f"Server Name  : {server_name}\n"
            f"Database Name: {db_name}\n"
            f"Host Name    : {host_name}\n"
            f"Alert Type   : Deadlock Detection\n"
            f"Severity     : Critical\n"
            f"Date & Time  : {now_str}\n\n"
            f"Deadlock Info: {deadlock_info or 'Detected via wait event'}\n\n"
            f"Session Details:\n{session_lines or '  No session details available.'}\n"
            f"Action: Check v$session and trace files immediately."
        )
        send_alert_email(subject, body, alert_type="deadlock")
        db_state["deadlock_alert"] = True
        changed = True
    elif not deadlock and db_state.get("deadlock_alert"):
        db_state["deadlock_alert"] = False
        changed = True

    # 8. Blocking Session Alert Check
    has_blocking = stats.get("has_blocking", False)
    if has_blocking and not db_state.get("blocking_alert"):
        blocking_details = stats.get("blocking_details", [])
        session_lines = ""
        for s in blocking_details[:5]:
            session_lines += (
                f"  SID={s.get('SID','?')} Serial={s.get('SERIAL#','?')} "
                f"User={s.get('USERNAME','?')} Blocking SID={s.get('BLOCKING_SESSION','?')} "
                f"Status={s.get('STATUS','?')}\n"
            )
        subject = "Critical Alert - Blocking Session Detected"
        body = (
            f"Server Name  : {server_name}\n"
            f"Database Name: {db_name}\n"
            f"Host Name    : {host_name}\n"
            f"Alert Type   : Blocking Session\n"
            f"Severity     : Critical\n"
            f"Date & Time  : {now_str}\n\n"
            f"Blocking Sessions Detected: {len(blocking_details)}\n\n"
            f"Session Details:\n{session_lines or '  No session details available.'}\n"
            f"Action: Investigate blocking sessions in v$session immediately."
        )
        send_alert_email(subject, body, alert_type="blocking_sessions")
        db_state["blocking_alert"] = True
        changed = True
    elif not has_blocking and db_state.get("blocking_alert"):
        db_state["blocking_alert"] = False
        changed = True

    if changed:
        save_state(state)

