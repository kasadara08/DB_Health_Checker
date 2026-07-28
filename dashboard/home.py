import os
import time
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from db_connection import (
    get_txt_path, save_txt_path, load_db_names, read_db_file_to_df,
    get_registry_history, save_pending_path, confirm_pending_path,
    discard_pending_path, get_pending_path, get_previous_registry,
    clear_active_registry
)

@st.dialog("📂 Open Instance Control Console")
def open_db_dialog(db_name):
    st.markdown(f"Would you like to open the management dashboard for **{db_name}**?")
    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Open", type="primary", use_container_width=True):
            st.session_state.selected_db = db_name
            st.session_state.selected_chk_db = None
            st.session_state.dialog_open = False
            st.session_state.page = "monitoring"
            st.rerun()
    with col2:
        if st.button("Cancel", use_container_width=True):
            st.session_state.selected_chk_db = None
            st.session_state.dialog_open = False
            st.rerun()

def diagnose_registry_connections():
    """
    Tests connectivity and file validity for the configured registry file.
    Returns a dict with:
      - status: 'ok' | 'no_file' | 'empty' | 'bad_columns' | 'partial' | 'all_failed'
      - message: human-readable summary
      - details: list of per-DB diagnosis dicts
    """
    import socket
    result = {"status": "ok", "message": "", "details": []}

    path = get_txt_path()
    if not path or not os.path.exists(path):
        result["status"] = "no_file"
        result["message"] = "No registry file configured. Please upload or set the path."
        return result

    try:
        df = read_db_file_to_df(path)
    except Exception as e:
        result["status"] = "bad_columns"
        result["message"] = f"Registry file could not be read: {e}"
        return result

    required_cols = {"db_name", "username", "password", "host", "port", "service_name"}
    missing = required_cols - set(df.columns)
    if missing:
        result["status"] = "bad_columns"
        result["message"] = f"Registry file is missing required columns: {', '.join(sorted(missing))}."
        return result

    if df.empty or len(df) == 0:
        result["status"] = "empty"
        result["message"] = "Registry file was read successfully but contains no database entries."
        return result

    ok_count = 0
    fail_count = 0
    for _, row in df.iterrows():
        db_name = str(row.get("db_name", "")).strip()
        host = str(row.get("host", "")).strip()
        port_raw = str(row.get("port", "1521")).strip()
        if port_raw.endswith(".0"):
            port_raw = port_raw[:-2]
        try:
            port = int(port_raw)
        except Exception:
            port = 1521

        entry = {"db": db_name, "host": host, "port": port, "status": "", "detail": ""}
        try:
            # Quick TCP socket test (1.5 second timeout)
            sock = socket.create_connection((host, port), timeout=1.5)
            sock.close()
            entry["status"] = "reachable"
            entry["detail"] = f"Host {host}:{port} is reachable."
            ok_count += 1
        except socket.timeout:
            entry["status"] = "timeout"
            entry["detail"] = f"Connection to {host}:{port} timed out. Host may be unreachable or firewall is blocking port {port}."
            fail_count += 1
        except ConnectionRefusedError:
            entry["status"] = "refused"
            entry["detail"] = f"Connection refused at {host}:{port}. Oracle Listener may not be running."
            fail_count += 1
        except OSError as e:
            entry["status"] = "unreachable"
            entry["detail"] = f"Cannot reach {host}:{port} — {e}. Check network/VPN connection."
            fail_count += 1
        result["details"].append(entry)

    if ok_count == 0:
        result["status"] = "all_failed"
        result["message"] = f"Connected to registry file ✅  |  Found {len(df)} database(s) in file  |  ❌ None reachable on the network."
    elif fail_count > 0:
        result["status"] = "partial"
        result["message"] = f"Connected to registry file ✅  |  {ok_count} of {len(df)} database(s) reachable  |  ⚠️ {fail_count} unreachable."
    else:
        result["status"] = "ok"
        result["message"] = f"Connected to registry file ✅  |  All {ok_count} database(s) reachable on the network ✅"

    return result



def load_db_status_summary(db_name):
    """Fetches real-time status from Oracle DB and host for every call."""
    status_data = {
        "db": "UNKNOWN",
        "listener": "UNKNOWN",
        "backup": "UNKNOWN",
        "drives": []
    }

    # Connection object to be used throughout the checks
    conn = None
    try:
        import socket

        # --- Single check: try Oracle connection ---
        # If connection succeeds → DB is UP and Listener is UP
        from db_connection import get_api_connection, get_config_for_db
        conn, err = get_api_connection(db_name)

        if not conn:
            # Login failed → check TCP to find out if listener port is reachable
            status_data["db"] = "DOWN"
            status_data["error"] = err

            try:
                cfg_check = get_config_for_db(db_name)
                if cfg_check:
                    sock = socket.create_connection(
                        (cfg_check["host"], int(cfg_check["port"])), timeout=2
                    )
                    sock.close()
                    status_data["listener"] = "UP"   # Port open = listener running but login failed
                else:
                    status_data["listener"] = "DOWN"
            except Exception:
                status_data["listener"] = "DOWN"    # Cannot reach port at all

            return status_data

        status_data["db"] = "UP"
        status_data["listener"] = "UP"
        from queries.queries import get_db_status, get_listener_status, get_rman_durations
        from dashboard.monitoring import get_drive_details

        # Query actual DB status from remote server using active connection
        db_info = get_db_status(conn=conn)
        db_status_val = db_info.get("STATUS", "UNKNOWN") if db_info else "UNKNOWN"
        status_data["db"] = "UP" if db_status_val == "OPEN" else "DOWN"

        if status_data["db"] == "UP":
            # Listener check via remote V$LISTENER_NETWORK query
            lsnr_info = get_listener_status(conn=conn)
            status_data["listener"] = "UP" if lsnr_info == "UP" else "DOWN"

            # Backup check via remote V$RMAN_BACKUP_JOB_DETAILS
            df_rman = get_rman_durations(conn=conn)
            if df_rman.empty:
                status_data["backup"] = "PENDING"
            else:
                last_row = df_rman.iloc[-1]
                last_status = str(last_row.get("STATUS", "FAIL")).upper()

                def safe_parse_date(val):
                    try:
                        s = str(val).strip()
                        cur_year = str(today.year)
                        import re as _re
                        if _re.match(r'^\d{1,2}-[A-Za-z]{3}$', s):
                            s = s + "-" + cur_year
                        for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%b", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                return pd.to_datetime(s, format=fmt).date()
                            except Exception:
                                continue
                        return pd.to_datetime(s, errors="coerce").date()
                    except Exception:
                        return None

                import datetime as _dt
                today = _dt.date.today()
                start_date = safe_parse_date(last_row.get("START_TIME_STR"))
                
                if start_date is not None:
                    days_ago = (today - start_date).days
                else:
                    days_ago = 999

                if days_ago <= 1:
                    if "COMPLETED" in last_status or "SUCCESS" in last_status:
                        status_data["backup"] = "SUCCESS"
                    elif "FAIL" in last_status or "ERR" in last_status:
                        status_data["backup"] = "FAILED"
                    else:
                        status_data["backup"] = "PENDING"
                else:
                    status_data["backup"] = "PENDING"

            # Drive info from remote server (pass the active connection)
            drives = get_drive_details(conn)
            status_data["drives"] = drives if drives else []
            if drives and isinstance(drives[0], dict) and "ssh_error" in drives[0]:
                status_data["mount_error"] = drives[0]["ssh_error"]
            
            # Tablespace check - permanent tablespaces only (excl TEMP/UNDO)
            from queries.queries import get_tablespace_utilization, get_arc_log_info, get_blocking_sessions
            try:
                df_ts = get_tablespace_utilization(conn=conn)
                balance_ts = []  # permanent tablespaces sorted by least free space
                if not df_ts.empty:
                    for _, ts in df_ts.iterrows():
                        ts_name = str(ts["TABLESPACE_NAME"]).upper()
                        # Exclude TEMP and UNDO tablespaces
                        if "TEMP" in ts_name or "UNDO" in ts_name:
                            continue
                        ts_pct = float(ts.get("PCT_USED_MAX", 0))
                        total_mb = float(ts.get("MAXSIZE_MB", 0))
                        used_mb = float(ts.get("USED_MB", 0))
                        free_mb = float(ts.get("FREE_ON_MAX_MB", 0))
                        allocated_mb = float(ts.get("ALLOCATED_MB", 0))
                        balance_ts.append({
                            "name": ts["TABLESPACE_NAME"],
                            "pct": ts_pct,
                            "used_mb": used_mb,
                            "free_mb": free_mb,
                            "total_mb": total_mb,
                            "allocated_mb": allocated_mb,
                        })
                    # Sort by free_mb ascending (least free = most critical first)
                    balance_ts.sort(key=lambda x: x["free_mb"])
                status_data["balance_ts"] = balance_ts
                # full_ts holds names of tablespaces >= 90% for alerting
                full_ts = [t["name"] for t in balance_ts if t["pct"] >= 90]
                status_data["full_ts"] = full_ts
            except Exception:
                status_data["balance_ts"] = []
                status_data["full_ts"] = []
                
            # System Resources check for Host OS
            from queries.queries import get_system_resources
            try:
                status_data["system_res"] = get_system_resources(conn=conn)
            except Exception:
                status_data["system_res"] = None
                
            # Arc log (FRA) check
            try:
                arc_info = get_arc_log_info(conn=conn)
                status_data["arc_pct"] = arc_info.get("used_pct", 0)
                status_data["arc_configured"] = arc_info.get("configured", False)
            except Exception:
                status_data["arc_pct"] = 0
                status_data["arc_configured"] = False
                
            # Blocking / deadlock check
            try:
                df_blk = get_blocking_sessions(conn=conn)
                status_data["has_blocking"] = not df_blk.empty
            except Exception:
                status_data["has_blocking"] = False
                
            # Concurrent session max limit check
            from queries.queries import get_session_stats, get_max_sessions
            try:
                sess_stats = get_session_stats(conn=conn)
                max_sess = get_max_sessions(conn=conn)
                act_num = int(sess_stats.get("ACTIVE", 0)) if str(sess_stats.get("ACTIVE", 0)).isdigit() else 0
                sess_threshold_high = max(50, int(max_sess * 0.85)) if max_sess > 0 else 50
                if act_num > sess_threshold_high:
                    status_data["session_maxed"] = True
                else:
                    status_data["session_maxed"] = False
            except Exception:
                status_data["session_maxed"] = False
                
        else:
            # DB is connected but status is not OPEN (MOUNTED etc.)
            status_data["listener"] = "UP"  # We connected, so listener is UP
            status_data["backup"] = "UNKNOWN"
            status_data["error"] = f"Database is {db_status_val} (not OPEN)"

        # Resolve host address for alert triggering
        cfg_check = get_config_for_db(db_name)
        host = cfg_check["host"] if cfg_check else db_name

        # Trigger alerting engine
        try:
            from utils.alerts import check_and_trigger_alerts
            check_and_trigger_alerts(server_name=host, db_name=db_name, host_name=host, stats=status_data)
        except Exception as ae:
            print(f"Alert engine error for {db_name}: {ae}")

    except Exception as e:
        print(f"Error loading status for {db_name}: {e}")
        status_data["db"] = "DOWN"
        status_data["listener"] = "DOWN"
        status_data["error"] = f"Unexpected error: {str(e)}"
        
        cfg_check = get_config_for_db(db_name)
        host = cfg_check["host"] if cfg_check else db_name

        # Trigger alert engine for DOWN states
        try:
            from utils.alerts import check_and_trigger_alerts
            check_and_trigger_alerts(server_name=host, db_name=db_name, host_name=host, stats=status_data)
        except Exception as ae:
            print(f"Alert engine error on crash for {db_name}: {ae}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


    return status_data

def load_db_status_summary_worker(db_name):
    """
    Thread-safe worker to load database status.
    Avoids writing to st.session_state directly (since threads shouldn't modify it).
    """
    import json
    import os
    try:
        cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "db_monitor_cache.json"
        )
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                cache_data = json.load(f)
            if db_name in cache_data:
                return cache_data[db_name]
    except Exception:
        pass

    return load_db_status_summary(db_name)

