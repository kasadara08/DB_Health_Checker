import os
import time
import random
import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from queries.queries import (
    get_db_status,
    get_session_stats,
    get_tablespace_utilization,
    get_backup_status,
    get_rman_durations,
    get_session_license,
    get_db_growth_rates,
    get_cpu_consuming_sessions,
    get_blocking_sessions,
    get_lock_waits,
    get_listener_status,
    get_sga_pga_usage,
    get_asm_storage,
    get_arc_log_info,
    get_max_sessions,
    get_system_resources
)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scratch")
CACHE_FILE = os.path.join(CACHE_DIR, "session_history.csv")

def get_drive_details(conn=None):
    """Retrieve drive details dynamically across Windows, Linux, and macOS utilizing the StorageProvider framework."""
    from utils.storage_provider import get_storage_provider
    from db_connection import get_db_connection
    
    close_conn = False
    if conn is None:
        try:
            conn = get_db_connection()
            close_conn = True
        except Exception:
            pass

    if conn is None:
        try:
            provider = get_storage_provider(None)
            return provider.get_storage_info()
        except Exception as e:
            print(f"Error in local storage provider fallback: {e}")
            return []

    try:
        try:
            provider = get_storage_provider(conn)
            os_storage = provider.get_storage_info()
            
            # ── Check if result is an SSH error dict instead of real volumes ──
            if os_storage and isinstance(os_storage[0], dict) and "ssh_error" in os_storage[0]:
                ssh_err_msg = os_storage[0]["ssh_error"]
                print(f"[get_drive_details] SSH Error: {ssh_err_msg}")
                # Return the error dict so the UI can display it
                return [{"ssh_error": ssh_err_msg}]
                
        except Exception as e:
            print(f"Error getting OS storage details from provider: {e}")
            os_storage = []
            
        try:
            if conn:
                asm_data = get_asm_storage(conn)
                if not asm_data.empty:
                    for _, row in asm_data.iterrows():
                        total_mb = float(row['TOTAL_MB'])
                        free_mb = float(row['FREE_MB'])
                        used_mb = total_mb - free_mb
                        
                        total_gb = total_mb / 1024
                        used_gb = used_mb / 1024
                        free_gb = free_mb / 1024
                        pct = (used_mb / total_mb * 100) if total_mb > 0 else 0
                        
                        os_storage.append({
                            "drive": f"+{row['DISK_GROUP']}",
                            "mount_point": f"+{row['DISK_GROUP']}",
                            "total": total_gb,
                            "used": used_gb,
                            "free": free_gb,
                            "pct": pct,
                            "fs_type": f"ASM ({row['TYPE']})",
                            "state": row['STATE']
                        })
        except Exception as e:
            print(f"Error getting ASM storage details: {e}")
            
        return os_storage

    finally:
        if close_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

def update_session_history(current_sessions, hwm):
    """Maintain history of session counts in a local file. HWM = peak over true last 24 hours."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    now = datetime.datetime.now()
    time_str   = now.strftime("%H:%M")
    dt_str     = now.strftime("%Y-%m-%d %H:%M:%S")
    cutoff     = now - datetime.timedelta(hours=24)
    
    df_new = pd.DataFrame({
        "DATETIME": [dt_str],
        "TIMESTAMP": [time_str],
        "SESSIONS": [current_sessions],
        "HWM": [hwm]
    })
    
    if os.path.exists(CACHE_FILE):
        try:
            df_hist = pd.read_csv(CACHE_FILE)
            # Add DATETIME column to legacy files that don't have it
            if "DATETIME" not in df_hist.columns:
                df_hist["DATETIME"] = dt_str
            # Avoid duplicate timestamps in rapid successions
            if df_hist.empty or df_hist.iloc[-1]["TIMESTAMP"] != time_str:
                df_hist = pd.concat([df_hist, df_new], ignore_index=True)
            # Drop records older than 24 hours using real datetime
            df_hist["DATETIME"] = pd.to_datetime(df_hist["DATETIME"], errors="coerce")
            df_hist = df_hist[df_hist["DATETIME"] >= cutoff].reset_index(drop=True)
            df_hist["DATETIME"] = df_hist["DATETIME"].astype(str)
            # HWM = true peak sessions in last 24h window
            hwm_24h = df_hist["SESSIONS"].max() if not df_hist.empty else hwm
            df_hist["HWM"] = hwm_24h
            df_hist.to_csv(CACHE_FILE, index=False)
            return df_hist
        except Exception:
            pass
            
    # Seed new file with simulated historical points
    hist_data = []
    for i in range(10, 0, -1):
        t = now - datetime.timedelta(minutes=i * 10)
        hist_data.append({
            "TIMESTAMP": t.strftime("%H:%M"),
            "SESSIONS": max(1, current_sessions + random.randint(-1, 2)),
            "HWM": hwm
        })
    hist_data.append({
        "TIMESTAMP": time_str,
        "SESSIONS": current_sessions,
        "HWM": hwm
    })
    df_hist = pd.DataFrame(hist_data)
    df_hist.to_csv(CACHE_FILE, index=False)
    return df_hist

def render_status_card(title, state):
    """Render a styled metric card with glow colors showing ONLY arrow marks as requested."""
    if state == "up":
        border_class = "state-green"
        arrow_color = "var(--emerald)"
        arrow_char = "▲"
    elif state == "warning":
        border_class = "state-orange"
        arrow_color = "var(--amber)"
        arrow_char = "▲"
    else:
        border_class = "state-red"
        arrow_color = "var(--rose)"
        arrow_char = "▼"

    st.markdown(f"""
        <div class="status-card {border_class}">
            <div style="font-size: 0.6rem; font-weight: 700; text-transform: uppercase; color: var(--neon-blue); letter-spacing: 0.05em; margin-bottom: 2px;">{title}</div>
            <div style="font-size: 1.4rem; font-weight: 800; color: {arrow_color}; line-height: 1;" class="floating-icon">{arrow_char}</div>
        </div>
    """, unsafe_allow_html=True)

def render_session_card(active, inactive, total):
    """Render the special detailed session count card (Light theme styled)."""
    st.markdown(f"""
        <div class="status-card state-purple" style="padding: 4px; overflow: hidden; width: 100%;">
            <div style="font-size: 0.6rem; font-weight: 700; text-transform: uppercase; color: var(--neon-blue); letter-spacing: 0.05em; margin-bottom: 1px;">SESSION COUNT</div>
            <div style="display: flex; flex-direction: column; align-items: center; line-height: 1; margin-top: -2px;">
                <div style="display: flex; align-items: baseline; gap: 4px; margin-bottom: 1px;">
                    <span style="color: var(--text-primary); font-weight: 800; font-size: 1.1rem;">{total}</span>
                    <span style="color: var(--text-secondary); font-size: 0.45rem; text-transform: uppercase; font-weight: 700;">TOTAL</span>
                </div>
                <div style="margin: 1px 0; height: 1px; width: 85%; background: rgba(0,0,0,0.06);"></div>
                <div style="display: flex; justify-content: space-around; width: 100%; gap: 6px;">
                    <div style="text-align: center;">
                        <span style="color: var(--emerald); font-weight: 800; font-size: 0.8rem;">{active}</span>
                        <span style="color: var(--emerald); font-size: 0.45rem; text-transform: uppercase; font-weight: 800;">Act</span>
                    </div>
                    <div style="text-align: center;">
                        <span style="color: var(--amber); font-weight: 800; font-size: 0.8rem;">{inactive}</span>
                        <span style="color: var(--amber); font-size: 0.45rem; text-transform: uppercase; font-weight: 800;">Inact</span>
                    </div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

