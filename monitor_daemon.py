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

from db_connection import load_db_names_api, get_config_for_db, get_oracle_mode
from queries.queries import (
    QUERY_TABLESPACES, QUERY_ARC_LOG, QUERY_MAX_SESSIONS, QUERY_BACKUP_STATUS,
    QUERY_OS_STATS, QUERY_SYSMETRIC, QUERY_ORACLE_MEM, QUERY_ALERT_LOG
)
from utils.storage_provider import get_storage_provider
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

def collect_db_metrics(db_name: str) -> dict:
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

        # 3. Mount Points / Connected Storage (uses storage provider Java SP wrapper)
        try:
            sp = get_storage_provider(conn)
            drives = sp.get_storage_info()
            stats["drives"] = drives if drives else []
        except Exception as se:
            print(f"Storage query error: {se}")

        # 4. Tablespaces
        try:
            df_ts = run_query_df(conn, QUERY_TABLESPACES)
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
            df_arc = run_query_df(conn, QUERY_ARC_LOG)
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
            df_alerts = run_query_df(conn, QUERY_ALERT_LOG)
            if not df_alerts.empty:
                stats["alert_log_errors"] = df_alerts["MESSAGE"].tolist()[:10]
        except Exception:
            pass

        # 8. Host OS System Resources
        try:
            # Query v$osstat
            df_os = run_query_df(conn, QUERY_OS_STATS)
            os_stats = {}
            if not df_os.empty:
                for _, r in df_os.iterrows():
                    os_stats[r["STAT_NAME"]] = float(r["VALUE"])
            
            # Query v$sysmetric
            df_sys = run_query_df(conn, QUERY_SYSMETRIC)
            sys_stats = {}
            if not df_sys.empty:
                for _, r in df_sys.iterrows():
                    sys_stats[r["METRIC_NAME"]] = float(r["VALUE"])

            # Query Oracle Memory
            df_mem = run_query_df(conn, QUERY_ORACLE_MEM)
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
        except Exception as re:
            print(f"OS stats query failed: {re}")

        # 9. Session Maxed Check
        try:
            df_max = run_query_df(conn, QUERY_MAX_SESSIONS)
            if not df_max.empty and stats["system_res"]:
                max_sess = int(df_max.iloc[0].get("VALUE", 150))
                act_sess = stats["system_res"]["session_count"]
                if act_sess > max(50, int(max_sess * 0.85)):
                    stats["session_maxed"] = True
        except Exception:
            pass

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

            for db in db_names:
                print(f"Monitoring database: {db} at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                stats = collect_db_metrics(db)
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
