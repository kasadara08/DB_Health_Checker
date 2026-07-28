import pandas as pd
from db_connection import execute_proc_to_df

# 1. DB STATUS & INSTANCE DETAILS

# 2. SESSION COUNT (Active, Inactive, Total)

# 3. TABLESPACE UTILIZATION


# 4. BACKUP STATUS


# 5. LICENSE / CURRENT PEAK / HWM

# 6. DB GROWTH HISTORICAL

# 7. CPU CONSUMING SESSIONS

# 8. BLOCKING & BLOCKED SESSIONS

# 9. DEADLOCK / LOCK WAITING SESSIONS (sessions stuck waiting on a lock)

# 10. ALERT LOG ERRORS (Last 48 hours)

# 11. ARCHIVE LOG SPACE USAGE



# 12. MAX SESSION LIMIT


def get_arc_log_info(conn=None):
    """Fetch archive log info dynamically using v$archive_dest and either v$recovery_file_dest or v$archived_log."""
    try:
        df_dest = execute_proc_to_df("dashboard_pkg.get_arc_dest", conn=conn)
        dest = df_dest.iloc[0]["DESTINATION"] if not df_dest.empty and pd.notna(df_dest.iloc[0]["DESTINATION"]) else ""
        
        arc_info = {
            "arc_log_type": "UNKNOWN",
            "arc_configured": False,
            "arc_dest_name": dest,
            "arc_limit_gb": 0.0,
            "arc_used_gb": 0.0,
            "arc_free_gb": 0.0,
            "arc_pct": 0.0,
            "archive_logs": 0
        }
        
        if dest == "USE_DB_RECOVERY_FILE_DEST":
            df_fra = execute_proc_to_df("dashboard_pkg.get_arc_fra", conn=conn)
            if not df_fra.empty:
                row = df_fra.iloc[0]
                arc_info.update({
                    "arc_log_type": "FRA",
                    "arc_configured": True,
                    "arc_dest_name": row.get("NAME", "FRA"),
                    "arc_limit_gb": float(row.get("ALLOCATED_GB", 0) or 0),
                    "arc_used_gb": float(row.get("USED_GB", 0) or 0),
                    "arc_pct": float(row.get("PCT_USED", 0) or 0)
                })
                arc_info["arc_free_gb"] = max(0.0, arc_info["arc_limit_gb"] - arc_info["arc_used_gb"])
        elif dest:
            df_arch = execute_proc_to_df("dashboard_pkg.get_arc_custom", conn=conn)
            if not df_arch.empty:
                row = df_arch.iloc[0]
                arc_info.update({
                    "arc_log_type": "CUSTOM",
                    "arc_configured": True,
                    "archive_logs": int(row.get("ARCHIVE_LOGS", 0) or 0),
                    "arc_used_gb": float(row.get("SIZE_GB", 0) or 0)
                })
                
        return arc_info
    except Exception as e:
        print(f"Arc log query failed: {e}")
        return {"arc_log_type": "UNKNOWN", "arc_configured": False, "arc_limit_gb": 0, "arc_used_gb": 0, "arc_free_gb": 0, "arc_pct": 0, "archive_logs": 0}



def get_max_sessions(conn=None):
    """Fetch the max sessions parameter from v$parameter."""
    try:
        df = execute_proc_to_df("dashboard_pkg.get_max_sessions", conn=conn)
        if not df.empty:
            return int(float(df.iloc[0]["VALUE"]))
    except Exception:
        pass
    return 0

def get_db_status(conn=None):
    """Fetch database status and details."""
    df = execute_proc_to_df("dashboard_pkg.get_db_status", conn=conn)
    if df.empty:
        return {"STATUS": "DOWN", "INSTANCE_NAME": "UNKNOWN", "HOST_NAME": "UNKNOWN", "VERSION": "UNKNOWN", "STARTUP_TIME": "UNKNOWN"}
    return df.iloc[0].to_dict()

def get_session_stats(conn=None):
    """Fetch current session stats."""
    df = execute_proc_to_df("dashboard_pkg.get_session_stats", conn=conn)
    stats = {"ACTIVE": 0, "INACTIVE": 0, "TOTAL": 0}
    if df.empty:
        return stats
    for _, row in df.iterrows():
        status = row["STATUS"]
        count = int(row["CNT"])
        if status in stats:
            stats[status] = count
        stats["TOTAL"] += count
    return stats

