"""
monitor_thread.py
Background monitoring thread — runs inside the Streamlit app (no separate process).
Collects all configured DBs every 2 minutes, writes cache file, fires email alerts.
"""

import os
import sys
import time
import json
import datetime
import threading

# ── Path setup ──────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from db_connection import load_db_names_api, get_config_for_db

from utils.storage_provider import get_storage_provider
from utils.alerts import check_and_trigger_alerts

# ── Config paths ─────────────────────────────────────────────────────────────
CONFIG_DIR       = os.path.join(_BASE_DIR, "config")
CACHE_FILE       = os.path.join(CONFIG_DIR, "db_monitor_cache.json")
STATUS_FILE      = os.path.join(CONFIG_DIR, "daemon_status.json")
FORCE_FLAG       = os.path.join(CONFIG_DIR, "force_refresh.flag")

POLL_INTERVAL    = 120   # 2 minutes

# ── Thread singleton guard ───────────────────────────────────────────────────
_thread_lock   = threading.Lock()
_thread_started = False


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
def collect_db_metrics(db_name: str) -> dict:
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
        from db_connection import execute_proc_to_df, get_oracle_mode
        mode = get_oracle_mode(cfg["user"])
        conn = oracledb.connect(
            user=cfg["user"], password=cfg["password"], dsn=cfg["dsn"],
            mode=mode, tcp_connect_timeout=5
        )
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

        # 10. Connected storage & Running Processes
        try:
            sp = get_storage_provider(conn)
            drives_raw = sp.get_storage_info()
            # Check if result is an SSH error dict
            if drives_raw and isinstance(drives_raw[0], dict) and "ssh_error" in drives_raw[0]:
                stats["mount_error"] = drives_raw[0]["ssh_error"]
                stats["drives"] = []
                print(f"[monitor] SSH mount error for {db_name}: {drives_raw[0]['ssh_error']}")
            else:
                stats["drives"] = drives_raw if drives_raw else []
                stats["mount_error"] = None
            try:
                stats["processes"] = sp.get_process_info()
            except Exception as pe:
                print(f"[monitor] Process check failed: {pe}")
                stats["processes"] = []
        except Exception:
            pass


        conn.close()

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

    # ── Pre-compute tooltip_reasons ──────────────────────────────────────
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
    stats["tooltip_reasons"] = reasons

    # ── Reporting DB status check ─────────────────────────────────────────
    try:
        from db_connection import check_reporting_db_status
        rpt = check_reporting_db_status(db_name)
        stats["reporting_status"]   = rpt.get("status", "NOT_CONFIGURED")
        stats["reporting_db_name"]  = rpt.get("reporting_db_name", "")
        stats["reporting_configured"] = rpt.get("configured", False)
    except Exception as re:
        stats["reporting_status"]   = "NOT_CONFIGURED"
        stats["reporting_db_name"]  = ""
        stats["reporting_configured"] = False

    return stats


# ═══════════════════════════════════════════════════════════════════════════
# Status file helpers
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
    """Read daemon_status.json — safe to call from UI."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"fetching": "", "last_cycle_start": "", "last_cycle_end": ""}


def read_monitor_cache() -> dict:
    """Read db_monitor_cache.json — safe to call from UI."""
    try:
        if os.path.exists(CACHE_FILE):
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

            # Load existing cache to preserve stale entries on partial failures
            cache_data = {}
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r") as f:
                        cache_data = json.load(f)
                except Exception:
                    pass

            for db in db_names:
                _write_status(fetching=db, cycle_start=cycle_start)
                print(f"[monitor_thread] Collecting: {db}")
                stats = collect_db_metrics(db)
                cache_data[db] = stats

                # Persist cache after each DB so UI always has fresh partial data
                try:
                    with open(CACHE_FILE, "w") as f:
                        json.dump(cache_data, f, indent=2)
                except Exception:
                    pass

                # Fire alert engine
                cfg = get_config_for_db(db)
                host = cfg.get("host", "unknown") if cfg else "unknown"
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

        except Exception as e:
            print(f"[monitor_thread] Loop error: {e}")

        # Wait 2 minutes (but wake up early if force flag appears)
        waited = 0
        while waited < POLL_INTERVAL:
            if os.path.exists(FORCE_FLAG):
                break
            time.sleep(5)
            waited += 5


# ═══════════════════════════════════════════════════════════════════════════
# Public API — start thread once per process
# ═══════════════════════════════════════════════════════════════════════════
def start_monitor_thread():
    """
    Start the background monitoring thread.
    Safe to call multiple times — only starts once per process.
    """
    global _thread_started
    with _thread_lock:
        if _thread_started:
            return
        _thread_started = True

    t = threading.Thread(target=_monitor_loop, name="db-monitor", daemon=True)
    t.start()
    print("[monitor_thread] Thread launched.")


def trigger_force_refresh():
    """Write force_refresh.flag so the monitor loop runs immediately."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(FORCE_FLAG, "w") as f:
            f.write("1")
    except Exception:
        pass
