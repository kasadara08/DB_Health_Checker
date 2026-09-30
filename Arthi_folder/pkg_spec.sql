PACKAGE GREENWORLD_MONITOR_PKG AUTHID CURRENT_USER AS
    -- 1. Tablespace usage
    PROCEDURE GET_TABLESPACE_USED(p_cursor OUT SYS_REFCURSOR);
    
    -- 2. Last RMAN backup (includes yesterday's success count)
    PROCEDURE GET_LAST_BACKUP(
        p_backup_cursor OUT SYS_REFCURSOR,
        p_yesterday_count OUT NUMBER
    );
    
    -- 3. Backup history (past 14 days)
    PROCEDURE GET_BACKUP_HISTORY(p_cursor OUT SYS_REFCURSOR);
    
    -- 4. Daily backup stats for graphs
    PROCEDURE GET_DAILY_BACKUP_STATS(p_cursor OUT SYS_REFCURSOR);
    
    -- 5. Backup sessions with cumulative size
    PROCEDURE GET_BACKUP_SESSIONS_CHART(p_cursor OUT SYS_REFCURSOR);
    
    -- 6. Backup session logs
    PROCEDURE GET_SESSION_LOG(p_cursor OUT SYS_REFCURSOR);
    
    -- 7. Active sessions count by status and max allowed
    PROCEDURE GET_SESSIONS(
        p_status_filter IN VARCHAR2,
        p_session_counts OUT SYS_REFCURSOR,
        p_max_sessions OUT NUMBER
    );
    
    -- 8. Top 10 CPU consuming active sessions
    PROCEDURE GET_ACTIVE_SESSIONS_CPU(p_cursor OUT SYS_REFCURSOR);
    
    -- 9. Comprehensive database growth stats
    PROCEDURE GET_DATABASE_GROWTH(p_cursor OUT SYS_REFCURSOR);
    
    -- 10. Simplified db growth stats
    PROCEDURE GET_DB_GROWTH(p_cursor OUT SYS_REFCURSOR);
    
    -- 11. Memory info (SGA and PGA)
    PROCEDURE GET_MEMORY_INFO(
        p_sga_info OUT SYS_REFCURSOR,
        p_sga_free OUT NUMBER,
        p_pga_stats OUT SYS_REFCURSOR,
        p_pga_components OUT SYS_REFCURSOR
    );
    
    -- 12. Top memory consuming sessions (SGA/UGA and PGA)
    PROCEDURE GET_TOP_MEMORY_SESSIONS(
        p_uga_cursor OUT SYS_REFCURSOR,
        p_pga_cursor OUT SYS_REFCURSOR
    );
END GREENWORLD_MONITOR_PKG;