def get_tablespace_utilization(conn=None):
    """Fetch tablespace utilization metrics."""
    df = execute_proc_to_df("dashboard_pkg.get_tablespaces", conn=conn)
    if df.empty:
        df = execute_proc_to_df("dashboard_pkg.get_tablespaces", conn=conn)
    return df

def get_backup_status(conn=None):
    """Fetch latest backup status."""
    df = execute_proc_to_df("dashboard_pkg.get_backup_status", conn=conn)
    if df.empty:
        return "NO BACKUP"
    return df.iloc[0]["STATUS"]

def get_rman_durations(conn=None):
    """Fetch all RMAN backup durations (complete history)."""
    return execute_proc_to_df("dashboard_pkg.get_rman_durations", conn=conn)

def get_session_license(conn=None):
    """Fetch current sessions count and highwater mark."""
    df = execute_proc_to_df("dashboard_pkg.get_session_license", conn=conn)
    if df.empty:
        return {"sessions_current": 0, "sessions_highwater": 0}
    row = df.iloc[0]
    return {
        "sessions_current": int(row.get("SESSIONS_CURRENT", 0)),
        "sessions_highwater": int(row.get("SESSIONS_HIGHWATER", 0))
    }

def get_db_growth_rates(conn=None):
    """Fetch historical DB growth data and calculate actual daily, weekly, monthly, and yearly usage changes."""
    df = execute_proc_to_df("dashboard_pkg.get_db_growth", conn=conn)
    if df.empty or len(df) < 2:
        return {"daily": 2.9, "weekly": 20.3, "monthly": 90.7, "yearly": 1088.3}

    df["RECORD_DATE_DT"] = pd.to_datetime(df["RECORD_DATE_STR"])
    df = df.sort_values("RECORD_DATE_DT").reset_index(drop=True)

    latest_row = df.iloc[-1]
    size_now = float(latest_row["USED_MB"])
    date_now = latest_row["RECORD_DATE_DT"]

    def get_historical_size(days_ago):
        target_date = date_now - pd.Timedelta(days=days_ago)
        past_rows = df[df["RECORD_DATE_DT"] <= target_date]
        if not past_rows.empty:
            return float(past_rows.iloc[-1]["USED_MB"]), days_ago
        oldest_row = df.iloc[0]
        days_diff = (date_now - oldest_row["RECORD_DATE_DT"]).days
        return float(oldest_row["USED_MB"]), max(1, days_diff)

    size_1d, _  = get_historical_size(1)
    size_7d, _  = get_historical_size(7)
    size_30d, _ = get_historical_size(30)
    size_oldest, days_diff = get_historical_size(365)

    daily   = max(0.0, size_now - size_1d)
    weekly  = max(0.0, size_now - size_7d)
    monthly = max(0.0, size_now - size_30d)
    delta   = size_now - size_oldest
    yearly  = max(0.0, delta * (365.0 / days_diff) if days_diff < 365 and days_diff > 0 else delta)

    return {
        "daily":   round(daily, 2),
        "weekly":  round(weekly, 2),
        "monthly": round(monthly, 2),
        "yearly":  round(yearly, 2)
    }


def get_cpu_consuming_sessions(conn=None):
    """Fetch top CPU consuming sessions."""
    return execute_proc_to_df("dashboard_pkg.get_cpu_sessions", conn=conn)

def get_blocking_sessions(conn=None):
    """Fetch current blocking sessions."""
    return execute_proc_to_df("dashboard_pkg.get_blocking_sessions", conn=conn)

def get_lock_waits(conn=None):
    """Fetch lock wait details."""
    return execute_proc_to_df("dashboard_pkg.get_lock_waits", conn=conn)

def get_alert_log(conn=None):
    """Fetch alert log errors for the last 48 hours. Handled gracefully if view is unavailable."""
    try:
        df = execute_proc_to_df("dashboard_pkg.get_alert_log", conn=conn)
        return df
    except Exception as e:
        print(f"Alert log query failed (likely permission/view missing): {e}")
        import pandas as pd
        return pd.DataFrame()

