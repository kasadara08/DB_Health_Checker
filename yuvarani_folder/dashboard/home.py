import os
import time
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from db_connection import (
    get_txt_path, save_txt_path, load_db_names, read_db_file_to_df,
    get_registry_history, save_pending_path, confirm_pending_path,
    discard_pending_path, get_pending_path, get_previous_registry,
    clear_active_registry, get_reporting_db_config, check_reporting_db_status
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

# ── CACHING & QUERYING UTILITIES ──
import datetime

def seed_session_cache_from_file():
    """
    Initializes st.session_state caches for a fresh session.

    Deliberately does NOT seed status_cache from the background monitor's
    disk cache (config/db_monitor_cache.json) anymore. That file can be up
    to a few minutes stale relative to the registry file, so a fresh page
    load could show a database's OLD host/status/tablespace info — most
    noticeable right after editing the registry (.txt) file. Leaving
    status_cache empty means every DB card goes through the live,
    already-parallel fetch below (load_db_status_summary_basic via
    ThreadPoolExecutor, up to 30 DBs at once) instead, which reads the
    CURRENT registry file on every load and is still fast because it's
    fully parallelized rather than one-DB-at-a-time.
    """
    if "status_cache" not in st.session_state:
        st.session_state.status_cache = {}
    if "server_cache" not in st.session_state:
        st.session_state.server_cache = {}

def check_standby_db_status_live(db_name):
    import sys
    import os
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.append(_root)
        
    from db_connection import get_config_for_db, get_standby_db_config
    from generate_db_status_log import get_standby_sync_status
    
    primary_cfg = get_config_for_db(db_name)
    stby_cfg = get_standby_db_config(db_name)
    if not stby_cfg:
        return {"configured": False, "status": "NOT_CONFIGURED", "max_gap": 0, "threads": []}
        
    status_str, err_details = get_standby_sync_status(primary_cfg, stby_cfg)
    
    stby_lower = status_str.lower()
    is_valid_sync = status_str.startswith("UP (SYNCHRONIZED)") or status_str.startswith("UP (NOT SYNCHRONIZED")
    
    max_gap = 0
    if "Gap: " in status_str:
        try:
            max_gap = int(status_str.split("Gap: ")[1].split(" ")[0])
        except Exception:
            pass
            
    err_msg = err_details if err_details else ("" if is_valid_sync else status_str)
            
    return {
        "configured": is_valid_sync,
        "status": status_str,
        "max_gap": max_gap,
        "error": err_msg,
        "threads": []
    }

def load_db_status_summary_basic(db_name):
    """
    Lightweight query fetching ONLY basic DB stats for cards:
    - DB status, listener status, backup status, active sessions count,
      and whether tablespace is full (>=90%) with their names.
    Does NOT fetch system resources, mount points, or OS processes.
    """
    status_data = {
        "db": "UNKNOWN",
        "listener": "UNKNOWN",
        "backup": "UNKNOWN",
        "active_sessions": 0,
        "balance_ts": [],
        "full_ts": [],
        "tooltip_reasons": [],
        "error": None,
        "last_refresh": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    conn = None
    try:
        import socket
        from db_connection import get_api_connection, get_config_for_db
        
        # 1. Direct connection attempt
        conn, err = get_api_connection(db_name)
        if not conn:
            status_data["db"] = "DOWN"
            status_data["error"] = err
            # Check TCP port for listener state
            try:
                cfg_check = get_config_for_db(db_name)
                if cfg_check:
                    sock = socket.create_connection((cfg_check["host"], int(cfg_check["port"])), timeout=2.0)
                    sock.close()
                    status_data["listener"] = "UP"
                else:
                    status_data["listener"] = "DOWN"
            except Exception:
                status_data["listener"] = "DOWN"
            return status_data

        status_data["db"] = "UP"
        status_data["listener"] = "UP"

        # 2. Get DB Status (OPEN or otherwise)
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM v$instance")
            row = cur.fetchone()
            if row:
                status_data["db"] = "UP" if str(row[0]).upper() == "OPEN" else "DOWN"
            cur.close()
        except Exception:
            pass

        if status_data["db"] == "UP":
            # 3. Active sessions count
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM v$session WHERE status = 'ACTIVE' AND type = 'USER'")
                row = cur.fetchone()
                if row:
                    status_data["active_sessions"] = int(row[0])
                cur.close()
            except Exception:
                pass

            # 4. RMAN backup check
            try:
                cur = conn.cursor()
                cur.execute("SELECT status, start_time FROM v$rman_backup_job_details ORDER BY start_time DESC")
                row = cur.fetchone()
                if not row:
                    status_data["backup"] = "PENDING"
                else:
                    last_status = str(row[0]).upper()
                    st_val = row[1]
                    days_ago = 999
                    if st_val:
                        try:
                            import pandas as pd
                            days_ago = (datetime.datetime.now() - pd.to_datetime(st_val)).days
                        except Exception:
                            pass
                    if days_ago <= 1:
                        if "COMPLETED" in last_status or "SUCCESS" in last_status:
                            status_data["backup"] = "SUCCESS"
                        elif "FAIL" in last_status or "ERR" in last_status:
                            status_data["backup"] = "FAILED"
                        else:
                            status_data["backup"] = "PENDING"
                    else:
                        status_data["backup"] = "PENDING"
                cur.close()
            except Exception:
                status_data["backup"] = "UNKNOWN"

            # 5. Tablespaces check
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT df.tablespace_name,
                           ROUND(df.allocated_mb,2) AS allocated_mb,
                           ROUND(NVL(fs.free_mb,0),2) AS free_mb,
                           ROUND(df.allocated_mb - NVL(fs.free_mb,0),2) AS used_mb,
                           ROUND(df.max_mb,2) AS maxsize_mb,
                           ROUND(df.max_mb - (df.allocated_mb - NVL(fs.free_mb,0)),2) AS free_on_max_mb,
                           ROUND(((df.allocated_mb - NVL(fs.free_mb,0))/df.max_mb)*100,2) AS pct_used_max
                    FROM (SELECT tablespace_name, SUM(bytes)/1024/1024 allocated_mb,
                                 SUM(CASE WHEN autoextensible='YES' THEN maxbytes ELSE bytes END)/1024/1024 max_mb
                          FROM dba_data_files GROUP BY tablespace_name) df
                    LEFT JOIN (SELECT tablespace_name, SUM(bytes)/1024/1024 free_mb FROM dba_free_space GROUP BY tablespace_name) fs
                      ON df.tablespace_name = fs.tablespace_name
                    ORDER BY df.tablespace_name
                """)
                balance_ts = []
                for row in cur.fetchall():
                    name = str(row[0]).upper()
                    if "TEMP" in name or "UNDO" in name:
                        continue
                    pct = float(row[6] or 0)
                    used_mb = float(row[3] or 0)
                    free_mb = float(row[5] or 0)
                    total_mb = float(row[4] or 0)
                    alloc_mb = float(row[1] or 0)
                    balance_ts.append({
                        "name": name,
                        "pct": pct,
                        "used_mb": used_mb,
                        "free_mb": free_mb,
                        "total_mb": total_mb,
                        "allocated_mb": alloc_mb
                    })
                balance_ts.sort(key=lambda x: x["free_mb"])
                status_data["balance_ts"] = balance_ts
                status_data["full_ts"] = [t["name"] for t in balance_ts if t["pct"] >= 90]
                cur.close()
            except Exception:
                pass

    except Exception as e:
        status_data["db"] = "DOWN"
        status_data["listener"] = "DOWN"
        status_data["error"] = str(e)
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

    # Mark standby status as PENDING if standby DB is configured in registry
    from db_connection import get_standby_db_config
    stby_cfg = get_standby_db_config(db_name)
    if stby_cfg:
        status_data["standby_status"] = "PENDING"
        status_data["standby_configured"] = True
    else:
        status_data["standby_status"] = "NOT_CONFIGURED"
        status_data["standby_configured"] = False

    # Precompute tooltip reasons
    reasons = []
    if status_data["db"] == "DOWN":
        reasons.append("Database is DOWN")
    if status_data["listener"] == "DOWN":
        reasons.append("Listener is DOWN")
    for ts_name in status_data.get("full_ts", []):
        ts_entry = next((t for t in status_data["balance_ts"] if t["name"] == ts_name), None)
        pct_str = f" ({ts_entry['pct']:.1f}%)" if ts_entry else ""
        reasons.append(f"Tablespace {ts_name} is{pct_str} utilized (≥90%)")
    if status_data["backup"] == "FAILED":
        reasons.append("Last RMAN backup FAILED")
    status_data["tooltip_reasons"] = reasons

    return status_data


def fetch_host_top_processes(host, ssh_meta):
    """
    Server-wide top CPU/Memory processes for `host`, trying SSH first (via
    the bundled/configured key in ssh_meta) and falling back to a DB-only
    query (v$session, genuine live %CPU) against ssh_meta["active_db"] when
    no SSH key is configured or the SSH attempt comes back empty — so a
    host without SSH access still shows real numbers instead of nothing.
    """
    should_ssh = ssh_meta.get("should_ssh")
    active_db  = ssh_meta.get("active_db")

    if should_ssh:
        try:
            from utils.ssh_process_provider import get_top_processes_for_host
            proc_res = get_top_processes_for_host(
                host=host,
                username=ssh_meta["h_user"],
                limit=10,
                key_filename=ssh_meta["key_file_to_use"],
            )
        except Exception as e:
            proc_res = {"top_cpu": [], "top_mem": [], "source": "none", "error": str(e)}

        if proc_res.get("top_cpu") or proc_res.get("top_mem"):
            return proc_res
        # SSH configured but came back empty/erroring — fall through to the
        # DB-only fallback below rather than showing nothing.
    else:
        proc_res = {
            "top_cpu": [], "top_mem": [], "source": "none",
            "error": "No OCI key found in the keys/ folder for this host.",
        }

    if active_db:
        try:
            from utils.ssh_process_provider import get_top_processes_via_db_only
            db_res = get_top_processes_via_db_only(active_db, limit=10)
            if db_res.get("top_cpu") or db_res.get("top_mem"):
                return db_res
        except Exception:
            pass

    return proc_res


def load_server_status_summary(host, db_names_on_host, status_cache):
    """
    Query host server resources (CPU, Memory, Sessions),
    top processes, and mount points in sequence. Queries ONCE per host.
    """
    active_db = None
    # Use the passed-in status_cache dictionary to ensure thread-safety
    for db in db_names_on_host:
        stats = status_cache.get(db)
        if stats and stats.get("db") == "UP":
            active_db = db
            break

    server_info = {
        "system_res": None,
        "volumes": [],
        "mount_error": None,
        "processes": {"top_cpu": [], "top_mem": [], "source": "none", "error": None},
        "asm_diskgroups": {"diskgroups": [], "error": None},
    }

    if not active_db:
        server_info["mount_error"] = "All databases on this host are DOWN."
        return server_info

    conn = None
    try:
        from db_connection import get_api_connection, get_config_for_db
        import os

        conn, _ = get_api_connection(active_db)
        if conn:
            # 1. First, retrieve host server by oracle (DB-level resource metrics)
            from queries.queries import get_system_resources
            db_res = get_system_resources(conn=conn)

            # Check if there is an OCI Private Key (.pem) available
            from db_connection import get_bundled_oci_key
            cfg = get_config_for_db(active_db) or {}
            h_user = cfg.get("host_username", "").strip() or cfg.get("user", "").strip() or "opc"
            ssh_key_path = cfg.get("ssh_key_path") or cfg.get("oci_key_path") or cfg.get("key_filename")

            # Resolve which SSH key to use, in priority order:
            #   1. Bundled OCI key shipped with the app (keys/ folder).
            #   2. A key path configured directly in the registry file.
            bundled_key_path = get_bundled_oci_key(host)

            key_file_to_use = None
            if bundled_key_path and os.path.exists(bundled_key_path):
                key_file_to_use = bundled_key_path
            elif ssh_key_path and os.path.exists(ssh_key_path):
                key_file_to_use = ssh_key_path

            should_ssh = bool(key_file_to_use)

            # Remember how to reach this host over SSH so the top-process
            # table (rendered separately below) can re-fetch fresh data on
            # every 30-second fragment refresh without recomputing this.
            server_info["ssh_meta"] = {
                "h_user": h_user,
                "key_file_to_use": key_file_to_use,
                "should_ssh": should_ssh,
                "active_db": active_db,
            }

            # 2. Retrieve host server by OS (via SSH resources query)
            ssh_res = None
            if should_ssh:
                try:
                    from utils.ssh_resource_provider import get_system_resources_via_ssh
                    ssh_res = get_system_resources_via_ssh(
                        host=host,
                        username=h_user,
                        key_filename=key_file_to_use
                    )
                except Exception as se:
                    print(f"[fetch_server] SSH resources fetch failed for {host}: {se}")

            # Populate system resources
            if ssh_res:
                server_info["system_res"] = {
                    "num_cpus":            ssh_res["num_cpus"],
                    "cpu_host_used_pct":   ssh_res["cpu_host_used_pct"],
                    "cpu_host_free_pct":   ssh_res["cpu_host_free_pct"],
                    "cpu_oracle_used_pct": db_res.get("cpu_oracle_used_pct", 0.0),
                    "ram_total_gb":        ssh_res["ram_total_gb"],
                    "ram_used_gb":         ssh_res["ram_used_gb"],
                    "ram_free_gb":         ssh_res["ram_free_gb"],
                    "ram_oracle_gb":       db_res.get("ram_oracle_gb", 0.0),
                    "session_count":       db_res.get("session_count", 0)
                }
            else:
                server_info["system_res"] = db_res

            # 3. Retrieve the top cpu and ram consuming details (SSH first,
            #    falls back to a DB-only query when no SSH key is available)
            try:
                server_info["processes"] = fetch_host_top_processes(host, server_info["ssh_meta"])
            except Exception as e:
                server_info["processes"] = {
                    "top_cpu": [],
                    "top_mem": [],
                    "source": "none",
                    "error": str(e)
                }

            # 4. Retrieve ASM diskgroup usage — SSH into this host as
            #    "opc" (the cloud host's admin login), then `sudo su -
            #    grid` and query v$asm_diskgroup from there as the Grid
            #    Infrastructure OS user (OS-authenticated SYSASM, no
            #    separate Oracle password needed) — see
            #    utils/asm_provider.py for why this is a two-step hop
            #    rather than SSHing directly as grid. Reuses this host's
            #    already-configured host_password for the opc login —
            #    never hard-coded. Home portal page only; rendered just
            #    above the mount points section below.
            # Pass the same bundled SSH key resolved above too (not
            # password-only): "opc" is frequently only reachable via
            # key-based login at all (password SSH disabled server-side),
            # and it's the SAME account Mount Points/top processes already
            # connect with successfully using this exact key. asm_provider
            # tries the key first and falls back to the password only if
            # the key isn't accepted for opc specifically — never silently
            # skips the password.
            try:
                from utils.asm_provider import get_asm_diskgroup_info
                grid_password = (cfg.get("host_password") or "").strip()
                server_info["asm_diskgroups"] = get_asm_diskgroup_info(
                    host=host,
                    password=grid_password,
                    key_filename=key_file_to_use,
                )
            except Exception as e:
                server_info["asm_diskgroups"] = {"diskgroups": [], "error": str(e)}

            # 4. See the mount point configuration and retrieval process
            from utils.storage_provider import get_storage_provider
            try:
                storage_provider = get_storage_provider(conn)
                all_vols = storage_provider.get_storage_info()
                if all_vols and isinstance(all_vols[0], dict) and "ssh_error" in all_vols[0]:
                    server_info["mount_error"] = all_vols[0]["ssh_error"]
                else:
                    server_info["volumes"] = [
                        v for v in all_vols
                        if v.get("total", 0) > 0 and v.get("fs_type", "") not in ("tmpfs", "devtmpfs", "proc", "sysfs")
                    ]
            except Exception as e:
                print(f"[fetch_server] Storage info error for {host}: {e}")

            # Fallback SSH mount query if volumes list is empty and we had an OCI key
            if not server_info["volumes"] and should_ssh:
                try:
                    from utils.ssh_mount_provider import get_mounts_via_ssh
                    ssh_result = get_mounts_via_ssh(
                        host=host,
                        username=h_user,
                        key_filename=key_file_to_use
                    )
                    if ssh_result.get("volumes"):
                        server_info["volumes"] = ssh_result["volumes"]
                        server_info["mount_error"] = None
                    elif ssh_result.get("error"):
                        server_info["mount_error"] = f"SSH Error: {ssh_result['error']}"
                except Exception as e:
                    server_info["mount_error"] = f"SSH Error: {e}"

            conn.close()
    except Exception as e:
        server_info["mount_error"] = f"Connection error: {e}"
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

    return server_info

def determine_health_category(stats):
    """Categorizes database health: Healthy, Warning, or Critical based ONLY on database metrics."""
    db_val = stats.get("db", "UNKNOWN")
    if db_val == "UNKNOWN":
        return "Unknown"
    if db_val == "DOWN" or stats.get("listener") == "DOWN" or stats.get("backup") == "FAILED" or stats.get("log_gap_alert"):
        return "Critical"
    if (
        stats.get("backup") in ["PENDING", "WARNING"] or
        stats.get("full_ts") or
        stats.get("has_blocking") or
        stats.get("deadlock_detected") or
        stats.get("session_maxed") or
        stats.get("alert_log_error")
    ):
        return "Warning"
    return "Healthy"

def build_db_card_html(db, stats, host_name, is_active, dot_colors):
    cat = determine_health_category(stats).lower()
    
    def _status_dot(val, ok="UP", fail="DOWN"):
        if val == ok:   return "dot-green",  "val-green"
        if val == fail: return "dot-red",    "val-red"
        return           "dot-amber",  "val-amber"

    db_dot,   db_val   = _status_dot(stats.get("db", "UNKNOWN"))
    lsnr_dot, lsnr_val = _status_dot(stats.get("listener", "UNKNOWN"))
    bkp_ok   = stats.get("backup") == "SUCCESS"
    bkp_fail = stats.get("backup") == "FAILED"
    bkp_dot  = "dot-green" if bkp_ok else ("dot-red" if bkp_fail else "dot-amber")
    bkp_val  = "val-green" if bkp_ok else ("val-red" if bkp_fail else "val-amber")
    
    # Tablespace check
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

    # Tooltip Reasons
    tooltip_reasons = list(stats.get("tooltip_reasons", []))
    if not tooltip_reasons and cat in ("warning", "critical"):
        if stats.get("db") == "DOWN":
            tooltip_reasons.append("Database is DOWN")
        if stats.get("listener") == "DOWN":
            tooltip_reasons.append("Listener is DOWN")
        if stats.get("backup") == "FAILED":
            tooltip_reasons.append("Last RMAN backup FAILED")
        for q_ts in qualifying_ts:
            tooltip_reasons.append(f"Tablespace {q_ts['name']} is {q_ts['pct']:.1f}% utilized (≥90%)")

    if tooltip_reasons:
        tooltip_items = "".join([f"<li>{r}</li>" for r in tooltip_reasons])
        tooltip_html = f"<b>System Diagnostics:</b><ul style='margin:4px 0 0 12px;padding:0;'>{tooltip_items}</ul>"
        dot_color = "var(--rose)" if cat == "critical" else "var(--amber)"
        dot_anim = "animation: pulse-ring 1.5s cubic-bezier(0.215, 0.610, 0.355, 1) infinite;"
        card_status_class = f"db-card-{cat}"
    else:
        dot_color  = dot_colors.get(cat, "#10b981")
        dot_anim   = ""
        card_status_class = f"db-card-{cat}"
        if cat == "unknown":
            tooltip_html = "<b>Status: Unknown</b><br>Fetching database health metrics..."
        else:
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
    
    mount_err = stats.get("mount_error", "")
    if mount_err and any(term in mount_err.lower() for term in ["authentication failed", "wrong username", "permission denied", "auth failed"]):
        error_html += '<div class="db-error-msg" style="color:#ef4444; font-weight:800; margin-top:4px;">❌ host username and password is wrong</div>'

    # Reporting DB section
    rpt_status     = stats.get("reporting_status", "NOT_CONFIGURED")
    rpt_db_name    = stats.get("reporting_db_name", "")
    rpt_configured = stats.get("reporting_configured", False)
    rpt_resp_time  = stats.get("reporting_response_time", 0)

    from db_connection import get_reporting_db_config
    rpt_cfg = get_reporting_db_config(db)
    if rpt_cfg:
        rpt_db_name = rpt_cfg.get("db_name", rpt_db_name)
        rpt_configured = True

    if rpt_configured and rpt_db_name:
        if rpt_status == "UP":
            rpt_dot          = "dot-green"
            rpt_status_label = "UP"
            rpt_status_color = "#10b981"
            rpt_badge_bg     = "rgba(16,185,129,0.10)"
            rpt_badge_border = "rgba(16,185,129,0.35)"
        else:
            rpt_dot          = "dot-red"
            rpt_status_label = "issue check it"
            rpt_status_color = "#ef4444"
            rpt_badge_bg     = "rgba(239,68,68,0.10)"
            rpt_badge_border = "rgba(239,68,68,0.35)"
        
        rpt_box_html = (
            f'<div style="background:{rpt_badge_bg}; border:1px solid {rpt_badge_border}; border-radius:6px; '
            f'padding:4px 6px; display:flex; flex-direction:column; justify-content:center; height:32px;">'
            f'<div style="display:flex; align-items:center; justify-content:space-between; width:100%;">'
            f'<span style="font-size:0.62rem; font-weight:700; color:var(--text-primary); white-space:nowrap; '
            f'overflow:hidden; text-overflow:ellipsis; max-width:65%;" title="{rpt_db_name}">{rpt_db_name}</span>'
            f'<div style="display:flex; align-items:center; gap:2px; flex-shrink:0;">'
            f'<span class="{rpt_dot}"></span>'
            f'<span style="font-size:0.6rem; font-weight:700; color:{rpt_status_color};">{rpt_status_label}</span>'
            f'</div></div></div>'
        )
    else:
        # No usable reporting config for this DB — either its row is missing
        # from the registry, reporting_db_name is blank/"nc", or one of the
        # other required reporting_* columns is missing.
        rpt_box_html = (
            '<div style="font-size:0.55rem; color:var(--text-secondary); line-height:1.2; '
            'padding:4px 6px; background:rgba(128,128,128,0.06); border-radius:5px; height:32px; '
            'display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;" '
            'title="No reporting DB configuration found for this database.">'
            '<span style="font-style:italic;">No data found</span>'
            '<span style="font-weight:800; color:var(--text-primary); font-size:0.62rem; letter-spacing:0.03em;">NONE</span>'
            '</div>'
        )

    # Standby DB section
    from db_connection import get_standby_db_config
    stby_cfg = get_standby_db_config(db)
    
    if stby_cfg:
        stby_db_name = stby_cfg.get("db_name", "")
        
        # Check if there is an archive destination error on the STANDBY
        # destination row specifically (dest_id=2) - a dest_id=1 (primary's
        # own local archive dest) error isn't a standby-sync problem.
        dest_error = ""
        for d in stats.get("standby_dests", []):
            if d.get("role") == "standby" and d.get("error"):
                dest_error = d.get("error")
                break
                
        gap_info = stats.get("standby_log_gap", {})
        stby_status_raw = str(stats.get("standby_status") or "").upper()
        stby_error = (gap_info.get("error") if isinstance(gap_info, dict) else "") or stats.get("standby_error") or ""
        
        if dest_error:
            stby_box_html = (
                f'<div style="font-size:0.54rem; color:#ef4444; font-weight:700; padding:4px 6px; '
                f'background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.2); border-radius:6px; '
                f'height:32px; display:flex; align-items:center; justify-content:center; text-align:center; '
                f'line-height:1.1;" title="{dest_error}">⚠️ {dest_error[:18]}...</div>'
            )
        elif stby_error or "DOWN" in stby_status_raw or "FAIL" in stby_status_raw or "TIMED OUT" in stby_status_raw or "UNKNOWN" in stby_status_raw:
            err_title = stby_error or stby_status_raw or "Standby Error"
            stby_box_html = (
                f'<div style="font-size:0.54rem; color:#ef4444; font-weight:700; padding:4px 6px; '
                f'background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.2); border-radius:6px; '
                f'height:32px; display:flex; align-items:center; justify-content:center; text-align:center; '
                f'line-height:1.1;" title="{err_title}">🔴 DOWN / ERROR</div>'
            )
        elif gap_info and isinstance(gap_info, dict) and gap_info.get("configured"):
            max_g = gap_info.get("max_gap", 0)
            # synchronized if sequence number difference is 0 or 1
            if max_g >= 2:
                stby_status_label = "NOT-SYNC"
                stby_status_color = "#ef4444"
                stby_bg = "rgba(239,68,68,0.10)"
                stby_border = "rgba(239,68,68,0.35)"
                stby_icon = "🔴"
            else:
                stby_status_label = "SYNC"
                stby_status_color = "#10b981"
                stby_bg = "rgba(16,185,129,0.10)"
                stby_border = "rgba(16,185,129,0.35)"
                stby_icon = "✅"
            
            _thread_lines = "\n".join(
                f"Thread {t.get('thread')}: Generated={t.get('primary_generated')} "
                f"Received={t.get('standby_received')} Applied={t.get('standby_applied')} Gap={t.get('gap')}"
                for t in gap_info.get("threads", [])
            )
            _dest_lines = "\n".join(
                f"Dest {d.get('dest_id')} ({d.get('role') or '?'}) {d.get('dest_name','')}: "
                f"Status={d.get('status','')} Sync={d.get('synchronization_status','')} "
                f"DB_Unique_Name={d.get('db_unique_name','')}"
                + (f" | ERROR: {d.get('error')}" if d.get("error") else "")
                for d in stats.get("standby_dests", [])
            )
            full_status_title = (
                f"Standby DB: {stby_db_name}\n"
                f"Target: {stby_cfg.get('host','')}:{stby_cfg.get('port','')}/{stby_cfg.get('service_name','')}\n"
                f"Status: {stby_status_label} (Max Gap: {max_g} sequences)\n"
                f"{_thread_lines}"
                + (f"\n{_dest_lines}" if _dest_lines else "")
            ).replace('"', "'")

            stby_box_html = (
                f'<div title="{full_status_title}" style="background:{stby_bg}; border:1px solid {stby_border}; border-radius:6px; '
                f'padding:4px 6px; display:flex; flex-direction:column; justify-content:center; height:32px;">'
                f'<div style="display:flex; align-items:center; justify-content:space-between; width:100%;">'
                f'<span style="font-size:0.62rem; font-weight:700; color:var(--text-primary); white-space:nowrap; '
                f'overflow:hidden; text-overflow:ellipsis; max-width:50%;">{stby_db_name}</span>'
                f'<div style="display:flex; align-items:center; gap:1px; flex-shrink:0;">'
                f'<span style="font-size:0.56rem; font-weight:800; color:{stby_status_color}; '
                f'white-space:nowrap;">{stby_status_label} ({max_g}){stby_icon}</span>'
                f'</div></div></div>'
            )
        else:
            stby_box_html = (
                '<div style="font-size:0.55rem; color:var(--text-secondary); font-style:italic; '
                'padding:4px 6px; background:rgba(128,128,128,0.06); border-radius:5px; height:32px; '
                'display:flex; align-items:center; justify-content:center; text-align:center;">'
                'Connecting...</div>'
            )
    else:
        # No usable standby config for this DB — either its row is missing
        # from the registry entirely, or the row exists but standby_host /
        # standby_username aren't filled in (get_standby_db_config returns
        # None in both cases).
        stby_box_html = (
            '<div style="font-size:0.5rem; color:var(--text-secondary); line-height:1.2; '
            'padding:4px 6px; background:rgba(128,128,128,0.04); border-radius:5px; height:32px; '
            'display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;" '
            'title="No standby/DR configuration found for this database.">'
            '<span style="font-style:italic;">No data found</span>'
            '<span style="font-weight:800; color:var(--text-primary); font-size:0.62rem; letter-spacing:0.03em;">NONE</span>'
            '</div>'
        )

    # Combined Reporting & Standby Section HTML
    reporting_standby_section_html = (
        f'<div style="margin-top:8px; border-top: 1.5px solid var(--border-color); padding-top:7px;">'
        f'<div style="display:flex; gap:6px; width:100%;">'
        f'<div style="flex:1; min-width:0;">'
        f'<div style="font-size:0.56rem; font-weight:700; color:var(--text-secondary); '
        f'text-transform:uppercase; letter-spacing:0.04em; margin-bottom:3px;">📊 Reporting</div>'
        f'{rpt_box_html}'
        f'</div>'
        f'<div style="flex:1; min-width:0;">'
        f'<div style="font-size:0.56rem; font-weight:700; color:var(--text-secondary); '
        f'text-transform:uppercase; letter-spacing:0.04em; margin-bottom:3px;">📡 Standby</div>'
        f'{stby_box_html}'
        f'</div>'
        f'</div></div>'
    )

    card_html = (
        f'<a href="?selected_db={db}" target="_self" style="text-decoration:none; color:inherit; display:block;">'
        f'<div class="{card_class}">'
        f'<div class="db-card-header">'
        f'<div class="db-card-name" title="{db}">{db}</div>'
        f'{health_dot_html}</div>'
        f'<hr class="db-divider" style="margin: 6px 0;">'
        f'<div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:4px; font-weight:700;">🖥️ Host: {host_name}</div>'
        f'<div class="db-status-row">'
        f'<span class="db-status-label"><span class="{db_dot}"></span> Database</span>'
        f'<span class="{db_val}" style="font-size:1.1rem; line-height:0.8;">{"⇧" if stats.get("db") == "UP" else ("⇩" if stats.get("db") == "DOWN" else "▵")}</span></div>'
        f'<div class="db-status-row">'
        f'<span class="db-status-label"><span class="{lsnr_dot}"></span> Listener</span>'
        f'<span class="{lsnr_val}" style="font-size:1.1rem; line-height:0.8;">{"⇧" if stats.get("listener") == "UP" else ("⇩" if stats.get("listener") == "DOWN" else "▵")}</span></div>'
        f'<div class="db-status-row">'
        f'<span class="db-status-label"><span class="{bkp_dot}"></span> Backup</span>'
        f'<span class="{bkp_val}">{stats.get("backup", "UNKNOWN")}</span></div>'
        f'<div class="db-status-row" style="align-items: flex-start;">'
        f'<span class="db-status-label"><span class="{ts_dot}"></span> Tablespace</span>'
        f'<span class="{ts_val_class}">{ts_text}</span></div>'
        f'<div class="db-status-row">'
        f'<span class="db-status-label">👥 Active Sessions</span>'
        f'<span style="font-size:0.75rem; font-weight:700; color:var(--text-primary);">{stats.get("active_sessions", 0)}</span></div>'
        f'{error_html}'
        f'{reporting_standby_section_html}'
        f'</div>'
        f'</a>'
    )
    return card_html

def build_db_card_loading_html(db, host_name):
    card_html = (
        f'<div class="db-card db-card-unknown">'
        f'<div class="db-card-header">'
        f'<div class="db-card-name" title="{db}">{db}</div>'
        f'<div class="health-dot-wrapper"><div style="width:12px;height:12px;border-radius:50%;background:#6b7280;flex-shrink:0;"></div></div>'
        f'</div>'
        f'<hr class="db-divider" style="margin: 6px 0;">'
        f'<div style="font-size:0.65rem; color:var(--text-secondary); margin-bottom:4px; font-weight:700;">🖥️ Host: {host_name}</div>'
        f'<div style="padding: 24px 0; text-align: center; font-size: 0.72rem; color: var(--text-secondary); font-style: italic;">'
        f'<div style="display:inline-block; width:12px; height:12px; border:2px solid rgba(59,130,246,0.1); border-top-color:#3b82f6; border-radius:50%; animation: spin-loader 1s linear infinite; margin-right:6px; vertical-align:middle;"></div>'
        f'Connecting...</div>'
        f'</div>'
    )
    return card_html


@st.fragment(run_every=30)
def render_home_dashboard_fragment(db_names, search_query, _selected, sort_order):
    import time
    import os
    import json
    import pandas as pd
    from db_connection import get_config_for_db, get_reporting_db_config, check_reporting_db_status

    # 1. Initialize caches in session state if not present
    if "status_cache" not in st.session_state:
        st.session_state.status_cache = {}
    if "server_cache" not in st.session_state:
        st.session_state.server_cache = {}

    dot_colors = {"healthy": "#10b981", "warning": "#f59e0b", "critical": "#ef4444", "unknown": "#6b7280"}

    # Ensures status_cache/server_cache exist in session state. Does NOT
    # seed them from disk (see seed_session_cache_from_file's docstring) —
    # every DB/host below is always fetched live so this page reflects the
    # CURRENT registry file, not a possibly-stale background snapshot.
    seed_session_cache_from_file()

    # Filter caches to include only databases present in the current config file
    st.session_state.status_cache = {
        db: stats for db, stats in st.session_state.status_cache.items()
        if db in db_names
    }

    # Build host mapping
    host_map = {}  # host -> [db_name, ...]
    for db in db_names:
        cfg = get_config_for_db(db)
        host = cfg["host"] if cfg else "unknown"
        host_map.setdefault(host, []).append(db)

    # Filter server cache to include only hosts present in current config file
    active_hosts = set(host_map.keys())
    st.session_state.server_cache = {
        host: info for host, info in st.session_state.server_cache.items()
        if host in active_hosts
    }

    # Determine missing database status values
    missing_dbs = [db for db in db_names if db not in st.session_state.status_cache]

    # Pre-render placeholders in alphabetical grid order
    db_list_sorted_temp = sorted(db_names, key=lambda x: x.lower())
    cols_per_row = 4
    card_placeholders = {}

    if missing_dbs:
        st.write("### 🗄️ Database Instances")
        for i in range(0, len(db_list_sorted_temp), cols_per_row):
            row_items = db_list_sorted_temp[i:i+cols_per_row]
            cols = st.columns(cols_per_row)
            for idx, db in enumerate(row_items):
                cfg = get_config_for_db(db)
                host_name = cfg["host"] if cfg else "unknown"
                with cols[idx]:
                    card_placeholders[db] = st.empty()
                    if db in st.session_state.status_cache:
                        stats = st.session_state.status_cache[db]
                        is_active = (st.session_state.selected_chk_db == db)
                        card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                    else:
                        card_html = build_db_card_loading_html(db, host_name)
                    card_placeholders[db].markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

        # Run ThreadPoolExecutor to query basic status of missing DBs in parallel (30 concurrent max)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(missing_dbs), 30)) as executor:
            future_to_db = {
                executor.submit(load_db_status_summary_basic, db): db
                for db in missing_dbs
            }
            for future in as_completed(future_to_db):
                db = future_to_db[future]
                cfg = get_config_for_db(db)
                host_name = cfg["host"] if cfg else "unknown"
                try:
                    stats = future.result()
                except Exception as e:
                    stats = {
                        "db": "DOWN", "listener": "DOWN", "backup": "UNKNOWN",
                        "active_sessions": 0, "balance_ts": [], "full_ts": [],
                        "error": str(e), "tooltip_reasons": [str(e)],
                        "standby_status": "NOT_CONFIGURED", "standby_configured": False
                    }

                # Set reporting status from config check only (no subprocess - fast)
                from db_connection import get_reporting_db_config
                rpt_cfg = get_reporting_db_config(db)
                if rpt_cfg:
                    stats["reporting_status"]        = "PENDING"  # Mark as pending reporting query
                    stats["reporting_listener"]      = "NOT_CONFIGURED"
                    stats["reporting_db_name"]       = rpt_cfg.get("db_name", "")
                    stats["reporting_configured"]    = True
                    stats["reporting_response_time"] = 0
                else:
                    stats["reporting_status"]        = "NOT_CONFIGURED"
                    stats["reporting_listener"]      = "NOT_CONFIGURED"
                    stats["reporting_db_name"]       = ""
                    stats["reporting_configured"]    = False
                    stats["reporting_response_time"] = 0

                st.session_state.status_cache[db] = stats
                is_active = (st.session_state.selected_chk_db == db)
                card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                card_placeholders[db].markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
        st.rerun()

    # Determine databases needing reporting DB check
    pending_rpt_dbs = [
        db for db in db_names 
        if db in st.session_state.status_cache 
        and st.session_state.status_cache[db].get("reporting_status") == "PENDING"
    ]

    if pending_rpt_dbs:
        # Pre-render placeholders in alphabetical grid order
        st.write("### 🗄️ Database Instances")
        for i in range(0, len(db_list_sorted_temp), cols_per_row):
            row_items = db_list_sorted_temp[i:i+cols_per_row]
            cols = st.columns(cols_per_row)
            for idx, db in enumerate(row_items):
                cfg = get_config_for_db(db)
                host_name = cfg["host"] if cfg else "unknown"
                with cols[idx]:
                    card_placeholders[db] = st.empty()
                    if db in st.session_state.status_cache:
                        stats = st.session_state.status_cache[db]
                        is_active = (st.session_state.selected_chk_db == db)
                        card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                    else:
                        card_html = build_db_card_loading_html(db, host_name)
                    card_placeholders[db].markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

        # Run ThreadPoolExecutor for reporting DB checks (max 5 parallel to avoid Windows process overload)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from db_connection import check_reporting_db_status
        
        with ThreadPoolExecutor(max_workers=min(len(pending_rpt_dbs), 5)) as executor:
            future_to_db = {
                executor.submit(check_reporting_db_status, db): db
                for db in pending_rpt_dbs
            }
            for future in as_completed(future_to_db):
                db = future_to_db[future]
                cfg = get_config_for_db(db)
                host_name = cfg["host"] if cfg else "unknown"
                try:
                    rpt_result = future.result()
                except Exception:
                    rpt_result = {"configured": True, "status": "DOWN"}
                
                if db in st.session_state.status_cache:
                    stats = st.session_state.status_cache[db]
                    stats["reporting_status"]         = rpt_result.get("status", "NOT_CONFIGURED")
                    stats["reporting_listener"]       = rpt_result.get("listener", "NOT_CONFIGURED")
                    stats["reporting_db_name"]        = rpt_result.get("reporting_db_name", "")
                    stats["reporting_configured"]     = rpt_result.get("configured", False)
                    stats["reporting_error"]          = rpt_result.get("error", "")
                    stats["reporting_response_time"]  = rpt_result.get("response_time_ms", 0)
                    st.session_state.status_cache[db] = stats
                    
                    is_active = (st.session_state.selected_chk_db == db)
                    card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                    card_placeholders[db].markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
        st.rerun()

    # Determine databases needing standby check
    pending_stby_dbs = [
        db for db in db_names 
        if db in st.session_state.status_cache 
        and st.session_state.status_cache[db].get("standby_status") == "PENDING"
    ]

    if pending_stby_dbs:
        # Pre-render placeholders in alphabetical grid order
        st.write("### 🗄️ Database Instances")
        for i in range(0, len(db_list_sorted_temp), cols_per_row):
            row_items = db_list_sorted_temp[i:i+cols_per_row]
            cols = st.columns(cols_per_row)
            for idx, db in enumerate(row_items):
                cfg = get_config_for_db(db)
                host_name = cfg["host"] if cfg else "unknown"
                with cols[idx]:
                    card_placeholders[db] = st.empty()
                    if db in st.session_state.status_cache:
                        stats = st.session_state.status_cache[db]
                        is_active = (st.session_state.selected_chk_db == db)
                        card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                    else:
                        card_html = build_db_card_loading_html(db, host_name)
                    card_placeholders[db].markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

        # Run ThreadPoolExecutor for standby DB checks (max 5 parallel)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(pending_stby_dbs), 5)) as executor:
            future_to_db = {
                executor.submit(check_standby_db_status_live, db): db
                for db in pending_stby_dbs
            }
            for future in as_completed(future_to_db):
                db = future_to_db[future]
                cfg = get_config_for_db(db)
                host_name = cfg["host"] if cfg else "unknown"
                try:
                    stby_res = future.result()
                except Exception as ex:
                    stby_res = {"configured": False, "status": "DOWN", "max_gap": 0, "error": str(ex)}
                
                if db in st.session_state.status_cache:
                    stats = st.session_state.status_cache[db]
                    is_cfg = stby_res.get("configured", False)
                    err_msg = stby_res.get("error", "")
                    
                    stats["standby_status"] = stby_res.get("status", "DOWN")
                    stats["standby_configured"] = is_cfg
                    stats["standby_error"] = err_msg
                    stats["standby_log_gap"] = {
                        "configured": is_cfg,
                        "max_gap": stby_res.get("max_gap", 0),
                        "has_gap": stby_res.get("max_gap", 0) >= 2,
                        "error": err_msg,
                        "threads": []
                    }
                    st.session_state.status_cache[db] = stats
                    
                    is_active = (st.session_state.selected_chk_db == db)
                    card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                    card_placeholders[db].markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
        st.rerun()

    # Load and cache status summaries for KPI calculations
    db_list_data = []
    total_count = len(db_names)
    healthy_count = 0
    warning_count = 0
    critical_count = 0

    for db in db_names:
        stats = st.session_state.status_cache[db]
        cat = determine_health_category(stats)
        if cat == "Healthy":
            healthy_count += 1
        elif cat == "Warning":
            warning_count += 1
        elif cat == "Critical":
            critical_count += 1

        cfg = get_config_for_db(db)
        db_list_data.append({
            "name": db,
            "status_info": stats,
            "category": cat,
            "host": cfg["host"] if cfg else "unknown"
        })

    # Executive Summary Ribbon
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

    # Apply Search Filter
    filtered_data = db_list_data
    if search_query:
        filtered_data = [x for x in filtered_data if search_query.lower() in x["name"].lower()]

    # Apply Health dropdown filter
    _show_all = ("All Statuses" in _selected) or not _selected
    if not _show_all:
        _allowed = set()
        if "🟢 Healthy"  in _selected: _allowed.add("Healthy")
        if "🟡 Warning"  in _selected: _allowed.add("Warning")
        if "🔴 Critical" in _selected: _allowed.add("Critical")
        filtered_data = [x for x in filtered_data if x["category"] in _allowed]

    # Sorting
    rev_sort = (sort_order == "Sort Z-A")
    priority_map = {"Critical": 0, "Warning": 1, "Healthy": 2, "Unknown": 3}
    db_list_data_sorted = sorted(filtered_data, key=lambda x: (priority_map.get(x["category"], 3), x["name"].lower()), reverse=False)
    if rev_sort:
        db_list_data_sorted = sorted(filtered_data, key=lambda x: (priority_map.get(x["category"], 3), x["name"].lower()), reverse=True)

    # Render DB cards sorted & filtered
    st.write("### 🗄️ Database Instances")
    if not db_list_data_sorted:
        st.info("No databases match the selected filters.")
    else:
        for i in range(0, len(db_list_data_sorted), cols_per_row):
            row_items = db_list_data_sorted[i:i+cols_per_row]
            cols = st.columns(cols_per_row)
            for idx, item in enumerate(row_items):
                db    = item["name"]
                stats = item["status_info"]
                host_name = item["host"]
                is_active = (st.session_state.selected_chk_db == db)
                card_html = build_db_card_html(db, stats, host_name, is_active, dot_colors)
                with cols[idx]:
                    st.markdown(f'<div class="db-card-wrapper">{card_html}</div>', unsafe_allow_html=True)
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)

    # Group server resources by host and render below all database cards
    st.markdown("<hr style='margin:35px 0; border-color:var(--border-color);'>", unsafe_allow_html=True)
    st.write("### 🖥️ Host Server Resources & Diagnostics")

    # Build unique hosts list sorted by severity
    host_priorities = {}
    for item in db_list_data:
        h = item["host"]
        prio = priority_map.get(item["category"], 2)
        if h not in host_priorities or prio < host_priorities[h]:
            host_priorities[h] = prio
    sorted_hosts = sorted(host_priorities.keys(), key=lambda h: (host_priorities[h], h.lower()))

    # Rendering function for server details panel
    def render_server_details_for_host(host, server_info, host_dbs):
        sys_res = server_info.get("system_res")
        real_vols = server_info.get("volumes", [])
        _mount_error = server_info.get("mount_error")

        # Top processes are re-fetched on every call (this whole fragment
        # re-runs every 30s) so the server-wide top CPU/MEM tables stay
        # live — SSH first, DB-only fallback if no key is available. The
        # SSH fetch itself is throttled to 30s process-wide by
        # ssh_process_provider's cache, so repeated 30s polls (and other
        # hosts/sessions hitting the same host) don't hammer SSH.
        ssh_meta = server_info.get("ssh_meta", {})
        try:
            proc_res = fetch_host_top_processes(host, ssh_meta)
        except Exception as e:
            proc_res = {"top_cpu": [], "top_mem": [], "source": "none", "error": str(e)}
        server_info["processes"] = proc_res

        if sys_res:
            cpu_used   = sys_res.get("cpu_host_used_pct", 0)
            cpu_free   = sys_res.get("cpu_host_free_pct", 0)
            ram_total  = sys_res.get("ram_total_gb", 0)
            ram_used   = sys_res.get("ram_used_gb", 0)
            ram_free   = sys_res.get("ram_free_gb", 0)
            ram_oracle = sys_res.get("ram_oracle_gb", 0)
            cpu_oracle = sys_res.get("cpu_oracle_used_pct", 0)
            num_cpus   = sys_res.get("num_cpus", 1)
            sess_count = sys_res.get("session_count", 0)

            # OS Colors
            cpu_os_color = "#ef4444" if cpu_used > 85 else ("#f59e0b" if cpu_used > 70 else "#10b981")
            ram_pct      = (ram_used / ram_total * 100) if ram_total > 0 else 0
            ram_os_color = "#ef4444" if ram_pct > 85 else ("#f59e0b" if ram_pct > 70 else "#10b981")

            # Oracle Colors
            cpu_oracle_color = "#ef4444" if cpu_oracle > 85 else ("#f59e0b" if cpu_oracle > 70 else "#3b82f6")
            ram_oracle_pct   = (ram_oracle / ram_total * 100) if ram_total > 0 else 0
            ram_oracle_color = "#ef4444" if ram_oracle_pct > 85 else ("#f59e0b" if ram_oracle_pct > 70 else "#8b5cf6")

            def _pct_bar(pct, color):
                w = min(pct, 100)
                return (
                    f'<div style="background:rgba(128,128,128,0.15);border-radius:6px;height:10px;'
                    f'overflow:hidden;position:relative;width:100%;">'
                    f'<div style="position:absolute;left:0;top:0;height:100%;width:{w:.1f}%;'
                    f'background:linear-gradient(90deg,{color}cc,{color});border-radius:6px;"></div></div>'
                )

            # Premium Cards layout: Oracle Card followed by OS Card
            st.markdown(f"""
<!-- Card 1: Host Server by Oracle -->
<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:12px;
     padding:16px 20px;margin-bottom:12px;border-left:4px solid #3b82f6;">
  <div style="font-size:0.8rem;font-weight:800;color:#3b82f6;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;">
    🔷 Host Server Resources by Oracle
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-size:0.75rem;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;">⚡ Oracle CPU Workload</span>
        <span style="font-size:1.05rem;font-weight:800;color:{cpu_oracle_color};">{cpu_oracle:.1f}%</span>
      </div>
      {_pct_bar(cpu_oracle, cpu_oracle_color)}
      <div style="display:flex;justify-content:space-between;font-size:0.67rem;color:var(--text-secondary);margin-top:5px;">
        <span>🔷 Oracle instance CPU load</span>
        <span>🟢 CPUs: {num_cpus}</span>
      </div>
    </div>
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-size:0.75rem;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;">🧠 Oracle RAM Utilization</span>
        <span style="font-size:1.05rem;font-weight:800;color:{ram_oracle_color};">{ram_oracle:.1f}/{ram_total:.1f} GB <span style="font-size:0.72rem;">({ram_oracle_pct:.1f}%)</span></span>
      </div>
      {_pct_bar(ram_oracle_pct, ram_oracle_color)}
      <div style="display:flex;justify-content:space-between;font-size:0.67rem;color:var(--text-secondary);margin-top:5px;">
        <span>🔷 Oracle SGA + PGA size</span>
        <span>🟢 Sessions: {sess_count}</span>
      </div>
    </div>
  </div>
</div>

<!-- Card 2: Host Server by OS -->
<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:12px;
     padding:16px 20px;margin-bottom:12px;border-left:4px solid #10b981;">
  <div style="font-size:0.8rem;font-weight:800;color:#10b981;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;">
    🖥️ Host Server Resources by OS
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-size:0.75rem;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;">⚡ Host CPU Utilization</span>
        <span style="font-size:1.05rem;font-weight:800;color:{cpu_os_color};">{cpu_used:.1f}%</span>
      </div>
      {_pct_bar(cpu_used, cpu_os_color)}
      <div style="display:flex;justify-content:space-between;font-size:0.67rem;color:var(--text-secondary);margin-top:5px;">
        <span>🖥️ Host CPU load (All tasks)</span>
        <span>🟢 Free CPU: {cpu_free:.1f}%</span>
      </div>
    </div>
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-size:0.75rem;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;">🧠 Host RAM Utilization</span>
        <span style="font-size:1.05rem;font-weight:800;color:{ram_os_color};">{ram_used:.1f}/{ram_total:.1f} GB <span style="font-size:0.72rem;">({ram_pct:.1f}%)</span></span>
      </div>
      {_pct_bar(ram_pct, ram_os_color)}
      <div style="display:flex;justify-content:space-between;font-size:0.67rem;color:var(--text-secondary);margin-top:5px;">
        <span>🖥️ Host memory usage (All tasks)</span>
        <span>🟢 Free RAM: {ram_free:.1f} GB</span>
      </div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

            # Top CPU & MEM processes
            top_cpu_procs = proc_res.get("top_cpu", [])
            top_mem_procs = proc_res.get("top_mem", [])
            _proc_src     = proc_res.get("source", "none")
            _proc_err     = proc_res.get("error")
            _src_label    = "v$session" if _proc_src == "v$session" else ("SSH" if _proc_src == "ssh" else "—")

            def _proc_table_html(procs, mode, src_label):
                header_title = "⚡ Top CPU Consuming Processes" if mode == "cpu" else "🧠 Top Memory Consuming Processes"
                if not procs:
                    return (f'<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px 16px;">'
                            f'<div style="font-size:0.75rem;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px;">'
                            f'{header_title} <span style="font-size:0.62rem;font-weight:500;opacity:0.6;margin-left:6px;">via {src_label}</span></div>'
                            f'<div style="font-size:0.72rem;color:var(--text-secondary);font-style:italic;">No active Oracle processes found.</div></div>')
                rows = ""
                for p in procs[:10]:
                    uname   = p.get("user_label") or p.get("username") or "oracle"
                    pid_str = p.get("pid", "—")
                    sid_v   = p.get("sid", "N/A")
                    cmd_str = (p.get("args") or p.get("name") or "")[:38]
                    cpu_val = f"{p.get('cpu', p.get('cpu_sec', 0.0)):.1f}%"
                    mem_val = f"{p.get('rss_mb', p.get('mem', p.get('vsz_mb', 0.0))):.1f} MB"
                    rows += (
                        f'<tr style="border-bottom:1px solid var(--border-color);">'
                        f'<td style="padding:4px 6px;font-size:0.68rem;font-weight:700;color:var(--text-primary);">#{pid_str}</td>'
                        f'<td style="padding:4px 6px;font-size:0.68rem;color:var(--text-secondary);">{sid_v}</td>'
                        f'<td style="padding:4px 6px;font-size:0.68rem;color:var(--text-primary);font-weight:600;">{uname}</td>'
                        f'<td style="padding:4px 6px;font-size:0.65rem;color:var(--text-secondary);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{cmd_str}">{cmd_str}</td>'
                        f'<td style="padding:4px 6px;font-size:0.68rem;font-weight:700;color:#ef4444;">{cpu_val}</td>'
                        f'<td style="padding:4px 6px;font-size:0.68rem;font-weight:700;color:#3b82f6;">{mem_val}</td>'
                        f'</tr>'
                    )
                return (
                    f'<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px 16px;">'
                    f'<div style="font-size:0.75rem;font-weight:800;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:10px;">'
                    f'{header_title} <span style="font-size:0.62rem;font-weight:500;opacity:0.6;margin-left:6px;">via {src_label}</span></div>'
                    f'<div style="overflow-x:auto;max-height:220px;">'
                    f'<table style="width:100%;border-collapse:collapse;font-family:\'Inter\',sans-serif;">'
                    f'<thead><tr style="border-bottom:1px solid var(--border-color);text-align:left;font-size:0.65rem;color:var(--text-secondary);font-weight:700;">'
                    f'<th style="padding:4px 6px;">PID</th>'
                    f'<th style="padding:4px 6px;">SID</th>'
                    f'<th style="padding:4px 6px;">USER</th>'
                    f'<th style="padding:4px 6px;">COMMAND</th>'
                    f'<th style="padding:4px 6px;">CPU %</th>'
                    f'<th style="padding:4px 6px;">MEM (MB)</th>'
                    f'</tr></thead>'
                    f'<tbody>{rows}</tbody></table></div></div>'
                )

            proc_col1, proc_col2 = st.columns(2)
            with proc_col1:
                st.markdown(_proc_table_html(top_cpu_procs, "cpu", _src_label), unsafe_allow_html=True)
            with proc_col2:
                st.markdown(_proc_table_html(top_mem_procs, "mem", _src_label), unsafe_allow_html=True)

            if _proc_err and not top_cpu_procs and not top_mem_procs:
                st.caption(f"ℹ️ Process info: {_proc_err}")
        else:
            st.markdown("""
<div style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;
     padding:14px 16px;display:flex;align-items:center;gap:8px;">
  <span style="font-size:0.72rem;color:var(--text-secondary);font-style:italic;">
    🖥️ Server resources offline or unreachable — all databases on this host are DOWN.
  </span>
</div>""", unsafe_allow_html=True)

        # ASM Diskgroup Information Section — Home Portal page only,
        # rendered directly above Mount Points per the requested layout.
        asm_info = server_info.get("asm_diskgroups", {}) or {}
        asm_groups = asm_info.get("diskgroups", [])
        asm_error = asm_info.get("error")

        st.markdown(
            f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-left:4px solid #8b5cf6; border-radius:6px; padding:10px 14px; margin-top:18px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;'> "
            f"<span style='font-size:0.92rem; font-weight:800; color:var(--text-primary); text-transform:uppercase; letter-spacing:0.02em;'>🗄️ ASM DISKGROUP INFORMATION — {host}</span>"
            f"</div>",
            unsafe_allow_html=True
        )

        if asm_groups:
            theme_asm = st.session_state.get("theme", "Light")
            asm_hdr_bg = "#1e2d45" if theme_asm == "Dark" else "#f1f5f9"
            asm_hdr_fg = "#38bdf8" if theme_asm == "Dark" else "#1e293b"
            asm_row_fg = "#e2e8f0" if theme_asm == "Dark" else "#1e293b"
            asm_alt_bg = "rgba(255,255,255,0.03)" if theme_asm == "Dark" else "rgba(0,0,0,0.02)"
            asm_border = "#2a354f" if theme_asm == "Dark" else "#e5e7eb"

            def _asm_bar(pct):
                if pct > 90:   color = "#ef4444"
                elif pct > 75: color = "#f59e0b"
                else:          color = "#10b981"
                return (f'<div style="background:rgba(128,128,128,0.15);border-radius:4px;height:8px;width:100%;position:relative;overflow:hidden;">'
                        f'<div style="position:absolute;left:0;top:0;height:100%;width:{min(pct,100):.1f}%;background:{color};border-radius:4px;"></div></div>'
                        f'<span style="font-size:0.68rem;color:{color};font-weight:700;">{pct:.1f}%</span>')

            asm_rows_html = ""
            for gi, g in enumerate(sorted(asm_groups, key=lambda x: x.get('used_pct', 0), reverse=True)):
                pct = g.get('used_pct', 0)
                row_bg = "rgba(239,68,68,0.12)" if pct > 90 else (asm_alt_bg if gi % 2 == 0 else "transparent")
                row_border = "border-left:3px solid #ef4444;" if pct > 90 else ""
                asm_rows_html += f"""
                <tr style="background:{row_bg};{row_border}">
                    <td style="padding:6px 10px;font-weight:600;color:{asm_row_fg};">{g.get('name','')}</td>
                    <td style="padding:6px 10px;color:{asm_row_fg};text-align:right;">{g.get('total_mb',0):,.0f}</td>
                    <td style="padding:6px 10px;color:{asm_row_fg};text-align:right;">{g.get('free_mb',0):,.0f}</td>
                    <td style="padding:6px 10px;color:{asm_row_fg};text-align:right;">{g.get('used_gb',0):.2f} GB</td>
                    <td style="padding:6px 10px;color:{asm_row_fg};text-align:right;">{g.get('free_gb',0):.2f} GB</td>
                    <td style="padding:6px 10px;min-width:120px;">{_asm_bar(pct)}</td>
                </tr>"""

            asm_table = f"""
            <div style="overflow-x:auto;border:1px solid {asm_border};border-radius:8px;">
            <table style="width:100%;border-collapse:collapse;font-family:'Inter',sans-serif;font-size:0.75rem;">
              <thead>
                <tr style="background:{asm_hdr_bg};position:sticky;top:0;z-index:2;">
                  <th style="padding:8px 10px;text-align:left;color:{asm_hdr_fg};font-weight:700;border-bottom:1px solid {asm_border};">Diskgroup Name</th>
                  <th style="padding:8px 10px;text-align:right;color:{asm_hdr_fg};font-weight:700;border-bottom:1px solid {asm_border};">Total MB</th>
                  <th style="padding:8px 10px;text-align:right;color:{asm_hdr_fg};font-weight:700;border-bottom:1px solid {asm_border};">Free MB</th>
                  <th style="padding:8px 10px;text-align:right;color:{asm_hdr_fg};font-weight:700;border-bottom:1px solid {asm_border};">Used GB</th>
                  <th style="padding:8px 10px;text-align:right;color:{asm_hdr_fg};font-weight:700;border-bottom:1px solid {asm_border};">Free GB</th>
                  <th style="padding:8px 10px;text-align:left;color:{asm_hdr_fg};font-weight:700;border-bottom:1px solid {asm_border};">Used %</th>
                </tr>
              </thead>
              <tbody>{asm_rows_html}</tbody>
            </table>
            </div>"""
            st.markdown(asm_table, unsafe_allow_html=True)
        else:
            if asm_error:
                st.info(f"🗄️ ASM diskgroup info unavailable: {asm_error}")
            else:
                st.info(f"🗄️ No ASM diskgroups found for {host}.")

        st.markdown("<hr style='margin:20px 0; border-color: rgba(128,128,128,0.15);'>", unsafe_allow_html=True)

        # Storage / Mount points Section
        st.markdown(
            f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-left:4px solid #10b981; border-radius:6px; padding:10px 14px; margin-top:18px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;'> "
            f"<span style='font-size:0.92rem; font-weight:800; color:var(--text-primary); text-transform:uppercase; letter-spacing:0.02em;'>💽 SERVER MOUNT POINTS — {host}</span>"
            f"</div>",
            unsafe_allow_html=True
        )

        # ── Mount points connect automatically using the bundled OCI key
        #    (keys/ folder) at initial load via load_server_status_summary().
        #    If that didn't produce any volumes (no bundled/registry key for
        #    this host), the error/info message below explains why.
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
            if _mount_error:
                st.error(_mount_error)
            else:
                st.info(f"No OCI key found in the keys/ folder for {host}. Add one to retrieve mount points.")

        st.markdown("<hr style='margin:20px 0; border-color: rgba(128,128,128,0.15);'>", unsafe_allow_html=True)

    # Check for missing hosts
    missing_hosts = [host for host in sorted_hosts if host not in st.session_state.server_cache]
    server_placeholders = {}

    for host in sorted_hosts:
        st.markdown(f"<p style='font-size:0.85rem; font-weight:700; color:var(--text-secondary); margin-top:20px; margin-bottom:2px;'>🖥️ Server: <span style=\"color:#3b82f6;\">{host}</span> &nbsp;({len(host_map[host])} database{'s' if len(host_map[host]) > 1 else ''})</p>", unsafe_allow_html=True)
        server_placeholders[host] = st.empty()
        if host in st.session_state.server_cache:
            with server_placeholders[host].container():
                render_server_details_for_host(host, st.session_state.server_cache[host], host_map[host])
        else:
            server_placeholders[host].info(f"⏳ Connecting to host **{host}** to retrieve server diagnostics...")

    # Load missing hosts in parallel using ThreadPoolExecutor
    if missing_hosts:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(missing_hosts), 5)) as executor:
            future_to_host = {
                executor.submit(load_server_status_summary, host, host_map[host], st.session_state.status_cache): host
                for host in missing_hosts
            }
            for future in as_completed(future_to_host):
                host = future_to_host[future]
                try:
                    server_info = future.result()
                except Exception as e:
                    server_info = {
                        "system_res": None, "volumes": [], "mount_error": str(e),
                        "processes": {"top_cpu": [], "top_mem": [], "source": "none", "error": str(e)}
                    }
                st.session_state.server_cache[host] = server_info
                with server_placeholders[host].container():
                    render_server_details_for_host(host, server_info, host_map[host])
        st.rerun()
    else:
        # Reached the end of this pass with nothing left to fetch — every
        # DB card AND every host's server-resources section is populated.
        # This is the single source of truth the "RUNNING.../ALREADY
        # LOADED" banner in render_home_page() reads, so the animation
        # keeps running for as long as anything is actually still loading.
        st.session_state.home_load_complete = True


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

    # Drives the "RUNNING..." / "ALREADY LOADED" banner below. Sourced from
    # render_home_dashboard_fragment's own completion flag — set only once
    # EVERY DB card and EVERY host's server-resources section has actually
    # finished loading (not from a disk-cache file's age, which could be
    # "fresh" while nothing has actually rendered in this session yet).
    _cache_is_fresh = st.session_state.get("home_load_complete", False)

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
            f"<span id='db-load-status-text' style='color: {status_color}; font-weight: 800; letter-spacing: 0.04em;'>{status_text}</span>"
            f"</div>"
        )

    st.markdown(f'<div class="welcome-card-sq"><div class="welcome-title-sq">ENTERPRISE DATABASE CONTROL PORTAL</div><div class="welcome-subtitle-sq">Executive Management Console | Real-time Instance Selection & Storage Diagnostics</div>{anim_html}</div>', unsafe_allow_html=True)

    if session_done:
        # Mirror Streamlit's OWN top-right "running" indicator
        # ([data-testid="stStatusWidget"], shown near the Deploy/menu area
        # whenever ANY script or fragment is actively executing) onto this
        # animation, instead of relying only on our own home_load_complete
        # flag, which only reflects what THIS page explicitly tracks.
        #
        # IMPORTANT: this used to also use a MutationObserver watching the
        # whole page body (subtree: true) to react to changes instantly.
        # That was a real bug, not just inefficient — Streamlit's own UI
        # updates the DOM constantly, and the sync() callback itself writes
        # to the DOM (textContent/style), so the observer could retrigger
        # itself in a tight loop and pin the browser's main thread, causing
        # Chrome's "Page Unresponsive" freeze. A plain interval poll is
        # cheap, bounded, and cannot create that feedback loop — use that
        # only, checked once a second (fast enough to feel live, far too
        # infrequent to meaningfully load the page).
        import streamlit.components.v1 as components
        components.html("""
            <script>
            (function() {
                function sync() {
                    try {
                        var doc = window.parent.document;
                        var running = !!doc.querySelector('[data-testid="stStatusWidget"]');
                        var emojis = doc.querySelectorAll('.em1, .em2, .em3, .em4, .em5, .em6');
                        emojis.forEach(function(el) {
                            // setProperty(..., 'important') is required here,
                            // not el.style.animationPlayState = ... — the
                            // Python-rendered stylesheet sets this same
                            // property with !important (to pick the correct
                            // initial state on first paint), and a plain
                            // inline style can NEVER override an !important
                            // stylesheet rule. Without this, the "ALREADY
                            // LOADED" text updates correctly (no competing
                            // !important there) but the spinner keeps
                            // animating forever regardless of state.
                            el.style.setProperty('animation-play-state', running ? 'running' : 'paused', 'important');
                        });
                        var label = doc.getElementById('db-load-status-text');
                        if (label) {
                            if (running) {
                                label.textContent = 'RUNNING...';
                                label.style.color = '#3b82f6';
                            } else {
                                label.textContent = 'ALREADY LOADED';
                                label.style.color = '#10b981';
                            }
                        }
                    } catch (e) {}
                }
                sync();
                setInterval(sync, 1000);
            })();
            </script>
        """, height=0)

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



    # Background fetching is handled asynchronously inside the dashboard fragment


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
            if "server_cache" in st.session_state:
                del st.session_state.server_cache
            if "diag_result" in st.session_state:
                del st.session_state.diag_result
            if "home_processes_cache" in st.session_state:
                del st.session_state.home_processes_cache
            if "home_mounts_cache" in st.session_state:
                del st.session_state.home_mounts_cache
            st.session_state.home_load_complete = False

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





    # Load and cache status summaries for KPI calculations (cached check)
    render_home_dashboard_fragment(db_names, search_query, _selected, sort_order)

    # ── Trigger background monitor thread if cache is stale or missing ──
    try:
        import os as _os, json as _json
        _cache_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "config", "db_monitor_cache.json"
        )
        _cache_missing = not _os.path.exists(_cache_path)
        if not _cache_missing:
            with open(_cache_path, "r") as _cf:
                _cache_data = _json.load(_cf)
            _cache_missing = any(db not in _cache_data for db in db_names)
        if _cache_missing or st.session_state.get("force_live_refresh", False):
            st.session_state.force_live_refresh = False
            from monitor_thread import trigger_force_refresh
            trigger_force_refresh()
    except Exception:
        pass

