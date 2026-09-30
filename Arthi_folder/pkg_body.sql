PACKAGE BODY GREENWORLD_MONITOR_PKG AS

    -- 1. Tablespace Usage
    PROCEDURE GET_TABLESPACE_USED(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            df.tablespace_name,
            ROUND((df.allocated_mb - NVL(fs.free_mb,0)) / 1024, 2) AS used_gb,
            ROUND(df.max_mb / 1024, 2) AS total_gb,
            ROUND(((df.allocated_mb - NVL(fs.free_mb,0)) / df.max_mb) * 100, 2) AS used_percent,
            ROUND((df.max_mb - (df.allocated_mb - NVL(fs.free_mb,0))) / 1024, 2) AS free_gb
        FROM
        (
            SELECT
                tablespace_name,
                SUM(bytes)/1024/1024 allocated_mb,
                SUM(
                    CASE
                        WHEN autoextensible=''YES''
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
        ORDER BY df.tablespace_name';
    END GET_TABLESPACE_USED;

    -- 2. Last Backup
    PROCEDURE GET_LAST_BACKUP(
        p_backup_cursor OUT SYS_REFCURSOR,
        p_yesterday_count OUT NUMBER
    ) IS
    BEGIN
        OPEN p_backup_cursor FOR
            'SELECT session_key, input_type, status, start_time, end_time, ROUND(input_bytes / 1024 / 1024, 2) AS size_mb
            FROM (
                SELECT session_key, input_type, status, start_time, end_time, input_bytes
                FROM v$rman_backup_job_details
                WHERE start_time >= (SELECT MAX(start_time) FROM v$rman_backup_job_details)
            )
            WHERE ROWNUM = 1';
            
        BEGIN
            EXECUTE IMMEDIATE 
            'SELECT COUNT(*) FROM v$rman_backup_job_details 
             WHERE start_time >= TRUNC(SYSDATE) - 1 
               AND start_time < TRUNC(SYSDATE) 
               AND status IN (''COMPLETED'', ''SUCCESS'')' 
            INTO p_yesterday_count;
        EXCEPTION
            WHEN OTHERS THEN
                p_yesterday_count := 0;
        END;
    END GET_LAST_BACKUP;

    -- 3. Backup History
    PROCEDURE GET_BACKUP_HISTORY(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            SESSION_KEY,
            INPUT_TYPE,
            STATUS,
            TO_CHAR(START_TIME, ''DD-MON HH24:MI'') AS START_TIME,
            TO_CHAR(END_TIME, ''DD-MON HH24:MI'') AS END_TIME,
            ROUND(OUTPUT_BYTES / 1024 / 1024, 2) AS SIZE_MB
        FROM
            V$RMAN_BACKUP_JOB_DETAILS
        WHERE
            START_TIME >= SYSDATE - 14
        ORDER BY
            START_TIME ASC';
    END GET_BACKUP_HISTORY;

    -- 4. Daily Backup Stats
    PROCEDURE GET_DAILY_BACKUP_STATS(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            TO_CHAR(START_TIME, ''DD-MON'') AS DAY,
            COUNT(*) AS TOTAL_BACKUPS,
            ROUND(SUM(OUTPUT_BYTES)/1024/1024, 2) AS TOTAL_MB
        FROM
            V$RMAN_BACKUP_JOB_DETAILS
        WHERE
            START_TIME >= SYSDATE - 14
        GROUP BY
            TO_CHAR(START_TIME, ''DD-MON'')
        ORDER BY
            DAY';
    END GET_DAILY_BACKUP_STATS;

    -- 5. Backup Sessions Chart
    PROCEDURE GET_BACKUP_SESSIONS_CHART(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            TO_CHAR(START_TIME, ''DD MON'') AS DAY,
            ROWNUM AS SESSION_NUM,
            STATUS,
            ROUND(OUTPUT_BYTES/1024/1024, 2) AS SIZE_MB,
            ROUND(SUM(OUTPUT_BYTES) OVER (ORDER BY START_TIME)/1024/1024, 2) AS CUMULATIVE_MB
        FROM
            V$RMAN_BACKUP_JOB_DETAILS
        WHERE
            START_TIME >= SYSDATE - 14
        ORDER BY
            START_TIME';
    END GET_BACKUP_SESSIONS_CHART;

    -- 6. Session Log
    PROCEDURE GET_SESSION_LOG(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            TO_CHAR(START_TIME, ''DD MON'') AS DATE_STR,
            SESSION_KEY AS SESSION_NUM,
            STATUS,
            ROUND(OUTPUT_BYTES/1024/1024, 2) AS SIZE_MB,
            ROUND((END_TIME - START_TIME) * 24 * 60, 0) AS DURATION_MINUTES
        FROM
            V$RMAN_BACKUP_JOB_DETAILS
        WHERE
            START_TIME >= SYSDATE - 14
        ORDER BY
            START_TIME DESC';
    END GET_SESSION_LOG;

    -- 7. Get Sessions (Count all non-null usernames to include SYS/SYSTEM user sessions)
    PROCEDURE GET_SESSIONS(
        p_status_filter IN VARCHAR2,
        p_session_counts OUT SYS_REFCURSOR,
        p_max_sessions OUT NUMBER
    ) IS
        v_sql VARCHAR2(1000);
    BEGIN
        v_sql := 'SELECT count(1) AS Session_Count, status
                  FROM v$session
                  WHERE username IS NOT NULL';
        
        IF p_status_filter IS NOT NULL THEN
            v_sql := v_sql || ' AND status = :1 GROUP BY status ORDER BY status, MIN(logon_time)';
            OPEN p_session_counts FOR v_sql USING p_status_filter;
        ELSE
            v_sql := v_sql || ' GROUP BY status ORDER BY status, MIN(logon_time)';
            OPEN p_session_counts FOR v_sql;
        END IF;
            
        BEGIN
            EXECUTE IMMEDIATE 'SELECT TO_NUMBER(max_utilization) FROM v$resource_limit WHERE resource_name = ''sessions''' INTO p_max_sessions;
        EXCEPTION
            WHEN OTHERS THEN
                p_max_sessions := NULL;
        END;
    END GET_SESSIONS;

    -- 8. Get Active Sessions CPU
    PROCEDURE GET_ACTIVE_SESSIONS_CPU(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT s.sid, s.serial#, s.username, s.osuser, s.machine, 
               p.spid AS unix_process, 
               (se.value / 100) AS cpu_usage_seconds, 
               q.sql_id, q.sql_text,
               s.blocking_session,
               s.program,
               s.module,
               COALESCE(s.last_call_et, 0) AS active_seconds
        FROM v$session s
        JOIN v$sesstat se ON s.sid = se.sid
        JOIN v$statname sn ON se.statistic# = sn.statistic#
        JOIN v$process p ON s.paddr = p.addr
        LEFT JOIN v$sqlarea q ON s.sql_id = q.sql_id
        WHERE sn.name = ''CPU used by this session''
          AND s.username IS NOT NULL
          AND UPPER(s.username) NOT IN (''SYS'', ''SYSTEM'', ''DBSNMP'', ''SYSMAN'', ''SYSDG'', ''SYSBACKUP'', ''SYSKM'', ''SYSRAC'')
        ORDER BY cpu_usage_seconds DESC
        FETCH FIRST 10 ROWS ONLY';
    END GET_ACTIVE_SESSIONS_CPU;

    -- 9. Database Growth
    PROCEDURE GET_DATABASE_GROWTH(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            (SELECT MIN(creation_time) FROM v$datafile) AS creation_time,
            d.name AS database_name,
            ROUND(SUM(allocated_bytes) / 1024 / 1024, 2) AS database_size_mb,
            ROUND(SUM(used_bytes) / 1024 / 1024, 2) AS used_space_mb,
            ROUND((CASE WHEN SUM(allocated_bytes) > 0 THEN SUM(used_bytes) / SUM(allocated_bytes) ELSE 0 END) * 100, 2) AS used_percent,
            ROUND((SUM(allocated_bytes) - SUM(used_bytes)) / 1024 / 1024, 2) AS free_space_mb,
            ROUND((CASE WHEN SUM(allocated_bytes) > 0 THEN (SUM(allocated_bytes) - SUM(used_bytes)) / SUM(allocated_bytes) ELSE 0 END) * 100, 2) AS free_percent
         FROM (
             SELECT 
                 ts.tablespace_name,
                 NVL(mu.used_space * ts.block_size, 0) AS used_bytes,
                 NVL(df.allocated_bytes, 0) AS allocated_bytes
             FROM dba_tablespaces ts
             LEFT JOIN dba_tablespace_usage_metrics mu ON ts.tablespace_name = mu.tablespace_name
             LEFT JOIN (
                 SELECT tablespace_name, SUM(bytes) AS allocated_bytes 
                 FROM (
                     SELECT tablespace_name, bytes FROM dba_data_files
                     UNION ALL
                     SELECT tablespace_name, bytes FROM dba_temp_files
                 )
                 GROUP BY tablespace_name
             ) df ON ts.tablespace_name = df.tablespace_name
         )
         CROSS JOIN v$database d
         GROUP BY d.name';
    END GET_DATABASE_GROWTH;

    -- 10. DB Growth
    PROCEDURE GET_DB_GROWTH(p_cursor OUT SYS_REFCURSOR) IS
    BEGIN
        OPEN p_cursor FOR
        'SELECT
            d.name AS db_name,
            ROUND(SUM(allocated_bytes) / 1024 / 1024, 2) AS total_mb,
            ROUND(SUM(used_bytes) / 1024 / 1024, 2) AS used_mb,
            ROUND(
                (CASE WHEN (sysdate - (SELECT MIN(creation_time) FROM v$datafile)) > 0 
                      THEN (SUM(used_bytes) / 1024 / 1024) / (sysdate - (SELECT MIN(creation_time) FROM v$datafile))
                      ELSE 0 
                 END),
            2) AS growth_day_mb
         FROM (
             SELECT 
                 ts.tablespace_name,
                 NVL(mu.used_space * ts.block_size, 0) AS used_bytes,
                 NVL(df.allocated_bytes, 0) AS allocated_bytes
             FROM dba_tablespaces ts
             LEFT JOIN dba_tablespace_usage_metrics mu ON ts.tablespace_name = mu.tablespace_name
             LEFT JOIN (
                 SELECT tablespace_name, SUM(bytes) AS allocated_bytes 
                 FROM (
                     SELECT tablespace_name, bytes FROM dba_data_files
                     UNION ALL
                     SELECT tablespace_name, bytes FROM dba_temp_files
                 )
                 GROUP BY tablespace_name
             ) df ON ts.tablespace_name = df.tablespace_name
         )
         CROSS JOIN v$database d
         GROUP BY d.name';
    END GET_DB_GROWTH;


    -- 11. Memory Info
    PROCEDURE GET_MEMORY_INFO(
        p_sga_info OUT SYS_REFCURSOR,
        p_sga_free OUT NUMBER,
        p_pga_stats OUT SYS_REFCURSOR,
        p_pga_components OUT SYS_REFCURSOR
    ) IS
    BEGIN
        OPEN p_sga_info FOR 'SELECT name, bytes FROM v$sgainfo';
            
        BEGIN
            EXECUTE IMMEDIATE 'SELECT SUM(bytes) FROM v$sgastat WHERE name = ''free memory''' INTO p_sga_free;
        EXCEPTION
            WHEN OTHERS THEN
                p_sga_free := 0;
        END;
        
        OPEN p_pga_stats FOR 'SELECT name, value FROM v$pgastat';
            
        OPEN p_pga_components FOR 'SELECT category, sum(allocated) as allocated FROM v$process_memory GROUP BY category';
    END GET_MEMORY_INFO;

    -- 12. Top Memory Sessions (SGA/UGA and PGA)
    PROCEDURE GET_TOP_MEMORY_SESSIONS(
        p_uga_cursor OUT SYS_REFCURSOR,
        p_pga_cursor OUT SYS_REFCURSOR
    ) IS
    BEGIN
        OPEN p_uga_cursor FOR
        'SELECT s.sid, COALESCE(s.username, ''SYS'') AS username, 
               ROUND(sn.value / (1024 * 1024), 2) AS memory_mb,
               COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
               COALESCE(q1.sql_text, q2.sql_text) AS sql_text
        FROM v$session s
        JOIN v$sesstat sn ON s.sid = sn.sid
        JOIN v$statname n ON sn.statistic# = n.statistic#
        LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
        LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
        WHERE n.name = ''session uga memory''
          AND s.username IS NOT NULL
          AND UPPER(s.username) NOT IN (''SYS'', ''SYSTEM'')
          AND sn.value > 0
        ORDER BY sn.value DESC
        FETCH FIRST 3 ROWS ONLY';

        OPEN p_pga_cursor FOR
        'SELECT s.sid, COALESCE(s.username, ''SYS'') AS username, 
               ROUND(p.pga_alloc_mem / (1024 * 1024), 2) AS memory_mb,
               COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
               COALESCE(q1.sql_text, q2.sql_text) AS sql_text
        FROM v$session s
        JOIN v$process p ON s.paddr = p.addr
        LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
        LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
        WHERE s.username IS NOT NULL
          AND UPPER(s.username) NOT IN (''SYS'', ''SYSTEM'')
          AND p.pga_alloc_mem > 0
        ORDER BY p.pga_alloc_mem DESC
        FETCH FIRST 3 ROWS ONLY';
    END GET_TOP_MEMORY_SESSIONS;

END GREENWORLD_MONITOR_PKG;