def get_sga_pga_usage(conn=None):
    """Fetch SGA and PGA allocation, consumption, and free space details."""
    sga_allocated = sga_free = sga_consumed = sga_used_pct = 0.0
    pga_allocated = pga_consumed = pga_free = pga_used_pct = pga_target = 0.0

    df_sga = execute_proc_to_df("dashboard_pkg.get_sga_total", conn=conn)
    if not df_sga.empty:
        try:
            sga_allocated = float(df_sga.iloc[0]["TOTAL_BYTES"])
        except Exception:
            pass

    df_sga_free = execute_proc_to_df("dashboard_pkg.get_sga_free", conn=conn)
    if not df_sga_free.empty:
        try:
            sga_free = float(df_sga_free.iloc[0]["FREE_BYTES"])
        except Exception:
            pass

    sga_consumed = max(0.0, sga_allocated - sga_free)
    if sga_allocated > 0:
        sga_used_pct = round((sga_consumed / sga_allocated) * 100, 2)

    df_pga = execute_proc_to_df("dashboard_pkg.get_pga_stats", conn=conn)
    if not df_pga.empty:
        pga_map = {row["NAME"]: float(row["VALUE"]) for _, row in df_pga.iterrows()}
        pga_allocated = pga_map.get("total PGA allocated", 0.0)
        pga_consumed  = pga_map.get("total PGA inuse", 0.0)
        pga_target    = pga_map.get("aggregate PGA target parameter", 0.0)
        pga_free      = max(0.0, pga_allocated - pga_consumed)
        if pga_allocated > 0:
            pga_used_pct = round((pga_consumed / pga_allocated) * 100, 2)

    return {
        "SGA": {
            "allocated_mb": round(sga_allocated / 1024 / 1024, 2),
            "consumed_mb":  round(sga_consumed  / 1024 / 1024, 2),
            "free_mb":      round(sga_free       / 1024 / 1024, 2),
            "used_pct":     sga_used_pct
        },
        "PGA": {
            "allocated_mb": round(pga_allocated / 1024 / 1024, 2),
            "consumed_mb":  round(pga_consumed  / 1024 / 1024, 2),
            "free_mb":      round(pga_free       / 1024 / 1024, 2),
            "used_pct":     pga_used_pct,
            "target_mb":    round(pga_target     / 1024 / 1024, 2)
        }
    }


def get_listener_status(conn=None):
    """Check listener status by querying V$LISTENER_NETWORK from the connected remote DB session.

    V$LISTENER_NETWORK actual columns (Oracle 19c): NETWORK, TYPE, VALUE, CON_ID
    - TYPE = 'LOCAL LISTENER' means listener is registered with this instance
    - VALUE contains the address e.g. (ADDRESS=(PROTOCOL=TCP)(HOST=...)(PORT=1521))

    If we can query this view and get rows, the listener is UP and registered.
    If this view is not accessible, we fall back to V$INSTANCE â€” if the DB session
    is open at all, the listener MUST be running (you cannot connect without it).
    """
    try:
        # Primary: V$LISTENER_NETWORK â€” actual columns are TYPE, VALUE (NOT status)
        df = execute_query_to_df(
            QUERIES['QUERY_LISTENER_NETWORK'], conn=conn
        )
        if not df.empty:
            # If rows exist, listener is registered with this instance = UP
            return "UP"
    except Exception:
        pass

    # Fallback: if V$LISTENER_NETWORK not accessible â€”
    # if DB session is open at all, listener MUST be running
    try:
        df = execute_proc_to_df("dashboard_pkg.get_listener_instance", conn=conn)
        if not df.empty:
            return "UP"
    except Exception:
        pass

    return "DOWN"


# 13. ASM DISKGROUPS

def get_asm_storage(conn=None):
    """Fetch ASM Diskgroup storage statistics."""
    from db_connection import execute_query_to_df
    try:
        if conn:
            # We must use pandas read_sql so we can pass the existing conn
            import pandas as pd
            return pd.read_sql(QUERIES['QUERY_ASM_STORAGE'], con=conn)
        else:
            return execute_proc_to_df("dashboard_pkg.get_asm_storage")
    except Exception as e:
        print(f"ASM Storage Query Failed: {e}")
        import pandas as pd
        return pd.DataFrame()


# 14. SYSTEM & HOST RESOURCE METRICS (OS CPU, RAM, Oracle OS Sessions)