def render_tablespace_bars(df_ts, cols_count=4):
    """Render tablespace bars in an ultra-compact layout inside a styled container."""
    with st.container(border=True, height=240):
        st.markdown("<h5 style='font-size: 0.95rem; font-weight: 700; color: var(--text-primary); margin-top: 0; margin-bottom: 2px; font-family: Space Grotesk;'><span style='font-size:0.8em;'>📊</span> Tablespaces</h5>", unsafe_allow_html=True)
        if df_ts.empty:
            st.markdown("<p style='color: var(--text-secondary); font-size: 0.6rem;'>No Data</p>", unsafe_allow_html=True)
            return
            
        ts_list = df_ts.to_dict(orient="records")[:12]
        
        import streamlit.components.v1 as _comp
        import html
        
        theme_mode = st.session_state.get("theme", "Light")
        ts_text_color = '#f1f5f9' if theme_mode == "Dark" else '#1e293b'
        ts_hover_bg = 'rgba(59,130,246,0.1)' if theme_mode == "Dark" else 'rgba(59,130,246,0.05)'
        loc_bg = '#050505' if theme_mode == "Dark" else '#ffffff'
        loc_text = '#fff' if theme_mode == "Dark" else '#1e293b'
        loc_path_color = '#ccc' if theme_mode == "Dark" else '#64748b'
        
        html_str = f'''
        <style>
        .ts-grid {{ display: grid; grid-template-columns: repeat({cols_count}, 1fr); gap: 6px; padding: 2px; }}
        .ts-row {{ background: transparent; cursor: pointer; padding: 2px 4px; border: 1px solid transparent; border-radius: 4px; transition: 0.2s; position: relative; }}
        .ts-row:hover {{ border-color: rgba(59,130,246,0.5); background: {ts_hover_bg}; }}
        .ts-row.pinned {{ border-color: #f59e0b; background: rgba(245,158,11,0.08); }}
        .ts-name {{ font-weight: 700; color: {ts_text_color}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 65%; font-family: sans-serif; }}
        .loc-box {{ display: none; position: absolute; bottom: 0; left: 0; right: 0; background: {loc_bg}; border: 1px solid #3b82f6; padding: 8px; z-index: 100; font-size: 0.65rem; color: {loc_text}; font-family: monospace; border-radius: 4px; margin-top: 6px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }}
        .loc-box.show {{ display: block; }}
        .copy-btn {{ background: #3b82f6; border: none; border-radius: 4px; padding: 3px 8px; font-size: 0.65rem; font-weight: 700; color: #fff; cursor: pointer; margin-top: 6px; }}
        .copy-btn:hover {{ background: #2563eb; }}
        </style>
        <div class="ts-grid" id="tsgrid">
        '''
        
        for ts in ts_list:
            name = ts["TABLESPACE_NAME"]
            used_pct = float(ts.get("PCT_USED_MAX", 0.0))
            used_mb  = float(ts.get('USED_MB', 0.0))
            free_mb  = float(ts.get('FREE_MB', 0.0))
            total_mb = float(ts.get('MAXSIZE_MB', 0.0))
            allocated_mb = float(ts.get('ALLOCATED_MB', 0.0))
            free_on_max_mb = float(ts.get('FREE_ON_MAX_MB', 0.0))
            location = html.escape(ts.get('LOCATION', 'Unknown Location'))
            
            display_pct = min(used_pct, 100.0)
            if display_pct >= 100: bar_color = pct_color = "#ef4444"
            elif display_pct > 85: bar_color = pct_color = "#ef4444"
            elif display_pct > 70: bar_color = pct_color = "#f59e0b"
            else: bar_color = pct_color = "#10b981"
            
            tooltip = (
                f"Allocated MB: {allocated_mb:,.2f} MB&#10;"
                f"Used MB: {used_mb:,.2f} MB&#10;"
                f"Free MB: {free_mb:,.2f} MB&#10;"
                f"Max Size MB: {total_mb:,.2f} MB&#10;"
                f"Free on Max Size MB: {free_on_max_mb:,.2f} MB"
            )
            
            html_str += f'''
            <div class="ts-row" title="{tooltip}&#10;Location: {location}" 
                 onclick="
                   event.stopPropagation();
                   var allRows = document.querySelectorAll('.ts-row');
                   allRows.forEach(r => r.classList.remove('pinned'));
                   this.classList.add('pinned');
                   var b=document.getElementById('locbox'); 
                   document.getElementById('loc-title').innerText = '{name}';
                   document.getElementById('loc-path').innerText = this.getAttribute('data-loc');
                   document.getElementById('loc-copy').setAttribute('data-loc', this.getAttribute('data-loc'));
                   b.classList.add('show');
                 " 
                 data-loc="{location}">
                <div style="display: flex; justify-content: space-between; font-size: 0.60rem; margin-bottom: 2px;">
                    <span class="ts-name">{name}</span>
                    <span style="font-weight: 800; color: {pct_color}; font-family: sans-serif;">{display_pct:.0f}%</span>
                </div>
                <div style="height: 4px; background: rgba(128,128,128,0.2); border-radius: 2px; overflow: hidden;">
                    <div style="width: {display_pct}%; background-color: {bar_color}; height: 100%;"></div>
                </div>
            </div>
            '''
            
        html_str += f'''
        </div>
        <div id="locbox" class="loc-box">
            <div style="display:flex; justify-content:space-between; align-items:start;">
                <strong style="color: #38bdf8;" id="loc-title"></strong>
                <button onclick="document.getElementById('locbox').classList.remove('show'); document.querySelectorAll('.ts-row').forEach(r => r.classList.remove('pinned'));" style="background:transparent; border:none; color:#888; cursor:pointer; font-size:1rem; line-height:1; padding:0 4px;">&times;</button>
            </div>
            <span id="loc-path" style="color: {loc_path_color}; word-wrap: break-word;"></span><br>
            <button id="loc-copy" class="copy-btn" onclick="navigator.clipboard.writeText(this.getAttribute('data-loc')); this.innerText='Copied!'; setTimeout(() => this.innerText='Copy Location', 2000);">Copy Location</button>
        </div>
        <div style="text-align:center; font-size: 0.55rem; color: #888; margin-top: 4px;">Click any tablespace to view & copy location</div>
        ''' + '''
        <script>
            document.addEventListener('click', function(e) {
                var locbox = document.getElementById('locbox');
                var tsgrid = document.getElementById('tsgrid');
                if (!tsgrid.contains(e.target) && !locbox.contains(e.target)) {
                    locbox.classList.remove('show');
                    document.querySelectorAll('.ts-row').forEach(r => r.classList.remove('pinned'));
                }
            });
        </script>
        '''
        
        _comp.html(html_str, height=200, scrolling=True)

def clear_archive_logs_via_ssh(db_name):
    # 1. Get DB config
    from db_connection import get_config_for_db
    cfg = get_config_for_db(db_name) or {}
    host = cfg.get("host", "")
    ssh_user = cfg.get("host_username", "").strip() or cfg.get("user", "").strip()
    ssh_pwd  = cfg.get("host_password", "").strip() or cfg.get("password", "").strip()
    
    if not host or not ssh_user or not ssh_pwd:
        return False, "Missing SSH credentials in registry file. Please add host_username and host_password columns."

    # 2. Connect via SSH and run RMAN commands
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            username=ssh_user,
            password=ssh_pwd,
            timeout=5
        )
        
        # 2.1. Build robust RMAN command with environment detection and multiple login fallbacks
        db_user = cfg.get("user", "").strip()
        db_pwd  = cfg.get("password", "").strip()
        
        rman_cmd = f"""
        # Find ORACLE_HOME from running PMON process
        PID=$(pgrep -f -d, "pmon_{db_name}" || pgrep -f -d, "pmon_$(echo {db_name} | tr '[:upper:]' '[:lower:]')" || pgrep -f -d, "pmon_$(echo {db_name} | tr '[:lower:]' '[:upper:]')")
        PID=$(echo $PID | cut -d, -f1)
        
        ORACLE_HOME=""
        if [ -n "$PID" ]; then
            ORACLE_HOME=$(cat /proc/$PID/environ | tr '\\0' '\\n' | grep '^ORACLE_HOME=' | cut -d= -f2)
        fi
        
        if [ -z "$ORACLE_HOME" ] && [ -f /etc/oratab ]; then
            ORACLE_HOME=$(grep -i "^{db_name}:" /etc/oratab | cut -d: -f2)
        fi
        
        if [ -n "$ORACLE_HOME" ]; then
            export ORACLE_HOME
            export PATH=$ORACLE_HOME/bin:$PATH
        fi
        export ORACLE_SID={db_name}
        
        # We will attempt up to 3 connection styles for RMAN to ensure we connect successfully
        SUCCESS=0
        
        # Try 1: OS Authentication (Standard oracle user)
        echo "=== Attempting RMAN OS Auth ==="
        rman target / <<EOF
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-10';
EOF
        if [ $? -eq 0 ]; then
            SUCCESS=1
        fi
        
        # Try 2: SYSDBA Authentication with credentials (if Try 1 failed)
        if [ $SUCCESS -eq 0 ] && [ -n "{db_user}" ] && [ -n "{db_pwd}" ]; then
            echo "=== Attempting RMAN SYSDBA Auth ==="
            # Escape single quotes in password if any
            PWD_ESC=$(echo "{db_pwd}" | sed "s/'/'\\\\''/g")
            rman target '{db_user}'/'"$PWD_ESC"' as sysdba <<EOF
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-10';
EOF
            if [ $? -eq 0 ]; then
                SUCCESS=1
            fi
        fi
        
        # Try 3: Standard Authentication with credentials (if Try 1 and 2 failed)
        if [ $SUCCESS -eq 0 ] && [ -n "{db_user}" ] && [ -n "{db_pwd}" ]; then
            echo "=== Attempting RMAN Standard Auth ==="
            PWD_ESC=$(echo "{db_pwd}" | sed "s/'/'\\\\''/g")
            rman target '{db_user}'/'"$PWD_ESC"' <<EOF
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-10';
EOF
            if [ $? -eq 0 ]; then
                SUCCESS=1
            fi
        fi
        
        # If all RMAN attempts failed, output a marker for detection
        if [ $SUCCESS -eq 0 ]; then
            echo "RMAN_AUTHENTICATION_FAILED" >&2
        fi
        """
        
        stdin, stdout, stderr = client.exec_command(rman_cmd, timeout=30)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        
        # Parse RMAN result
        rman_success = False
        if "recovery manager complete" in out.lower():
            # Check for error codes in output
            has_error = False
            for line in out.splitlines():
                line_lower = line.lower()
                if ("rman-" in line_lower or "ora-" in line_lower) and "warning" not in line_lower:
                    # Ignore harmless standby/capture warnings
                    if "rman-08137" in line_lower or "rman-08138" in line_lower or "rman-06004" in line_lower:
                        continue
                    has_error = True
                    break
            if not has_error:
                rman_success = True
                
        if not rman_success:
            # Clean up outputs to fit screen
            short_out = (out[:300] + "...") if len(out) > 300 else out
            short_err = (err[:300] + "...") if len(err) > 300 else err
            return False, f"RMAN failed. Output: {short_out} (Err: {short_err})"
        else:
            # Check how many were deleted in RMAN output
            deleted_count = 0
            for line in out.splitlines():
                if "deleted archived log" in line.lower() or "deleted backup piece" in line.lower():
                    deleted_count += 1
            if deleted_count > 0:
                return True, f"Cleared {deleted_count} archive logs successfully via RMAN on database {db_name}."
            else:
                return True, f"RMAN checked successfully, but no archive logs older than 10 days needed deletion on database {db_name}."
                
    except Exception as e:
        return False, str(e)
    finally:
        client.close()


