"""
monitor_thread.py
Background monitoring thread - runs inside the Streamlit app (no separate process).
Collects all configured DBs every 2 minutes, writes cache file, fires email alerts.
"""

import os
import sys
import time
import json
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# -- Path setup --------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from db_connection import (
    load_db_names_api,
    get_config_for_db,
    get_standby_db_config,
    _ensure_thick_mode,
    _ORACLE_CONFIG_DIR,
)
from utils.alerts import check_and_trigger_alerts

# -- Config paths -------------------------------------------------------------
CONFIG_DIR       = os.path.join(_BASE_DIR, "config")
CACHE_FILE       = os.path.join(CONFIG_DIR, "db_monitor_cache.json")
STATUS_FILE      = os.path.join(CONFIG_DIR, "daemon_status.json")
FORCE_FLAG       = os.path.join(CONFIG_DIR, "force_refresh.flag")

POLL_INTERVAL    = 120   # 2 minutes
MAX_PARALLEL_DBS = 8     # databases checked concurrently per cycle

# -- Thread singleton guard ---------------------------------------------------
_thread_lock   = threading.Lock()
_thread_started = False

# -- Cooperative stop signal for graceful shutdown -----------------------------
# Checked between cycles (and during the poll wait) so the loop stops starting
# NEW work once shutdown is requested; it can't interrupt a query already
# blocked on network I/O, so callers should still bound how long they wait.
_stop_event = threading.Event()

# -- Guards for shared JSON files touched while DBs are checked in parallel --
_cache_write_lock = threading.Lock()
_alert_lock       = threading.Lock()   # alert_state.json is read-modify-write


