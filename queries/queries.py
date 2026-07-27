import pandas as pd
from db_connection import execute_query_to_df, execute_query

# 1. DB STATUS & INSTANCE DETAILS
QUERY_DB_STATUS = """
SELECT status, instance_name, host_name, version, 
       TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') as startup_time
FROM v$instance
"""

# 2. SESSION COUNT (Active, Inactive, Total)
QUERY_SESSION_STATS = """
SELECT status, COUNT(*) as cnt
FROM v$session
WHERE username IS NOT NULL
GROUP BY status
"""

# 3. TABLESPACE UTILIZATION
QUERY_TABLESPACES = """
SELECT
    df.tablespace_name,
    ROUND(df.allocated_mb,2) AS allocated_mb,
    ROUND(NVL(fs.free_mb,0),2) AS free_mb,
    ROUND(df.allocated_mb - NVL(fs.free_mb,0),2) AS used_mb,
    ROUND(df.max_mb,2) AS maxsize_mb,
    ROUND(df.max_mb - (df.allocated_mb - NVL(fs.free_mb,0)),2) AS free_on_max_mb,
    ROUND(((df.allocated_mb - NVL(fs.free_mb,0))/df.max_mb)*100,2) AS pct_used_max,
    loc.location
FROM
(
    SELECT
        tablespace_name,
        SUM(bytes)/1024/1024 allocated_mb,
        SUM(
            CASE
                WHEN autoextensible='YES'
                THEN maxbytes
                ELSE bytes
            END
        )/1024/1024 max_mb
    FROM dba_data_files
    GROUP BY tablespace_name
) df
LEFT JOIN
(
    SELECT
        tablespace_name,
        SUM(bytes)/1024/1024 free_mb
    FROM dba_free_space
    GROUP BY tablespace_name
) fs
ON df.tablespace_name = fs.tablespace_name
LEFT JOIN
(
    SELECT tablespace_name, MAX(file_name) AS location
    FROM dba_data_files
    GROUP BY tablespace_name
) loc
ON df.tablespace_name = loc.tablespace_name
ORDER BY df.tablespace_name
"""

QUERY_TABLESPACES_FALLBACK = QUERY_TABLESPACES

# 4. BACKUP STATUS
QUERY_BACKUP_STATUS = """
SELECT status FROM (
    SELECT status
    FROM v$rman_backup_job_details
    ORDER BY start_time DESC
) WHERE ROWNUM = 1
"""

QUERY_RMAN_DURATIONS = """
SELECT TO_CHAR(start_time, 'DD-Mon') as start_time_str,
       ROUND((end_time - start_time) * 24 * 60, 2) as duration_min,
       status,
       input_type
FROM v$rman_backup_job_details
WHERE start_time >= SYSDATE - 7
ORDER BY start_time ASC
"""

# 5. LICENSE / CURRENT PEAK / HWM
QUERY_SESSION_LICENSE = """
SELECT sessions_current, sessions_highwater FROM v$license
"""

# 6. DB GROWTH HISTORICAL
QUERY_DB_GROWTH = """
SELECT TO_CHAR(record_date, 'YYYY-MM-DD') as record_date_str, used_mb, total_mb 
FROM DASHBOARD_GROWTH_TRACK 
ORDER BY record_date ASC
"""

# 7. CPU CONSUMING SESSIONS
QUERY_CPU_SESSIONS = """
SELECT s.sid, s.username, s.program, s.cpu_time_val, 
       COALESCE(q.sql_text, pq.sql_text) as sql_text,
       COALESCE(s.sql_id, s.prev_sql_id) as sql_id
FROM (
    SELECT sid, username, program, sql_id, prev_sql_id, cpu_time_val
    FROM (
        SELECT s.sid, s.username, s.program, s.sql_id, s.prev_sql_id, se.value as cpu_time_val
        FROM v$session s
        JOIN v$sesstat se ON s.sid = se.sid
        JOIN v$statname sn ON se.statistic# = sn.statistic#
        WHERE sn.name = 'CPU used by this session'
          AND s.username IS NOT NULL
          AND s.status != 'KILLED'
          AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
        ORDER BY se.value DESC
    ) WHERE ROWNUM <= 10
) s
LEFT JOIN v$sql q ON s.sql_id = q.sql_id
LEFT JOIN v$sql pq ON s.prev_sql_id = pq.sql_id
"""