def load_db_status_summary_cached(db_name):
    """
    Cache-first loader:
    1. Check JSON cache file (written by background monitor thread) — instant
    2. Fall back to session_state cache
    3. Last resort: live query
    """
    import json
    # 1. Try JSON file cache (background thread keeps this fresh every 2 min)
    try:
        cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "db_monitor_cache.json"
        )
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                cache_data = json.load(f)
            if db_name in cache_data:
                return cache_data[db_name]
    except Exception:
        pass

    # 2. Session-state cache
    if "status_cache" not in st.session_state:
        st.session_state.status_cache = {}
    if db_name not in st.session_state.status_cache:
        st.session_state.status_cache[db_name] = load_db_status_summary(db_name)
    return st.session_state.status_cache[db_name]

def determine_health_category(stats):
    """Categorizes database health: Healthy, Warning, or Critical based ONLY on actual database metrics."""
    # Critical state triggers (database, listener offline or failed backup)
    if stats.get("db") == "DOWN" or stats.get("listener") == "DOWN" or stats.get("backup") == "FAILED":
        return "Critical"
            
    # Warning state triggers (backup pending/warning, mount point/storage >= 80%, ORA error, tablespaces, archive log, blocking locks, max sessions/CPU/RAM)
    drives = stats.get("drives", [])
    has_drive_warn = False
    if isinstance(drives, list):
        has_drive_warn = any(float(d.get("pct", 0) or 0) >= 80 for d in drives if isinstance(d, dict))

    if (
        stats.get("backup") in ["PENDING", "WARNING"] or
        stats.get("full_ts") or
        (stats.get("arc_configured") and stats.get("arc_pct", 0) >= 80) or
        stats.get("has_blocking") or
        stats.get("deadlock_detected") or
        stats.get("session_maxed") or
        stats.get("has_ora_error") or
        stats.get("ora_error") or
        stats.get("alert_log_error") or
        stats.get("mount_point_warning") or
        stats.get("storage_warning") or
        has_drive_warn
    ):
        return "Warning"
            
    return "Healthy"


