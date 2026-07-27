"""
dashboard/mail_config.py
──────────────────────────────────────────────────────────────────────────────
Full Email Configuration page — rendered inside Portal Home when user clicks
"⚙️ Email Configuration" in the sidebar.

Includes:
  • SMTP settings form (server, port, username, password, from, to)
  • Test Email button
  • Save Configuration button
  • Complete notification preferences (all alert types as checkboxes)
  • Persistent storage of all preferences
"""

import os
import sys
import json
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_DIR   = os.path.join(_BASE_DIR, "config")
PREFS_FILE   = os.path.join(CONFIG_DIR, "notification_prefs.json")

# ── All supported alert types (label → internal key) ─────────────────────────
ALERT_TYPES = [
    # Critical DB
    ("Database Down",                     "db_down"),
    ("Listener Down",                     "listener_down"),
    ("Database Not Reachable",            "db_not_reachable"),
    ("Database Instance Restarted",       "db_restarted"),
    ("Host Server Down",                  "host_down"),
    # Backup
    ("Backup Failure",                    "backup_failure"),
    # Tablespace
    ("Tablespace Usage Critical (≥90%)",  "tablespace_critical"),
    ("Tablespace Usage Warning (≥80%)",   "tablespace_warning"),
    ("Undo Tablespace Critical",          "undo_tablespace_critical"),
    ("Temporary Tablespace Critical",     "temp_tablespace_critical"),
    ("Datafile Autoextend Failure",       "datafile_autoextend"),
    # Archive / FRA
    ("Archive Log Destination Full",      "archive_log_full"),
    ("Archive Log Generation Failure",    "archive_log_failure"),
    ("FRA Usage Critical (≥90%)",         "fra_critical"),
    # Sessions / Locks
    ("High Session Count (>85% max)",     "session_maxed"),
    ("Blocking Sessions Detected",        "blocking_sessions"),
    ("Long Running Sessions",             "long_running_sessions"),
    ("Deadlock Detected",                 "deadlock"),
    # Resources
    ("High CPU Usage",                    "high_cpu"),
    ("High Memory Usage",                 "high_memory"),
    ("CPU Consuming Process Threshold",   "cpu_process_threshold"),
    ("Memory Consuming Process Threshold","mem_process_threshold"),
    # Storage / Mount
    ("Mount Point Usage Critical (≥90%)", "mount_critical"),
    ("Mount Point Usage Warning (≥80%)",  "mount_warning"),
    ("Filesystem Not Accessible",         "filesystem_not_accessible"),
    ("Disk Usage Critical",               "disk_critical"),
    ("ASM Diskgroup Usage Critical",      "asm_critical"),
    # Network / Listener
    ("Listener Not Reachable",            "listener_not_reachable"),
    ("SSH Connection Failure",            "ssh_failure"),
    # Errors
    ("Alert Log Critical Errors",         "alert_log_errors"),
    ("Invalid Database Objects",          "invalid_objects"),
    ("ORA Error Detected",                "ora_error"),
]

# Grouped layout for the checkboxes
ALERT_GROUPS = {
    "🗄️ Database Availability": [
        "db_down", "listener_down", "db_not_reachable",
        "db_restarted", "host_down",
    ],
    "💾 Backup": [
        "backup_failure",
    ],
    "📦 Tablespace & Datafiles": [
        "tablespace_critical", "tablespace_warning",
        "undo_tablespace_critical", "temp_tablespace_critical",
        "datafile_autoextend",
    ],
    "📁 Archive Log & FRA": [
        "archive_log_full", "archive_log_failure", "fra_critical",
    ],
    "👥 Sessions & Locks": [
        "session_maxed", "blocking_sessions",
        "long_running_sessions", "deadlock",
    ],
    "⚡ CPU & Memory": [
        "high_cpu", "high_memory",
        "cpu_process_threshold", "mem_process_threshold",
    ],
    "💽 Storage & Mount Points": [
        "mount_critical", "mount_warning",
        "filesystem_not_accessible", "disk_critical", "asm_critical",
    ],
    "🌐 Network & Connectivity": [
        "listener_not_reachable", "ssh_failure",
    ],
    "🚨 Errors & Objects": [
        "alert_log_errors", "invalid_objects", "ora_error",
    ],
}

# Build label lookup
_KEY_TO_LABEL = {key: label for label, key in ALERT_TYPES}


def _load_prefs() -> dict:
    """Load notification preferences from the custom registry JSON. Default: all disabled (blank)."""
    from utils.alerts import load_config
    cfg = load_config()
    return cfg.get("preferences", {})