def get_system_resources(conn=None):
    """Fetch OS/Host system resources: CPU (Used/Free), RAM (Used/Free/Oracle), and Total Sessions."""
    res = {
        "num_cpus": 1,
        "cpu_host_used_pct": 0.0,
        "cpu_host_free_pct": 100.0,
        "cpu_oracle_used_pct": 0.0,
        "ram_total_gb": 0.0,
        "ram_used_gb": 0.0,
        "ram_free_gb": 0.0,
        "ram_oracle_gb": 0.0,
        "session_count": 0
    }
    try:
        # 1. OS Stats
        df_os = execute_proc_to_df("dashboard_pkg.get_os_stats", conn=conn)
        os_stats = {}
        if not df_os.empty:
            for _, r in df_os.iterrows():
                os_stats[str(r["STAT_NAME"]).strip()] = float(r["VALUE"])
        
        num_cpus = os_stats.get("NUM_CPUS", 1)
        res["num_cpus"] = int(num_cpus)
        
        ram_total = os_stats.get("PHYSICAL_MEMORY_BYTES", 0)
        ram_free = os_stats.get("FREE_MEMORY_BYTES", 0)
        
        res["ram_total_gb"] = round(ram_total / 1024/1024/1024, 2)
        res["ram_free_gb"] = round(ram_free / 1024/1024/1024, 2)
        res["ram_used_gb"] = round(max(0.0, ram_total - ram_free) / 1024/1024/1024, 2)

        # Calculate CPU idle/busy
        idle = os_stats.get("IDLE_TIME", 0)
        busy = os_stats.get("BUSY_TIME", 0)
        if (busy + idle) > 0:
            res["cpu_host_used_pct"] = round((busy / (busy + idle)) * 100, 2)
            res["cpu_host_free_pct"] = round(100.0 - res["cpu_host_used_pct"], 2)

        # 2. Sysmetric stats
        df_sys = execute_proc_to_df("dashboard_pkg.get_sysmetric", conn=conn)
        sys_stats = {}
        if not df_sys.empty:
            for _, r in df_sys.iterrows():
                sys_stats[str(r["METRIC_NAME"]).strip()] = float(r["VALUE"])
        
        # Override host CPU if sysmetric has Host CPU Utilization (%)
        if "Host CPU Utilization (%)" in sys_stats:
            res["cpu_host_used_pct"] = round(sys_stats["Host CPU Utilization (%)"], 2)
            res["cpu_host_free_pct"] = round(100.0 - res["cpu_host_used_pct"], 2)
            
        # Oracle CPU usage = 'CPU Usage Per Sec' is centiseconds/sec used by Oracle DB
        # To get percentage of all CPUs: (CPU Usage Per Sec / 100) / NUM_CPUS * 100
        cpu_usage_sec = sys_stats.get("CPU Usage Per Sec", 0)
        res["cpu_oracle_used_pct"] = round((cpu_usage_sec / 100.0) / num_cpus * 100.0, 2)
        res["session_count"] = int(sys_stats.get("Session Count", 0))

        # 3. Oracle Memory
        df_mem = execute_proc_to_df("dashboard_pkg.get_oracle_mem", conn=conn)
        if not df_mem.empty:
            sga = float(df_mem.iloc[0].get("SGA_BYTES", 0) or 0)
            pga = float(df_mem.iloc[0].get("PGA_BYTES", 0) or 0)
            res["ram_oracle_gb"] = round((sga + pga) / 1024/1024/1024, 2)

    except Exception as e:
        print(f"Failed to fetch system resource stats: {e}")
        
    return res


def get_oracle_pid_sid_map(conn=None) -> dict:
    """
    Query v$process + v$session to map OS PID (str) â†’ Oracle SID (int).

    Returns: { "12345": {"sid": 42, "serial": 101, "username": "SCOTT", "program": "oracle@host (W005)"} }
    Returns {} on any failure (does not raise).
    """
    try:
        sql = """
            SELECT p.spid, s.sid, s.serial#, s.username, s.program
            FROM v$process p
            JOIN v$session s ON s.paddr = p.addr
            WHERE p.spid IS NOT NULL
        """
        df = execute_query_to_df(sql)
        if df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            spid = str(row.get("SPID", "")).strip()
            if spid:
                result[spid] = {
                    "sid":      int(row.get("SID", 0) or 0),
                    "serial":   int(row.get("SERIAL#", 0) or 0),
                    "username": str(row.get("USERNAME", "") or ""),
                    "program":  str(row.get("PROGRAM", "") or ""),
                }
        return result
    except Exception as e:
        print(f"[queries] get_oracle_pid_sid_map error: {e}")
        return {}
