import os
import sys
import json
import time
import base64
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import msal

# Resolve settings path portably
if getattr(sys, "frozen", False):
    SETTINGS_PATH = os.path.join(os.path.dirname(sys.executable), "data", "email_settings.json")
    HISTORY_PATH = os.path.join(os.path.dirname(sys.executable), "data", "email_history.json")
else:
    SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "email_settings.json")
    HISTORY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "email_history.json")

# In-memory token cache (Do NOT store access token permanently on disk)
_OAUTH_TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0
}

# --- SECRET MASKING & ENCRYPTION HELPERS ---

def obfuscate_secret(secret_str):
    if not secret_str:
        return ""
    try:
        return "ENC:" + base64.b64encode(secret_str.encode("utf-8")).decode("ascii")
    except Exception:
        return secret_str

def deobfuscate_secret(secret_str):
    if not secret_str:
        return ""
    if isinstance(secret_str, str) and secret_str.startswith("ENC:"):
        try:
            return base64.b64decode(secret_str[4:].encode("ascii")).decode("utf-8")
        except Exception:
            return secret_str[4:]
    return secret_str

def mask_secret(secret_str):
    if not secret_str:
        return ""
    if len(secret_str) <= 6:
        return "••••••••"
    return secret_str[:3] + "••••••••" + secret_str[-3:]

def is_masked(val):
    if not val:
        return False
    return "••••" in str(val) or "*****" in str(val)


# --- DEFAULT SETTINGS ---

def get_default_email_settings():
    return {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "auth_type": "OAuth2",
        "tenant_id": "",
        "client_id": "",
        "client_secret": "",
        "smtp_username": "",
        "smtp_password": "",
        "sender_name": "GreenWorld Monitor",
        "sender_email": "alerts@company.com",
        "encryption": "STARTTLS",
        "timeout": 60,
        "tls_validation": True,
        "debug_logging": False,
        "retry_count": 3,
        "retry_interval": 5,
        "recipients": [],
        "alert_types": {
            "db_down": True,
            "listener_down": True,
            "tablespace_90": True,
            "disk_90": True,
            "cpu_90": True,
            "mem_90": True,
            "rman_failed": True,
            "archive_full": True,
            "blocking_sessions": True,
            "db_startup": True,
            "db_shutdown": True,
            "standby_down": True,
            "standby_not_synced": True,
            "standby_log_gap": True,
            "standby_destination_error": True,
            "reporting_db_down": True,
            "mount_point_critical": True
        },
        "custom_alert_rules": [],
        "frequency": "once",
        "template_subject": "[CRITICAL] Database Alert - {DATABASE_NAME}",
        "template_message": "Hello Team,\n\nDatabase : {DATABASE_NAME}\nHost : {HOST}\nStatus : {STATUS}\nTime : {DATE_TIME}\n\nPlease investigate immediately.\n\nRegards,\nGreenWorld Database Monitoring",
        "connection_status": "Not Connected",
        "last_connected_at": "",
        "last_error": ""
    }


# --- CONFIGURATION LOAD & SAVE ---

def load_global_email_settings(mask_secrets=False):
    defaults = get_default_email_settings()

    if not os.path.exists(SETTINGS_PATH):
        if mask_secrets:
            res = defaults.copy()
            res["client_secret"] = mask_secret(res["client_secret"])
            res["smtp_password"] = mask_secret(res["smtp_password"])
            return res
        return defaults

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            all_settings = json.load(f)

        if not isinstance(all_settings, dict):
            return defaults

        cfg = None
        if "global" in all_settings and isinstance(all_settings["global"], dict):
            cfg = all_settings["global"]
        else:
            for k, v in all_settings.items():
                if isinstance(v, dict) and (v.get("tenant_id") or v.get("client_id") or v.get("smtp_host")):
                    cfg = v
                    break
            if not cfg:
                cfg = defaults

        merged = defaults.copy()
        for k, v in cfg.items():
            if k == "alert_types" and isinstance(v, dict):
                merged_alerts = defaults["alert_types"].copy()
                merged_alerts.update(v)
                merged["alert_types"] = merged_alerts
            else:
                merged[k] = v

        # Deobfuscate secrets stored on disk
        merged["client_secret"] = deobfuscate_secret(merged.get("client_secret", ""))
        merged["smtp_password"] = deobfuscate_secret(merged.get("smtp_password", ""))

        if mask_secrets:
            sanitized = merged.copy()
            sanitized["client_secret"] = mask_secret(sanitized["client_secret"])
            sanitized["smtp_password"] = mask_secret(sanitized["smtp_password"])
            return sanitized

        return merged
    except Exception as e:
        print(f"Error loading email settings: {e}")
        return defaults