def _save_prefs(prefs: dict):
    from utils.alerts import load_config, get_custom_config_path
    import json, os
    cfg = load_config()
    cfg["preferences"] = prefs
    config_file = get_custom_config_path()
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    with open(config_file, "w") as f:
        json.dump(cfg, f, indent=4)


def render_mail_config_page():
    """Render the full Email Configuration page."""
    from utils.alerts import load_config, get_custom_config_path, send_alert_email
    import datetime

    st.markdown("""
    <style>
    .mc-header {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%);
        border-radius: 14px;
        padding: 24px 32px;
        margin-bottom: 28px;
        border: 1px solid #1e40af;
    }
    .mc-header h1 {
        color: #38bdf8;
        font-family: 'Space Grotesk', sans-serif;
        font-size: 1.6rem;
        margin: 0 0 4px 0;
    }
    .mc-header p {
        color: #94a3b8;
        font-size: 0.85rem;
        margin: 0;
    }
    .mc-section {
        background: var(--card-bg, #ffffff);
        border: 1px solid var(--border-color, #e5e7eb);
        border-radius: 12px;
        padding: 22px 26px;
        margin-bottom: 22px;
    }
    .mc-section-title {
        font-size: 1rem;
        font-weight: 800;
        color: var(--text-primary, #1e293b);
        margin-bottom: 16px;
        display: flex;
        align-items: center;
        gap: 8px;
        border-bottom: 1px solid var(--border-color, #e5e7eb);
        padding-bottom: 10px;
    }
    .mc-group-label {
        font-size: 0.82rem;
        font-weight: 700;
        color: #3b82f6;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin: 18px 0 8px 0;
    }
    .mc-tip {
        background: #eff6ff;
        border: 1px solid #bfdbfe;
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 0.8rem;
        color: #1e40af;
        margin-bottom: 16px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="mc-header">
        <h1>⚙️ Email Configuration</h1>
        <p>Configure SMTP settings, test email delivery, and choose which alert events trigger notifications.</p>
    </div>
    """, unsafe_allow_html=True)

    cfg = load_config()

    # ── SECTION 1: SMTP Settings ──────────────────────────────────────────────
    st.markdown("<div class='mc-section'>", unsafe_allow_html=True)
    st.markdown("<div class='mc-section-title'>📧 SMTP Server Settings</div>", unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        smtp_server = st.text_input(
            "SMTP Server",
            value=cfg.get("smtp_server", ""),
            placeholder="e.g. smtp.office365.com",
            help="Your mail server hostname or IP address",
            key="mc_smtp_server"
        )
        smtp_username = st.text_input(
            "SMTP Username",
            value=cfg.get("smtp_username", ""),
            placeholder="e.g. alerts@company.com",
            help="Username for SMTP authentication (leave blank if relay doesn't require auth)",
            key="mc_smtp_user"
        )
        from_email = st.text_input(
            "Sender Email Address",
            value=cfg.get("from_email", ""),
            placeholder="e.g. oracle-alerts@company.com",
            help="The 'From' address that appears in all alert emails",
            key="mc_from_email"
        )

    with col2:
        port_val = cfg.get("smtp_port")
        try:
            port_val = int(port_val) if port_val else None
        except Exception:
            port_val = None

        smtp_port = st.number_input(
            "SMTP Port",
            value=port_val,
            placeholder="e.g. 587",
            step=1,
            help="Common ports: 25 (relay), 587 (TLS/STARTTLS), 465 (SSL)",
            key="mc_smtp_port"
        )
        smtp_password = st.text_input(
            "SMTP Password",
            value=cfg.get("smtp_password", ""),
            type="password",
            placeholder="Enter SMTP password",
            help="Password for SMTP authentication (leave blank if not required)",
            key="mc_smtp_pass"
        )
        to_list  = cfg.get("to_emails", [])
        to_str   = ", ".join(to_list) if to_list else ""
        to_emails_input = st.text_input(
            "Recipient Email Address(es)",
            value=to_str,
            placeholder="admin1@company.com, admin2@company.com",
            help="Comma-separated list of email addresses that will receive alert notifications",
            key="mc_to_emails"
        )

    enabled = st.toggle(
        "✅ Enable Email Alerts",
        value=cfg.get("enabled", False),
        help="Master switch — turn off to suppress all alert emails (alerts are still logged)",
        key="mc_enabled"
    )

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Action Buttons Row ────────────────────────────────────────────────────
    btn1, btn2, btn3 = st.columns([1, 1, 2])

    def _build_new_cfg():
        from utils.alerts import load_config
        old_cfg = load_config()
        return {
            "smtp_server":   smtp_server.strip(),
            "smtp_port":     int(smtp_port) if smtp_port else None,
            "smtp_username": smtp_username.strip(),
            "smtp_password": smtp_password.strip(),
            "from_email":    from_email.strip(),
            "to_emails":     [e.strip() for e in to_emails_input.split(",") if e.strip()],
            "enabled":       enabled,
            "preferences":   old_cfg.get("preferences", {})
        }

    with btn1:
        if st.button("💾 Save Configuration", use_container_width=True, type="primary", key="mc_save_btn"):
            new_cfg = _build_new_cfg()
            config_file = get_custom_config_path()
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, "w") as f:
                json.dump(new_cfg, f, indent=4)
            st.success("✅ Email configuration saved successfully!")
            st.toast("Configuration saved!", icon="💾")

    with btn2:
        if st.button("📨 Send Test Email", use_container_width=True, key="mc_test_btn"):
            new_cfg = _build_new_cfg()
            new_cfg["enabled"] = True   # Force enabled for test
            config_file = get_custom_config_path()
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, "w") as f:
                json.dump(new_cfg, f, indent=4)

            test_subject = "🧪 Test Alert — DB Health Dashboard"
            test_body = (
                f"This is a TEST email from the DB Health Dashboard.\n\n"
                f"If you received this email, your SMTP configuration is working correctly.\n\n"
                f"SMTP Server : {smtp_server}:{smtp_port}\n"
                f"From        : {from_email}\n"
                f"To          : {to_emails_input}\n"
                f"Sent At     : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"— DB Health Dashboard Alert Engine"
            )
            with st.spinner("Sending test email..."):
                result = send_alert_email(test_subject, test_body)

            if result.get("success"):
                st.success(f"✅ Test email sent successfully to: {to_emails_input}")
                st.toast("Test email sent!", icon="📧")
            else:
                err = result.get("error", "Unknown error")
                st.error(f"❌ Failed to send test email:\n\n**{err}**")
                st.toast(f"Email failed: {err[:60]}", icon="🚨")

            # Restore enabled flag
            new_cfg["enabled"] = enabled
            with open(config_file, "w") as f:
                json.dump(new_cfg, f, indent=4)

    # ── SECTION 2: Notification Preferences ──────────────────────────────────
    st.markdown("<div class='mc-section'>", unsafe_allow_html=True)
    st.markdown("<div class='mc-section-title'>🔔 Notification Preferences</div>", unsafe_allow_html=True)
    st.markdown("""
    <div class='mc-tip'>
        ✅ <b>Select the alert types</b> for which you want to receive email notifications.
        Deselected alerts will be suppressed — no email will be sent, but events are still logged.
    </div>
    """, unsafe_allow_html=True)

    prefs = _load_prefs()

    # Select All / Deselect All
    sel_col1, sel_col2, sel_col3 = st.columns([1, 1, 4])
    with sel_col1:
        if st.button("☑️ Select All", key="mc_sel_all", use_container_width=True):
            for _, key in ALERT_TYPES:
                st.session_state[f"mc_pref_{key}"] = True
            st.rerun()
    with sel_col2:
        if st.button("☐ Deselect All", key="mc_desel_all", use_container_width=True):
            for _, key in ALERT_TYPES:
                st.session_state[f"mc_pref_{key}"] = False
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # Render grouped checkboxes
    new_prefs = {}
    for group_name, keys_in_group in ALERT_GROUPS.items():
        st.markdown(f"<div class='mc-group-label'>{group_name}</div>", unsafe_allow_html=True)
        # 3 columns for compact layout
        items = [(k, _KEY_TO_LABEL[k]) for k in keys_in_group if k in _KEY_TO_LABEL]
        cols = st.columns(3)
        for i, (key, label) in enumerate(items):
            widget_key = f"mc_pref_{key}"
            default_val = prefs.get(key, False)
            # respect session state override from Select All / Deselect All
            current_val = st.session_state.get(widget_key, default_val)
            checked = cols[i % 3].checkbox(label, value=current_val, key=widget_key)
            new_prefs[key] = checked

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Save Preferences Button ───────────────────────────────────────────────
    if st.button("💾 Save Notification Preferences", use_container_width=True,
                 type="primary", key="mc_save_prefs_btn"):
        _save_prefs(new_prefs)
        st.success("✅ Notification preferences saved! Email alerts will respect these settings immediately.")
        st.toast("Preferences saved!", icon="🔔")

    # ── Preferences Summary ───────────────────────────────────────────────────
    enabled_count  = sum(1 for v in new_prefs.values() if v)
    disabled_count = len(new_prefs) - enabled_count
    st.caption(
        f"**{enabled_count}** alert types enabled · **{disabled_count}** suppressed "
        f"out of **{len(ALERT_TYPES)}** total"
    )
