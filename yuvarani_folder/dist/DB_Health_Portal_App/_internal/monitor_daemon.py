import os
import sys
import time
import json
import datetime
import oracledb
import pandas as pd

# Add current dir to python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from db_connection import load_db_names_api, get_config_for_db, get_oracle_mode, get_standby_db_config
from utils.alerts import check_and_trigger_alerts

CACHE_DIR = os.path.join(current_dir, "config")
CACHE_FILE = os.path.join(CACHE_DIR, "db_monitor_cache.json")

def run_query_df(conn, sql: str) -> pd.DataFrame:
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        records = cursor.fetchall()
        cols = [col[0] for col in cursor.description]
        cursor.close()
        df = pd.DataFrame(records, columns=cols)
        df.columns = [c.upper() for c in df.columns]
        return df
    except Exception as e:
        print(f"Query failed: {e}")
        return pd.DataFrame()

def collect_db_metrics(db_name: str, host_cache: dict = None) -> dict:
    cfg = get_config_for_db(db_name)
    if not cfg:
        return {"db": "DOWN", "listener": "DOWN", "error": "No database config in registry."}

    stats = {
        "db": "DOWN",
        "listener": "DOWN",
        "backup": "UNKNOWN",
        "balance_ts": [],
        "full_ts": [],
        "drives": [],
        "system_res": None,
        "arc_pct": 0.0,
        "arc_configured": False,
        "has_blocking": False,
        "session_maxed": False,
        "alert_log_errors": [],
        "last_refresh": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    conn = None
    try:
        mode = get_oracle_mode(cfg["user"])
        conn = oracledb.connect(
            user=cfg["user"],
            password=cfg["password"],
            dsn=cfg["dsn"],
            mode=mode,
            tcp_connect_timeout=3
        )
        
        stats["db"] = "UP"
        
        # 1. Listener Check
        try:
            df_lsnr = run_query_df(conn, "SELECT type, value FROM v$listener_network WHERE ROWNUM = 1")
            if not df_lsnr.empty:
                stats["listener"] = "UP"
            else:
                df_inst = run_query_df(conn, "SELECT status FROM v$instance WHERE ROWNUM = 1")
                stats["listener"] = "UP" if not df_inst.empty else "DOWN"
        except Exception:
            stats["listener"] = "DOWN"

        # 2. Backup Check
        try:
            df_rman = run_query_df(conn, "SELECT status, start_time FROM v$rman_backup_job_details ORDER BY start_time DESC")
            if df_rman.empty:
                stats["backup"] = "PENDING"
            else:
                last_row = df_rman.iloc[0]
                last_status = str(last_row.get("STATUS", "FAIL")).upper()
                start_time = last_row.get("START_TIME")
                
                days_ago = 999
                if start_time:
                    try:
                        start_dt = pd.to_datetime(start_time)
                        days_ago = (datetime.datetime.now() - start_dt).days
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

        # Mount points are NOT collected in the background.
        # They are fetched on-demand only when the user opens a specific
        # DB dashboard page (load_db_status_summary in dashboard/home.py).
        host = cfg.get("host", "").strip()

        # 4. Tablespaces
        try:
            df_ts = run_query_df(conn, """
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
            if not df_ts.empty:
                for _, ts in df_ts.iterrows():
                    ts_name = str(ts["TABLESPACE_NAME"]).upper()
                    if "TEMP" in ts_name or "UNDO" in ts_name:
                        continue
                    ts_pct = float(ts.get("PCT_USED_MAX", 0))
                    total_mb = float(ts.get("MAXSIZE_MB", 0))
                    used_mb = float(ts.get("USED_MB", 0))
                    free_mb = float(ts.get("FREE_ON_MAX_MB", 0))
                    allocated_mb = float(ts.get("ALLOCATED_MB", 0))
                    location = str(ts.get("LOCATION", "Unknown Location"))
                    
                    balance_ts.append({
                        "name": ts["TABLESPACE_NAME"],
                        "pct": ts_pct,
                        "used_mb": used_mb,
                        "free_mb": free_mb,
                        "total_mb": total_mb,
                        "allocated_mb": allocated_mb,
                        "location": location
                    })
                balance_ts.sort(key=lambda x: x["free_mb"])
            stats["balance_ts"] = balance_ts
            stats["full_ts"] = [t["name"] for t in balance_ts if t["pct"] >= 90]
        except Exception as tse:
            print(f"Tablespace query error: {tse}")

        # 5. Archive log space (FRA)
        try:
            df_arc = run_query_df(conn, """
                SELECT
                  (SELECT COUNT(*) FROM v$archived_log WHERE deleted='NO') AS archive_logs,
                  (SELECT ROUND(NVL(SUM(blocks*block_size), 0)/1024/1024/1024, 2) FROM v$archived_log WHERE deleted='NO') AS size_gb,
                  (SELECT value FROM V$PARAMETER WHERE name = 'db_recovery_file_dest') AS fra_dest_param,
                  (SELECT ROUND(SPACE_LIMIT / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_limit_gb,
                  (SELECT ROUND(SPACE_USED / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_used_gb
                FROM DUAL""")
            if not df_arc.empty:
                row = df_arc.iloc[0]
                limit_mb = float(row.get("LIMIT_MB", 0))
                used_pct = float(row.get("USED_PCT", 0))
                stats["arc_pct"] = round(used_pct, 2)
                stats["arc_configured"] = limit_mb > 0
        except Exception:
            pass

        # 6. Blocking Sessions
        try:
            df_blk = run_query_df(conn, "SELECT count(*) FROM v$session WHERE blocking_session IS NOT NULL")
            stats["has_blocking"] = not df_blk.empty and int(df_blk.iloc[0].iloc[0]) > 0
        except Exception:
            pass

        # 7. Alert Log Checks
        try:
            df_alerts = run_query_df(conn, """SELECT * FROM (SELECT TO_CHAR(originating_timestamp,'YYYY-MM-DD HH24:MI:SS') as time_str, message_text FROM v$diag_alert_ext WHERE originating_timestamp >= SYSDATE - 2 AND message_text LIKE '%ORA-%' ORDER BY originating_timestamp DESC) WHERE ROWNUM <= 50""")
            if not df_alerts.empty:
                stats["alert_log_errors"] = df_alerts["MESSAGE"].tolist()[:10]
        except Exception:
            pass

        # 8. Host OS System Resources
        if host_cache is not None and host in host_cache and host_cache[host].get("system_res"):
            stats["system_res"] = host_cache[host]["system_res"]
        else:
            try:
                # Query v$osstat
                df_os = run_query_df(conn, "SELECT stat_name, value FROM v$osstat WHERE stat_name IN ('NUM_CPUS', 'PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'IDLE_TIME', 'BUSY_TIME')")
                os_stats = {}
                if not df_os.empty:
                    for _, r in df_os.iterrows():
                        os_stats[r["STAT_NAME"]] = float(r["VALUE"])
                
                # Query v$sysmetric
                df_sys = run_query_df(conn, "SELECT metric_name, value FROM v$sysmetric WHERE metric_name IN ('Host CPU Utilization (%)', 'CPU Usage Per Sec', 'Session Count') AND group_id = 2")
                sys_stats = {}
                if not df_sys.empty:
                    for _, r in df_sys.iterrows():
                        sys_stats[r["METRIC_NAME"]] = float(r["VALUE"])

                # Query Oracle Memory
                df_mem = execute_proc_to_df('dashboard_pkg.get_oracle_mem', conn=conn)
                sga_val, pga_val = 0.0, 0.0
                if not df_mem.empty:
                    sga_val = float(df_mem.iloc[0].get("SGA_BYTES", 0))
                    pga_val = float(df_mem.iloc[0].get("PGA_BYTES", 0))

                num_cpus = int(os_stats.get("NUM_CPUS", 1))
                total_ram_bytes = float(os_stats.get("PHYSICAL_MEMORY_BYTES", 0))
                free_ram_bytes  = float(os_stats.get("FREE_MEMORY_BYTES", 0))
                
                cpu_used_pct = float(sys_stats.get("Host CPU Utilization (%)", 0.0))
                cpu_free_pct = max(0.0, 100.0 - cpu_used_pct)
                
                cpu_oracle_pct = 0.0
                if num_cpus > 0:
                    cpu_oracle_pct = float(sys_stats.get("CPU Usage Per Sec", 0.0)) / num_cpus

                ram_total_gb = total_ram_bytes / (1024**3)
                ram_free_gb  = free_ram_bytes  / (1024**3)
                ram_used_gb  = max(0.0, ram_total_gb - ram_free_gb)
                ram_oracle_gb = (sga_val + pga_val) / (1024**3)

                stats["system_res"] = {
                    "num_cpus": num_cpus,
                    "cpu_host_used_pct": round(cpu_used_pct, 1),
                    "cpu_host_free_pct": round(cpu_free_pct, 1),
                    "cpu_oracle_used_pct": round(cpu_oracle_pct, 1),
                    "ram_total_gb": round(ram_total_gb, 2),
                    "ram_used_gb": round(ram_used_gb, 2),
                    "ram_free_gb": round(ram_free_gb, 2),
                    "ram_oracle_gb": round(ram_oracle_gb, 2),
                    "session_count": int(sys_stats.get("Session Count", 0))
                }
                if host_cache is not None:
                    if host not in host_cache: host_cache[host] = {}
                    host_cache[host]["system_res"] = stats["system_res"]
            except Exception as re:
                print(f"OS stats query failed: {re}")

        # 9. Session Maxed Check
        try:
            df_max = run_query_df(conn, "SELECT name, value FROM v$parameter WHERE name = 'sessions'")
            if not df_max.empty and stats["system_res"]:
                max_sess = int(df_max.iloc[0].get("VALUE", 150))
                act_sess = stats["system_res"]["session_count"]
                if act_sess > max(50, int(max_sess * 0.85)):
                    stats["session_maxed"] = True
        except Exception:
            pass

        # 10. Standby destination status (from V$ARCHIVE_DEST_STATUS on Primary)
        standby_dests = []
        try:
            cur = conn.cursor()
            cur.execute("SELECT DEST_ID, STATUS, TARGET, DB_UNIQUE_NAME, ERROR FROM V$ARCHIVE_DEST_STATUS WHERE TARGET = 'STANDBY'")
            dest_rows = cur.fetchall()
            dest_cols = [c[0].upper() for c in cur.description]
            cur.close()
            for r in dest_rows:
                d = dict(zip(dest_cols, r))
                status = str(d.get("STATUS") or "").strip()
                error_msg = str(d.get("ERROR") or "").strip()
                db_uniq = str(d.get("DB_UNIQUE_NAME") or "").strip()
                dest_id = d.get("DEST_ID")
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
            p_seq_rows = []
            try:
                cur = conn.cursor()
                cur.execute("SELECT THREAD#, MAX(SEQUENCE#) AS SEQ FROM V$ARCHIVED_LOG GROUP BY THREAD#")
                rows = cur.fetchall()
                cols = [c[0].upper() for c in cur.description]
                cur.close()
                p_seq_rows = [dict(zip(cols, row)) for row in rows]
            except Exception:
                pass
            for r in p_seq_rows:
                t = int(r.get("THREAD#", 1))
                primary_seqs[t] = int(r.get("SEQ") or 0)
        except Exception:
            pass
        stats["primary_seqs"] = primary_seqs

        conn.close()

    except Exception as e:
        stats["db"] = "DOWN"
        stats["error"] = str(e)
        
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

    # -- Standby DB log gap check ------------------------------------------
    stats["standby_configured"]  = False
    stats["standby_log_gap"]     = {}
    stats["log_gap_alert"]       = False
    try:
        stby_cfg = get_standby_db_config(db_name)
        if stby_cfg:
            import oracledb as _oci
            from db_connection import get_oracle_mode

            stby_conn = None
            try:
                # Connect to standby DB
                s_mode = get_oracle_mode(stby_cfg["user"])
                stby_conn = _oci.connect(
                    user=stby_cfg["user"], password=stby_cfg["password"],
                    dsn=stby_cfg["dsn"], mode=s_mode, tcp_connect_timeout=5
                )
                
                # Query standby for log sequences
                stby_rows = []
                try:
                    cur = stby_conn.cursor()
                    cur.execute("""
                        SELECT THREAD#,
                               MAX(SEQUENCE#) AS LAST_RECEIVED,
                               MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LAST_APPLIED,
                               MAX(SEQUENCE#) - MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LOG_GAP
                        FROM V$ARCHIVED_LOG
                        WHERE DEST_ID = 1
                        GROUP BY THREAD#
                        ORDER BY THREAD#
                    """)
                    rows = cur.fetchall()
                    cols = [c[0].upper() for c in cur.description]
                    cur.close()
                    stby_rows = [dict(zip(cols, row)) for row in rows]
                except Exception:
                    pass
                
                primary_seqs = stats.get("primary_seqs", {})
                threads = []
                max_gap = 0
                for r in stby_rows:
                    t = int(r.get("THREAD#", 1))
                    received = int(r.get("LAST_RECEIVED") or 0)
                    applied = int(r.get("LAST_APPLIED") or 0)
                    gap = int(r.get("LOG_GAP") or 0)
                    primary = primary_seqs.get(t, received) # fallback to received
                    
                    if gap > max_gap:
                        max_gap = gap
                        
                    threads.append({
                        "thread":             t,
                        "primary_generated":  primary,
                        "standby_received":   received,
                        "standby_applied":    applied,
                        "gap":                gap,
                    })
                
                if threads:
                    stats["standby_configured"] = True
                    stats["standby_log_gap"] = {
                        "configured": True,
                        "threads": threads,
                        "max_gap": max_gap,
                        "has_gap": max_gap >= 2,
                        "error": "",
                    }
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
                    stats["standby_log_gap"] = {"configured": False, "error": "No thread logs found on standby"}
            except Exception as stby_err:
                print(f"[monitor] Standby query/conn error for {db_name}: {stby_err}")
                stats["standby_log_gap"] = {"configured": False, "error": str(stby_err)}
            finally:
                if stby_conn:
                    try: stby_conn.close()
                    except Exception: pass
    except Exception as stby_outer_err:
        print(f"[monitor] Standby gap check outer error for {db_name}: {stby_outer_err}")

    return stats

def main_loop():
    print("Background monitoring daemon started.")
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    while True:
        try:
            db_names = load_db_names_api()
            if not db_names:
                print("No configured databases found in registry.")
                time.sleep(15)
                continue

            # Load existing cache to preserve old db stats if they fail to check in this cycle
            cache_data = {}
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r") as f:
                        cache_data = json.load(f)
                except Exception:
                    pass

            host_cache = {}
            for db in db_names:
                print(f"Monitoring database: {db} at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                stats = collect_db_metrics(db, host_cache)
                cache_data[db] = stats

                # Fire alerting engine checks
                cfg = get_config_for_db(db)
                host = cfg.get("host", "unknown") if cfg else "unknown"
                try:
                    check_and_trigger_alerts(server_name=host, db_name=db, host_name=host, stats=stats)
                except Exception as ae:
                    print(f"Failed to check alerts for {db}: {ae}")

            # Dump updated cache
            with open(CACHE_FILE, "w") as f:
                json.dump(cache_data, f, indent=4)
            print("Cache updated successfully.")

        except Exception as e:
            print(f"Error in daemon monitoring loop: {e}")

        time.sleep(60) # Poll every 60 seconds

if __name__ == "__main__":
    main_loop()
