CREATE OR REPLACE PACKAGE dashboard_pkg AS
    PROCEDURE get_db_status(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_session_stats(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_tablespaces(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_backup_status(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_rman_durations(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_session_license(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_db_growth(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_cpu_sessions(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_blocking_sessions(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_lock_waits(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_alert_log(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_max_sessions(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_asm_storage(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_os_stats(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_sysmetric(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_oracle_mem(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_arc_log(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_arc_dest(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_arc_fra(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_arc_custom(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_sga_total(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_sga_free(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_pga_stats(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_listener_network(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_listener_instance(p_recordset OUT SYS_REFCURSOR);
    PROCEDURE get_pid_sid_map(p_recordset OUT SYS_REFCURSOR);
END dashboard_pkg;
/

CREATE OR REPLACE PACKAGE BODY dashboard_pkg AS

    PROCEDURE get_db_status(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT status, instance_name, host_name, version, 
               TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') as startup_time
        FROM v$instance;
    END get_db_status;

    PROCEDURE get_session_stats(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT status, COUNT(*) as cnt
        FROM v$session
        WHERE username IS NOT NULL
        GROUP BY status;
    END get_session_stats;

    PROCEDURE get_tablespaces(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
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
        ORDER BY df.tablespace_name;
    END get_tablespaces;

    PROCEDURE get_backup_status(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT status FROM (
            SELECT status
            FROM v$rman_backup_job_details
            ORDER BY start_time DESC
        ) WHERE ROWNUM = 1;
    END get_backup_status;

    PROCEDURE get_rman_durations(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT TO_CHAR(start_time, 'DD-Mon') as start_time_str,
               ROUND((end_time - start_time) * 24 * 60, 2) as duration_min,
               status,
               input_type
        FROM v$rman_backup_job_details
        WHERE start_time >= SYSDATE - 7
        ORDER BY start_time ASC;
    END get_rman_durations;

    PROCEDURE get_session_license(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT sessions_current, sessions_highwater FROM v$license;
    END get_session_license;

    PROCEDURE get_db_growth(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT TO_CHAR(record_date, 'YYYY-MM-DD') as record_date_str, used_mb, total_mb 
        FROM DASHBOARD_GROWTH_TRACK 
        ORDER BY record_date ASC;
    END get_db_growth;

    PROCEDURE get_cpu_sessions(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
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
        LEFT JOIN v$sql pq ON s.prev_sql_id = pq.sql_id;
    END get_cpu_sessions;

    PROCEDURE get_blocking_sessions(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
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
        ORDER BY s2.seconds_in_wait DESC;
    END get_blocking_sessions;

    PROCEDURE get_lock_waits(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
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
        ORDER BY s.seconds_in_wait DESC;
    END get_lock_waits;

    PROCEDURE get_alert_log(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT * FROM (
            SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') as time_str,
                   message_text
            FROM v$diag_alert_ext 
            WHERE originating_timestamp >= SYSDATE - 2 
              AND message_text LIKE '%ORA-%'
            ORDER BY originating_timestamp DESC
        ) WHERE ROWNUM <= 50;
    END get_alert_log;

    PROCEDURE get_max_sessions(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT name, value FROM v$parameter WHERE name = 'sessions';
    END get_max_sessions;

    PROCEDURE get_asm_storage(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT g.name as disk_group, 
               g.total_mb, 
               g.free_mb, 
               g.state, 
               g.type,
               (SELECT COUNT(*) FROM v$asm_disk d WHERE d.group_number = g.group_number) as disk_count
        FROM v$asm_diskgroup g;
    END get_asm_storage;

    PROCEDURE get_os_stats(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT stat_name, value FROM v$osstat 
        WHERE stat_name IN ('NUM_CPUS', 'PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'IDLE_TIME', 'BUSY_TIME');
    END get_os_stats;

    PROCEDURE get_sysmetric(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT metric_name, value FROM v$sysmetric 
        WHERE metric_name IN ('Host CPU Utilization (%)', 'CPU Usage Per Sec', 'Session Count')
          AND group_id = 2;
    END get_sysmetric;

    PROCEDURE get_oracle_mem(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT 
          (SELECT SUM(value) FROM v$sga) as sga_bytes,
          (SELECT SUM(pga_alloc_mem) FROM v$process) as pga_bytes
        FROM dual;
    END get_oracle_mem;

    PROCEDURE get_arc_log(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT
          (SELECT COUNT(*) FROM v$archived_log WHERE deleted='NO') AS archive_logs,
          (SELECT ROUND(NVL(SUM(blocks*block_size), 0)/1024/1024/1024, 2) FROM v$archived_log WHERE deleted='NO') AS size_gb,
          (SELECT value FROM V$PARAMETER WHERE name = 'db_recovery_file_dest') AS fra_dest_param,
          (SELECT ROUND(SPACE_LIMIT / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_limit_gb,
          (SELECT ROUND(SPACE_USED / 1024 / 1024 / 1024, 2) FROM V$RECOVERY_FILE_DEST WHERE rownum = 1) AS fra_used_gb
        FROM DUAL;
    END get_arc_log;

    PROCEDURE get_arc_dest(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT destination FROM v$archive_dest WHERE dest_id = 1;
    END get_arc_dest;

    PROCEDURE get_arc_fra(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT name, ROUND(space_limit/1024/1024/1024,2) AS allocated_gb, ROUND(space_used/1024/1024/1024,2) AS used_gb, ROUND((space_used/space_limit)*100,2) AS pct_used FROM v$recovery_file_dest;
    END get_arc_fra;

    PROCEDURE get_arc_custom(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT COUNT(*) AS archive_logs, ROUND(SUM(blocks*block_size)/1024/1024/1024,2) AS size_gb FROM v$archived_log;
    END get_arc_custom;

    PROCEDURE get_sga_total(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT SUM(value) as total_bytes FROM v$sga;
    END get_sga_total;

    PROCEDURE get_sga_free(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT SUM(bytes) as free_bytes FROM v$sgastat WHERE name = 'free memory';
    END get_sga_free;

    PROCEDURE get_pga_stats(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT name, value 
        FROM v$pgastat 
        WHERE name IN ('aggregate PGA target parameter', 'total PGA allocated', 'total PGA inuse');
    END get_pga_stats;

    PROCEDURE get_listener_network(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT type, value FROM v$listener_network WHERE ROWNUM = 1;
    END get_listener_network;

    PROCEDURE get_listener_instance(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT status FROM v$instance WHERE ROWNUM = 1;
    END get_listener_instance;

    PROCEDURE get_pid_sid_map(p_recordset OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_recordset FOR
        SELECT p.spid AS os_pid, s.sid AS oracle_sid, s.username
        FROM v$process p
        JOIN v$session s ON p.addr = s.paddr
        WHERE p.spid IS NOT NULL;
    END get_pid_sid_map;

END dashboard_pkg;
/