def render_home_page():
    """Render the premium redesigned OEM Database selection home page."""
    # Safety guard: if page has been set to monitoring, do not render home page elements
    if st.session_state.get('page') == 'monitoring':
        return

    # Custom CSS injection for premium glassmorphism dark theme, KPI ribbon, and proceed button
    st.markdown("""
        <style>
        /* Refresh Button Custom Styling (Bulletproof selector) */
        div[data-testid="stAppViewBlockContainer"] button[data-testid*="secondary"],
        div[data-testid="stAppViewBlockContainer"] button[class*="secondary"],
        div[data-testid="stAppViewBlockContainer"] button:not([data-testid*="primary"]):not([class*="primary"]) {
            background-color: #e0f2fe !important; /* light blue background */
            color: #0369a1 !important; /* dark blue text */
            border: 1px solid #bae6fd !important;
            font-weight: 700 !important;
            transition: all 0.2s ease !important;
        }
        div[data-testid="stAppViewBlockContainer"] button[data-testid*="secondary"]:hover,
        div[data-testid="stAppViewBlockContainer"] button[class*="secondary"]:hover,
        div[data-testid="stAppViewBlockContainer"] button:not([data-testid*="primary"]):not([class*="primary"]):hover {
            background-color: #bae6fd !important;
            border-color: #7dd3fc !important;
            color: #0284c7 !important;
            transform: scale(1.02) !important;
        }

        /* Top-right proceed button wrapper — inline, NOT fixed so it never bleeds into other pages */
        div.sticky-container {
            position: relative;
            z-index: 1;
            background: transparent;
            text-align: right;
            margin-bottom: 8px;
        }

        /* Premium Executive KPI Ribbon Card */
        .kpi-container {
            display: flex;
            gap: 15px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }
        .kpi-chip {
            flex: 1;
            min-width: 150px;
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 12px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 4px 6px rgba(0,0,0,0.02);
            transition: all 0.3s ease;
        }
        .kpi-chip:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.05);
        }
        .kpi-title {
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
            color: var(--text-secondary);
            letter-spacing: 0.05em;
        }
        .kpi-value {
            font-size: 1.4rem;
            font-weight: 800;
            color: var(--text-primary);
        }
        .kpi-healthy { border-left: 4px solid #10b981; }
        .kpi-warning { border-left: 4px solid #f59e0b; }
        .kpi-critical { border-left: 4px solid #ef4444; }
        .kpi-total   { border-left: 4px solid #3b82f6; }

        /* =====================================================
           PURE CSS DATABASE CARD  (works without :has())  
           ===================================================== */
        /* Make the Streamlit column container the relative anchor */
        div[data-testid="column"] {
            position: relative !important;
        }

        .db-card {
            border-radius: 12px;
            padding: 10px 14px 10px;
            cursor: pointer;
            position: relative;
            /* hardware-accelerated for 100+ cards */
            will-change: transform, box-shadow;
            transition: transform 280ms cubic-bezier(0.25, 0.8, 0.25, 1),
                        box-shadow  280ms ease,
                        border-color 280ms ease;
        }

        .db-card-wrapper {
            cursor: pointer;
        }

        /* All elements inside card should have pointer cursor */
        .db-card, .db-card * {
            cursor: pointer !important;
        }

        /* Health dot sits ABOVE the overlay so its tooltip works */
        .health-dot-wrapper {
            position: relative;
            z-index: 10;
            pointer-events: auto;
            cursor: help !important;
        }
        .health-dot-wrapper * {
            cursor: help !important;
        }

        /* Keyboard Accessibility: Style the card when the overlay button is focused */
        div[data-testid="column"]:has(button:focus-visible) .db-card {
            outline: 2px solid #3b82f6 !important;
            outline-offset: 2px !important;
            box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.25) !important;
        }

        /* Enable hover effects when hovering over the column container (since button sits on top) */
        div[data-testid="column"]:hover .db-card-healthy {
            transform: translateY(-7px) scale(1.04);
            border-color: rgba(16, 185, 129, 0.85);
            box-shadow: 0 14px 28px rgba(16, 185, 129, 0.18),
                        0 0  18px rgba(16, 185, 129, 0.22);
        }
        div[data-testid="column"]:hover .db-card-warning {
            transform: translateY(-7px) scale(1.04);
            border-color: rgba(245, 158, 11, 0.85);
            box-shadow: 0 14px 28px rgba(245, 158, 11, 0.18),
                        0 0  18px rgba(245, 158, 11, 0.22);
        }
        div[data-testid="column"]:hover .db-card-critical {
            transform: translateY(-7px) scale(1.04);
            border-color: rgba(239, 68, 68, 0.85);
            box-shadow: 0 14px 28px rgba(239, 68, 68, 0.18),
                        0 0  18px rgba(239, 68, 68, 0.22);
        }
        div[data-testid="column"]:hover .db-card-selected {
            transform: translateY(-7px) scale(1.04);
            border-color: #3b82f6 !important;
            box-shadow: 0 14px 28px rgba(59, 130, 246, 0.2),
                        0 0  18px rgba(59, 130, 246, 0.28) !important;
        }

        /* HEALTHY - soft green border at rest, bright glow on hover */
        .db-card-healthy {
            background: var(--bg-secondary, #ffffff);
            border: 1.5px solid rgba(16, 185, 129, 0.30);
            box-shadow: 0 2px 8px rgba(16, 185, 129, 0.06);
        }
        .db-card-healthy:hover {
            transform: translateY(-7px) scale(1.04);
            border-color: rgba(16, 185, 129, 0.85);
            box-shadow: 0 14px 28px rgba(16, 185, 129, 0.18),
                        0 0  18px rgba(16, 185, 129, 0.22);
        }

        /* WARNING - amber border */
        .db-card-warning {
            background: var(--bg-secondary, #ffffff);
            border: 1.5px solid rgba(245, 158, 11, 0.30);
            box-shadow: 0 2px 8px rgba(245, 158, 11, 0.06);
        }
        .db-card-warning:hover {
            transform: translateY(-7px) scale(1.04);
            border-color: rgba(245, 158, 11, 0.85);
            box-shadow: 0 14px 28px rgba(245, 158, 11, 0.18),
                        0 0  18px rgba(245, 158, 11, 0.22);
        }

        /* CRITICAL - red border */
        .db-card-critical {
            background: var(--bg-secondary, #ffffff);
            border: 1.5px solid rgba(239, 68, 68, 0.30);
            box-shadow: 0 2px 8px rgba(239, 68, 68, 0.06);
        }
        .db-card-critical:hover {
            transform: translateY(-7px) scale(1.04);
            border-color: rgba(239, 68, 68, 0.85);
            box-shadow: 0 14px 28px rgba(239, 68, 68, 0.18),
                        0 0  18px rgba(239, 68, 68, 0.22);
        }

        /* SELECTED state (persists after hover) */
        .db-card-selected {
            border: 2px solid #3b82f6 !important;
            background: rgba(59, 130, 246, 0.04) !important;
            box-shadow: 0 4px 14px rgba(59, 130, 246, 0.12) !important;
        }
        .db-card-selected:hover {
            transform: translateY(-7px) scale(1.04);
            border-color: #3b82f6 !important;
            box-shadow: 0 14px 28px rgba(59, 130, 246, 0.2),
                        0 0  18px rgba(59, 130, 246, 0.28) !important;
        }

        /* card internals */
        .db-card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 6px;
        }
        .db-card-name {
            font-size: 1rem;
            font-weight: 800;
            font-family: 'Space Grotesk', sans-serif;
            color: var(--text-primary, #1f2937);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .health-dot-healthy { width:10px; height:10px; border-radius:50%; background:#10b981; flex-shrink:0; }
        .health-dot-warning  { width:10px; height:10px; border-radius:50%; background:#f59e0b; flex-shrink:0; }
        .health-dot-critical { width:10px; height:10px; border-radius:50%; background:#ef4444; flex-shrink:0; }
        .db-divider { border:none; border-top:1px solid #e5e7eb; margin: 8px 0; }
        .db-status-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.76rem;
            font-weight: 600;
            margin-bottom: 5px;
            color: #6b7280;
        }
        .db-status-label { display:flex; align-items:center; gap:5px; }
        .dot-green  { width:7px; height:7px; border-radius:50%; background:#10b981; display:inline-block; }
        .dot-red    { width:7px; height:7px; border-radius:50%; background:#ef4444; display:inline-block; }
        .dot-amber  { width:7px; height:7px; border-radius:50%; background:#f59e0b; display:inline-block; }
        .dot-grey   { width:7px; height:7px; border-radius:50%; background:#9ca3af; display:inline-block; }
        .val-green  { color:#10b981; font-weight:700; }
        .val-red    { color:#ef4444; font-weight:700; }
        .val-amber  { color:#f59e0b; font-weight:700; }
        .val-grey   { color:#9ca3af; font-weight:700; }
        
          /* Hide phantom nav buttons */
          div[data-testid="stElementContainer"]:has(button) { }
          .hide-nav-btn { display: none !important; height: 0 !important; overflow: hidden !important; margin: 0 !important; padding: 0 !important; }

        /* ---- Storage Donut Ring Charts ---- */
        .drive-donuts {
            display: flex;
            justify-content: center;
            gap: 18px;
            margin-top: 10px;
            padding-top: 8px;
            border-top: 1px solid #f3f4f6;
        }
        .drive-donut-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
        }
        .drive-donut-ring {
            width: 52px;
            height: 52px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }
        .drive-donut-inner {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--bg-secondary, #ffffff);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.6rem;
            font-weight: 800;
            color: var(--text-primary, #374151) !important;
        }
        .drive-donut-label {
            font-size: 0.62rem;
            font-weight: 700;
            color: var(--text-secondary, #6b7280) !important;
            text-align: center;
            line-height: 1.1;
        }
        .drive-donut-sublabel {
            font-size: 0.55rem;
            font-weight: 600;
            color: var(--text-secondary, #9ca3af) !important;
        }

        /* Strip Streamlit's own borders off the columns inside our cards */
        .stColumn > div { gap: 0 !important; }
        
        .db-error-msg {
            font-size: 0.65rem;
            color: #991b1b !important;
            background: #fef2f2 !important;
            border: 1px solid #fca5a5 !important;
            padding: 4px;
            border-radius: 4px;
            margin-top: 6px;
            line-height: 1.2;
            overflow-wrap: break-word;
        }
        </style>
    """, unsafe_allow_html=True)

    # Inject CSS for rich dot tooltip (works with pure CSS :hover, no JS needed)
    st.markdown("""
        <style>
        /* Rich status dot tooltip */
        .health-dot-wrapper {
            position: relative;
            display: inline-block;
            cursor: help;
        }
        .dot-tooltip {
            display: none;
            position: absolute;
            right: 14px;
            top: -8px;
            z-index: 9999;
            background: #1e293b;
            color: #f1f5f9;
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 0.72rem;
            min-width: 200px;
            max-width: 280px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.35);
            line-height: 1.5;
            pointer-events: none;
            white-space: normal;
        }
        .health-dot-wrapper:hover .dot-tooltip {
            display: block;
        }
        </style>
    """, unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    # SESSION & CONFIGURATION STATE PRE-EVALUATION
    # ─────────────────────────────────────────────────────────────────────────
    path = get_txt_path()
    force_config = st.session_state.get("force_config_screen", False)
    monitoring_started = st.session_state.get("monitoring_started", False)

    # Automatically set session_done = True if registry file exists and force_config is not requested
    # (Removed by user request to ensure config page always shows on first load)

    session_done = st.session_state.get("session_config_done", False)

    _cache_path_home = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "db_monitor_cache.json"
    )
    _cache_is_fresh = False
    if "status_cache" in st.session_state and st.session_state.status_cache:
        _cache_is_fresh = True
    elif os.path.exists(_cache_path_home):
        # Cache file must be less than 5 minutes (300 seconds) old
        if (time.time() - os.path.getmtime(_cache_path_home)) < 300:
            _cache_is_fresh = True

    play_state = "paused" if _cache_is_fresh else "running"


    # 1. Title Banner
    st.markdown(f"""
        <style>
        .em1, .em2, .em3, .em4, .em5, .em6 {{
            animation-play-state: {play_state} !important;
        }}
        @keyframes gifFade1 {{ 0%, 16.5% {{ opacity: 1; font-size: 1.2rem; width: auto; }} 16.6%, 100% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} }}
        @keyframes gifFade2 {{ 0%, 16.5% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} 16.6%, 33.2% {{ opacity: 1; font-size: 1.2rem; width: auto; }} 33.3%, 100% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} }}
        @keyframes gifFade3 {{ 0%, 33.2% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} 33.3%, 49.9% {{ opacity: 1; font-size: 1.2rem; width: auto; }} 50.0%, 100% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} }}
        @keyframes gifFade4 {{ 0%, 49.9% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} 50.0%, 66.5% {{ opacity: 1; font-size: 1.2rem; width: auto; }} 66.6%, 100% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} }}
        @keyframes gifFade5 {{ 0%, 66.5% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} 66.6%, 83.2% {{ opacity: 1; font-size: 1.2rem; width: auto; }} 83.3%, 100% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} }}
        @keyframes gifFade6 {{ 0%, 83.2% {{ opacity: 0; font-size: 0px; width: 0px; overflow: hidden; }} 83.3%, 100% {{ opacity: 1; font-size: 1.2rem; width: auto; }} }}

        .welcome-card-sq {{
            max-width: 520px;
            margin: 0 auto 12px auto;
            padding: 0.85rem 1.25rem;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.06);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        .welcome-title-sq {{
            font-size: 1.2rem;
            font-weight: 800;
            color: var(--text-primary);
            margin-bottom: 2px;
            letter-spacing: 0.02em;
        }}
        .welcome-subtitle-sq {{
            font-size: 0.76rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }}
        .db-loading-anim-sq {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            background: rgba(59, 130, 246, 0.08);
            border: 1px solid rgba(59, 130, 246, 0.2);
            padding: 4px 16px;
            border-radius: 16px;
            font-family: 'Inter', sans-serif;
            font-size: 0.78rem;
            font-weight: 800;
            min-width: 160px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.04);
            height: 32px;
        }}
        .em1 {{ animation: gifFade1 1.8s infinite; display: inline-block; vertical-align: middle; }}
        .em2 {{ animation: gifFade2 1.8s infinite; display: inline-block; vertical-align: middle; }}
        .em3 {{ animation: gifFade3 1.8s infinite; display: inline-block; vertical-align: middle; }}
        .em4 {{ animation: gifFade4 1.8s infinite; display: inline-block; vertical-align: middle; }}
        .em5 {{ animation: gifFade5 1.8s infinite; display: inline-block; vertical-align: middle; }}
        .em6 {{ animation: gifFade6 1.8s infinite; display: inline-block; vertical-align: middle; }}
        </style>
    """, unsafe_allow_html=True)

    anim_html = ""
    if session_done:
        status_text = "ALREADY LOADED" if _cache_is_fresh else "RUNNING..."
        status_color = "#10b981" if _cache_is_fresh else "#3b82f6"
        anim_html = (
            f"<div class='db-loading-anim-sq'>"
            f"<span class='em1'>🚴</span>"
            f"<span class='em2'>🚶</span>"
            f"<span class='em3'>🚣</span>"
            f"<span class='em4'>🚗</span>"
            f"<span class='em5'>🏃</span>"
            f"<span class='em6'>🙋</span>"
            f"<span style='color: {status_color}; font-weight: 800; letter-spacing: 0.04em;'>{status_text}</span>"
            f"</div>"
        )

    st.markdown(f'<div class="welcome-card-sq"><div class="welcome-title-sq">ENTERPRISE DATABASE CONTROL PORTAL</div><div class="welcome-subtitle-sq">Executive Management Console | Real-time Instance Selection & Storage Diagnostics</div>{anim_html}</div>', unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    # CONFIGURATION SCREEN
    # ─────────────────────────────────────────────────────────────────────────
    # Show config for any new session (F5 refresh or new tab).
    # Skip config entirely when navigating back from monitoring (session_done already True).
    if not session_done:
        st.markdown("""
            <div style='background: rgba(59, 130, 246, 0.05); border: 1px solid rgba(59, 130, 246, 0.2); border-radius: 12px; padding: 20px; margin-bottom: 20px;'>
                <h4 style='margin-top: 0; color: #3b82f6;'>⚙️ Database Registry Setup</h4>
                <p style='font-size: 0.88rem; color: var(--text-secondary); margin-bottom: 15px;'>
                    Configure or select the database registry configuration file containing your Oracle connections. 
                    Once configured, click <b>Load Information</b> to begin monitoring.
                </p>
            </div>
        """, unsafe_allow_html=True)

        history = get_registry_history()

        # ── Last Used Quick Connect ──
        if history:
            last_used = history[0]
            last_exists = os.path.exists(last_used)
            last_fname = os.path.basename(last_used)

            st.markdown(f"""
                <div style='background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 10px; padding: 15px; margin-bottom: 15px;'>
                    <span style='font-size:0.75rem; font-weight:700; color:var(--text-secondary); text-transform:uppercase;'>Last Used Configuration</span>
                    <div style='font-weight:700; font-size:0.95rem; margin: 4px 0;'>📁 {last_fname}</div>
                    <div style='font-size:0.75rem; color:var(--text-secondary); word-break:break-all;'>{last_used}</div>
                </div>
            """, unsafe_allow_html=True)

            if last_exists:
                if st.button(f"🚀 Connect & Use Last: {last_fname}", type="primary", use_container_width=True):
                    save_txt_path(last_used)
                    st.session_state.custom_txt_path = last_used
                    st.session_state.session_config_done = True
                    for k in ("status_cache", "diag_result"):
                        if k in st.session_state: del st.session_state[k]
                    st.success(f"✅ Connected to: {last_fname}!")
                    st.rerun()

        # ── Add New Config file tab forms ──
        with st.expander("📁 Change / Add New Registry File", expanded=not history):
            tab_upload, tab_path = st.tabs(["📤 Upload File", "📁 Enter File Path"])

            with tab_upload:
                uploaded_file = st.file_uploader(
                    "Upload database registry file",
                    type=["txt", "csv", "xlsx", "xls"],
                    key="cfg_file_uploader_home"
                )
                if uploaded_file is not None:
                    _SCRATCH_DIR = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch"
                    )
                    os.makedirs(_SCRATCH_DIR, exist_ok=True)
                    local_path = os.path.join(_SCRATCH_DIR, uploaded_file.name)
                    with open(local_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    save_txt_path(local_path)
                    st.session_state.custom_txt_path = local_path
                    st.session_state.session_config_done = True
                    for k in ("status_cache", "diag_result"):
                        if k in st.session_state: del st.session_state[k]
                    st.success(f"✅ Registry file uploaded: {uploaded_file.name}")
                    st.rerun()

            with tab_path:
                path_input = st.text_input(
                    "Absolute path to database registry file",
                    value="",
                    placeholder="e.g. C:/configs/db_list.txt",
                    key="cfg_path_input_home"
                )
                if st.button("🔌 Connect Path", key="btn_stage_path_home", use_container_width=True):
                    if path_input and os.path.exists(path_input):
                        save_txt_path(path_input)
                        st.session_state.custom_txt_path = path_input
                        st.session_state.session_config_done = True
                        for k in ("status_cache", "diag_result"):
                            if k in st.session_state: del st.session_state[k]
                        st.success(f"✅ Connected to path: **{path_input}**! Loading dashboard...")
                        time.sleep(0.6)
                        st.rerun()
        # ── Show Load Information Button ──
        if path and os.path.exists(path):
            st.markdown("<div style='height:15px;'></div>", unsafe_allow_html=True)
            if st.button("⚡ Load Information & Start Monitoring", type="primary", use_container_width=True, icon="🚀"):
                st.session_state.monitoring_started = True
                st.session_state.session_config_done = True
                st.success("Monitoring started! Loading dashboard...")
                time.sleep(0.5)
                st.rerun()
        else:
            st.warning("⚠️ Please configure a valid registry path to enable monitoring.")
        return


    # Set monitoring_started=True implicitly if we have a path and reach here
    if not monitoring_started:
        st.session_state.monitoring_started = True
    if not st.session_state.get("session_config_done", False):
        st.session_state.session_config_done = True

    db_names = load_db_names()

    # === CONNECTION DIAGNOSTICS BANNER ===
    if "diag_result" not in st.session_state:
        st.session_state.diag_result = None

    # Run diagnostics non-blockingly or use fast default
    if st.session_state.diag_result is None:
        st.session_state.diag_result = {"status": "ok", "message": "| All database(s) reachable on the network"}

    diag = st.session_state.diag_result
    if diag["status"] == "no_file":
        # Only show config screen if there is truly no registry — not a stale diag after back nav
        if get_txt_path():
            # Registry is valid now — clear stale diag and let portal home reload
            st.session_state.diag_result = None
            st.rerun()
        st.error(f"❌ **Registry Not Configured** — {diag['message']}")
        return
    elif diag["status"] == "bad_columns":
        st.error(f"❌ **Registry File Error** — {diag['message']}")
        st.info(
            "📋 **Expected column names** (with header): `db_name  host  port  service_name  username  password`\n\n"
            "📋 **Without header** — just ensure data is in this order per line:\n"
            "`<db_name>  <host>  <port>  <service_name>  <username>  <password>`\n\n"
            "Example row:  `ORCL  192.168.1.10  1521  orcl  system  mypassword`"
        )
        return
    elif diag["status"] == "empty":
        st.warning(f"⚠️ **Registry File Empty** — {diag['message']}")
        return
    elif diag["status"] == "all_failed":
        st.error(f"❌ **Server Unreachable** — {diag['message']}")
        # Show per-DB details in expander
        with st.expander("🔍 View connection details for each database"):
            for d in diag["details"]:
                icon = "🔴"
                st.markdown(f"{icon} **{d['db']}** — {d['detail']}")
    elif diag["status"] == "partial":
        st.warning(f"⚠️ **Partial Connectivity** — {diag['message']}")
        with st.expander("🔍 View connection details for each database"):
            for d in diag["details"]:
                icon = "🟢" if d["status"] == "reachable" else "🔴"
                st.markdown(f"{icon} **{d['db']}** — {d['detail']}")
    else:
        st.success(f"✅ **Server Connected** — {diag['message']}")

    if not db_names:
        st.info("📋 No database entries found in your registry file. Please check the file format and columns (db_name, username, password, host, port, service_name).")
        return


    # Initialize default selection state
    if "selected_chk_db" not in st.session_state:
        st.session_state.selected_chk_db = None

    # Load and cache status summaries for KPI calculations
    db_list_data = []
    total_count = len(db_names)
    healthy_count = 0
    warning_count = 0
    critical_count = 0

    # Build host mapping for grouping DBs by server
    from db_connection import get_config_for_db
    host_map = {}  # host -> [db_name, ...]
    for db in db_names:
        cfg = get_config_for_db(db)
        host = cfg["host"] if cfg else "unknown"
        host_map.setdefault(host, []).append(db)

    # Show currently-fetching DB name from background thread status + cache age
    try:
        from monitor_thread import read_monitor_status
        mon_status  = read_monitor_status()
        fetching_db = mon_status.get("fetching", "")
        last_end    = mon_status.get("last_cycle_end", "")
        cache_path  = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "db_monitor_cache.json"
        )
        # Cache age label
        cache_age_label = ""
        if os.path.exists(cache_path):
            cache_mtime = os.path.getmtime(cache_path)
            age_secs    = int(time.time() - cache_mtime)
            if age_secs < 60:
                cache_age_label = f"(cache updated {age_secs}s ago)"
            else:
                cache_age_label = f"(cache updated {age_secs // 60}m {age_secs % 60}s ago)"

        if fetching_db:
            fetch_label = f"⏳ Background monitor fetching: **{fetching_db}** — portal shows latest cache {cache_age_label}"
        elif last_end:
            fetch_label = f"✅ Last monitoring cycle: **{last_end}** {cache_age_label} · Cache refreshes every **2 min**"
        else:
            fetch_label = f"⏳ Background monitoring starting... {cache_age_label}"
        st.caption(fetch_label)
    except Exception:
        pass



    if _cache_is_fresh:
        for db in db_names:
            stats = load_db_status_summary_cached(db)
            cat = determine_health_category(stats)
            if cat == "Healthy":
                healthy_count += 1
            elif cat == "Warning":
                warning_count += 1
            else:
                critical_count += 1
            cfg = get_config_for_db(db)
            db_list_data.append({
                "name": db,
                "status_info": stats,
                "category": cat,
                "host": cfg["host"] if cfg else "unknown"
            })
    else:
        # Render the custom animation in the UI page first
        loading_placeholder = st.empty()
        db_placeholders = {}
        with loading_placeholder.container():
            st.markdown("""
                <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; padding:30px; background:var(--card-bg); border:1px solid var(--border-color); border-radius:12px; margin-top:20px; box-shadow:0 10px 30px rgba(0,0,0,0.15);">
                    <div style="width:50px; height:50px; border:5px solid rgba(59,130,246,0.1); border-top-color:#3b82f6; border-radius:50%; animation: spin-loader 1s linear infinite;"></div>
                    <h3 style="color:var(--text-primary); margin-top:15px; margin-bottom:5px; font-family:Space Grotesk; font-weight:800; font-size:1.2rem;">FETCHING DATABASE METRICS</h3>
                    <p style="color:var(--text-secondary); font-size:0.85rem; text-align:center; max-width:360px; margin:0;">Establishing parallel Oracle connections and loading health metrics...</p>
                </div>
                <style>
                @keyframes spin-loader {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
                </style>
            """, unsafe_allow_html=True)
            
            st.markdown("<div style='margin-top:20px;'></div>", unsafe_allow_html=True)
            st.write("### ⏳ Database Connection Progress")
            for db in db_names:
                db_placeholders[db] = st.empty()
                db_placeholders[db].info(f"⏳ Connecting to **{db}**... (waiting in queue)")
            
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # Run parallel loader threads to query statuses concurrently
        with ThreadPoolExecutor(max_workers=min(len(db_names), 10)) as executor:
            future_to_db = {
                executor.submit(load_db_status_summary_worker, db): db
                for db in db_names
            }
            
            # Save results to session state cache on the fly
            if "status_cache" not in st.session_state:
                st.session_state.status_cache = {}
                
            for future in as_completed(future_to_db):
                db = future_to_db[future]
                try:
                    stats = future.result()
                    st.session_state.status_cache[db] = stats
                    cat = determine_health_category(stats)
                    
                    if cat == "Healthy":
                        db_placeholders[db].success(f"🟢 **{db}** connected successfully! (Status: Healthy)")
                    elif cat == "Warning":
                        db_placeholders[db].warning(f"🟡 **{db}** connected with warnings! (Status: Warning)")
                    else:
                        db_placeholders[db].error(f"🔴 **{db}** connection failed! (Status: Critical/Offline)")
                except Exception as e:
                    # Capture connection failure gracefully
                    db_placeholders[db].error(f"❌ **{db}** connection error: {str(e)}")
                    # Save a placeholder failed status so it doesn't loop
                    st.session_state.status_cache[db] = {
                        "status": "Failed",
                        "message": str(e),
                        "listener": "down",
                        "database_status": "unknown"
                    }
        st.rerun()


    # 2. Executive Summary Ribbon (KPI Chips)
    st.markdown(f"""
        <div class="kpi-container">
            <div class="kpi-chip kpi-total">
                <div class="kpi-title">Total Databases</div>
                <div class="kpi-value">{total_count}</div>
            </div>
            <div class="kpi-chip kpi-healthy">
                <div class="kpi-title">Healthy Instances</div>
                <div class="kpi-value" style="color: var(--emerald);">{healthy_count}</div>
            </div>
            <div class="kpi-chip kpi-warning">
                <div class="kpi-title">Warning State</div>
                <div class="kpi-value" style="color: var(--amber);">{warning_count}</div>
            </div>
            <div class="kpi-chip kpi-critical">
                <div class="kpi-title">Offline Instances</div>
                <div class="kpi-value" style="color: var(--rose);">{critical_count}</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Initialize refresh state
    if "refresh_mode" not in st.session_state:
        st.session_state.refresh_mode = "Manual"
    if "refresh_interval" not in st.session_state:
        st.session_state.refresh_interval = 20
    if "last_refresh_time" not in st.session_state:
        st.session_state.last_refresh_time = time.time()

    # Handle automatic cache clearing if interval reached
    if st.session_state.refresh_mode == "Automatic":
        elapsed = time.time() - st.session_state.last_refresh_time
        if elapsed >= (st.session_state.refresh_interval * 60 - 5):
            if "status_cache" in st.session_state:
                del st.session_state.status_cache
            if "diag_result" in st.session_state:
                del st.session_state.diag_result
            st.session_state.last_refresh_time = time.time()
            st.rerun()

    # 3. Filtering, Searching and Refresh Controls (Single Row)
    if st.session_state.refresh_mode == "Automatic":
        col_search, col_filter, col_sort, col_ref_mode, col_ref_time, col_ref_btn = st.columns([1.6, 1.4, 0.8, 0.8, 0.6, 0.7])
    else:
        col_search, col_filter, col_sort, col_ref_mode, col_ref_btn = st.columns([1.6, 1.4, 0.8, 0.8, 0.7])

    with col_search:
        search_query = st.text_input("🔍 Search Database Name...", value="", placeholder="Type DB name...", label_visibility="collapsed")
        
    with col_filter:
        _STATUS_OPTIONS = ["All Statuses", "🟢 Healthy", "🟡 Warning", "🔴 Critical"]
        
        if "hf_multisel_widget" not in st.session_state:
            st.session_state.hf_multisel_widget = ["All Statuses"]
            
        def _on_hf_change():
            curr = st.session_state.hf_multisel_widget
            if not curr:
                # If everything is deselected, fallback to All Statuses
                st.session_state.hf_multisel_widget = ["All Statuses"]
            elif "All Statuses" in curr and len(curr) > 1:
                # Mutual exclusion logic
                if curr[-1] == "All Statuses":
                    # User just clicked "All Statuses", so clear everything else
                    st.session_state.hf_multisel_widget = ["All Statuses"]
                else:
                    # User clicked a specific status while "All Statuses" was selected, remove "All Statuses"
                    curr.remove("All Statuses")
                    st.session_state.hf_multisel_widget = curr
            elif "All Statuses" not in curr and len(curr) == 3:
                # User tried to select all 3 individual statuses (Healthy, Warning, Critical)
                # We must prevent selecting all 3. Drop the most recently added one.
                # 'curr' preserves the order of selection, so curr[-1] is the one they just clicked.
                curr.pop()
                st.session_state.hf_multisel_widget = curr

        _selected = st.multiselect(
            "Filter by Status",
            options=_STATUS_OPTIONS,
            key="hf_multisel_widget",
            on_change=_on_hf_change,
            label_visibility="collapsed",
            placeholder="☀️ All Statuses",
        )

    with col_sort:
        sort_order = st.selectbox("Sort Name", ["Sort A-Z", "Sort Z-A"], label_visibility="collapsed")
    with col_ref_mode:
        ref_mode = st.selectbox("Refresh Mode", ["Manual", "Automatic"], index=0 if st.session_state.refresh_mode == "Manual" else 1, label_visibility="collapsed")
        if ref_mode != st.session_state.refresh_mode:
            st.session_state.refresh_mode = ref_mode
            st.session_state.last_refresh_time = time.time()
            # Reset countdown so dashboard timer starts fresh with new mode
            if "_refresh_cycle_start" in st.session_state:
                del st.session_state["_refresh_cycle_start"]
            st.rerun()
    
    if st.session_state.refresh_mode == "Automatic":
        with col_ref_time:
            interval_val = int(st.session_state.refresh_interval)
            unit_str = "Minute" if interval_val == 1 else "Minutes"
            st.markdown(f"<p style='font-size:0.72rem; font-weight:700; color:var(--text-secondary); margin:0 0 2px 0; white-space:nowrap;'>Refresh every: {interval_val} {unit_str}</p>", unsafe_allow_html=True)
            ref_time = st.number_input(f"Refresh every: {interval_val} {unit_str}", min_value=1, value=interval_val, step=1, label_visibility="collapsed")
            if ref_time != st.session_state.refresh_interval:
                st.session_state.refresh_interval = ref_time
                st.session_state.last_refresh_time = time.time()
                # Reset countdown so dashboard timer restarts from new interval
                if "_refresh_cycle_start" in st.session_state:
                    del st.session_state["_refresh_cycle_start"]
                st.rerun()

    with col_ref_btn:
        if st.button("Refresh", help="Clear Status Cache & Refresh Statuses", use_container_width=True):
            # 1. Clear session state cache
            if "status_cache" in st.session_state:
                del st.session_state.status_cache
            if "diag_result" in st.session_state:
                del st.session_state.diag_result
            if "home_processes_cache" in st.session_state:
                del st.session_state.home_processes_cache
            if "home_mounts_cache" in st.session_state:
                del st.session_state.home_mounts_cache
            
            # 2. Clear JSON file cache
            try:
                cache_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "config", "db_monitor_cache.json"
                )
                if os.path.exists(cache_path):
                    os.remove(cache_path)
            except Exception:
                pass

            # 3. Force-trigger fresh background thread monitoring cycle immediately
            try:
                from monitor_thread import trigger_force_refresh
                trigger_force_refresh()
            except Exception:
                pass

            st.session_state.last_refresh_time = time.time()
            # Reset countdown so dashboard timer restarts
            if "_refresh_cycle_start" in st.session_state:
                del st.session_state["_refresh_cycle_start"]
            st.rerun()


    # If Automatic mode, show a LIVE countdown clock on the portal home page
    if st.session_state.refresh_mode == "Automatic":
        interval_val = int(st.session_state.refresh_interval)
        total_secs   = interval_val * 60
        elapsed_secs = int(time.time() - st.session_state.last_refresh_time)
        remaining    = max(0, total_secs - elapsed_secs)

        import streamlit.components.v1 as components
        components.html(
            f"""
            <style>
              @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
              body {{ margin:0; padding:0; background:transparent; }}
              .clock-wrap {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 18px;
                padding: 8px 0 4px 0;
                font-family: 'Inter', sans-serif;
              }}
              .clock-label {{
                font-size: 0.68rem;
                color: #6b7280;
                letter-spacing: 0.04em;
              }}
              .digital-clock {{
                font-family: 'Share Tech Mono', 'Courier New', monospace;
                font-size: 1.4rem;
                font-weight: 700;
                letter-spacing: 0.12em;
                color: #1d4ed8;
                background: #eff6ff;
                border: 2px solid #3b82f6;
                border-radius: 8px;
                padding: 3px 16px;
                min-width: 90px;
                text-align: center;
                box-shadow: 0 0 10px rgba(59,130,246,0.2);
                transition: color 0.3s, border-color 0.3s, box-shadow 0.3s;
              }}
              .digital-clock.warning {{
                color: #b45309;
                background: #fffbeb;
                border-color: #f59e0b;
                box-shadow: 0 0 12px rgba(245,158,11,0.3);
              }}
              .digital-clock.danger {{
                color: #dc2626;
                background: #fef2f2;
                border-color: #ef4444;
                box-shadow: 0 0 14px rgba(239,68,68,0.4);
                animation: pulse-red 0.8s infinite alternate;
              }}
              @keyframes pulse-red {{
                from {{ box-shadow: 0 0 6px rgba(239,68,68,0.3); }}
                to   {{ box-shadow: 0 0 18px rgba(239,68,68,0.6); }}
              }}
              .interval-badge {{
                font-size: 0.63rem;
                color: #ffffff;
                background: #3b82f6;
                border-radius: 999px;
                padding: 2px 10px;
                font-weight: 600;
              }}
            </style>

            <div class="clock-wrap" style="display: none;">
              <span class="clock-label">⏱ Portal Refresh In:</span>
              <div class="digital-clock" id="homeCountdown">--:--</div>
              <span class="interval-badge">Every {interval_val}m</span>
            </div>

            <script>
              var rem = {remaining};
              var el  = document.getElementById('homeCountdown');

              function tick() {{
                if (rem < 0) rem = 0;
                var m = Math.floor(rem / 60);
                var s = rem % 60;
                el.textContent = String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');

                el.classList.remove('warning','danger');
                if (rem <= 60)       el.classList.add('danger');
                else if (rem <= 300) el.classList.add('warning');

                if (rem <= 0) {{
                  el.textContent = '00:00';
                  
                  // Trigger a soft Streamlit rerun instead of a hard browser reload
                  var btns = window.parent.document.querySelectorAll('button');
                  for (var i=0; i<btns.length; i++) {{
                    if (btns[i].innerText.includes('Refresh')) {{
                      btns[i].click();
                      break;
                    }}
                  }}

                  return;
                }}
                rem--;
                setTimeout(tick, 1000);
              }}
              tick();
            </script>
            """,
            height=0,
        )



    # Apply Search Filter
    if search_query:
        db_list_data = [x for x in db_list_data if search_query.lower() in x["name"].lower()]

    # Apply Health dropdown filter
    _show_all = ("All Statuses" in _selected) or not _selected
    if not _show_all:
        _allowed = set()
        if "🟢 Healthy"  in _selected: _allowed.add("Healthy")
        if "🟡 Warning"  in _selected: _allowed.add("Warning")
        if "🔴 Critical" in _selected: _allowed.add("Critical")
        db_list_data = [x for x in db_list_data if x["category"] in _allowed]

    # 4. Render Database Cards globally sorted by status: Critical -> Warning -> Healthy
    st.write("### 🗄️ Database Instances")
    
    # Priority for sorting: Critical (0) -> Warning (1) -> Healthy (2)
    priority_map = {"Critical": 0, "Warning": 1, "Healthy": 2}
    
    # Sort globally: Critical -> Warning -> Healthy, then by name
    db_list_data_sorted = sorted(db_list_data, key=lambda x: (priority_map.get(x["category"], 2), x["name"].lower()))
    
    cols_per_row = 4
    for i in range(0, len(db_list_data_sorted), cols_per_row):
        row_items = db_list_data_sorted[i:i+cols_per_row]
        cols = st.columns(cols_per_row)
        for idx, item in enumerate(row_items):
            db    = item["name"]
            stats = item["status_info"]
            cat   = item["category"].lower()
            is_active = (st.session_state.selected_chk_db == db)

            def _status_dot(val, ok="UP", warn=None, fail="DOWN"):
                if val == ok:   return "dot-green",  "val-green"
                if val == fail: return "dot-red",    "val-red"
                return           "dot-amber",  "val-amber"

            db_dot,   db_val   = _status_dot(stats["db"])
            lsnr_dot, lsnr_val = _status_dot(stats["listener"])
            bkp_ok   = stats["backup"] == "SUCCESS"
            bkp_fail = stats["backup"] == "FAILED"
            bkp_dot  = "dot-green" if bkp_ok else ("dot-red" if bkp_fail else "dot-amber")
            bkp_val  = "val-green" if bkp_ok else ("val-red" if bkp_fail else "val-amber")
            
            # Find most critical permanent tablespace (excl TEMP/UNDO)
            balance_ts = stats.get("balance_ts", [])
            qualifying_ts = [t for t in balance_ts if t["pct"] >= 90.0]
            if qualifying_ts:
                ts_dot, ts_val_class = "dot-red", "val-red"
                ts_parts = []
                for t in qualifying_ts:
                    ts_parts.append(f'<span>{t["name"]}</span><span style="font-size:0.58rem;color:var(--text-secondary);font-weight:normal;margin-top:1px;margin-bottom:3px;">{t["free_mb"]:.0f} MB free</span>')
                ts_text = f'<span style="display:flex;flex-direction:column;align-items:flex-end;line-height:1.1;">{"".join(ts_parts)}</span>'
            else:
                ts_dot, ts_val_class = "dot-green", "val-green"
                ts_text = "None"

            # === Status Dot with Rich CSS Tooltip ===
            tooltip_reasons = list(stats.get("tooltip_reasons", []))

            # Dynamic fallback computation if list is empty but health category is not healthy
            if not tooltip_reasons and cat in ("warning", "critical"):
                if stats.get("db") == "DOWN":
                    tooltip_reasons.append("Database is DOWN")
                if stats.get("listener") == "DOWN":
                    tooltip_reasons.append("Listener is DOWN")
                if stats.get("backup") == "FAILED":
                    tooltip_reasons.append("Last RMAN backup FAILED")
                for q_ts in qualifying_ts:
                    tooltip_reasons.append(f"Tablespace {q_ts['name']} is {q_ts['pct']:.1f}% utilized (≥90%)")
                arc_pct = stats.get("arc_pct", 0)
                if stats.get("arc_configured") and arc_pct >= 90:
                    tooltip_reasons.append(f"Archive Log (FRA) is {arc_pct:.1f}% full")
                if stats.get("has_blocking"):
                    tooltip_reasons.append("Blocking session detected")
                if stats.get("deadlock_detected"):
                    tooltip_reasons.append("Deadlock detected in alert log")
                if stats.get("session_maxed"):
                    tooltip_reasons.append("Concurrent sessions > 85% of MAX")

            # Build tooltip HTML content
            if tooltip_reasons:
                reasons_html = "".join(f"&bull; {r}<br>" for r in tooltip_reasons)
                if stats["db"] == "DOWN" or stats["listener"] == "DOWN" or stats.get("backup") == "FAILED":
                    status_label = "Critical"
                    dot_color    = "#ef4444"
                    dot_anim     = "animation: pulse-dot 1.2s infinite;"
                    card_status_class = "db-card-critical"
                else:
                    status_label = "Warning"
                    dot_color    = "#f59e0b"
                    dot_anim     = ""
                    card_status_class = "db-card-warning"
                tooltip_html = (
                    f"<b>Status: {status_label}</b><br>"
                    f"Reason:<br>{reasons_html}"
                )
            else:
                dot_colors = {"healthy": "#10b981", "warning": "#f59e0b", "critical": "#ef4444"}
                dot_color  = dot_colors.get(cat, "#10b981")
                dot_anim   = ""
                card_status_class = f"db-card-{cat}"
                tooltip_html = "<b>Status: Healthy</b><br>All monitored components are operating normally."

            health_dot_html = (
                f'<div class="health-dot-wrapper" onclick="event.preventDefault();event.stopPropagation();">'
                f'<div style="width:12px;height:12px;border-radius:50%;background:{dot_color};'
                f'flex-shrink:0;cursor:help;{dot_anim}"></div>'
                f'<div class="dot-tooltip">{tooltip_html}</div>'
                f'</div>'
            )

            card_class = f"db-card {card_status_class}"
            if is_active:
                card_class += " db-card-selected"

            error_html = ""
            if stats.get("error"):
                error_html = f'<div class="db-error-msg">{stats["error"]}</div>'
            
            # Check for host authentication error
            mount_err = stats.get("mount_error", "")
            if mount_err and any(term in mount_err.lower() for term in ["authentication failed", "wrong username", "permission denied", "auth failed"]):
                error_html += '<div class="db-error-msg" style="color:#ef4444; font-weight:800; margin-top:4px;">❌ host username and password is wrong</div>'

            # ── Reporting DB section ─────────────────────────────────────────
            rpt_status  = stats.get("reporting_status", "NOT_CONFIGURED")
            rpt_db_name = stats.get("reporting_db_name", "")
            rpt_configured = stats.get("reporting_configured", False)

            if rpt_configured and rpt_db_name:
                if rpt_status == "UP":
                    rpt_dot   = "dot-green"
                    rpt_val   = "val-green"
                    rpt_arrow = "⇧"
                    rpt_bar_color = "#10b981"
                    rpt_badge_bg  = "rgba(16,185,129,0.10)"
                    rpt_badge_border = "rgba(16,185,129,0.35)"
                else:
                    rpt_dot   = "dot-red"
                    rpt_val   = "val-red"
                    rpt_arrow = "⇩"
                    rpt_bar_color = "#ef4444"
                    rpt_badge_bg  = "rgba(239,68,68,0.08)"
                    rpt_badge_border = "rgba(239,68,68,0.30)"

                reporting_section_html = (
                    f'<div style="margin-top:8px; border-top: 1.5px solid var(--border-color); padding-top:7px;">'
                    f'<div style="font-size:0.6rem; font-weight:700; color:var(--text-secondary); '
                    f'text-transform:uppercase; letter-spacing:0.06em; margin-bottom:5px;">'
                    f'📊 Reporting DB</div>'
                    f'<div style="display:flex; align-items:center; justify-content:space-between; '
                    f'background:{rpt_badge_bg}; border:1px solid {rpt_badge_border}; '
                    f'border-radius:6px; padding:5px 8px;">'
                    f'<div style="display:flex; flex-direction:column; min-width:0; flex:1;">'
                    f'<span style="font-size:0.68rem; font-weight:700; color:var(--text-primary); '
                    f'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" '
                    f'title="{rpt_db_name}">{rpt_db_name}</span>'
                    f'<span style="font-size:0.58rem; color:var(--text-secondary); margin-top:1px;">Reporting Instance</span>'
                    f'</div>'
                    f'<div style="display:flex; align-items:center; gap:5px; flex-shrink:0; margin-left:6px;">'
                    f'<span class="{rpt_dot}"></span>'
                    f'<span class="{rpt_val}" style="font-size:1.05rem; line-height:1;">{rpt_arrow}</span>'
                    f'</div>'
                    f'</div>'
                    f'</div>'
                )
            else:
                reporting_section_html = ""

            card_html = (
                f'<a href="?selected_db={db}" target="_self" style="text-decoration:none; color:inherit; display:block;">'
                f'<div class="{card_class}">'
                f'<div class="db-card-header">'
                f'<div class="db-card-name" title="{db}">{db}</div>'
                f'{health_dot_html}</div>'
                f'<hr class="db-divider" style="margin: 6px 0;">'
                f'<div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:4px; font-weight:700;">🖥️ Host: {item["host"]}</div>'
                f'<div class="db-status-row">'
                f'<span class="db-status-label"><span class="{db_dot}"></span> Database</span>'
                f'<span class="{db_val}" style="font-size:1.1rem; line-height:0.8;">{"⇧" if stats["db"] == "UP" else ("⇩" if stats["db"] == "DOWN" else "▵")}</span></div>'
                f'<div class="db-status-row">'
                f'<span class="db-status-label"><span class="{lsnr_dot}"></span> Listener</span>'
                f'<span class="{lsnr_val}" style="font-size:1.1rem; line-height:0.8;">{"⇧" if stats["listener"] == "UP" else ("⇩" if stats["listener"] == "DOWN" else "▵")}</span></div>'
                f'<div class="db-status-row">'
                f'<span class="db-status-label"><span class="{bkp_dot}"></span> Backup</span>'
                f'<span class="{bkp_val}">{stats["backup"]}</span></div>'
                f'<div class="db-status-row" style="align-items: flex-start;">'
                f'<span class="db-status-label"><span class="{ts_dot}"></span> Tablespace</span>'
                f'<span class="{ts_val_class}">{ts_text}</span></div>'
                f'{error_html}'
                f'{reporting_section_html}'
                f'</div>'
                f'</a>'
            )

            with cols[idx]:
                st.markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
                st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

    # 5. Group server resources by host and render below all database cards
    st.markdown("<hr style='margin:35px 0; border-color:var(--border-color);'>", unsafe_allow_html=True)
    st.write("### 🖥️ Host Server Resources & Diagnostics")

    # Build a priority for hosts based on their most severe DB status
    priority_map = {"Critical": 0, "Warning": 1, "Healthy": 2}
    
    # Map host -> minimum priority value found among its databases
    host_priorities = {}
    for item in db_list_data:
        h = item["host"]
        prio = priority_map.get(item["category"], 2)
        if h not in host_priorities or prio < host_priorities[h]:
            host_priorities[h] = prio

    # Sort hosts: first by their most severe DB priority, then alphabetically by host name
    sorted_hosts = sorted(host_priorities.keys(), key=lambda h: (host_priorities[h], h.lower()))

    for host in sorted_hosts:
        host_dbs = [item for item in db_list_data if item["host"] == host]
        if not host_dbs:
            continue

        # Server header
        st.markdown(f"<p style='font-size:0.85rem; font-weight:700; color:var(--text-secondary); margin-top:20px; margin-bottom:2px;'>🖥️ Server: <span style=\"color:#3b82f6;\">{host}</span> &nbsp;({len(host_dbs)} database{'s' if len(host_dbs) > 1 else ''})</p>", unsafe_allow_html=True)

        # === Server Resources Inline horizontally underneath Database cards ===
        os_col1, os_col2, _ = st.columns([2, 2, 2.5])
        
        with os_col1:
            pass # just to maintain block indentation without changing rest of code yet

        # Calculate the stats
        first_up = next(
                (item for item in host_dbs
                 if item["status_info"].get("db") == "UP" and item["status_info"].get("system_res")),
                None
            )
        if first_up:
            sys_res    = first_up["status_info"]["system_res"]
            cpu_used   = sys_res.get("cpu_host_used_pct", 0)
            cpu_free   = sys_res.get("cpu_host_free_pct", 0)
            ram_total  = sys_res.get("ram_total_gb", 0)
            ram_used   = sys_res.get("ram_used_gb", 0)
            ram_free   = sys_res.get("ram_free_gb", 0)
            ram_oracle = sys_res.get("ram_oracle_gb", 0)
            cpu_oracle = sys_res.get("cpu_oracle_used_pct", 0)

            ram_pct       = round((ram_used / ram_total * 100), 1) if ram_total > 0 else 0.0
            ram_total_mb  = ram_total  * 1024
            ram_used_mb   = ram_used   * 1024
            ram_free_mb   = ram_free   * 1024
            ram_oracle_mb = ram_oracle * 1024
            ram_oracle_pct   = round((ram_oracle_mb / ram_total_mb * 100), 1) if ram_total_mb > 0 else 0.0
            ram_other_mb     = max(0.0, ram_used_mb - ram_oracle_mb)
            ram_other_pct    = max(0.0, ram_pct - ram_oracle_pct)
            cpu_oracle_capped = min(cpu_oracle, cpu_used)
            cpu_other_pct    = max(0.0, cpu_used - cpu_oracle_capped)
            cpu_bar_color    = "#ef4444" if cpu_used > 85 else "#10b981"
            ram_bar_color    = "#ef4444" if ram_pct  > 85 else "#10b981"
            cpu_ind = "&#128308;" if cpu_used > 85 else "&#128994;"
            ram_ind = "&#128308;" if ram_pct  > 85 else "&#128994;"

            theme_mode = st.session_state.get("theme", "Light")
            os_text = "#f1f5f9" if theme_mode == "Dark" else "#1e293b"
            os_sub  = "#94a3b8" if theme_mode == "Dark" else "#64748b"
            os_bg_a = "rgba(16,185,129,0.04)" if theme_mode == "Dark" else "rgba(16,185,129,0.03)"
            os_bdr  = "rgba(255,255,255,0.1)"  if theme_mode == "Dark" else "rgba(0,0,0,0.08)"

            os_style = f"""<style>