def load_db_email_settings(db_id=None):
    return load_global_email_settings()

def save_global_email_settings(new_settings):
    existing = load_global_email_settings(mask_secrets=False)
    merged = existing.copy()

    for k, v in new_settings.items():
        if k in ["client_secret", "smtp_password"]:
            # If the user passed a masked value back from UI, retain existing real secret
            if is_masked(v) or v == "":
                continue
            merged[k] = v
        else:
            merged[k] = v

    # Obfuscate secrets before writing to disk
    disk_copy = merged.copy()
    disk_copy["client_secret"] = obfuscate_secret(disk_copy.get("client_secret", ""))
    disk_copy["smtp_password"] = obfuscate_secret(disk_copy.get("smtp_password", ""))

    all_settings = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                all_settings = json.load(f)
        except Exception:
            all_settings = {}
    if not isinstance(all_settings, dict):
        all_settings = {}

    all_settings["global"] = disk_copy

    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(all_settings, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving global email settings: {e}")
        return False

def save_db_email_settings(db_id, db_settings):
    return save_global_email_settings(db_settings)


# --- OAUTH2 & MSAL FUNCTIONS ---

def connect_oauth(tenant_id, client_id, client_secret):
    """
    Authenticate with Microsoft Identity Platform using MSAL ConfidentialClientApplication
    and acquire an OAuth2 access token for Office 365 Exchange Online.
    """
    if not tenant_id or not client_id or not client_secret:
        return False, "Missing required OAuth2 credentials: Tenant ID, Client ID, or Client Secret."

    authority = f"https://login.microsoftonline.com/{tenant_id.strip()}"
    scopes = ["https://outlook.office365.com/.default"]

    try:
        app = msal.ConfidentialClientApplication(
            client_id=client_id.strip(),
            client_credential=client_secret.strip(),
            authority=authority
        )

        result = app.acquire_token_for_client(scopes=scopes)

        if "access_token" in result:
            access_token = result["access_token"]
            expires_in = result.get("expires_in", 3600)
            
            # Cache token in memory
            _OAUTH_TOKEN_CACHE["access_token"] = access_token
            _OAUTH_TOKEN_CACHE["expires_at"] = time.time() + expires_in - 300
            
            return True, access_token
        else:
            error_desc = result.get("error_description") or result.get("error") or "Authentication failed with Microsoft Entra ID"
            return False, error_desc
    except Exception as e:
        return False, f"MSAL OAuth2 Connection Error: {str(e)}"

def refresh_token(settings=None):
    """
    Returns valid cached token or acquires a new token via MSAL.
    """
    if _OAUTH_TOKEN_CACHE["access_token"] and time.time() < _OAUTH_TOKEN_CACHE["expires_at"]:
        return True, _OAUTH_TOKEN_CACHE["access_token"]

    if not settings:
        settings = load_global_email_settings(mask_secrets=False)

    tenant_id = settings.get("tenant_id", "")
    client_id = settings.get("client_id", "")
    client_secret = settings.get("client_secret", "")

    return connect_oauth(tenant_id, client_id, client_secret)

def build_xoauth2_string(user, access_token):
    """
    Constructs SASL XOAUTH2 string according to RFC 7628 for Office 365 SMTP.
    """
    xoauth_raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(xoauth_raw.encode("utf-8")).decode("ascii")


# --- SMTP XOAUTH2 & BASIC TEST / CONNECTION ---

def test_connection(settings=None):
    """
    Tests SMTP connection and authentication (XOAUTH2 or Basic).
    Returns (success_bool, message_str).
    """
    if not settings:
        settings = load_global_email_settings(mask_secrets=False)

    host = settings.get("smtp_host", "smtp.office365.com")
    port = int(settings.get("smtp_port", 587))
    auth_type = settings.get("auth_type", "OAuth2")
    sender_email = settings.get("sender_email", "")
    timeout = int(settings.get("timeout", 60))
    tls_val = settings.get("tls_validation", True)
    debug_log = settings.get("debug_logging", False)

    try:
        # 1. Acquire Token if OAuth2
        access_token = None
        if auth_type == "OAuth2":
            success, token_or_err = refresh_token(settings)
            if not success:
                return False, f"Microsoft OAuth2 Authentication Failed: {token_or_err}"
            access_token = token_or_err

        # 2. Connect to SMTP server
        server = smtplib.SMTP(host, port, timeout=timeout)
        if debug_log:
            server.set_debuglevel(1)

        server.ehlo()

        # 3. STARTTLS
        if not tls_val:
            ssl_context = ssl._create_unverified_context()
        else:
            ssl_context = ssl.create_default_context()

        server.starttls(context=ssl_context)
        server.ehlo()

        # 4. Authenticate
        if auth_type == "OAuth2":
            if not sender_email:
                server.quit()
                return False, "Sender Email is required for XOAUTH2 authentication."
            auth_str = build_xoauth2_string(sender_email, access_token)
            code, resp = server.docmd("AUTH", "XOAUTH2 " + auth_str)
            if code != 235:
                server.quit()
                err_msg = resp.decode("utf-8", errors="ignore") if isinstance(resp, bytes) else str(resp)
                return False, f"SMTP XOAUTH2 Authentication Error ({code}): {err_msg}"
        else:
            username = settings.get("smtp_username", "")
            password = settings.get("smtp_password", "")
            if username and password:
                server.login(username, password)

        server.quit()
        return True, "Successfully authenticated with Microsoft Office 365 SMTP server via OAuth2 (XOAUTH2)."

    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP Authentication Error: {e.smtp_error.decode('utf-8', errors='ignore') if isinstance(e.smtp_error, bytes) else str(e)}"
    except smtplib.SMTPException as e:
        return False, f"SMTP Protocol Error: {str(e)}"
    except Exception as e:
        return False, f"Connection Failure: {str(e)}"


# --- MAIL SENDING & HISTORY ---

def send_email(settings, subject, message_text, recipient_emails, html_body=None, db_id=None):
    if not settings:
        settings = load_global_email_settings(mask_secrets=False)

    host = settings.get("smtp_host", "smtp.office365.com")
    port = int(settings.get("smtp_port", 587))
    auth_type = settings.get("auth_type", "OAuth2")
    sender_name = settings.get("sender_name", "GreenWorld Monitor")
    sender_email = settings.get("sender_email", "")
    timeout = int(settings.get("timeout", 60))
    tls_val = settings.get("tls_validation", True)
    debug_log = settings.get("debug_logging", False)
    retry_count = int(settings.get("retry_count", 3))
    retry_interval = int(settings.get("retry_interval", 5))

    if not sender_email:
        return False, "Sender email address is missing."
    if not recipient_emails:
        return False, "Recipient email list is empty."

    # Parse list of strings if string provided
    if isinstance(recipient_emails, str):
        recipient_emails = [r.strip() for r in recipient_emails.split(",") if r.strip()]

    # Extract email addresses if dicts passed or comma-separated strings
    clean_recipients = []
    for r in recipient_emails:
        if isinstance(r, dict) and "email" in r:
            raw_em = str(r.get("email", ""))
            for em in raw_em.split(","):
                em_clean = em.strip()
                if em_clean and "@" in em_clean:
                    clean_recipients.append(em_clean)
        elif isinstance(r, str):
            for em in r.split(","):
                em_clean = em.strip()
                if em_clean and "@" in em_clean:
                    clean_recipients.append(em_clean)
    
    # Deduplicate while preserving order
    seen = set()
    dedup_recipients = []
    for em in clean_recipients:
        if em.lower() not in seen:
            seen.add(em.lower())
            dedup_recipients.append(em)
    clean_recipients = dedup_recipients

    if not clean_recipients:
        return False, "No valid recipient email addresses specified."

    msg = MIMEMultipart('alternative')
    msg['From'] = f"{sender_name} <{sender_email}>"
    msg['To'] = ", ".join(clean_recipients)
    msg['Subject'] = subject

    msg.attach(MIMEText(message_text, 'plain', 'utf-8'))
    if html_body:
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    last_error = ""

    for attempt in range(1, retry_count + 1):
        try:
            access_token = None
            if auth_type == "OAuth2":
                success, token_or_err = refresh_token(settings)
                if not success:
                    raise Exception(f"OAuth2 Token Refresh Failed: {token_or_err}")
                access_token = token_or_err

            server = smtplib.SMTP(host, port, timeout=timeout)
            if debug_log:
                server.set_debuglevel(1)

            server.ehlo()

            if not tls_val:
                ssl_context = ssl._create_unverified_context()
            else:
                ssl_context = ssl.create_default_context()

            server.starttls(context=ssl_context)
            server.ehlo()

            if auth_type == "OAuth2":
                auth_str = build_xoauth2_string(sender_email, access_token)
                code, resp = server.docmd("AUTH", "XOAUTH2 " + auth_str)
                if code != 235:
                    err_msg = resp.decode("utf-8", errors="ignore") if isinstance(resp, bytes) else str(resp)
                    raise Exception(f"XOAUTH2 Auth Failed ({code}): {err_msg}")
            else:
                username = settings.get("smtp_username", "")
                password = settings.get("smtp_password", "")
                if username and password:
                    server.login(username, password)

            server.sendmail(sender_email, clean_recipients, msg.as_string())
            server.quit()

            log_sent_email(db_id, subject, clean_recipients, "Success")
            return True, "Email alert dispatched successfully."

        except Exception as e:
            last_error = str(e)
            print(f"[Email Retry {attempt}/{retry_count}] Failed to send email: {last_error}")
            if attempt < retry_count:
                time.sleep(retry_interval)

    log_sent_email(db_id, subject, clean_recipients, "Failed", last_error)
    return False, f"Failed after {retry_count} attempts. Last error: {last_error}"


def send_alert_email(settings, subject, message, recipient_emails, db_id=None):
    return send_email(settings, subject, message, recipient_emails, db_id=db_id)


# --- MAIL HISTORY HELPERS ---

def load_all_email_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                flat = []
                for k, list_val in data.items():
                    if isinstance(list_val, list):
                        for item in list_val:
                            if isinstance(item, dict):
                                item_copy = item.copy()
                                if "db_id" not in item_copy:
                                    item_copy["db_id"] = k
                                flat.append(item_copy)
                flat.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                return flat
            return []
    except Exception as e:
        print(f"Error loading email history: {e}")
        return []

def save_all_email_history(history_list):
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history_list, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving email history: {e}")
        return False

def log_sent_email(db_id, subject, recipients, status, error_msg=None):
    import datetime
    history_list = load_all_email_history()
    
    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_id": db_id or "System",
        "subject": subject,
        "recipients": recipients,
        "status": status,
        "error": error_msg
    }
    
    history_list.insert(0, entry)
    history_list = history_list[:100]
    save_all_email_history(history_list)

def get_email_history(db_id=None):
    history_list = load_all_email_history()
    if db_id:
        filtered = [h for h in history_list if str(h.get("db_id")).lower() == str(db_id).lower()]
        return filtered if filtered else history_list
    return history_list

def clear_email_history(db_id=None):
    return save_all_email_history([])