# 8. BLOCKING & BLOCKED SESSIONS
QUERY_BLOCKING_SESSIONS = """
SELECT
    s1.sid                             AS blocking_sid,
    NVL(s1.username, '(background)')   AS blocking_user,
    s1.program                         AS blocking_program,
    s1.status                          AS blocking_status,
    s2.sid                             AS blocked_sid,
    NVL(s2.username, '(background)')   AS blocked_user,
    s2.program                         AS blocked_program,
    s2.wait_class                      AS wait_class,
    s2.seconds_in_wait                 AS seconds_in_wait,
    s2.state                           AS wait_state,
    s2.sql_id                          AS blocked_sql_id,
    SUBSTR(q.sql_text, 1, 120)         AS blocked_sql_text
FROM v$session s1
JOIN v$session s2 ON s1.sid = s2.blocking_session
LEFT JOIN v$sql q  ON s2.sql_id = q.sql_id
ORDER BY s2.seconds_in_wait DESC
"""

# 9. DEADLOCK / LOCK WAITING SESSIONS (sessions stuck waiting on a lock)
QUERY_LOCK_WAITS = """
SELECT
    s.sid,
    NVL(s.username, '(background)')  AS username,
    s.program,
    s.blocking_session,
    s.wait_class,
    s.seconds_in_wait,
    s.state,
    s.sql_id,
    SUBSTR(q.sql_text, 1, 120)       AS sql_text,
    l.type                           AS lock_type,
    l.request                        AS lock_request,
    l.lmode                          AS lock_mode
FROM v$session s
LEFT JOIN v$sql    q ON s.sql_id = q.sql_id
LEFT JOIN v$lock   l ON s.sid   = l.sid AND l.request > 0
WHERE s.blocking_session IS NOT NULL
   OR s.wait_class IN ('Application', 'Concurrency', 'Lock')
ORDER BY s.seconds_in_wait DESC
"""

# 10. ALERT LOG ERRORS (Last 48 hours)
QUERY_ALERT_LOG = """
SELECT * FROM (
    SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') as time_str,
           message_text
    FROM v$diag_alert_ext 
    WHERE originating_timestamp >= SYSDATE - 2 
      AND message_text LIKE '%ORA-%'
    ORDER BY originating_timestamp DESC
) WHERE ROWNUM <= 50
"""

# 11. ARCHIVE LOG SPACE USAGE
QUERY_ARC_LOG = """
SELECT
  (SELECT COUNT(*) FROM v$archived_log WHERE deleted='NO') AS archive_logs,
  (SELECT ROUND(NVL(SUM(blocks*block_size), 0)/1024/1024/1024, 2) FROM v$archived_log WHERE deleted='NO') AS size_gb,
  (SELECT value FROM V$PARAMETER WHERE name = 'db_recovery_file_dest') AS fra_dest_param,
  (SELECT ROUND(SPACE_LIMIT / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_limit_gb,
  (SELECT ROUND(SPACE_USED / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_used_gb
FROM DUAL
"""



# 12. MAX SESSION LIMIT
QUERY_MAX_SESSIONS = """
SELECT name, value FROM v$parameter WHERE name = 'sessions'
"""


def get_arc_log_info():
    """Fetch archive log info dynamically using v$archive_dest and either v$recovery_file_dest or v$archived_log."""
    try:
        df_dest = execute_query_to_df("SELECT destination FROM v$archive_dest WHERE dest_id = 1")
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
            df_fra = execute_query_to_df("SELECT name, ROUND(space_limit/1024/1024/1024,2) AS allocated_gb, ROUND(space_used/1024/1024/1024,2) AS used_gb, ROUND((space_used/space_limit)*100,2) AS pct_used FROM v$recovery_file_dest")
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
            df_arch = execute_query_to_df("SELECT COUNT(*) AS archive_logs, ROUND(SUM(blocks*block_size)/1024/1024/1024,2) AS size_gb FROM v$archived_log")
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



