CREATE OR REPLACE PACKAGE DASHBOARD_PKG IS
  -- Returns tablespace usage metrics
  PROCEDURE GET_TABLESPACE(p_cursor OUT SYS_REFCURSOR);

  -- Returns session statistics (ACTIVE, INACTIVE, TOTAL)
  PROCEDURE GET_SESSION_STATS(p_cursor OUT SYS_REFCURSOR);

  -- Returns the latest RMAN backup status
  PROCEDURE GET_BACKUP_STATUS(p_cursor OUT SYS_REFCURSOR);

  -- Returns currently blocking sessions information
  PROCEDURE GET_BLOCKING_SESSIONS(p_cursor OUT SYS_REFCURSOR);

  -- Returns top CPU consuming sessions
  PROCEDURE GET_TOP_CPU_SESSIONS(p_cursor OUT SYS_REFCURSOR);

  -- Returns database instance status and name
  PROCEDURE GET_DATABASE_STATUS(p_cursor OUT SYS_REFCURSOR);
END DASHBOARD_PKG;
/

CREATE OR REPLACE PACKAGE BODY DASHBOARD_PKG IS

  PROCEDURE GET_TABLESPACE(p_cursor OUT SYS_REFCURSOR) IS
  BEGIN
    OPEN p_cursor FOR
      SELECT tablespace_name,
             ROUND(used_space * 100 / tablespace_size, 2) AS used_pct,
             ROUND((tablespace_size - used_space) * 100 / tablespace_size, 2) AS free_pct
      FROM dba_tablespace_usage_metrics
      ORDER BY used_pct DESC;
  END GET_TABLESPACE;

  PROCEDURE GET_SESSION_STATS(p_cursor OUT SYS_REFCURSOR) IS
  BEGIN
    OPEN p_cursor FOR
      SELECT status,
             COUNT(1) AS cnt
      FROM v$session
      WHERE username IS NOT NULL
        AND username NOT IN ('SYS','SYSTEM')
      GROUP BY status;
  END GET_SESSION_STATS;

  PROCEDURE GET_BACKUP_STATUS(p_cursor OUT SYS_REFCURSOR) IS
  BEGIN
    OPEN p_cursor FOR
      SELECT status
      FROM (
        SELECT status
        FROM v$rman_backup_job_details
        WHERE start_time >= (SELECT MAX(start_time) FROM v$rman_backup_job_details)
        ORDER BY start_time DESC
      )
      WHERE ROWNUM = 1;
  END GET_BACKUP_STATUS;

  PROCEDURE GET_BLOCKING_SESSIONS(p_cursor OUT SYS_REFCURSOR) IS
  BEGIN
    OPEN p_cursor FOR
      SELECT s.sid, s.serial#, s.username, s.status, s.sql_id, s.wait_class,
             s.event, s.seconds_in_wait, s.blocking_session
      FROM v$session s
      WHERE s.blocking_session IS NOT NULL;
  END GET_BLOCKING_SESSIONS;

  PROCEDURE GET_TOP_CPU_SESSIONS(p_cursor OUT SYS_REFCURSOR) IS
  BEGIN
    OPEN p_cursor FOR
      SELECT sid, serial#, username, cpu_time, status
      FROM v$session
      WHERE username IS NOT NULL
      ORDER BY cpu_time DESC
      FETCH FIRST 10 ROWS ONLY;
  END GET_TOP_CPU_SESSIONS;

  PROCEDURE GET_DATABASE_STATUS(p_cursor OUT SYS_REFCURSOR) IS
  BEGIN
    OPEN p_cursor FOR
      SELECT name AS db_name, open_mode,
             (SELECT status FROM v$instance) AS instance_status
      FROM v$database;
  END GET_DATABASE_STATUS;

END DASHBOARD_PKG;
/