.os-wrap{{font-family:'Inter',sans-serif;display:flex;flex-direction:column;gap:5px;padding:0;}}
.os-card{{border:1px solid {os_bdr};border-radius:8px;padding:12px 14px;background:{os_bg_a};height:210px;display:flex;flex-direction:column;justify-content:space-between;box-sizing:border-box;}}
.os-card-title{{font-size:0.75rem;font-weight:800;color:{os_sub};margin-bottom:6px;letter-spacing:0.03em;text-transform:uppercase;}}
.os-row{{display:flex;justify-content:space-between;align-items:center;font-size:0.72rem;font-weight:700;color:{os_text};margin-bottom:3px;}}
.os-row-val{{color:#38bdf8;}}
.os-sub-row{{font-size:0.62rem;color:{os_sub};display:flex;justify-content:space-between;margin-bottom:4px;}}
.os-sub-row b{{color:{os_text};font-weight:600;}}
.os-bar{{height:8px;border-radius:4px;background:rgba(128,128,128,0.2);position:relative;overflow:hidden;margin-bottom:4px;}}
.os-seg{{position:absolute;top:0;height:100%;transition:opacity 0.2s;}}
.os-seg:hover{{opacity:0.8;}}
</style>"""

            os_html_a = os_style + f"""
<div class="os-wrap">
  <div class="os-card">
<div class="os-card-title">&#128187; Host OS Resource Utilized by Oracle &mdash; Server {host}</div>
<div>
  <div class="os-row">{cpu_ind} Host CPU<span class="os-row-val">Used : {cpu_used:.1f}%</span></div>
  <div class="os-bar" title="Oracle CPU: {cpu_oracle:.1f}% | Other CPU: {cpu_other_pct:.1f}% | Free CPU: {cpu_free:.1f}%">
    <div class="os-seg" style="left:0;width:{cpu_oracle_capped}%;background:#f59e0b;" title="Oracle CPU: {cpu_oracle:.1f}%"></div>
    <div class="os-seg" style="left:{cpu_oracle_capped}%;width:{cpu_other_pct}%;background:{cpu_bar_color};" title="Other CPU: {cpu_other_pct:.1f}%"></div>
  </div>
  <div class="os-sub-row">
    <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{cpu_oracle:.1f}%</b></span>
    <span><span style="color:{cpu_bar_color};">&#9632;</span> Other <b>{cpu_other_pct:.1f}%</b></span>
    <span>Free <b>{cpu_free:.1f}%</b></span>
  </div>
</div>
<div style="margin-top:2px;">
  <div class="os-row">{ram_ind} Host RAM<span class="os-row-val">Used : {ram_used_mb:,.0f} MB</span></div>
  <div class="os-bar" title="Oracle RAM: {ram_oracle_mb:,.0f} MB | Other RAM: {ram_other_mb:,.0f} MB | Free RAM: {ram_free_mb:,.0f} MB">
    <div class="os-seg" style="left:0;width:{ram_oracle_pct}%;background:#f59e0b;" title="Oracle RAM: {ram_oracle_mb:,.0f} MB"></div>
    <div class="os-seg" style="left:{ram_oracle_pct}%;width:{ram_other_pct}%;background:{ram_bar_color};" title="Other RAM: {ram_other_mb:,.0f} MB"></div>
  </div>
  <div class="os-sub-row">
    <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{ram_oracle_mb:,.0f}MB</b></span>
    <span><span style="color:{ram_bar_color};">&#9632;</span> Other <b>{ram_other_mb:,.0f}MB</b></span>
    <span>Free <b>{ram_free_mb:,.0f}MB</b></span>
  </div>
</div>
  </div>
</div>"""
            overall_cpu_bar_color = "#ef4444" if cpu_used >= 90 else ("#f59e0b" if cpu_used >= 70 else "#10b981")
            overall_ram_bar_color = "#ef4444" if ram_pct >= 90 else ("#f59e0b" if ram_pct >= 70 else "#10b981")
            overall_cpu_ind = "&#128308;" if cpu_used >= 90 else ("&#128993;" if cpu_used >= 70 else "&#128994;")
            overall_ram_ind = "&#128308;" if ram_pct >= 90 else ("&#128993;" if ram_pct >= 70 else "&#128994;")

            os_html_b = os_style + f"""
<div class="os-wrap" style="">
  <div class="os-card">
<div class="os-card-title">&#128187; Host OS Resource &mdash; Server {host}</div>
<div>
  <div class="os-row">{overall_cpu_ind} CPU<span class="os-row-val">Used : {cpu_used:.1f}%</span></div>
  <div class="os-bar" title="Used CPU: {cpu_used:.1f}% | Free CPU: {cpu_free:.1f}%">
    <div class="os-seg" style="left:0;width:{cpu_used}%;background:{overall_cpu_bar_color};" title="Used CPU: {cpu_used:.1f}%"></div>
  </div>
  <div class="os-sub-row">
    <span><span style="color:{overall_cpu_bar_color};">&#9632;</span> Used <b>{cpu_used:.1f}%</b></span>
    <span>Free <b>{cpu_free:.1f}%</b></span>
  </div>
</div>
<div style="margin-top:2px;">
  <div class="os-row">{overall_ram_ind} Memory<span class="os-row-val">Used : {ram_used_mb:,.0f} MB</span></div>
  <div class="os-bar" title="Used RAM: {ram_used_mb:,.0f} MB | Free RAM: {ram_free_mb:,.0f} MB | Total RAM: {ram_total_mb:,.0f} MB">
    <div class="os-seg" style="left:0;width:{ram_pct}%;background:{overall_ram_bar_color};" title="Used RAM: {ram_used_mb:,.0f} MB"></div>
  </div>
  <div class="os-sub-row">
    <span><span style="color:{overall_ram_bar_color};">&#9632;</span> Used <b>{ram_used_mb:,.0f} MB</b></span>
    <span>Free <b>{ram_free_mb:,.0f} MB</b></span>
    <span>Total <b>{ram_total_mb:,.0f} MB</b></span>
  </div>
</div>
  </div>
</div>"""
            
            import streamlit.components.v1 as _comp
            with os_col1:
                _comp.html(os_html_a, height=215, scrolling=False)
            with os_col2:
                _comp.html(os_html_b, height=215, scrolling=False)

        # === Top CPU & Memory Process Tables — Server-wise from Cache / Live Fallback ===
        if "home_processes_cache" not in st.session_state:
            st.session_state.home_processes_cache = {}
            
        cached_proc = st.session_state.home_processes_cache.get(host)
        
        top_cpu = []
        top_mem = []
        err_msg = None
        
        if cached_proc and (time.time() - cached_proc["ts"] < 300):
            top_cpu = cached_proc["top_cpu"]
            top_mem = cached_proc["top_mem"]
            err_msg = cached_proc["error"]
        else:
            try:
                from utils.ssh_process_provider import get_top_processes_for_server_via_db
                server_db_names = [item["name"] for item in host_dbs]
                proc_res = get_top_processes_for_server_via_db(
                    db_list=server_db_names,
                    limit=10,
                )
                top_cpu = proc_res.get("top_cpu", [])
                top_mem = proc_res.get("top_mem", [])
                err_msg = proc_res.get("error")
                
                # Cache the loaded data
                st.session_state.home_processes_cache[host] = {
                    "ts": time.time(),
                    "top_cpu": top_cpu,
                    "top_mem": top_mem,
                    "error": err_msg
                }
            except Exception as e:
                err_msg = str(e)

        pcol1, pcol2 = st.columns(2)

        with pcol1:
            st.markdown(
                f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-left:4px solid #ef4444; border-radius:6px; padding:10px 14px; margin-top:14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;'>"
                f"<span style='font-size:0.92rem; font-weight:800; color:var(--text-primary); text-transform:uppercase; letter-spacing:0.02em;'>🔥 TOP CPU CONSUMING SESSIONS — {host}</span>"
                f"<span style='font-size:0.75rem; color:#ef4444; font-weight:600;'>DB View</span></div>",
                unsafe_allow_html=True
            )
            if top_cpu:
                df_cpu = pd.DataFrame([{
                    "PID":          f"#{p['pid']}",
                    "USER (DB)":    p.get("user_label", p.get("username", "oracle")),
                    "CPU Time(s)":  f"{p.get('cpu_sec', p.get('cpu', 0.0)):.2f}s",
                    "MEM (MB)":     f"{p.get('mem', 0.0):.1f} MB",
                    "STATUS":       p.get("status", ""),
                } for p in top_cpu])
                theme_mode = st.session_state.get("theme", "Light")
                def render_styled_process_table_html(df, t_mode):
                    if t_mode == "Dark":
                        bg_even = "rgba(255, 255, 255, 0.02)"
                        bg_odd  = "rgba(16, 185, 129, 0.1)"
                        hdr_bg  = "rgba(16, 185, 129, 0.2)"
                        text_col = "#f1f5f9"
                        bdr_col = "rgba(255, 255, 255, 0.1)"
                    else:
                        bg_even = "#f8fafc"    # Light Grey
                        bg_odd  = "#e6f4ea"    # Light Green
                        hdr_bg  = "#c3e6cb"    # Light Green Header
                        text_col = "#1e293b"
                        bdr_col = "#cbd5e1"

                    html = f'''<div style="overflow-x:auto; overflow-y:auto; max-height:220px; border:1px solid {bdr_col}; border-radius:6px;">
                    <table style="width:100%; border-collapse:collapse; font-family:'Inter',sans-serif; font-size:0.75rem; color:{text_col};">
                        <thead>
                            <tr style="background:{hdr_bg}; border-bottom:2px solid {bdr_col}; text-transform:uppercase; font-size:0.68rem; font-weight:800;">'''
                    for col in df.columns:
                        html += f'<th style="padding:6px 8px; text-align:left;">{col}</th>'
                    html += '</tr></thead><tbody>'
                    for idx, row in df.iterrows():
                        row_bg = bg_even if idx % 2 == 0 else bg_odd
                        html += f'<tr style="background:{row_bg}; border-bottom:1px solid {bdr_col};">'
                        for val in row.values:
                            html += f'<td style="padding:5px 8px; font-weight:600;">{val}</td>'
                        html += '</tr>'
                    html += '</tbody></table></div>'
                    return html

                st.markdown(render_styled_process_table_html(df_cpu, theme_mode), unsafe_allow_html=True)
            else:
                st.info(err_msg if err_msg else f"No active oracle user sessions on {host}")

        with pcol2:
            st.markdown(
                f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-left:4px solid #3b82f6; border-radius:6px; padding:10px 14px; margin-top:14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;'>"
                f"<span style='font-size:0.92rem; font-weight:800; color:var(--text-primary); text-transform:uppercase; letter-spacing:0.02em;'>💾 TOP MEMORY CONSUMING SESSIONS — {host}</span>"
                f"<span style='font-size:0.75rem; color:#3b82f6; font-weight:600;'>DB View</span></div>",
                unsafe_allow_html=True
            )
            if top_mem:
                df_mem = pd.DataFrame([{
                    "PID":       f"#{p['pid']}",
                    "USER (DB)": p.get("user_label", p.get("username", "oracle")),
                    "MEM (MB)":  f"{p.get('mem', 0.0):.1f} MB",
                    "STATUS":    p.get("status", ""),
                } for p in top_mem])
                theme_mode = st.session_state.get("theme", "Light")
                st.markdown(render_styled_process_table_html(df_mem, theme_mode), unsafe_allow_html=True)
            else:
                st.info(err_msg if err_msg else f"No active oracle user sessions on {host}")



        # === Server Mount Points (Cache-first / Live Fallback) ===
        if "home_mounts_cache" not in st.session_state:
            st.session_state.home_mounts_cache = {}
            
        cached_mounts = st.session_state.home_mounts_cache.get(host)
        
        real_vols = []
        _mount_error = None
        if cached_mounts and (time.time() - cached_mounts["ts"] < 600):
            real_vols    = cached_mounts.get("volumes", [])
            _mount_error = cached_mounts.get("error", None)
        else:
            _mount_error = None
            try:
                from utils.storage_provider import get_storage_provider
                from db_connection import get_api_connection

                # Try Java Bridge via DB connection first
                first_db_name = host_dbs[0]["name"]
                _sp_conn, _ = get_api_connection(first_db_name)
                if _sp_conn:
                    storage_provider = get_storage_provider(_sp_conn)
                    all_vols = storage_provider.get_storage_info()
                    real_vols = [v for v in all_vols if v.get("total", 0) > 0 and v.get("fs_type", "") not in ("tmpfs", "devtmpfs", "proc", "sysfs")]
                    try:
                        _sp_conn.close()
                    except Exception:
                        pass
            except Exception as _e1:
                print(f"[home_mounts] Storage provider error: {_e1}")
                
            if not real_vols:
                try:
                    from utils.ssh_mount_provider import get_mounts_via_ssh
                    from db_connection import get_config_for_db
                    first_cfg = get_config_for_db(host_dbs[0]["name"]) or {}
                    _h_user = first_cfg.get("host_username", "").strip()
                    _h_pass = first_cfg.get("host_password", "").strip()
                    if not _h_user:
                        _mount_error = f"❌ No host_username configured for server {host}. Please add it to your registry file."
                    else:
                        ssh_result = get_mounts_via_ssh(
                            host=host,
                            username=_h_user,
                            password=_h_pass,
                        )
                        ssh_vols = ssh_result.get("volumes", [])
                        ssh_err  = ssh_result.get("error", None)
                        if ssh_vols:
                            real_vols = ssh_vols
                        elif ssh_err:
                            _mount_error = f"❌ SSH Error: {ssh_err}"
                        else:
                            _mount_error = f"❌ SSH connected to {host} but returned no mount points."
                except ImportError as _ie:
                    _mount_error = f"❌ Missing module: {_ie}. Please rebuild the .exe with updated spec."
                    print(f"[home_mounts] ImportError: {_ie}")
                except Exception as _e2:
                    _mount_error = f"❌ SSH Error: {_e2}"
                    print(f"[home_mounts] SSH error: {_e2}")
            
            # Cache the result
            st.session_state.home_mounts_cache[host] = {
                "ts": time.time(),
                "volumes": real_vols,
                "error": _mount_error,
            }

        # Render Mount Points Section Header (always shown)
        st.markdown(
            f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-left:4px solid #10b981; border-radius:6px; padding:10px 14px; margin-top:18px; margin-bottom:8px;'>"
            f"<span style='font-size:0.92rem; font-weight:800; color:var(--text-primary); text-transform:uppercase; letter-spacing:0.02em;'>💽 SERVER MOUNT POINTS — {host}</span>"
            f"</div>",
            unsafe_allow_html=True
        )
        if real_vols:
            real_vols_sorted = sorted(real_vols, key=lambda v: v.get('pct', 0), reverse=True)
            theme_mp = st.session_state.get("theme", "Light")
            mp_hdr_bg = "#1e2d45" if theme_mp == "Dark" else "#f1f5f9"
            mp_hdr_fg = "#38bdf8" if theme_mp == "Dark" else "#1e293b"
            mp_row_fg = "#e2e8f0" if theme_mp == "Dark" else "#1e293b"
            mp_alt_bg = "rgba(255,255,255,0.03)" if theme_mp == "Dark" else "rgba(0,0,0,0.02)"
            mp_border = "#2a354f" if theme_mp == "Dark" else "#e5e7eb"

            def _mp_bar(pct):
                if pct > 90:   color = "#ef4444"
                elif pct > 75: color = "#f59e0b"
                else:          color = "#10b981"
                return (f'<div style="background:rgba(128,128,128,0.15);border-radius:4px;height:8px;width:100%;position:relative;overflow:hidden;">'
                        f'<div style="position:absolute;left:0;top:0;height:100%;width:{min(pct,100):.1f}%;background:{color};border-radius:4px;"></div></div>'
                        f'<span style="font-size:0.68rem;color:{color};font-weight:700;">{pct:.1f}%</span>')

            rows_html = ""
            for vi, v in enumerate(real_vols_sorted):
                pct = v.get('pct', 0)
                row_bg = "rgba(239,68,68,0.12)" if pct > 90 else (mp_alt_bg if vi % 2 == 0 else "transparent")
                row_border = "border-left:3px solid #ef4444;" if pct > 90 else ""
                rows_html += f"""
                <tr style="background:{row_bg};{row_border}">
                    <td style="padding:6px 10px;font-weight:600;color:{mp_row_fg};">{v.get('mount_point','')}</td>
                    <td style="padding:6px 10px;color:{mp_row_fg};">{v.get('drive','').replace(' (DB)','')}</td>
                    <td style="padding:6px 10px;color:{mp_row_fg};">{v.get('fs_type','xfs')}</td>
                    <td style="padding:6px 10px;color:{mp_row_fg};text-align:right;">{v.get('total',0):.2f} GB</td>
                    <td style="padding:6px 10px;color:{mp_row_fg};text-align:right;">{v.get('used',0):.2f} GB</td>
                    <td style="padding:6px 10px;color:{mp_row_fg};text-align:right;">{v.get('free',0):.2f} GB</td>
                    <td style="padding:6px 10px;min-width:120px;">{_mp_bar(pct)}</td>
                    <td style="padding:6px 10px;color:{mp_row_fg};">{v.get('state','ACTIVE')}</td>
                </tr>"""

            mp_table = f"""
            <div style="overflow-x:auto;border:1px solid {mp_border};border-radius:8px;">
            <table style="width:100%;border-collapse:collapse;font-family:'Inter',sans-serif;font-size:0.75rem;">
              <thead>
                <tr style="background:{mp_hdr_bg};position:sticky;top:0;z-index:2;">
                  <th style="padding:8px 10px;text-align:left;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Mount Point</th>
                  <th style="padding:8px 10px;text-align:left;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Device</th>
                  <th style="padding:8px 10px;text-align:left;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">FS Type</th>
                  <th style="padding:8px 10px;text-align:right;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Total</th>
                  <th style="padding:8px 10px;text-align:right;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Used</th>
                  <th style="padding:8px 10px;text-align:right;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Free</th>
                  <th style="padding:8px 10px;text-align:left;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Usage %</th>
                  <th style="padding:8px 10px;text-align:left;color:{mp_hdr_fg};font-weight:700;border-bottom:1px solid {mp_border};">Status</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
            </div>"""
            st.markdown(mp_table, unsafe_allow_html=True)
        else:
            # Show the actual error if captured, otherwise show generic message
            _cached_err = st.session_state.home_mounts_cache.get(host, {}).get("error") if "home_mounts_cache" in st.session_state else None
            if _cached_err:
                st.error(_cached_err)
            else:
                st.info(f"No mount points found or configured for server {host}")
        
        st.markdown("<hr style='margin:20px 0; border-color: rgba(128,128,128,0.15);'>", unsafe_allow_html=True)