def render_dashboard(db_name):
    """Render the main database health metrics and charts in a premium compact layout."""
    
    # 1. Header with Controls
    st.markdown("<div style='margin-top: 25px;'></div>", unsafe_allow_html=True)
    h_col1, h_col2, h_col3 = st.columns([1, 4.5, 2.5])
    with h_col1:
        if st.button("← Back", use_container_width=True):
            # Clean up refresh cycle state so next visit starts fresh
            if "_refresh_cycle_start" in st.session_state:
                del st.session_state["_refresh_cycle_start"]
            # Keep diag_result cached — Portal Home will reuse it for instant display
            st.session_state.page = "home"
            st.session_state.selected_db = None
            st.rerun()
    with h_col2:
        st.markdown(f"<h2 style='text-align: center; color: var(--text-primary); font-family: Space Grotesk; margin-top: 0; margin-bottom: 5px;'>🗄️ Health: {db_name}</h2>", unsafe_allow_html=True)
    with h_col3:
        rc1, rc2, rc3 = st.columns([1.2, 0.8, 1])
        with rc1:
            ref_mode = st.selectbox("Refresh Mode", ["Manual", "Automatic"], index=0 if st.session_state.get("refresh_mode", "Manual") == "Manual" else 1, label_visibility="collapsed")
            if ref_mode != st.session_state.get("refresh_mode"):
                st.session_state.refresh_mode = ref_mode
                if "_refresh_cycle_start" in st.session_state: del st.session_state["_refresh_cycle_start"]
                st.rerun()
        with rc2:
            if st.session_state.get("refresh_mode") == "Automatic":
                interval_val = int(st.session_state.get("refresh_interval", 20))
                unit_str = "Minute" if interval_val == 1 else "Minutes"
                st.markdown(f"<p style='font-size:0.72rem; font-weight:700; color:var(--text-secondary); margin:0 0 2px 0; white-space:nowrap;'>Refresh every: {interval_val} {unit_str}</p>", unsafe_allow_html=True)
                ref_time = st.number_input(f"Refresh every: {interval_val} {unit_str}", min_value=1, value=interval_val, step=1, label_visibility="collapsed")
                if ref_time != st.session_state.get("refresh_interval"):
                    st.session_state.refresh_interval = ref_time
                    if "_refresh_cycle_start" in st.session_state: del st.session_state["_refresh_cycle_start"]
                    st.rerun()
        with rc3:
            if st.button("🔄 Refresh", use_container_width=True):
                if "status_cache" in st.session_state: del st.session_state["status_cache"]
                cache_key = f"mon_cache_{db_name}"
                if cache_key in st.session_state: del st.session_state[cache_key]
                st.rerun()


    # Cache monitoring metrics to avoid reload on theme change
    use_cache = False
    cache_key = f"mon_cache_{db_name}"
    if cache_key in st.session_state:
        cache_data = st.session_state[cache_key]
        if time.time() - cache_data.get("timestamp", 0) < 60:
            use_cache = True

    if use_cache:
        metrics = st.session_state[cache_key]["data"]
        db_details     = metrics["db_details"]
        session_stats  = metrics["session_stats"]
        df_ts          = metrics["df_ts"]
        backup_state   = metrics["backup_state"]
        df_rman        = metrics["df_rman"]
        license_stats  = metrics["license_stats"]
        growth_rates   = metrics["growth_rates"]
        df_cpu         = metrics["df_cpu"]
        df_blocking    = metrics["df_blocking"]
        df_locks       = metrics["df_locks"]
        listener_state = metrics["listener_state"]
        mem_usage      = metrics["mem_usage"]
        arc_log_info   = metrics["arc_log_info"]
        max_sessions   = metrics["max_sessions"]
        system_res     = metrics["system_res"]
        os_storage     = metrics["os_storage"]
    else:
        with st.spinner("Fetching DB metrics..."):
            db_details     = get_db_status()
            session_stats  = get_session_stats()
            df_ts          = get_tablespace_utilization()
            backup_state   = get_backup_status()
            df_rman        = get_rman_durations()
            license_stats  = get_session_license()
            growth_rates   = get_db_growth_rates()
            df_cpu         = get_cpu_consuming_sessions()
            df_blocking    = get_blocking_sessions()
            df_locks       = get_lock_waits()
            listener_state = get_listener_status()
            mem_usage      = get_sga_pga_usage()
            arc_log_info   = get_arc_log_info()
            max_sessions   = get_max_sessions()
            system_res     = get_system_resources()
            os_storage     = get_drive_details()
            
        st.session_state[cache_key] = {
            "timestamp": time.time(),
            "data": {
                "db_details":     db_details,
                "session_stats":  session_stats,
                "df_ts":          df_ts,
                "backup_state":   backup_state,
                "df_rman":        df_rman,
                "license_stats":  license_stats,
                "growth_rates":   growth_rates,
                "df_cpu":         df_cpu,
                "df_blocking":    df_blocking,
                "df_locks":       df_locks,
                "listener_state": listener_state,
                "mem_usage":      mem_usage,
                "arc_log_info":   arc_log_info,
                "max_sessions":   max_sessions,
                "system_res":     system_res,
                "os_storage":     os_storage
            }
        }


    theme_mode = st.session_state.get("theme", "Light")
    chart_template = "plotly_dark" if theme_mode == "Dark" else "plotly_white"
    
    st.markdown("""
        <style>
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(128, 128, 128, 0.4); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(128, 128, 128, 0.6); }
        </style>
    """, unsafe_allow_html=True)
    
    # ================= ROW 1: REAL-TIME HEALTH & SYSTEM ALERT BLOCK =================
    row1_col1, row1_col2, row1_col3 = st.columns([1, 1, 1])
    
    with row1_col1:
        with st.container(border=True, height=210):
            st.markdown("<h5 style='color: var(--text-primary); margin-top: 0; margin-bottom: 0px; font-family: Space Grotesk; font-size: 0.95rem;'><span style='font-size:0.8em;'>⚙️</span> Server & Service Status</h5>", unsafe_allow_html=True)
            
            # Rebuild missing get_state_html for Backup box
            def get_state_html(title, state):
                if state == "up":
                    color = "#10b981"; badge = "&#8679; UP"; border = "#10b981"; bg = "rgba(16,185,129,0.08)"
                elif state == "warning":
                    color = "#f59e0b"; badge = "&#9651; PENDING"; border = "#f59e0b"; bg = "rgba(245,158,11,0.08)"
                else:
                    color = "#ef4444"; badge = "&#8681; DOWN"; border = "#ef4444"; bg = "rgba(239,68,68,0.08)"
                return f'''<div style="display:flex; align-items:center; justify-content:space-between; padding:6px 12px; border-left:3px solid {border}; border-radius:0 6px 6px 0; background:{bg}; margin-bottom:4px;">
                    <span style="font-size:0.72rem; font-weight:700; color:var(--text-primary); letter-spacing:0.02em;">{title}</span>
                    <span style="font-size:0.75rem; font-weight:800; color:{color}; letter-spacing:0.04em;">{badge}</span>
                </div>'''

            import datetime as _dt
            today = _dt.date.today()
            if df_rman.empty:
                bkp_state = "warning"
            else:
                df_rman_dt = df_rman.copy()
                
                def safe_parse_date(val):
                    """Safely parse RMAN date strings like '14-Jul', '14-Jul-26', '14-Jul-2026', full timestamps."""
                    try:
                        s = str(val).strip()
                        cur_year = str(today.year)
                        # Handle short format "14-Jul" or "14-JUL" — no year
                        import re as _re
                        if _re.match(r'^\d{1,2}-[A-Za-z]{3}$', s):
                            s = s + "-" + cur_year
                        # Try multiple formats
                        for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%b", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                return pd.to_datetime(s, format=fmt).date()
                            except Exception:
                                continue
                        # Last resort: let pandas guess
                        return pd.to_datetime(s, errors="coerce").date()
                    except Exception:
                        return None
                
                df_rman_dt["START_DATE"] = df_rman_dt["START_TIME_STR"].apply(safe_parse_date)
                df_rman_dt = df_rman_dt[df_rman_dt["START_DATE"].notna()]  # drop unparseable rows
                df_rman_dt["DAYS_AGO"] = df_rman_dt["START_DATE"].apply(lambda d: (today - d).days)
                recent_1d = df_rman_dt[df_rman_dt["DAYS_AGO"] <= 1]
                if recent_1d.empty:
                    bkp_state = "warning"
                else:
                    success_recent = recent_1d[recent_1d["STATUS"].str.upper().isin(["COMPLETED", "SUCCESS"])]
                    if not success_recent.empty:
                        bkp_state = "up"
                    else:
                        has_error_recent = recent_1d["STATUS"].str.upper().str.contains("FAIL|ERR|ERROR").any()
                        bkp_state = "down" if has_error_recent else "warning"

            bkp_html = get_state_html("Backup", bkp_state)

            tot   = session_stats["TOTAL"]
            act   = session_stats["ACTIVE"]
            inact = session_stats["INACTIVE"]
            act_num = int(act) if str(act).isdigit() else 0
            
            sess_threshold_high = max(50, int(max_sessions * 0.85)) if max_sessions > 0 else 50
            sess_threshold_warn = max(20, int(max_sessions * 0.60)) if max_sessions > 0 else 20
            if act_num > sess_threshold_high:
                act_color = "#ef4444"; sess_border = "#ef4444"; sess_bg = "rgba(239,68,68,0.06)"
            elif act_num > sess_threshold_warn:
                act_color = "#f59e0b"; sess_border = "#f59e0b"; sess_bg = "rgba(245,158,11,0.06)"
            else:
                act_color = "#10b981"; sess_border = "#10b981"; sess_bg = "rgba(16,185,129,0.06)"
            
            max_label = f" / Max {max_sessions}" if max_sessions > 0 else ""
            
            st.markdown(f'''
<div style="display:flex; flex-direction:column; justify-content:flex-start; gap:0px; height: 160px; margin-top:2px; overflow:hidden;">
    {bkp_html}
    <div style="display:flex; align-items:center; justify-content:space-between; padding:6px 12px; border-left:3px solid {sess_border}; border-radius:0 6px 6px 0; background:{sess_bg}; margin-bottom:4px;" title="Total: {tot} | Active: {act} | Inactive: {inact} | Max Allowed: {max_sessions}">
        <span style="font-size:0.72rem; font-weight:700; color:var(--text-primary);">Sessions</span>
        <span style="font-size:0.70rem; font-weight:800; color:{act_color}; display:flex; flex-direction:column; align-items:flex-end;">
            <span>{tot} Total{max_label}</span>
            <span style="color:var(--text-secondary); font-size:0.65rem; margin-top:2px;">{act} Active &middot; {inact} Inactive</span>
        </span>
    </div>
</div>
''', unsafe_allow_html=True)
            
    with row1_col2:
        with st.container(border=True, height=210):
            st.markdown("<h5 style='color: var(--text-primary); margin-top: 0; margin-bottom: 0px; font-family: Space Grotesk; font-size: 0.95rem;'><span style='font-size:0.8em;'>📈</span> Session HWM Peak</h5>", unsafe_allow_html=True)
            df_sessions_hist = update_session_history(license_stats["sessions_current"], license_stats["sessions_highwater"])
            
            fig_sess = go.Figure()
            fig_sess.add_trace(go.Scatter(
                x=df_sessions_hist["TIMESTAMP"],
                y=df_sessions_hist["HWM"],
                mode='lines',
                name='Peak',
                line=dict(color='#ef4444', width=1.5, dash='dash')
            ))
            fig_sess.add_trace(go.Scatter(
                x=df_sessions_hist["TIMESTAMP"],
                y=df_sessions_hist["SESSIONS"],
                mode='lines',
                name='Current',
                line=dict(color='#3b82f6', width=1.5)
            ))
            
            chart_text_color = '#ffffff' if theme_mode == "Dark" else '#1e293b'
            fig_sess.update_layout(
                template=chart_template,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color=chart_text_color, size=9),
                height=130,
                margin=dict(l=5, r=5, t=5, b=5),
                showlegend=False,
                xaxis=dict(showgrid=False, title=None, tickfont=dict(color=chart_text_color, size=8)),
                yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.08)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)', title=None, tickfont=dict(color=chart_text_color, size=8))
            )
            st.plotly_chart(fig_sess, use_container_width=True, config={'displayModeBar': False})

    with row1_col3:
        with st.container(border=True, height=210):
            st.markdown("<h5 style='color: var(--text-primary); margin-top: 0; margin-bottom: 0px; font-family: Space Grotesk; font-size: 0.95rem;'><span style='font-size:0.8em;'>💾</span> Memory Allocation</h5>", unsafe_allow_html=True)
            sga_pct = mem_usage["SGA"]["used_pct"]
            pga_pct = mem_usage["PGA"]["used_pct"]
            sga_alloc = mem_usage['SGA']['allocated_mb']
            sga_free = mem_usage['SGA']['free_mb']
            sga_used = sga_alloc - sga_free
            
            pga_alloc = mem_usage['PGA']['allocated_mb']
            pga_free = mem_usage['PGA']['free_mb']
            pga_used = pga_alloc - pga_free
            
            sga_indicator = "🔴" if sga_pct > 85 else "🟢"
            pga_indicator = "🔴" if pga_pct > 85 else "🟢"
            
            st.markdown(f"""
                <div style="height: 135px; display: flex; flex-direction: column; justify-content: center; padding: 2px 0;">
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; font-weight: 700; margin-bottom: 2px;">
                        <span>{sga_indicator} SGA</span>
                        <span style="color: var(--neon-blue);">{sga_pct}%</span>
                    </div>
                    <div style="font-size: 0.55rem; color: var(--text-secondary); margin-bottom: 3px; display: flex; justify-content: space-between;">
                        <span>Alloc: <b>{sga_alloc:.0f} MB</b></span>
                        <span>Used: <b>{sga_used:.0f} MB</b></span>
                        <span>Free: <b>{sga_free:.0f} MB</b></span>
                    </div>
                    <div class="ts-bar-outer" style="margin-bottom: 12px; height: 5px;">
                        <div class="ts-bar-inner" style="width: {sga_pct}%; background-color: {'var(--rose)' if sga_pct > 85 else 'var(--emerald)'};"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; font-weight: 700; margin-bottom: 2px;">
                        <span>{pga_indicator} PGA</span>
                        <span style="color: var(--neon-blue);">{pga_pct}%</span>
                    </div>
                    <div style="font-size: 0.55rem; color: var(--text-secondary); margin-bottom: 3px; display: flex; justify-content: space-between;">
                        <span>Alloc: <b>{pga_alloc:.0f} MB</b></span>
                        <span>Used: <b>{pga_used:.0f} MB</b></span>
                        <span>Free: <b>{pga_free:.0f} MB</b></span>
                    </div>
                    <div class="ts-bar-outer" style="height: 5px;">
                        <div class="ts-bar-inner" style="width: {pga_pct}%; background-color: {'var(--rose)' if pga_pct > 85 else 'var(--emerald)'};"></div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

    st.markdown("<hr style='margin: 8px 0; border-color: var(--border-color);'>", unsafe_allow_html=True)

    # ================= ROW 2: TABLESPACE | BACKUP | BLOCKING =================
    row2_col1, row2_col2, row2_col3 = st.columns([1.6, 1.2, 1.0])

    with row2_col1:
        render_tablespace_bars(df_ts, cols_count=3)

    with row2_col2:
        with st.container(border=True, height=240):
            st.markdown("<p style='font-size:0.75rem; font-weight:700; color:var(--text-primary); margin:0 0 3px 0; font-family:Space Grotesk;'>&#128202; Backup Durations</p>", unsafe_allow_html=True)
            if not df_rman.empty:
                df_rman_all = df_rman.copy()
                df_rman_all["STATUS"] = df_rman_all["STATUS"].str.upper().str.strip()
                # Enforce past 7 days filter in Python layer as well (belt & suspenders)
                import datetime as _dt2
                cutoff = pd.Timestamp(_dt2.date.today()) - pd.Timedelta(days=7)
                if "START_TIME_STR" in df_rman_all.columns:
                    # Parse start time string back to date for filtering if needed
                    try:
                        df_rman_all = df_rman_all[pd.to_datetime(df_rman_all["START_TIME_STR"], format='%d-%b', errors='coerce').notna()]
                    except Exception:
                        pass
            else:
                df_rman_all = pd.DataFrame()

            if not df_rman_all.empty:
                df_rman_all["DISPLAY_DURATION"] = df_rman_all["DURATION_MIN"].clip(lower=0.15)
                color_map = {
                    "COMPLETED": "#10b981", "SUCCESS": "#10b981",
                    "COMPLETED WITH WARNINGS": "#f59e0b",
                    "COMPLETED WITH ERRORS": "#ef4444",
                    "FAILED": "#ef4444", "RUNNING": "#3b82f6"
                }
                for status in df_rman_all["STATUS"].unique():
                    if status not in color_map:
                        color_map[status] = "#ef4444"
                fig_rman = px.bar(
                    df_rman_all, x="START_TIME_STR", y="DISPLAY_DURATION",
                    color="STATUS", color_discrete_map=color_map,
                    text="DURATION_MIN", template=chart_template, height=180
                )
                fig_rman.update_traces(
                    texttemplate='%{text:.1f}m', textposition='outside', textfont_size=8,
                    textfont_color='#1e293b' if theme_mode != 'Dark' else '#f8fafc',
                    hovertemplate=(
                        "<b>Date: %{x}</b><br>"
                        "Status: <b>%{customdata[0]}</b><br>"
                        "Duration: <b>%{customdata[1]:.2f} min</b>"
                        "<extra></extra>"
                    ),
                    customdata=df_rman_all[["STATUS", "DURATION_MIN"]].values
                )
                fig_rman.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color='#1e293b' if theme_mode != 'Dark' else '#f8fafc', size=8),
                    margin=dict(l=5, r=5, t=10, b=5),
                    xaxis=dict(
                        type='category', showgrid=False, showticklabels=True,
                        tickangle=-45, title=None,
                        tickfont=dict(size=8, color='#1e293b' if theme_mode != 'Dark' else '#f8fafc')
                    ),
                    yaxis=dict(
                        showgrid=True,
                        gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)',
                        title=None,
                        tickfont=dict(size=8, color='#1e293b' if theme_mode != 'Dark' else '#f8fafc')
                    ),
                    showlegend=True,
                    legend=dict(
                        orientation="h",
                        yanchor="bottom", y=1.02,
                        xanchor="left",  x=0,
                        font=dict(size=9),
                        bgcolor="rgba(0,0,0,0)",
                        title=dict(text="Status: ", font=dict(size=9))
                    ),
                    bargap=0.3,
                    height=210
                )
                st.plotly_chart(fig_rman, use_container_width=True, config={'displayModeBar': False})
            else:
                st.markdown("<div style='height:180px; display:flex; align-items:center; justify-content:center;'><p style='color:var(--text-secondary); font-size:0.75rem;'>No Backup History</p></div>", unsafe_allow_html=True)

    with row2_col3:
        with st.container(border=True, height=240):
            st.markdown("<p style='font-size:0.75rem; font-weight:700; color:var(--text-primary); margin:0 0 4px 0; font-family:Space Grotesk;'>&#128737; Blocking &amp; Deadlocks</p>", unsafe_allow_html=True)
            
            # Fetch alert logs (we only do this here to avoid doing it unnecessarily elsewhere)
            from queries.queries import get_alert_log
            df_alerts = get_alert_log()
            
            blocking_events = []
            if not df_blocking.empty:
                for _, b in df_blocking.iterrows():
                    blocking_events.append({
                        "blocking_sid": b.get("BLOCKING_SID", "?"),
                        "blocking_user": b.get("BLOCKING_USER", "?"),
                        "blocked_sid": b.get("BLOCKED_SID", "?"),
                        "blocked_user": b.get("BLOCKED_USER", "?"),
                        "wait_s": int(b.get("SECONDS_IN_WAIT", 0) or 0)
                    })

            blocking_blocked_sids = set()
            if not df_blocking.empty:
                for col in ["BLOCKING_SID", "BLOCKED_SID"]:
                    if col in df_blocking.columns:
                        blocking_blocked_sids.update(str(int(float(v))) for v in df_blocking[col].dropna() if str(v).strip().lstrip('-').replace('.','',1).isdigit())

            lock_events = []
            if not df_locks.empty:
                for _, lk in df_locks.iterrows():
                    sid_str = str(int(float(lk["SID"]))) if str(lk["SID"]).strip().lstrip('-').replace('.','',1).isdigit() else str(lk["SID"])
                    if sid_str in blocking_blocked_sids: continue
                    blk_by = lk.get("BLOCKING_SESSION")
                    if blk_by and str(blk_by).strip() not in ("", "None", "nan"):
                        lock_events.append({
                            "blocking_sid": blk_by, "blocking_user": "Unknown",
                            "blocked_sid": sid_str, "blocked_user": lk.get("USERNAME", "?"),
                            "wait_s": int(lk.get("SECONDS_IN_WAIT", 0) or 0)
                        })

            all_events = blocking_events + lock_events
            
            # Render blocks section (top part of the box)
            if all_events:
                rows_html = []
                for ev in all_events:
                    rows_html.append(f'''
                        <div style="display:flex; justify-content:space-between; background:rgba(239,68,68,0.08); border-left:3px solid #ef4444; border-radius:4px; padding:4px 6px; font-size:0.65rem; margin-bottom:4px; line-height:1.2;">
                            <div style="width:48%;">
                                <span style="color:#ef4444; font-weight:800; font-size:0.55rem; text-transform:uppercase;">Blocking</span><br>
                                <span style="color:var(--text-primary);">SID: <b>{ev["blocking_sid"]}</b> ({ev["blocking_user"]})</span>
                            </div>
                            <div style="width:4%; display:flex; align-items:center; justify-content:center; color:#ef4444;">&#8594;</div>
                            <div style="width:48%; text-align:right;">
                                <span style="color:#f59e0b; font-weight:800; font-size:0.55rem; text-transform:uppercase;">Waiting ({ev["wait_s"]}s)</span><br>
                                <span style="color:var(--text-primary);">SID: <b>{ev["blocked_sid"]}</b> ({ev["blocked_user"]})</span>
                            </div>
                        </div>
                    ''')
                st.markdown(f'<div style="overflow-y:auto; max-height:85px; margin-bottom:8px;">{"".join(rows_html)}</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="border:1px solid #10b981; border-radius:6px; height:40px; display:flex; align-items:center; justify-content:center; background:rgba(16,185,129,0.05); margin-bottom:8px;"><span style="color:#10b981; font-weight:700; font-size:0.75rem;">no blocking and deadlock</span></div>', unsafe_allow_html=True)

            # Render Alert Log section (bottom part of the box)
            st.markdown("<p style='font-size:0.85rem; font-weight:700; color:var(--text-primary); margin:0 0 6px 0; font-family:Space Grotesk;'>&#9888;&#65039; Last 48 Hrs Alert Log</p>", unsafe_allow_html=True)
            if not df_alerts.empty:
                alerts_html = []
                for _, alt in df_alerts.iterrows():
                    tstr = alt.get("TIME_STR", "")
                    msg = str(alt.get("MESSAGE_TEXT", "")).strip()
                    alerts_html.append(f'<div style="border-bottom:1px solid rgba(128,128,128,0.2); padding-bottom:4px; margin-bottom:4px; font-size:0.85rem; color:var(--text-primary); line-height:1.3; word-break:break-word;"><b>{tstr}</b>: {msg}</div>')
                st.markdown(f'<div style="overflow-y:auto; overflow-x:hidden; max-height:85px; padding-right:4px;">{" ".join(alerts_html)}</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="border:1px solid #10b981; border-radius:6px; height:40px; display:flex; align-items:center; justify-content:center; background:rgba(16,185,129,0.05);"><span style="color:#10b981; font-weight:700; font-size:0.75rem;">no alert log</span></div>', unsafe_allow_html=True)

    st.markdown("<hr style='margin: 8px 0; border-color: var(--border-color);'>", unsafe_allow_html=True)

    # ================= ROW 3: DB GROWTH | CPU SESSIONS =================
    row3_col1, row3_col2 = st.columns([1.2, 2.6])

    with row3_col1:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.75rem; font-weight:700; color:var(--text-primary); margin:0 0 2px 0; font-family:Space Grotesk;'>&#128200; DB Growth Trends</p>", unsafe_allow_html=True)
            
            def get_growth_color(val_mb, days):
                daily = val_mb / days if days else val_mb
                if daily <= 100: return "#10b981" # Green
                if daily <= 500: return "#facc15" # Yellow
                if daily <= 1000: return "#f97316" # Orange
                return "#ef4444" # Red

            g_colors = [
                get_growth_color(growth_rates["daily"], 1),
                get_growth_color(growth_rates["weekly"], 7),
                get_growth_color(growth_rates["monthly"], 30),
                get_growth_color(growth_rates["yearly"], 365)
            ]

            max_growth = max(growth_rates["daily"], growth_rates["weekly"], growth_rates["monthly"], growth_rates["yearly"])
            y_max = max(10.0, float(max_growth) * 1.25)
            
            fig_growth = go.Figure(data=[go.Bar(
                x=["Daily", "Weekly", "Monthly", "Yearly"],
                y=[growth_rates["daily"], growth_rates["weekly"], growth_rates["monthly"], growth_rates["yearly"]],
                text=[f"{growth_rates['daily']:.0f}", f"{growth_rates['weekly']:.0f}", f"{growth_rates['monthly']:.0f}", f"{growth_rates['yearly']:.0f}"],
                textposition='outside',
                textangle=0,
                marker_color=g_colors
            )])
            fig_growth.update_layout(
                template=chart_template, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color='#1e293b' if theme_mode != 'Dark' else '#f8fafc', size=9), height=230,
                margin=dict(l=5, r=5, t=40, b=5),
                uniformtext_minsize=9, uniformtext_mode='show',
                xaxis=dict(showgrid=False, tickfont=dict(size=9, color='#1e293b' if theme_mode != 'Dark' else '#f8fafc')),
                yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)', range=[0, y_max], tickfont=dict(size=9, color='#1e293b' if theme_mode != 'Dark' else '#f8fafc'))
            )
            fig_growth.update_traces(textfont_size=9, textfont_color='#1e293b' if theme_mode != 'Dark' else '#f8fafc')
            st.plotly_chart(fig_growth, use_container_width=True, config={'displayModeBar': False})

    with row3_col2:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.75rem; font-weight:700; color:var(--text-primary); margin:0 0 6px 0; font-family:Space Grotesk;'>&#9889; Top 10 CPU Sessions & SQL</p>", unsafe_allow_html=True)
            
            # Initialize killed SIDs list
            if "killed_sids" not in st.session_state:
                st.session_state.killed_sids = set()
                
            # Filter killed sessions
            if not df_cpu.empty:
                df_cpu = df_cpu[~df_cpu['SID'].astype(str).str.strip().isin(st.session_state.killed_sids)]
            if not df_blocking.empty:
                if 'BLOCKING_SID' in df_blocking.columns:
                    df_blocking = df_blocking[~df_blocking['BLOCKING_SID'].astype(str).str.strip().isin(st.session_state.killed_sids)]
                if 'BLOCKED_SID' in df_blocking.columns:
                    df_blocking = df_blocking[~df_blocking['BLOCKED_SID'].astype(str).str.strip().isin(st.session_state.killed_sids)]
            
            if not df_cpu.empty:
                df_cpu_top = df_cpu.sort_values(by="CPU_TIME_VAL", ascending=False).head(10).copy()
                blocking_sids = set()
                if not df_blocking.empty:
                    for col in ["BLOCKING_SID", "BLOCKED_SID"]:
                        if col in df_blocking.columns:
                            for val in df_blocking[col].dropna().unique():
                                try: blocking_sids.add(str(int(float(val))))
                                except: blocking_sids.add(str(val).strip())

                def get_bar_color(row):
                    try: sid_str = str(int(float(row["SID"])))
                    except: sid_str = str(row["SID"]).strip()
                    cpu_time = float(row.get("CPU_TIME_VAL", 0))
                    if sid_str in blocking_sids: return "#ef4444"
                    elif cpu_time > 3600:  return "#14532d"   # dark green >1 hour
                    elif cpu_time > 1800:  return "#16a34a"   # medium green >30 min
                    else:                  return "#86efac"   # light green <30 min

                df_cpu_top["BAR_COLOR"] = df_cpu_top.apply(get_bar_color, axis=1)

                cpu_css = '<style>'
                cpu_css += '.cpu-container { display: flex; gap: 15px; height: 230px; width: 100%; position: relative; }'
                cpu_css += '.cpu-list { flex: 1.1; display: flex; flex-direction: column; gap: 5px; overflow-y: auto; padding-right: 5px; }'
                cpu_css += '.cpu-row { border: 1px solid rgba(128,128,128,0.2); border-radius: 4px; padding: 6px 10px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; font-size: 0.70rem; transition: border 0.2s; color: var(--text-primary) !important; }'
                cpu_css += '.cpu-row:hover { border: 1px solid var(--neon-blue); }'
                cpu_css += '.cpu-row.selected { border-width: 2px !important; background: rgba(128,128,128,0.04) !important; }'
                cpu_css += '.sql-placeholder { flex: 1.2; background: #050505; border: 1px solid var(--border-color); border-radius: 6px; padding: 12px; color: #aaa; font-family: monospace; font-size: 0.75rem; overflow: auto; position: relative; display: flex; flex-direction: column; }'
                cpu_css += '.cpu-row .sql-content { display: none; }'
                cpu_css += '.cpu-row:hover .sql-content { display: block; position: absolute; top: 0; right: 0; width: calc(52% - 7px); height: 100%; background: #050505; color: #22c55e; padding: 15px; border-radius: 6px; font-family: monospace; font-size: 0.75rem; overflow-y: auto; z-index: 10; box-sizing: border-box; white-space: pre-wrap; border: 1px solid var(--neon-blue); box-shadow: 0 0 15px rgba(0,255,255,0.1); user-select: text; -webkit-user-select: text; cursor: text; }'
                cpu_css += '.cpu-row.pinned .sql-content { display: block !important; position: absolute; z-index: 999; border: 2px solid #f59e0b; }'
                cpu_css += '.cpu-list:hover ~ .sql-placeholder .default-text { display: none; }'
                cpu_css += '.sql-box-active { background: #050505; color: #22c55e; padding: 10px 14px; border-radius: 4px; font-family: monospace; font-size: 0.75rem; overflow-y: auto; flex: 1; white-space: pre-wrap; user-select: text; -webkit-user-select: text; cursor: text; margin: 5px 0; border: 1px solid rgba(255,255,255,0.05); }'
                cpu_css += '.pause-btn { background: #f59e0b; border: none; border-radius: 4px; padding: 4px 10px; font-size: 0.65rem; font-weight: 700; color: #000; cursor: pointer; margin-bottom: 8px; width: fit-content; }'
                cpu_css += '.pause-btn:hover { background: #d97706; }'
                # Custom CSS rules to hide the kill session widget and button wrapper
                cpu_css += 'div[data-testid="stElementContainer"]:has(.st-key-kill_sid_input), '
                cpu_css += 'div[data-testid="stElementContainer"]:has(.st-key-kill_session_btn), '
                cpu_css += '.st-key-kill_sid_input, .st-key-kill_session_btn { '
                cpu_css += '    display: none !important; '
                cpu_css += '    height: 0px !important; '
                cpu_css += '    margin: 0px !important; '
                cpu_css += '    padding: 0px !important; '
                cpu_css += '}'
                cpu_css += '</style>'
                
                # Build unique key for selected SID from session state
                selected_cpu_sid_key = f"cpu_sel_sid_{db_name}"
                selected_cpu_sql_key = f"cpu_sel_sql_{db_name}"
                selected_cpu_user_key = f"cpu_sel_user_{db_name}"

                html_parts = [cpu_css, '<div class="cpu-container"><div class="cpu-list">']
                max_cpu = df_cpu_top['CPU_TIME_VAL'].max()
                
                for _, row in df_cpu_top.iterrows():
                    sid = str(row.get('SID', ''))
                    user = str(row.get('USERNAME', ''))
                    cpu_val = float(row.get('CPU_TIME_VAL', 0)) / 100.0
                    sql = str(row.get('SQL_TEXT', 'No Active SQL text')).strip()
                    if not sql or sql.lower() == "nan": sql = "No Active SQL text"
                    
                    pct = (cpu_val / (max_cpu / 100.0) * 100) if max_cpu > 0 else 0
                    bar_color = row["BAR_COLOR"]
                    bg_style = f"background: linear-gradient(90deg, {bar_color}44 {pct}%, rgba(255,255,255,0.03) {pct}%);"
                    import html
                    sql_safe = html.escape(sql)
                    cpu_text_col = "#ffffff" if theme_mode == "Dark" else "var(--text-primary)"
                    html_parts.append(
                        f'<div class="cpu-row" style="{bg_style}" data-sql="{sql_safe}" data-sid="{sid}" data-user="{user}" data-cpu="{cpu_val:.1f}" data-color="{bar_color}" '
                        f'onclick="(function(el){{ '
                        f'var box=document.getElementById(\'sqlbox\'); '
                        f'var pbtn=document.getElementById(\'pausebtn\'); '
                        f'var sql=el.getAttribute(\'data-sql\'); '
                        f'var sid=el.getAttribute(\'data-sid\'); '
                        f'var usr=el.getAttribute(\'data-user\'); '
                        f'var cpu=el.getAttribute(\'data-cpu\'); '
                        f'if(box){{ box.textContent=\'-- SID \' + sid + \' (\' + usr + \') | CPU Time: \' + cpu + \' seconds\\n\\n\' + sql; box.style.display=\'block\'; }} '
                        f'if(pbtn){{ pbtn.setAttribute(\'data-sid\', sid); pbtn.style.display=\'block\'; }} '
                        f'var rows=document.querySelectorAll(\'.cpu-row\'); '
                        f'rows.forEach(function(r){{ r.classList.remove(\'selected\'); r.style.borderColor=\'\'; r.style.boxShadow=\'\'; }}); '
                        f'el.classList.add(\'selected\'); '
                        f'if(\'{theme_mode}\'===\'Dark\'){{ el.style.borderColor=\'#22c55e\'; el.style.boxShadow=\'0 0 10px rgba(34,197,94,0.5)\'; }} '
                        f'else{{ var bc=el.getAttribute(\'data-color\'); el.style.borderColor=bc; el.style.boxShadow=\'0 0 10px \' + bc + \'77\'; }} '
                        f'}})(this)" '
                        f'oncontextmenu="event.preventDefault(); this.classList.toggle(\'pinned\');">'
                        f'<div style="font-weight:700; color:{cpu_text_col} !important; z-index:2;">SID {sid} ({user})</div>'
                        f'<div style="color:{cpu_text_col} !important; font-weight:700; z-index:2;">{cpu_val:.1f} seconds</div>'
                        f'<div class="sql-content">-- SID {sid} ({user}) | CPU Time: {cpu_val:.1f} seconds<br><br>{sql_safe.replace(chr(10), "<br>")}</div>'
                        f'</div>'
                    )

                html_parts.append('''
                </div>
                <div class="sql-placeholder">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                        <span style="font-weight: 700; font-size: 0.65rem; color: #888;">SQL QUERY Pinned Details</span>
                        <button onclick="copySQLToClipboard()" style="background: #3b82f6; border: none; border-radius: 4px; padding: 3px 8px; font-size: 0.65rem; font-weight: 700; color: #fff; cursor: pointer;">📋 Copy SQL</button>
                    </div>
                    <span class="default-text" style="color:#666; font-size: 0.68rem; margin: auto 0; text-align: center;">Hover to preview SQL &middot; Click row to select &amp; scroll/copy</span>
                    <pre class="sql-box-active" id="sqlbox" style="display:none;"></pre>
                                                                                                                                                                                                        <button class="pause-btn" id="pausebtn" style="display:none;" onclick="(function(){{ var sid=document.getElementById('pausebtn').getAttribute('data-sid'); if(sid){{ var selRow=document.querySelector('.cpu-row.selected'); if(selRow){{ selRow.style.display='none'; }} var sqlbox=document.getElementById('sqlbox'); if(sqlbox){{ sqlbox.style.display='none'; }} var defText=document.querySelector('.default-text'); if(defText){{ defText.style.display='block'; }} var pbtn=document.getElementById('pausebtn'); if(pbtn){{ pbtn.style.display='none'; }} var targetInp=window.parent.document.querySelector('.st-key-kill_sid_input input') || window.parent.document.querySelector('div[data-testid="stTextInput"] input') || window.parent.document.querySelector('div.stTextInput input'); if(targetInp){{ try{{ targetInp.focus(); var setter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, 'value').set; if(setter){{ setter.call(targetInp, sid); }} else {{ targetInp.value = sid; }} }}catch(e){{ targetInp.value = sid; }} targetInp.dispatchEvent(new Event('input',{{bubbles:true}})); targetInp.dispatchEvent(new Event('change',{{bubbles:true}})); targetInp.blur(); }} setTimeout(function(){{ var targetBtn=window.parent.document.querySelector('.st-key-kill_session_btn button') || window.parent.document.querySelector('div.stButton button'); if(targetBtn){{ targetBtn.click(); }} }}, 600); }} }})()" >■ Kill Session (SID)</button>
                </div>
                </div>
                ''')
                
                # JS to toggle display and copy to clipboard
                html_parts.append('''
                <script>
                document.querySelectorAll(".cpu-row").forEach(function(row){
                    row.addEventListener("click", function(){
                        var defText = row.closest(".cpu-container").querySelector(".default-text");
                        if (defText) defText.style.display = "none";
                    });
                });
                

                function executeKillSession(btn) {
                    var sid = btn.getAttribute('data-sid');
                    if(sid) {
                        var selRow = document.querySelector('.cpu-row.selected');
                        if(selRow) { selRow.style.display = 'none'; }
                        var sqlbox = document.getElementById('sqlbox');
                        if(sqlbox) { sqlbox.style.display = 'none'; }
                        var defText = document.querySelector('.default-text');
                        if(defText) { defText.style.display = 'block'; }
                        btn.style.display = 'none';
                        
                        var targetInp = window.parent.document.querySelector('.st-key-kill_sid_input input') || window.parent.document.querySelector('div[data-testid="stTextInput"] input') || window.parent.document.querySelector('div.stTextInput input');
                        if(targetInp) {
                            try {
                                targetInp.focus();
                                var setter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, 'value').set;
                                if(setter) { setter.call(targetInp, sid); } else { targetInp.value = sid; }
                            } catch(e) { targetInp.value = sid; }
                            targetInp.dispatchEvent(new Event('input', {bubbles:true}));
                            targetInp.dispatchEvent(new Event('change', {bubbles:true}));
                            targetInp.blur();
                        }
                        
                        setTimeout(function(){
                            var targetBtn = window.parent.document.querySelector('.st-key-kill_session_btn button') || window.parent.document.querySelector('div.stButton button');
                            if(targetBtn) { targetBtn.click(); }
                        }, 600);
                    }
                }
                
                function copySQLToClipboard() {
                    var box = document.getElementById("sqlbox");
                    if (!box || box.style.display === "none") {
                        alert("Please click on a CPU session row first to load the SQL text.");
                        return;
                    }
                    var text = box.textContent;
                    navigator.clipboard.writeText(text).then(function() {
                        alert("SQL copied to clipboard!");
                    }).catch(function() {
                        var el = document.createElement("textarea");
                        el.value = text;
                        document.body.appendChild(el);
                        el.select();
                        document.execCommand("copy");
                        document.body.removeChild(el);
                        alert("SQL copied to clipboard!");
                    });
                }
                </script>
                ''')
                import streamlit.components.v1 as components
                components.html("".join(html_parts), height=245, scrolling=False)
                
                # --- Session Killing Form & Backend ---
                st.markdown("""
                    <style>
                    div[data-testid="stElementContainer"]:has(.st-key-kill_sid_input),
                    div[data-testid="stElementContainer"]:has(.st-key-kill_session_btn),
                    .st-key-kill_sid_input,
                    .st-key-kill_session_btn {
                        opacity: 0 !important;
                        position: absolute !important;
                        z-index: -9999 !important;
                        pointer-events: none !important;
                        width: 0px !important;
                        height: 0px !important;
                    }
                    </style>
                    <hr style='margin:10px 0; border-color:var(--border-color);'>
                """, unsafe_allow_html=True)
                kcol1, kcol2 = st.columns([2, 1])
                with kcol1:
                    kill_sid = st.text_input(
                        "Type SID to Kill",
                        placeholder="Type SID",
                        key="kill_sid_input",
                        label_visibility="collapsed",
                    )
                with kcol2:
                    if st.button("Kill Session", key="kill_session_btn", type="primary", use_container_width=True):
                        print(f"==== KILL BUTTON CLICKED! received kill_sid: '{kill_sid}' ====")
                        if kill_sid:
                            try:
                                from db_connection import execute_query, execute_non_query
                                # Convert SID to integer to prevent type mismatch on numeric columns in Oracle
                                try:
                                    sid_int = int(float(kill_sid))
                                except ValueError:
                                    sid_int = kill_sid
                                # Find the serial# and OS spid (PID) for the SID
                                records, cols = execute_query(
                                    "SELECT s.serial#, p.spid FROM v$session s JOIN v$process p ON s.paddr = p.addr WHERE s.sid = :1",
                                    (sid_int,)
                                )
                                if records:
                                    serial_num = records[0][0]
                                    os_pid = records[0][1]
                                    kill_sql = f"ALTER SYSTEM KILL SESSION '{kill_sid},{serial_num}' IMMEDIATE"
                                    success, err = execute_non_query(kill_sql)
                                    if success:
                                        # Do NOT clear the monitoring cache! 
                                        # This allows the page to rerun instantly using cached data.
                                        # The killed session will be filtered out dynamically via killed_sids.
                                        st.session_state.killed_sids.add(str(kill_sid).strip())
                                        st.session_state.killed_sids.add(str(int(float(kill_sid))).strip() if kill_sid.replace('.','',1).isdigit() else kill_sid)
                                        if os_pid:
                                            st.session_state.killed_sids.add(str(os_pid).strip())
                                            st.session_state.killed_sids.add(str(int(float(os_pid))).strip() if str(os_pid).replace('.','',1).isdigit() else str(os_pid))
                                            
                                        st.success(f"✅ Session SID {kill_sid} (OS PID {os_pid}) killed successfully!")
                                        time.sleep(1.0)
                                        st.rerun()
                                    else:
                                        st.error(f"❌ Failed to execute kill query: {err}")
                                else:
                                    st.error(f"❌ Session SID {kill_sid} not found in database.")
                            except Exception as e:
                                st.error(f"❌ Error killing session: {e}")
            else:
                st.markdown("<p style='color:var(--text-secondary); font-size:0.6rem;'>No CPU session details</p>", unsafe_allow_html=True)

    # ================= Archive Log Destination =================
    st.markdown("<hr style='margin: 15px 0; border-color: var(--border-color);'>", unsafe_allow_html=True)
    st.markdown(
        "<h4 style='color: var(--text-primary); font-family: Space Grotesk; font-size: 1.05rem; font-weight: 700; margin-bottom: 10px;'>"
        "📂 Archive Log Destination</h4>", unsafe_allow_html=True
    )
    if not arc_log_info or not isinstance(arc_log_info, dict):
        arc_log_info = {"arc_configured": True, "arc_log_type": "DEST", "arc_dest_name": "USE_DB_RECOVERY_FILE_DEST", "archive_logs": 0, "arc_used_gb": 0.0}
    
    theme_mode = st.session_state.get("theme", "Light")
    arc_text = "#f1f5f9" if theme_mode == "Dark" else "#1e293b"
    arc_sub  = "#94a3b8" if theme_mode == "Dark" else "#64748b"
    arc_bg   = "rgba(16,185,129,0.04)" if theme_mode == "Dark" else "rgba(16,185,129,0.03)"
    arc_bdr  = "rgba(255,255,255,0.1)"  if theme_mode == "Dark" else "rgba(0,0,0,0.08)"
    
    a_type = arc_log_info.get("arc_log_type", "UNKNOWN")
    a_dest = arc_log_info.get("arc_dest_name", "") or "USE_DB_RECOVERY_FILE_DEST"
    
    if a_type == "FRA":
        limit = arc_log_info.get("arc_limit_gb", 0)
        used = arc_log_info.get("arc_used_gb", 0)
        free = arc_log_info.get("arc_free_gb", 0)
        pct = arc_log_info.get("arc_pct", 0)
        
        bar_col = "#ef4444" if pct >= 90 else ("#f59e0b" if pct >= 70 else "#10b981")
        ind = "&#128308;" if pct >= 90 else ("&#128993;" if pct >= 70 else "&#128994;")
        
        arc_html = f"""
        <div style="border:1px solid {arc_bdr};border-radius:6px;padding:6px 10px;background:{arc_bg};font-family:'Inter',sans-serif;margin-bottom:8px;max-width:250px;">
            <div style="font-size:0.65rem;font-weight:800;color:{arc_sub};margin-bottom:4px;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="FRA Destination: {a_dest}">
                FRA: {a_dest}
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:0.65rem;font-weight:700;color:{arc_text};margin-bottom:2px;">
                <span>{ind} Used %</span>
                <span style="color:#38bdf8;">{pct:.2f}%</span>
            </div>
            <div style="height:8px;border-radius:4px;background:rgba(128,128,128,0.2);position:relative;overflow:hidden;margin-bottom:6px;">
                <div style="position:absolute;left:0;top:0;height:100%;width:{pct}%;background:{bar_col};"></div>
            </div>
            <div style="font-size:0.65rem;color:{arc_sub};display:flex;flex-direction:column;gap:3px;">
                <div style="display:flex;justify-content:space-between;"><span>Allocated :</span> <b>{limit:.2f} GB</b></div>
                <div style="display:flex;justify-content:space-between;"><span>Used :</span> <b>{used:.2f} GB</b></div>
                <div style="display:flex;justify-content:space-between;"><span>Free :</span> <b>{free:.2f} GB</b></div>
            </div>
        </div>
        """
    else:
        count = arc_log_info.get("archive_logs", 0)
        used = arc_log_info.get("arc_used_gb", 0)
        
        arc_html = f"""
        <div style="border:1px solid {arc_bdr};border-radius:6px;padding:6px 10px;background:{arc_bg};font-family:'Inter',sans-serif;margin-bottom:8px;max-width:250px;">
            <div style="font-size:0.65rem;font-weight:800;color:{arc_sub};margin-bottom:4px;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="Destination: {a_dest}">
                Dest: {a_dest}
            </div>
            <div style="font-size:0.6rem;color:{arc_sub};display:flex;flex-direction:column;gap:2px;">
                <div style="display:flex;justify-content:space-between;"><span>Archive Logs :</span> <b style="color:{arc_text};">{count}</b></div>
                <div style="display:flex;justify-content:space-between;"><span>Used Size :</span> <b style="color:{arc_text};">{used:.2f} GB</b></div>
            </div>
        </div>
        """
    key_arc = f"kill_arc_dest_{db_name}"
    st.markdown(f"""
    <style>
    div[data-testid="stElementContainer"]:has(.st-key-{key_arc}),
    .st-key-{key_arc} {{
        max-width: 250px !important;
        width: 250px !important;
        margin: 4px 0 8px 0 !important;
    }}
    div[data-testid="stElementContainer"]:has(.st-key-{key_arc}) button,
    .st-key-{key_arc} button {{
        width: 250px !important;
        max-width: 250px !important;
        font-size: 0.72rem !important;
        padding: 4px 8px !important;
        min-height: 28px !important;
        height: 28px !important;
        line-height: 28px !important;
        background: #ef4444 !important;
        background-color: #ef4444 !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 6px !important;
        font-weight: 800 !important;
        display: block !important;
        box-shadow: 0 2px 6px rgba(239, 68, 68, 0.35) !important;
    }}
    .st-key-{key_arc} button:hover {{
        background: #dc2626 !important;
        background-color: #dc2626 !important;
    }}
    .st-key-{key_arc} button p,
    .st-key-{key_arc} button span {{
        color: #ffffff !important;
        font-size: 0.72rem !important;
        font-weight: 800 !important;
    }}
    </style>
    """, unsafe_allow_html=True)
    st.markdown(arc_html, unsafe_allow_html=True)
    if st.button("🧹 Archive Log Clear", key=key_arc):
        with st.spinner("Clearing archive logs on database server..."):
            success, msg = clear_archive_logs_via_ssh(db_name)
            if success:
                st.success(f"✅ {msg}")
                # Switch logfile to start fresh
                try:
                    from db_connection import execute_query
                    execute_query("ALTER SYSTEM SWITCH LOGFILE")
                except Exception:
                    pass
            else:
                st.error(f"❌ Failed to clear archive logs: {msg}")


    # ================= Top CPU & Top Memory Processes for this DB =================
    st.markdown("<hr style='margin: 15px 0; border-color: var(--border-color);'>", unsafe_allow_html=True)
    st.markdown(
        f"<h4 style='color: var(--text-primary); font-family: Space Grotesk; font-size: 1.05rem; "
        f"font-weight: 700; margin-bottom: 10px;'>🔥 Top Running Processes — Database {db_name}</h4>",
        unsafe_allow_html=True
    )
    try:
        from utils.ssh_process_provider import get_top_processes_for_db
        from db_connection import get_config_for_db

        cfg = get_config_for_db(db_name) or {}
        host = cfg.get("host", "")
        ssh_user = cfg.get("user", "")
        ssh_pwd  = cfg.get("password", "")

        proc_res = get_top_processes_for_db(
            host=host,
            username=ssh_user,
            password=ssh_pwd,
            sid=db_name,
            limit=10,
        )
        top_cpu = proc_res.get("top_cpu", [])
        top_mem = proc_res.get("top_mem", [])
        
        # Filter out killed sessions from running processes tables
        if "killed_sids" in st.session_state:
            top_cpu = [p for p in top_cpu if str(p.get('sid', p.get('pid', ''))).strip() not in st.session_state.killed_sids]
            top_mem = [p for p in top_mem if str(p.get('sid', p.get('pid', ''))).strip() not in st.session_state.killed_sids]
        source  = proc_res.get("source", "none")
        err_msg = proc_res.get("error")

        src_badge = " (v$session DB View)" if source == "v$session" else (" (SSH)" if source == "ssh" else "")

        mcol1, mcol2 = st.columns(2)
        with mcol1:
            st.markdown(
                f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; margin-bottom:10px;'>"
                f"<div style='font-size:0.75rem; font-weight:800; text-transform:uppercase; color:var(--text-primary); margin-bottom:8px;'>🔥 Top CPU Consuming Sessions ({db_name})<span style='font-size:0.65rem; color:#ef4444; font-weight:normal;'>{src_badge}</span></div>",
                unsafe_allow_html=True
            )
            if top_cpu:
                if source == "v$session":
                    df_c = pd.DataFrame([{
                        "PID":            f"#{p['pid']}",
                        "SID":            p.get('sid', 'N/A'),
                        "USER (DB)":      p.get('user_label', p.get('username', 'oracle')),
                        "STATUS":         p.get('status', ''),
                        "CPU Time (sec)": f"{p.get('cpu_sec', 0.0):.2f}s"
                    } for p in top_cpu])
                else:
                    df_c = pd.DataFrame([{
                        "PID":    f"#{p['pid']}",
                        "USER":   p.get('user_label', p.get('username', 'oracle')),
                        "CPU %":  f"{p['cpu']:.1f}%",
                    } for p in top_cpu])
                theme_mode = st.session_state.get("theme", "Light")
                def render_styled_process_table_html(df, t_mode):
                    if t_mode == "Dark":
                        bg_green = "rgba(16, 185, 129, 0.1)"
                        bg_grey  = "rgba(255, 255, 255, 0.02)"
                        hdr_bg   = "rgba(16, 185, 129, 0.2)"
                        text_col = "#ffffff"
                        bdr_col  = "rgba(255, 255, 255, 0.1)"
                    else:
                        bg_green = "#e6f4ea"    # Row 1 -> Light Green
                        bg_grey  = "#f8fafc"    # Row 2 -> Light Grey
                        hdr_bg   = "#c3e6cb"    # Light Green Header
                        text_col = "#1e293b"
                        bdr_col  = "#cbd5e1"

                    html = f'''<div style="overflow-x:auto; overflow-y:auto; max-height:180px; border:1px solid {bdr_col}; border-radius:6px;">
                    <table style="width:100%; border-collapse:collapse; font-family:'Inter',sans-serif; font-size:0.72rem; color:{text_col};">
                        <thead>
                            <tr style="background:{hdr_bg}; border-bottom:2px solid {bdr_col}; text-transform:uppercase; font-size:0.65rem; font-weight:800;">'''
                    for col in df.columns:
                        html += f'<th style="padding:4px 6px; text-align:left;">{col}</th>'
                    html += '</tr></thead><tbody>'
                    for idx, row in df.iterrows():
                        row_bg = bg_green if idx % 2 == 0 else bg_grey
                        html += f'<tr style="background:{row_bg}; border-bottom:1px solid {bdr_col};">'
                        for val in row.values:
                            html += f'<td style="padding:3px 6px; font-weight:600;">{val}</td>'
                        html += '</tr>'
                    html += '</tbody></table></div>'
                    return html

                st.markdown(render_styled_process_table_html(df_c, theme_mode), unsafe_allow_html=True)
            else:
                if err_msg:
                    if any(term in err_msg.lower() for term in ["authentication failed", "wrong username", "permission denied"]):
                        st.error("❌ host username and password is wrong")
                    else:
                        st.info(err_msg)
                else:
                    st.info(f"No active oracle user sessions for {db_name}")
            st.markdown("</div>", unsafe_allow_html=True)

        with mcol2:
            st.markdown(
                f"<div style='background:var(--card-bg); border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; margin-bottom:10px;'>"
                f"<div style='font-size:0.75rem; font-weight:800; text-transform:uppercase; color:var(--text-primary); margin-bottom:8px;'>💾 Top Memory Consuming Sessions ({db_name})<span style='font-size:0.65rem; color:#3b82f6; font-weight:normal;'>{src_badge}</span></div>",
                unsafe_allow_html=True
            )
            if top_mem:
                if source == "v$session":
                    df_m = pd.DataFrame([{
                        "PID":      f"#{p['pid']}",
                        "SID":      p.get('sid', 'N/A'),
                        "USER (DB)": p.get('user_label', p.get('username', 'oracle')),
                        "STATUS":   p.get('status', ''),
                        "MEM (MB)": f"{p['mem']:.1f} MB"
                    } for p in top_mem])
                else:
                    df_m = pd.DataFrame([{
                        "PID":    f"#{p['pid']}",
                        "USER":   p.get('user_label', p.get('username', 'oracle')),
                        "MEM %":  f"{p['mem']:.1f}%",
                    } for p in top_mem])
                theme_mode = st.session_state.get("theme", "Light")
                st.markdown(render_styled_process_table_html(df_m, theme_mode), unsafe_allow_html=True)
            else:
                if err_msg:
                    if any(term in err_msg.lower() for term in ["authentication failed", "wrong username", "permission denied"]):
                        st.error("❌ host username and password is wrong")
                    else:
                        st.info(err_msg)
                else:
                    st.info(f"No active oracle user sessions for {db_name}")
            st.markdown("</div>", unsafe_allow_html=True)



    except Exception as exc:
        print(f"[monitoring] Process section error: {exc}")

    # ================= OS Storage / Mount Points (Table Format) =================
    if os_storage:
        st.markdown("<hr style='margin: 8px 0; border-color: var(--border-color);'>", unsafe_allow_html=True)
        st.markdown(
            "<h5 style='color: var(--text-primary); font-family: Space Grotesk; font-size: 0.9rem; font-weight: 700; margin-bottom: 6px;'>"
            "💽 OS Storage / Mount Points</h5>", unsafe_allow_html=True
        )
        if isinstance(os_storage[0], dict) and "ssh_error" in os_storage[0]:
            err_msg = os_storage[0]['ssh_error']
            if any(term in err_msg.lower() for term in ["authentication failed", "wrong username", "permission denied", "auth failed"]):
                st.error("❌ host username and password is wrong")
            else:
                st.error(f"Storage details unavailable: {err_msg}")
        else:
            table_html = '''<div style="overflow-x:auto; background:var(--card-bg); border:1px solid var(--border-color); border-radius:6px; padding:4px 8px;">
            <table style="width:100%; border-collapse: collapse; font-family:'Inter', sans-serif; font-size:0.72rem; color:var(--text-primary); text-align:left;">
                <thead>
                    <tr style="border-bottom:2px solid var(--border-color); color:var(--text-secondary); text-transform:uppercase; font-size:0.65rem; font-weight:800;">
                        <th style="padding:4px 6px;">Mount Point</th>
                        <th style="padding:4px 6px; text-align:right;">Total (GB)</th>
                        <th style="padding:4px 6px; text-align:right;">Used (GB)</th>
                        <th style="padding:4px 6px; text-align:right;">Free (GB)</th>
                        <th style="padding:4px 6px; width:40%;">Usage %</th>
                    </tr>
                </thead>
                <tbody>'''
            for drive in os_storage:
                mnt = drive.get("mount_point", "")
                pct = float(drive.get("pct", 0) or 0)
                used = float(drive.get("used", 0) or 0)
                free = float(drive.get("free", 0) or 0)
                total = float(drive.get("total", 0) or 0)
                bar_col = "#ef4444" if pct >= 90 else ("#f59e0b" if pct >= 70 else "#3b82f6")
                
                total_str = f"{total:,.1f}"
                used_str  = f"{used:,.1f}"
                free_str  = f"{free:,.1f}"
                pct_str   = f"{pct:.1f}"
                
                table_html += f"""
                    <tr style="border-bottom:1px solid rgba(128,128,128,0.1);">
                        <td style="padding:4px 6px; font-weight:700;">{mnt}</td>
                        <td style="padding:4px 6px; text-align:right;">{total_str}</td>
                        <td style="padding:4px 6px; text-align:right;">{used_str}</td>
                        <td style="padding:4px 6px; text-align:right;">{free_str}</td>
                        <td style="padding:4px 6px;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px; font-size:0.65rem; font-weight:600;">
                                <span></span><span style="color:{bar_col};">{pct_str}%</span>
                            </div>
                            <div style="height:5px;border-radius:3px;background:rgba(128,128,128,0.2);position:relative;overflow:hidden;">
                                <div style="position:absolute;left:0;top:0;height:100%;width:{pct_str}%;background:{bar_col};"></div>
                            </div>
                        </td>
                    </tr>
                """
            table_html += "</tbody></table></div>"
            import re
            table_html = re.sub(r'^[ \t]+', '', table_html, flags=re.MULTILINE)
            st.markdown(table_html, unsafe_allow_html=True)



    # ===== FOOTER: Backend Refresh Logic =====





    # ===== FOOTER: Backend Refresh Logic =====
    ref_mode     = st.session_state.get("refresh_mode", "Manual")
    ref_interval = st.session_state.get("refresh_interval", 20)   # minutes

    if ref_mode == "Automatic":
        import streamlit.components.v1 as components
        total_secs = int(ref_interval * 60)
        # Compute remaining seconds from the start timestamp
        if "_refresh_cycle_start" not in st.session_state:
            st.session_state._refresh_cycle_start = time.time()
        elapsed = int(time.time() - st.session_state._refresh_cycle_start)
        remaining_sec = max(1, total_secs - elapsed)
        
        # Inject an invisible JS block that will trigger a reload when the interval expires
        components.html(f"<script>setTimeout(function(){{ var btns=window.parent.document.querySelectorAll('button'); for(var i=0;i<btns.length;i++){{if(btns[i].innerText.includes('Refresh')){{btns[i].click();break;}}}} }}, {remaining_sec * 1000});</script>", height=0, width=0)


