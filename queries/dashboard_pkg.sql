-- name: QUERY_DB_STATUS
SELECT status, instance_name, host_name, version, 
       TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') as startup_time
FROM v$instance

-- name: QUERY_SESSION_STATS
SELECT status, COUNT(*) as cnt
FROM v$session
WHERE username IS NOT NULL
GROUP BY status

-- name: QUERY_TABLESPACES
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

-- name: QUERY_BACKUP_STATUS
SELECT status FROM (
    SELECT status
    FROM v$rman_backup_job_details
    ORDER BY start_time DESC
) WHERE ROWNUM = 1

-- name: QUERY_RMAN_DURATIONS
SELECT TO_CHAR(start_time, 'DD-Mon') as start_time_str,
       ROUND((end_time - start_time) * 24 * 60, 2) as duration_min,
       status,
       input_type
FROM v$rman_backup_job_details
WHERE start_time >= SYSDATE - 7
ORDER BY start_time ASC

-- name: QUERY_SESSION_LICENSE
SELECT sessions_current, sessions_highwater FROM v$license

-- name: QUERY_DB_GROWTH
SELECT TO_CHAR(record_date, 'YYYY-MM-DD') as record_date_str, used_mb, total_mb 
FROM DASHBOARD_GROWTH_TRACK 
ORDER BY record_date ASC

-- name: QUERY_CPU_SESSIONS
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

-- name: QUERY_BLOCKING_SESSIONS
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

-- name: QUERY_LOCK_WAITS
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

-- name: QUERY_ALERT_LOG
SELECT * FROM (
    SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') as time_str,
           message_text
    FROM v$diag_alert_ext 
    WHERE originating_timestamp >= SYSDATE - 2 
      AND message_text LIKE '%ORA-%'
    ORDER BY originating_timestamp DESC
) WHERE ROWNUM <= 50

-- name: QUERY_MAX_SESSIONS
SELECT name, value FROM v$parameter WHERE name = 'sessions'

-- name: QUERY_ASM_STORAGE
SELECT g.name as disk_group, 
       g.total_mb, 
       g.free_mb, 
       g.state, 
       g.type,
       (SELECT COUNT(*) FROM v$asm_disk d WHERE d.group_number = g.group_number) as disk_count
FROM v$asm_diskgroup g

-- name: QUERY_OS_STATS
SELECT stat_name, value FROM v$osstat 
WHERE stat_name IN ('NUM_CPUS', 'PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'IDLE_TIME', 'BUSY_TIME')

-- name: QUERY_SYSMETRIC
SELECT metric_name, value FROM v$sysmetric 
WHERE metric_name IN ('Host CPU Utilization (%)', 'CPU Usage Per Sec', 'Session Count')
  AND group_id = 2

-- name: QUERY_ORACLE_MEM
SELECT 
  (SELECT SUM(value) FROM v$sga) as sga_bytes,
  (SELECT SUM(pga_alloc_mem) FROM v$process) as pga_bytes
FROM dual

-- name: QUERY_ARC_LOG
SELECT
  (SELECT COUNT(*) FROM v$archived_log WHERE deleted='NO') AS archive_logs,
  (SELECT ROUND(NVL(SUM(blocks*block_size), 0)/1024/1024/1024, 2) FROM v$archived_log WHERE deleted='NO') AS size_gb,
  (SELECT value FROM V$PARAMETER WHERE name = 'db_recovery_file_dest') AS fra_dest_param,
  (SELECT ROUND(SPACE_LIMIT / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_limit_gb,
  (SELECT ROUND(SPACE_USED / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_used_gb
FROM DUAL

-- name: QUERY_ARC_DEST
SELECT destination FROM v$archive_dest WHERE dest_id = 1

-- name: QUERY_ARC_FRA
SELECT name, ROUND(space_limit/1024/1024/1024,2) AS allocated_gb, ROUND(space_used/1024/1024/1024,2) AS used_gb, ROUND((space_used/space_limit)*100,2) AS pct_used FROM v$recovery_file_dest

-- name: QUERY_ARC_CUSTOM
SELECT COUNT(*) AS archive_logs, ROUND(SUM(blocks*block_size)/1024/1024/1024,2) AS size_gb FROM v$archived_log

-- name: QUERY_SGA_TOTAL
SELECT SUM(value) as total_bytes FROM v$sga

-- name: QUERY_SGA_FREE
SELECT SUM(bytes) as free_bytes FROM v$sgastat WHERE name = 'free memory'

-- name: QUERY_PGA_STATS
SELECT name, value 
FROM v$pgastat 
WHERE name IN ('aggregate PGA target parameter', 'total PGA allocated', 'total PGA inuse')

-- name: QUERY_LISTENER_NETWORK
SELECT type, value FROM v$listener_network WHERE ROWNUM = 1

-- name: QUERY_LISTENER_INSTANCE
SELECT status FROM v$instance WHERE ROWNUM = 1

-- name: QUERY_PID_SID_MAP
SELECT p.spid AS os_pid, s.sid AS oracle_sid, s.username
FROM v$process p
JOIN v$session s ON p.addr = s.paddr
WHERE p.spid IS NOT NULL