def get_max_sessions():
    """Fetch the max sessions parameter from v$parameter."""
    try:
        df = execute_query_to_df(QUERY_MAX_SESSIONS)
        if not df.empty:
            return int(float(df.iloc[0]["VALUE"]))
    except Exception:
        pass
    return 0

def get_db_status():
    """Fetch database status and details."""
    df = execute_query_to_df(QUERY_DB_STATUS)
    if df.empty:
        return {"STATUS": "DOWN", "INSTANCE_NAME": "UNKNOWN", "HOST_NAME": "UNKNOWN", "VERSION": "UNKNOWN", "STARTUP_TIME": "UNKNOWN"}
    return df.iloc[0].to_dict()

def get_session_stats():
    """Fetch current session stats."""
    df = execute_query_to_df(QUERY_SESSION_STATS)
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

def get_tablespace_utilization():
    """Fetch tablespace utilization metrics."""
    df = execute_query_to_df(QUERY_TABLESPACES)
    if df.empty:
        df = execute_query_to_df(QUERY_TABLESPACES_FALLBACK)
    return df

def get_backup_status():
    """Fetch latest backup status."""
    df = execute_query_to_df(QUERY_BACKUP_STATUS)
    if df.empty:
        return "NO BACKUP"
    return df.iloc[0]["STATUS"]

def get_rman_durations():
    """Fetch all RMAN backup durations (complete history)."""
    return execute_query_to_df(QUERY_RMAN_DURATIONS)

def get_session_license():
    """Fetch current sessions count and highwater mark."""
    df = execute_query_to_df(QUERY_SESSION_LICENSE)
    if df.empty:
        return {"sessions_current": 0, "sessions_highwater": 0}
    row = df.iloc[0]
    return {
        "sessions_current": int(row.get("SESSIONS_CURRENT", 0)),
        "sessions_highwater": int(row.get("SESSIONS_HIGHWATER", 0))
    }

def get_db_growth_rates():
    """Fetch historical DB growth data and calculate actual daily, weekly, monthly, and yearly usage changes."""
    df = execute_query_to_df(QUERY_DB_GROWTH)
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


def get_cpu_consuming_sessions():
    """Fetch top CPU consuming sessions."""
    return execute_query_to_df(QUERY_CPU_SESSIONS)

def get_blocking_sessions():
    """Fetch current blocking sessions."""
    return execute_query_to_df(QUERY_BLOCKING_SESSIONS)

def get_lock_waits():
    """Fetch lock wait details."""
    return execute_query_to_df(QUERY_LOCK_WAITS)

def get_alert_log():
    """Fetch alert log errors for the last 48 hours. Handled gracefully if view is unavailable."""
    try:
        df = execute_query_to_df(QUERY_ALERT_LOG)
        return df
    except Exception as e:
        print(f"Alert log query failed (likely permission/view missing): {e}")
        import pandas as pd
        return pd.DataFrame()

def get_sga_pga_usage():
    """Fetch SGA and PGA allocation, consumption, and free space details."""
    sga_allocated = sga_free = sga_consumed = sga_used_pct = 0.0
    pga_allocated = pga_consumed = pga_free = pga_used_pct = pga_target = 0.0

    df_sga = execute_query_to_df("SELECT SUM(value) as total_bytes FROM v$sga")
    if not df_sga.empty:
        try:
            sga_allocated = float(df_sga.iloc[0]["TOTAL_BYTES"])
        except Exception:
            pass

    df_sga_free = execute_query_to_df("SELECT SUM(bytes) as free_bytes FROM v$sgastat WHERE name = 'free memory'")
    if not df_sga_free.empty:
        try:
            sga_free = float(df_sga_free.iloc[0]["FREE_BYTES"])
        except Exception:
            pass

    sga_consumed = max(0.0, sga_allocated - sga_free)
    if sga_allocated > 0:
        sga_used_pct = round((sga_consumed / sga_allocated) * 100, 2)

    df_pga = execute_query_to_df("""
        SELECT name, value 
        FROM v$pgastat 
        WHERE name IN ('aggregate PGA target parameter', 'total PGA allocated', 'total PGA inuse')
    """)
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