# ═══════════════════════════════════════════════════════════════════════════
# Low-level query helper
# ═══════════════════════════════════════════════════════════════════════════
def _run_query(conn, sql: str):
    """Run SQL and return list-of-dicts. Never raises."""
    try:
        import pandas as pd
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [c[0].upper() for c in cur.description]
        cur.close()
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════
# Metric collection per DB
# ═══════════════════════════════════════════════════════════════════════════
def collect_db_metrics(db_name: str, host_cache: dict = None) -> dict:
    import oracledb

    cfg = get_config_for_db(db_name)
    if not cfg:
        return {
            "db": "DOWN", "listener": "DOWN", "backup": "UNKNOWN",
            "balance_ts": [], "full_ts": [], "drives": [], "processes": [],
            "system_res": None, "arc_pct": 0.0, "arc_configured": False,
            "has_blocking": False, "blocking_details": [],
            "ora_errors": [], "deadlock_detected": False,
            "session_maxed": False, "alert_log_errors": [],
            "tooltip_reasons": ["No database config in registry"],
            "last_refresh": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    stats = {
        "db": "DOWN", "listener": "DOWN", "backup": "UNKNOWN",
        "balance_ts": [], "full_ts": [], "drives": [], "processes": [],
        "system_res": None, "arc_pct": 0.0, "arc_configured": False,
        "arc_used_mb": 0.0, "arc_limit_mb": 0.0, "arc_free_mb": 0.0,
        "has_blocking": False, "blocking_details": [],
        "ora_errors": [], "deadlock_detected": False,
        "session_maxed": False, "alert_log_errors": [],
        "tooltip_reasons": [],
        "last_refresh": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    conn = None
    try:
        from db_connection import execute_proc_to_df, get_oracle_mode, get_standby_db_config
        c_dsn = str(cfg.get("dsn", ""))
        c_user = cfg.get("user", "")
        c_pass = cfg.get("password", "")
        mode = get_oracle_mode(c_user)

        try:
            conn = oracledb.connect(
                user=c_user, password=c_pass, dsn=c_dsn,
                mode=mode, tcp_connect_timeout=5
            )
        except Exception as conn_err:
            if cfg.get("host_username") and cfg.get("host_password"):
                print(f"[monitor] Direct connection failed ({conn_err}). Falling back to SSH Connection for {db_name}")
                from db_connection import SSHConnection
                conn = SSHConnection(cfg)
            else:
                raise conn_err
        stats["db"]       = "UP"
        stats["listener"] = "UP"

        # 1. Listener check
        try:
            rows = _run_query(conn, "SELECT type FROM v$listener_network WHERE ROWNUM=1")
            stats["listener"] = "UP" if rows else "DOWN"
        except Exception:
            stats["listener"] = "DOWN"

        # 2. Backup check
        try:
            rows = _run_query(conn,
                "SELECT status, start_time FROM v$rman_backup_job_details "
                "ORDER BY start_time DESC")
            if not rows:
                stats["backup"] = "PENDING"
            else:
                last_status = str(rows[0].get("STATUS", "FAIL")).upper()
                st_val = rows[0].get("START_TIME")
                days_ago = 999
                if st_val:
                    try:
                        import pandas as pd
                        days_ago = (datetime.datetime.now() - pd.to_datetime(st_val)).days
                    except Exception:
                        pass
                if days_ago <= 1:
                    if "COMPLETED" in last_status or "SUCCESS" in last_status:
                        stats["backup"] = "SUCCESS"
                    elif "FAIL" in last_status or "ERR" in last_status:
                        stats["backup"] = "FAILED"
                    else:
                        stats["backup"] = "PENDING"
                else:
                    stats["backup"] = "PENDING"
        except Exception:
            stats["backup"] = "UNKNOWN"

        # 3. Tablespaces
        try:
            rows = _run_query(conn, """
                SELECT df.tablespace_name,
                    ROUND(df.allocated_mb,2) AS allocated_mb,
                    ROUND(NVL(fs.free_mb,0),2) AS free_mb,
                    ROUND(df.allocated_mb - NVL(fs.free_mb,0),2) AS used_mb,
                    ROUND(df.max_mb,2) AS maxsize_mb,
                    ROUND(df.max_mb - (df.allocated_mb - NVL(fs.free_mb,0)),2) AS free_on_max_mb,
                    ROUND(((df.allocated_mb - NVL(fs.free_mb,0))/df.max_mb)*100,2) AS pct_used_max,
                    loc.location
                FROM (SELECT tablespace_name, SUM(bytes)/1024/1024 allocated_mb,
                    SUM(CASE WHEN autoextensible='YES' THEN maxbytes ELSE bytes END)/1024/1024 max_mb
                    FROM dba_data_files GROUP BY tablespace_name) df
                LEFT JOIN (SELECT tablespace_name, SUM(bytes)/1024/1024 free_mb FROM dba_free_space GROUP BY tablespace_name) fs
                    ON df.tablespace_name = fs.tablespace_name
                LEFT JOIN (SELECT tablespace_name, MAX(file_name) AS location FROM dba_data_files GROUP BY tablespace_name) loc
                    ON df.tablespace_name = loc.tablespace_name
                ORDER BY df.tablespace_name""")
            balance_ts = []
            for ts in rows:
                name = str(ts.get("TABLESPACE_NAME", "")).upper()
                if "TEMP" in name or "UNDO" in name:
                    continue
                pct       = float(ts.get("PCT_USED_MAX", 0) or 0)
                total_mb  = float(ts.get("MAXSIZE_MB", 0) or 0)
                used_mb   = float(ts.get("USED_MB", 0) or 0)
                free_mb   = float(ts.get("FREE_ON_MAX_MB", 0) or 0)
                alloc_mb  = float(ts.get("ALLOCATED_MB", 0) or 0)
                location  = str(ts.get("LOCATION", "Unknown"))
                balance_ts.append({
                    "name": ts["TABLESPACE_NAME"], "pct": pct,
                    "used_mb": used_mb, "free_mb": free_mb,
                    "total_mb": total_mb, "allocated_mb": alloc_mb,
                    "location": location
                })
            balance_ts.sort(key=lambda x: x["free_mb"])
            stats["balance_ts"] = balance_ts
            stats["full_ts"]    = [t["name"] for t in balance_ts if t["pct"] >= 90]
        except Exception as e:
            print(f"[monitor] Tablespace error {db_name}: {e}")

        # 4. Archive log (Dynamic Location)
        try:
            dest_rows = _run_query(conn, "SELECT destination FROM v$archive_dest WHERE dest_id = 1")
            dest = dest_rows[0].get("DESTINATION", "") if dest_rows else ""
            
            if dest == "USE_DB_RECOVERY_FILE_DEST":
                fra_rows = _run_query(conn, "SELECT name, ROUND(space_limit/1024/1024/1024,2) AS allocated_gb, ROUND(space_used/1024/1024/1024,2) AS used_gb, ROUND((space_used/space_limit)*100,2) AS pct_used FROM v$recovery_file_dest")
                if fra_rows:
                    r = fra_rows[0]
                    stats["arc_log_type"]   = "FRA"
                    stats["arc_configured"] = True
                    stats["arc_dest_name"]  = r.get("NAME", "FRA")
                    stats["arc_limit_gb"]   = float(r.get("ALLOCATED_GB", 0) or 0)
                    stats["arc_used_gb"]    = float(r.get("USED_GB", 0) or 0)
                    stats["arc_pct"]        = float(r.get("PCT_USED", 0) or 0)
                    stats["arc_free_gb"]    = max(0.0, stats["arc_limit_gb"] - stats["arc_used_gb"])
            else:
                arch_rows = _run_query(conn, "SELECT COUNT(*) AS archive_logs, ROUND(SUM(blocks*block_size)/1024/1024/1024,2) AS size_gb FROM v$archived_log")
                if arch_rows:
                    r = arch_rows[0]
                    stats["arc_log_type"]   = "CUSTOM"
                    stats["arc_configured"] = True
                    stats["arc_dest_name"]  = dest
                    stats["archive_logs"]   = int(r.get("ARCHIVE_LOGS", 0) or 0)
                    stats["arc_used_gb"]    = float(r.get("SIZE_GB", 0) or 0)
        except Exception:
            pass

        # 5. Blocking sessions
        try:
            rows = _run_query(conn,
                "SELECT s.sid, s.serial#, s.username, s.blocking_session, "
                "s.status, s.sql_id "
                "FROM v$session s WHERE s.blocking_session IS NOT NULL AND ROWNUM<=10")
            stats["has_blocking"]     = len(rows) > 0
            stats["blocking_details"] = rows
        except Exception:
            pass

        # 6. Deadlock detection from alert log / wait events
        try:
            deadlock_rows = _run_query(conn,
                "SELECT message_text FROM v$diag_alert_ext "
                "WHERE message_text LIKE '%deadlock%' "
                "AND originating_timestamp > SYSDATE - 1/24 "
                "AND ROWNUM <= 5")
            if deadlock_rows:
                stats["deadlock_detected"] = True
                stats["alert_log_errors"]  = [r.get("MESSAGE_TEXT", "") for r in deadlock_rows]
        except Exception:
            # Fallback: check v$session_event
            try:
                dl_rows = _run_query(conn,
                    "SELECT count(*) CNT FROM v$session_wait "
                    "WHERE event='enq: TX - row lock contention' AND ROWNUM<=1")
                stats["deadlock_detected"] = (dl_rows and int(dl_rows[0].get("CNT", 0)) > 0)
            except Exception:
                pass

        # 7. ORA errors from alert log
        try:
            ora_rows = _run_query(conn, """SELECT * FROM (SELECT TO_CHAR(originating_timestamp,'YYYY-MM-DD HH24:MI:SS') as time_str, message_text FROM v$diag_alert_ext WHERE originating_timestamp >= SYSDATE - 2 AND message_text LIKE '%ORA-%' ORDER BY originating_timestamp DESC) WHERE ROWNUM <= 50""")
            if ora_rows:
                msgs = [r.get("MESSAGE", r.get("MESSAGE_TEXT", "")) for r in ora_rows]
                ora_errors = [m for m in msgs if "ORA-" in str(m).upper()][:10]
                stats["ora_errors"] = ora_errors
        except Exception:
            pass

        # 8. Host OS resources
        host = cfg.get("host", "").strip()
        if host_cache is not None and host in host_cache and host_cache[host].get("system_res"):
            stats["system_res"] = host_cache[host]["system_res"]
        else:
            try:
                os_rows  = _run_query(conn, "SELECT stat_name, value FROM v$osstat WHERE stat_name IN ('NUM_CPUS', 'PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'IDLE_TIME', 'BUSY_TIME')")
                sys_rows = _run_query(conn, "SELECT metric_name, value FROM v$sysmetric WHERE metric_name IN ('Host CPU Utilization (%)', 'CPU Usage Per Sec', 'Session Count') AND group_id = 2")
                mem_rows = _run_query(conn, "SELECT (SELECT SUM(value) FROM v$sga) as sga_bytes, (SELECT SUM(pga_alloc_mem) FROM v$process) as pga_bytes FROM dual")

                os_stats  = {r["STAT_NAME"]: float(r["VALUE"] or 0) for r in os_rows}
                sys_stats = {r["METRIC_NAME"]: float(r["VALUE"] or 0) for r in sys_rows}

                sga_bytes = float(mem_rows[0].get("SGA_BYTES", 0) or 0) if mem_rows else 0.0
                pga_bytes = float(mem_rows[0].get("PGA_BYTES", 0) or 0) if mem_rows else 0.0

                num_cpus      = int(os_stats.get("NUM_CPUS", 1))
                total_ram     = float(os_stats.get("PHYSICAL_MEMORY_BYTES", 0))
                free_ram      = float(os_stats.get("FREE_MEMORY_BYTES", 0))
                cpu_used_pct  = float(sys_stats.get("Host CPU Utilization (%)", 0.0))
                cpu_oracle    = float(sys_stats.get("CPU Usage Per Sec", 0.0)) / max(num_cpus, 1)
                ram_total_gb  = total_ram / (1024**3)
                ram_free_gb   = free_ram  / (1024**3)
                ram_used_gb   = max(0.0, ram_total_gb - ram_free_gb)
                ram_oracle_gb = (sga_bytes + pga_bytes) / (1024**3)
                session_count = int(sys_stats.get("Session Count", 0))

                stats["system_res"] = {
                    "num_cpus":           num_cpus,
                    "cpu_host_used_pct":  round(cpu_used_pct, 1),
                    "cpu_host_free_pct":  round(max(0.0, 100.0 - cpu_used_pct), 1),
                    "cpu_oracle_used_pct": round(cpu_oracle, 1),
                    "ram_total_gb":       round(ram_total_gb, 2),
                    "ram_used_gb":        round(ram_used_gb, 2),
                    "ram_free_gb":        round(ram_free_gb, 2),
                    "ram_oracle_gb":      round(ram_oracle_gb, 2),
                    "session_count":      session_count
                }
                if host_cache is not None:
                    if host not in host_cache: host_cache[host] = {}
                    host_cache[host]["system_res"] = stats["system_res"]
            except Exception as e:
                print(f"[monitor] OS stats error {db_name}: {e}")

        # 9. Session max check
        try:
            max_rows = _run_query(conn, "SELECT name, value FROM v$parameter WHERE name = 'sessions'")
            if max_rows and stats["system_res"]:
                max_sess = int(max_rows[0].get("VALUE", 150))
                act_sess = stats["system_res"]["session_count"]
                stats["session_maxed"] = act_sess > max(50, int(max_sess * 0.85))
        except Exception:
            pass

        # 10. Standby destination status (from V$ARCHIVE_DEST_STATUS on Primary)
        standby_dests = []
        try:
            p_dest_rows = _run_query(conn, "SELECT DEST_ID, STATUS, TARGET, DB_UNIQUE_NAME, ERROR FROM V$ARCHIVE_DEST_STATUS WHERE TARGET = 'STANDBY'")
            for r in p_dest_rows:
                status = str(r.get("STATUS") or "").strip()
                error_msg = str(r.get("ERROR") or "").strip()
                db_uniq = str(r.get("DB_UNIQUE_NAME") or "").strip()
                dest_id = r.get("DEST_ID")
                standby_dests.append({
                    "dest_id": dest_id,
                    "status": status,
                    "db_unique_name": db_uniq,
                    "error": error_msg
                })
        except Exception:
            pass
        stats["standby_dests"] = standby_dests

        # 11. Primary last generated log sequence (for Data Guard gap check)
        primary_seqs = {}
        try:
            p_seq_rows = _run_query(conn, "SELECT THREAD#, MAX(SEQUENCE#) AS SEQ FROM V$ARCHIVED_LOG GROUP BY THREAD#")
            for r in p_seq_rows:
                t = int(r.get("THREAD#", 1))
                primary_seqs[t] = int(r.get("SEQ") or 0)
        except Exception:
            pass
        stats["primary_seqs"] = primary_seqs

        # conn is closed at the end of check_db_health after standby gap checks

    except Exception as e:
        stats["db"]       = "DOWN"
        stats["error"]    = str(e)
        
        # If DB connection fails, check TCP port to see if listener is up
        try:
            import socket
            sock = socket.create_connection((cfg["host"], int(cfg.get("port", 1521))), timeout=2)
            sock.close()
            stats["listener"] = "UP"
        except Exception:
            stats["listener"] = "DOWN"
        if conn:
            try: conn.close()
            except Exception: pass

    # -- Pre-compute tooltip_reasons --------------------------------------
    reasons = []
    if stats["db"] == "DOWN":
        reasons.append("Database is DOWN")
    if stats["listener"] == "DOWN":
        reasons.append("Listener is DOWN")
    for ts_name in stats.get("full_ts", []):
        ts_entry = next((t for t in stats["balance_ts"] if t["name"] == ts_name), None)
        pct_str  = f" ({ts_entry['pct']:.1f}%)" if ts_entry else ""
        reasons.append(f"Tablespace {ts_name} is{pct_str} utilized (≥90%)")
    if stats["arc_configured"] and stats["arc_pct"] >= 90:
        reasons.append(f"Archive Log (FRA) is {stats['arc_pct']:.1f}% full")
    if stats["has_blocking"]:
        reasons.append("Blocking/deadlock session detected")
    if stats["deadlock_detected"]:
        reasons.append("Deadlock detected in alert log")
    if stats.get("session_maxed"):
        reasons.append("Concurrent sessions > 85% of MAX_SESSIONS")
    if stats["ora_errors"]:
        reasons.append(f"ORA error: {stats['ora_errors'][0][:60]}")
    if stats["backup"] == "FAILED":
        reasons.append("Last RMAN backup FAILED")
    
    # Check for standby archive destination errors
    for d in stats.get("standby_dests", []):
        if d.get("error"):
            reasons.append(f"Data Guard Dest #{d['dest_id']} Error: {d['error'][:50]}")
            stats["log_gap_alert"] = True
            
    stats["tooltip_reasons"] = reasons

    # -- Reporting DB status check -----------------------------------------
    try:
        from db_connection import check_reporting_db_status
        rpt = check_reporting_db_status(db_name)
        stats["reporting_status"]         = rpt.get("status", "NOT_CONFIGURED")
        stats["reporting_listener"]       = rpt.get("listener", "NOT_CONFIGURED")
        stats["reporting_db_name"]        = rpt.get("reporting_db_name", "")
        stats["reporting_configured"]     = rpt.get("configured", False)
        stats["reporting_error_message"]  = rpt.get("error_message")
        stats["reporting_response_time"]  = rpt.get("response_time_ms", 0)
        stats["reporting_checked_at"]     = rpt.get("checked_at", "")
        stats["reporting_username"]        = rpt.get("reporting_username", "")
        stats["reporting_password"]        = rpt.get("reporting_password", "")
    except Exception as re:
        stats["reporting_status"]         = "NOT_CONFIGURED"
        stats["reporting_listener"]       = "NOT_CONFIGURED"
        stats["reporting_db_name"]        = ""
        stats["reporting_configured"]     = False
        stats["reporting_error"]          = str(re)
        stats["reporting_error_code"]     = "CLIENT_ERROR"
        stats["reporting_error_message"]  = str(re)
        stats["reporting_username"]        = ""
        stats["reporting_password"]        = ""

    # -- Standby DB log gap check ------------------------------------------
    stats["standby_configured"]  = False
    stats["standby_log_gap"]     = {}
    stats["log_gap_alert"]       = False
    try:
        stby_cfg = get_standby_db_config(db_name)
        if stby_cfg:
            _s_user = stby_cfg.get("user", "N/A")
            _s_dsn  = stby_cfg.get("service_name") or stby_cfg.get("dsn", "N/A")
            from queries.queries import get_log_gap_info
            from db_connection import SSHConnection

            # Query the standby via OCI-key SSH into the primary host: SSH in,
            # source the primary db's own .env, then sqlplus into the standby.
            if conn:
                try:
                    s_ssh = SSHConnection(cfg, is_standby=True, standby_cfg=stby_cfg)
                    dg_res = get_log_gap_info(primary_conn=conn, standby_conn=s_ssh)
                    if dg_res and dg_res.get("configured"):
                        stats["standby_configured"] = True
                        stats["standby_log_gap"] = dg_res
                        max_gap = dg_res.get("max_gap", 0)
                        threads = dg_res.get("threads", [])
                        if max_gap >= 2:
                            stats["log_gap_alert"] = True
                            if "tooltip_reasons" in stats:
                                stats["tooltip_reasons"].append(f"Standby DB log gap is {max_gap} sequences (≥2)")

                            # Send alert email
                            try:
                                from utils.alerts import send_alert_email
                                thread_details = "\n".join(
                                    f"  Thread {t['thread']}: Generated={t.get('primary_generated','N/A')} "
                                    f"Received={t.get('standby_received','N/A')} "
                                    f"Applied={t.get('standby_applied','N/A')} "
                                    f"Gap={t.get('gap','N/A')}"
                                    for t in threads
                                )
                                subject = f"⚠️ Data Guard Log Gap Alert — {db_name} (Gap: {max_gap} sequences)"
                                body = (
                                    f"Database: {db_name}\n"
                                    f"Standby DB: {stby_cfg['db_name']}\n"
                                    f"Maximum Log Gap: {max_gap} sequences\n\n"
                                    f"Thread Details:\n{thread_details}\n\n"
                                    f"Action Required: Investigate standby apply lag on {stby_cfg['host']}.\n"
                                    f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                )
                                send_alert_email(subject, body, alert_type="log_gap_alert")
                            except Exception as alert_err:
                                print(f"[monitor] Log gap alert email error for {db_name}: {alert_err}")
                    else:
                        err_msg = (dg_res or {}).get("error") or "Standby unreachable via OCI Key SSH"
                        stats["standby_log_gap"] = {"configured": False, "error": err_msg}
                except Exception as dg_err:
                    print(f"[monitor] Standby OCI-key SSH check failed for {db_name}: {dg_err}")
                    stats["standby_log_gap"] = {"configured": False, "error": str(dg_err)}
    except Exception as stby_outer_err:
        print(f"[monitor] Standby gap check outer error for {db_name}: {stby_outer_err}")

    if conn:
        try:
            conn.close()
        except Exception:
            pass

    return stats

# ═══════════════════════════════════════════════════════════════════════════

def _write_status(fetching: str = "", cycle_start: str = "", cycle_end: str = ""):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        data = {
            "fetching":          fetching,
            "last_cycle_start":  cycle_start,
            "last_cycle_end":    cycle_end
        }
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def read_monitor_status() -> dict:
    """Read daemon_status.json - safe to call from UI."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"fetching": "", "last_cycle_start": "", "last_cycle_end": ""}


def read_monitor_cache() -> dict:
    """Read db_monitor_cache.json - safe to call from UI."""
    try:
        # if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# Main monitoring loop
# ═══════════════════════════════════════════════════════════════════════════
def _monitor_loop():
    """Runs in background thread. Polls every 2 minutes."""
    print("[monitor_thread] Background monitoring started.")
    os.makedirs(CONFIG_DIR, exist_ok=True)

    while True:
        if _stop_event.is_set():
            print("[monitor_thread] Stop requested — exiting loop, not starting a new cycle.")
            return

        forced = os.path.exists(FORCE_FLAG)
        if forced:
            try: os.remove(FORCE_FLAG)
            except Exception: pass

        try:
            db_names = load_db_names_api()
            if not db_names:
                print("[monitor_thread] No configured databases found.")
                time.sleep(POLL_INTERVAL)
                continue

            cycle_start = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _write_status(fetching="", cycle_start=cycle_start)

            # Load existing cache and filter to keep only databases present in the active text file
            cache_data = {}
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r") as f:
                        raw_cache = json.load(f)
                        cache_data = {k: v for k, v in raw_cache.items() if k in db_names}
                except Exception:
                    pass

            # DBs are checked concurrently (each is an independent set of network
            # calls to its own Oracle/host/reporting/standby endpoints), so a
            # cycle over N databases takes roughly as long as the slowest one
            # instead of the sum of all of them.
            host_cache = {}

            def _collect_one(db):
                _write_status(fetching=db, cycle_start=cycle_start)
                print(f"[monitor_thread] Collecting: {db}")
                return db, collect_db_metrics(db, host_cache)

            with ThreadPoolExecutor(max_workers=min(len(db_names), MAX_PARALLEL_DBS)) as executor:
                futures = {executor.submit(_collect_one, db): db for db in db_names}
                for future in as_completed(futures):
                    db = futures[future]
                    try:
                        _, stats = future.result()
                    except Exception as e:
                        print(f"[monitor_thread] Collection error for {db}: {e}")
                        continue

                    cache_data[db] = stats

                    # Persist cache after each DB so UI always has fresh partial data.
                    # Locked because multiple worker threads finish around the same time.
                    with _cache_write_lock:
                        try:
                            with open(CACHE_FILE, "w") as f:
                                json.dump(cache_data, f, indent=2)
                        except Exception:
                            pass

                    # Fire alert engine. Locked because check_and_trigger_alerts does a
                    # full read-modify-write of the shared alert_state.json file.
                    cfg = get_config_for_db(db)
                    host = cfg.get("host", "unknown") if cfg else "unknown"
                    with _alert_lock:
                        try:
                            check_and_trigger_alerts(
                                server_name=host, db_name=db,
                                host_name=host, stats=stats
                            )
                        except Exception as ae:
                            print(f"[monitor_thread] Alert error {db}: {ae}")

            cycle_end = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _write_status(fetching="", cycle_start=cycle_start, cycle_end=cycle_end)
            print(f"[monitor_thread] Cycle complete at {cycle_end}")

            # Automatically generate/update db_status_report.log at the end of every check cycle
            try:
                from generate_db_status_log import generate_report
                generate_report()
            except Exception as gr_err:
                print(f"[monitor_thread] Failed to auto-generate db_status_report.log: {gr_err}")

        except Exception as e:
            print(f"[monitor_thread] Loop error: {e}")

        # Wait 2 minutes (but wake up early if force flag appears or shutdown was requested)
        waited = 0
        while waited < POLL_INTERVAL:
            if os.path.exists(FORCE_FLAG) or _stop_event.is_set():
                break
            time.sleep(5)
            waited += 5


# ═══════════════════════════════════════════════════════════════════════════
# Public API - start thread once per process
# ═══════════════════════════════════════════════════════════════════════════
def start_monitor_thread():
    """
    Start the background monitoring thread.
    Safe to call multiple times - only starts once per process.
    """
    global _thread_started
    with _thread_lock:
        if _thread_started:
            return
        _thread_started = True

    t = threading.Thread(target=_monitor_loop, name="db-monitor", daemon=True)
    t.start()
    print("[monitor_thread] Thread launched.")


def stop_monitor_thread():
    """
    Signal the background loop to stop starting new work. Safe to call from
    the shutdown handler: it prevents a NEW collection cycle from opening
    fresh DB connections, but (being a plain Python thread) it cannot force
    a query already blocked on network I/O to abort early — callers should
    still bound how long they wait for `is_monitor_idle()` before giving up.
    """
    _stop_event.set()


def is_monitor_idle() -> bool:
    """True if no DB collection is actively in progress right now."""
    return not read_monitor_status().get("fetching")


def trigger_force_refresh():
    """Write force_refresh.flag so the monitor loop runs immediately."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(FORCE_FLAG, "w") as f:
            f.write("1")
    except Exception:
        pass