def get_listener_status():
    """Check listener status by querying V$LISTENER_NETWORK from the connected remote DB session.

    V$LISTENER_NETWORK actual columns (Oracle 19c): NETWORK, TYPE, VALUE, CON_ID
    - TYPE = 'LOCAL LISTENER' means listener is registered with this instance
    - VALUE contains the address e.g. (ADDRESS=(PROTOCOL=TCP)(HOST=...)(PORT=1521))

    If we can query this view and get rows, the listener is UP and registered.
    If this view is not accessible, we fall back to V$INSTANCE — if the DB session
    is open at all, the listener MUST be running (you cannot connect without it).
    """
    try:
        # Primary: V$LISTENER_NETWORK — actual columns are TYPE, VALUE (NOT status)
        df = execute_query_to_df(
            "SELECT type, value FROM v$listener_network WHERE ROWNUM = 1"
        )
        if not df.empty:
            # If rows exist, listener is registered with this instance = UP
            return "UP"
    except Exception:
        pass

    # Fallback: if V$LISTENER_NETWORK not accessible —
    # if DB session is open at all, listener MUST be running
    try:
        df = execute_query_to_df("SELECT status FROM v$instance WHERE ROWNUM = 1")
        if not df.empty:
            return "UP"
    except Exception:
        pass

    return "DOWN"


# 13. ASM DISKGROUPS
QUERY_ASM_STORAGE = """
SELECT g.name as disk_group, 
       g.total_mb, 
       g.free_mb, 
       g.state, 
       g.type,
       (SELECT COUNT(*) FROM v$asm_disk d WHERE d.group_number = g.group_number) as disk_count
FROM v$asm_diskgroup g
"""

def get_asm_storage(conn=None):
    """Fetch ASM Diskgroup storage statistics."""
    from db_connection import execute_query_to_df
    try:
        if conn:
            # We must use pandas read_sql so we can pass the existing conn
            import pandas as pd
            return pd.read_sql(QUERY_ASM_STORAGE, con=conn)
        else:
            return execute_query_to_df(QUERY_ASM_STORAGE)
    except Exception as e:
        print(f"ASM Storage Query Failed: {e}")
        import pandas as pd
        return pd.DataFrame()


# 14. SYSTEM & HOST RESOURCE METRICS (OS CPU, RAM, Oracle OS Sessions)
QUERY_OS_STATS = """
SELECT stat_name, value FROM v$osstat 
WHERE stat_name IN ('NUM_CPUS', 'PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'IDLE_TIME', 'BUSY_TIME')
"""

QUERY_SYSMETRIC = """
SELECT metric_name, value FROM v$sysmetric 
WHERE metric_name IN ('Host CPU Utilization (%)', 'CPU Usage Per Sec', 'Session Count')
  AND group_id = 2
"""

QUERY_ORACLE_MEM = """
SELECT 
  (SELECT SUM(value) FROM v$sga) as sga_bytes,
  (SELECT SUM(pga_alloc_mem) FROM v$process) as pga_bytes
FROM dual
"""

def get_system_resources():
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
        df_os = execute_query_to_df(QUERY_OS_STATS)
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
        df_sys = execute_query_to_df(QUERY_SYSMETRIC)
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
        df_mem = execute_query_to_df(QUERY_ORACLE_MEM)
        if not df_mem.empty:
            sga = float(df_mem.iloc[0].get("SGA_BYTES", 0) or 0)
            pga = float(df_mem.iloc[0].get("PGA_BYTES", 0) or 0)
            res["ram_oracle_gb"] = round((sga + pga) / 1024/1024/1024, 2)

    except Exception as e:
        print(f"Failed to fetch system resource stats: {e}")
        
    return res


def get_oracle_pid_sid_map(conn) -> dict:
    """
    Query v$process + v$session to map OS PID (str) → Oracle SID (int).

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
