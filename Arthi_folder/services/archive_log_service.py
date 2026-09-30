import os
import sys
import json
import time
import logging
from datetime import datetime
from services.db_service import get_connection_by_id
from services.config_service import get_db_config
from services.ssh_service import get_ssh_connection

# Path to persistent cleanup logs
if getattr(sys, "frozen", False):
    HISTORY_FILE = os.path.join(os.path.dirname(sys.executable), "data", "archive_cleanup_history.json")
else:
    HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "archive_cleanup_history.json")

def _initialize_history():
    """Ensure history file exists and is valid JSON."""
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
    if not os.path.exists(HISTORY_FILE) or os.path.getsize(HISTORY_FILE) == 0:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4)

def get_last_cleanup_record(db_id):
    """Retrieve the latest cleanup operation for the given db_id from history log."""
    _initialize_history()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        # Filter and sort by start_time descending
        db_history = [h for h in history if h.get("db_id") == db_id]
        if db_history:
            # Parse datetime format to sort correctly
            db_history.sort(key=lambda x: x.get("start_time", ""), reverse=True)
            return db_history[0]
    except Exception as e:
        logging.error(f"Error reading cleanup history: {e}")
    return None

def get_archive_cleanup_info(db_id, retention_days=10):
    """
    Step 1 & 2 & 3: Detect Destination, FRA status & eligible old archive log count.
    """
    _initialize_history()
    result = {
        "log_mode": "UNKNOWN",
        "archive_type": "N/A",
        "archive_destination": "N/A",
        "fra_space_limit_gb": 0.0,
        "fra_space_used_gb": 0.0,
        "fra_space_reclaimable_gb": 0.0,
        "fra_space_free_gb": 0.0,
        "eligible_log_count": 0,
        "last_cleanup_status": "Never Run",
        "last_cleanup_time": "N/A"
    }

    conn = get_connection_by_id(db_id)
    if not conn:
        return {"error": "Cannot connect to database"}

    try:
        cursor = conn.cursor()

        # Step 1: Verify Log Mode
        cursor.execute("SELECT LOG_MODE FROM V$DATABASE")
        mode_row = cursor.fetchone()
        log_mode = mode_row[0] if mode_row else "UNKNOWN"
        result["log_mode"] = log_mode

        if log_mode != "ARCHIVELOG":
            cursor.close()
            conn.close()
            # Still check last cleanup stats from log file
            last_run = get_last_cleanup_record(db_id)
            if last_run:
                result["last_cleanup_status"] = last_run.get("status", "UNKNOWN")
                result["last_cleanup_time"] = last_run.get("start_time", "N/A")
            return result

        # Step 2: Detect Archive Log Destination
        # First, find the active destination
        cursor.execute("""
            SELECT DESTINATION, TARGET, STATUS 
            FROM V$ARCHIVE_DEST 
            WHERE STATUS = 'VALID' AND DEST_ID = 1
        """)
        dest_row = cursor.fetchone()
        
        # Fallback to general valid destinations if dest_id = 1 isn't active
        if not dest_row:
            cursor.execute("""
                SELECT DESTINATION, TARGET, STATUS 
                FROM V$ARCHIVE_DEST 
                WHERE STATUS = 'VALID' 
                ORDER BY DEST_ID
            """)
            dest_row = cursor.fetchone()

        destination = ""
        is_fra = False
        if dest_row:
            destination = str(dest_row[0] or "").strip()
            # If target is STANDBY or similar, handle appropriately, but usually it is PRIMARY
            if destination.upper() == "USE_DB_RECOVERY_FILE_DEST":
                is_fra = True
            
        # Also query parameter directly for displaying the folder OR validation
        cursor.execute("""
            SELECT NAME, VALUE 
            FROM V$PARAMETER 
            WHERE NAME = 'log_archive_dest_1'
        """)
        param_row = cursor.fetchone()
        param_value = str(param_row[1] or "").strip() if param_row else ""
        
        # If param value is location=... extract the path
        if param_value.upper().startswith("LOCATION="):
            extracted_path = param_value[9:].strip()
            if extracted_path.upper() == "USE_DB_RECOVERY_FILE_DEST":
                is_fra = True
                destination = "USE_DB_RECOVERY_FILE_DEST"
            else:
                destination = extracted_path if not destination else destination
        
        # Resolve to parameter location if still empty
        if not destination:
            destination = param_value or "N/A"

        result["archive_destination"] = destination

        # Determine Archive Type
        if is_fra or destination.upper() == "USE_DB_RECOVERY_FILE_DEST":
            result["archive_type"] = "Oracle FRA"
            
            # Step 3: Retrieve FRA details
            cursor.execute("""
                SELECT NAME, 
                       ROUND(SPACE_LIMIT / 1024 / 1024 / 1024, 2) AS LIMIT_GB,
                       ROUND(SPACE_USED / 1024 / 1024 / 1024, 2) AS USED_GB,
                       ROUND(SPACE_RECLAIMABLE / 1024 / 1024 / 1024, 2) AS RECLAIM_GB
                FROM V$RECOVERY_FILE_DEST
            """)
            fra_row = cursor.fetchone()
            if fra_row:
                name = str(fra_row[0] or "").strip()
                result["archive_destination"] = name if name else destination
                result["fra_space_limit_gb"] = float(fra_row[1] or 0.0)
                result["fra_space_used_gb"] = float(fra_row[2] or 0.0)
                result["fra_space_reclaimable_gb"] = float(fra_row[3] or 0.0)
                result["fra_space_free_gb"] = round(max(0.0, result["fra_space_limit_gb"] - result["fra_space_used_gb"] + result["fra_space_reclaimable_gb"]), 2)
        else:
            result["archive_type"] = "Filesystem"

        # Step 4: Determine Archive Logs Eligible for Cleanup (> retention_days Days)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM V$ARCHIVED_LOG 
            WHERE COMPLETION_TIME < SYSDATE - :retention_days AND DELETED = 'NO'
        """, {"retention_days": retention_days})
        eligible_row = cursor.fetchone()
        result["eligible_log_count"] = int(eligible_row[0] or 0)

        cursor.close()
        conn.close()

    except Exception as e:
        if conn:
            try: conn.close()
            except: pass
        logging.error(f"Error querying archive log info: {e}")
        return {"error": str(e)}

    # Fetch last cleanup stats from history file
    last_run = get_last_cleanup_record(db_id)
    if last_run:
        result["last_cleanup_status"] = last_run.get("status", "UNKNOWN")
        result["last_cleanup_time"] = last_run.get("start_time", "N/A")

    return result

def cleanup_archive_logs(db_id, retention_days=10):
    """
    Connect via SSH, run Oracle RMAN commands, parse output, log to history.
    """
    _initialize_history()
    
    # 1. Fetch info and verify prerequisites
    info = get_archive_cleanup_info(db_id, retention_days=retention_days)
    if "error" in info:
        return {"status": "FAILED", "errors": f"Failed to gather pre-cleanup database info: {info['error']}"}

    if info["log_mode"] != "ARCHIVELOG":
        return {"status": "FAILED", "errors": "Database is not in ARCHIVELOG mode."}

    db_cfg = get_db_config(db_id)
    if not db_cfg:
        return {"status": "FAILED", "errors": f"Database config not found for ID: {db_id}"}
        
    db_name = db_cfg.get("service_name") or db_cfg.get("host") or "kasorcl"

    # Start metadata logging
    start_dt = datetime.now()
    start_time_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 2. Establish SSH connection
    ssh_client, _, ssh_err = get_ssh_connection(db_id=db_id, timeout=15, banner_timeout=15)
    if not ssh_client:
        err_msg = ssh_err or "SSH connection failed."
        # Write failure record to history
        run_record = {
            "db_id": db_id,
            "db_name": db_name,
            "archive_dest": info["archive_destination"],
            "archive_type": info["archive_type"],
            "method": "Oracle RMAN",
            "retention_days": retention_days,
            "start_time": start_time_str,
            "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": round((datetime.now() - start_dt).total_seconds(), 2),
            "deleted_count": 0,
            "rman_output": f"SSH Connection Failed: {err_msg}",
            "errors": err_msg,
            "status": "FAILED"
        }
        _save_cleanup_record(run_record)
        return run_record

    # 3. Form RMAN script command based on OS Type
    oracle_sid = db_cfg.get("service_name")
    db_user = db_cfg.get("username") or ""
    db_pwd = db_cfg.get("password") or ""
    
    # Check if target host is Windows or Linux
    is_windows = False
    conn = get_connection_by_id(db_id)
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT PLATFORM_NAME FROM V$DATABASE")
            plat_row = cursor.fetchone()
            if plat_row and "WIN" in str(plat_row[0]).upper():
                is_windows = True
            cursor.close()
            conn.close()
        except:
            if conn:
                try: conn.close()
                except: pass

    if is_windows:
        # Windows command
        cmd = f'set ORACLE_SID={oracle_sid} && (echo CROSSCHECK ARCHIVELOG ALL; & echo DELETE NOPROMPT EXPIRED ARCHIVELOG ALL; & echo DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE "sysdate-{retention_days}"; & echo exit;) | rman target /'
    else:
        # Linux / Unix command (PMON/oratab auto-locating RMAN with 3 connection auth attempts)
        cmd = fr"""
        # Find ORACLE_HOME from running PMON process
        PID=$(pgrep -f -d, "pmon_{db_name}" || pgrep -f -d, "pmon_$(echo {db_name} | tr '[:upper:]' '[:lower:]')" || pgrep -f -d, "pmon_$(echo {db_name} | tr '[:lower:]' '[:upper:]')")
        PID=$(echo $PID | cut -d, -f1)
       
        ORACLE_HOME=""
        if [ -n "$PID" ]; then
            ORACLE_HOME=$(cat /proc/$PID/environ | tr '\\0' '\\n' | grep -a '^ORACLE_HOME=' | cut -d= -f2)
        fi
       
        if [ -z "$ORACLE_HOME" ] && [ -f /etc/oratab ]; then
            ORACLE_HOME=$(grep -a -i "^{db_name}:" /etc/oratab | cut -d: -f2)
        fi
       
        if [ -n "$ORACLE_HOME" ]; then
            export ORACLE_HOME
            export PATH=$ORACLE_HOME/bin:$PATH
        fi
        export ORACLE_SID={db_name}
       
        # We will attempt up to 3 connection styles for RMAN to ensure we connect successfully
        SUCCESS=0
       
        # Try 1: OS Authentication (Standard oracle user)
        echo "=== Attempting RMAN OS Auth ==="
        rman target / <<EOF
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-{retention_days}';
EOF
        if [ $? -eq 0 ]; then
            SUCCESS=1
        fi
       
        # Try 2: SYSDBA Authentication with credentials (if Try 1 failed)
        if [ $SUCCESS -eq 0 ] && [ -n "{db_user}" ] && [ -n "{db_pwd}" ]; then
            echo "=== Attempting RMAN SYSDBA Auth ==="
            # Escape single quotes in password if any
            PWD_ESC=$(echo "{db_pwd}" | sed "s/'/'\\\\''/g")
            rman target '{db_user}'/'"$PWD_ESC"' as sysdba <<EOF
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-{retention_days}';
EOF
            if [ $? -eq 0 ]; then
                SUCCESS=1
            fi
        fi
       
        # Try 3: Standard Authentication with credentials (if Try 1 and 2 failed)
        if [ $SUCCESS -eq 0 ] && [ -n "{db_user}" ] && [ -n "{db_pwd}" ]; then
            echo "=== Attempting RMAN Standard Auth ==="
            PWD_ESC=$(echo "{db_pwd}" | sed "s/'/'\\\\''/g")
            rman target '{db_user}'/'"$PWD_ESC"' <<EOF
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-{retention_days}';
EOF
            if [ $? -eq 0 ]; then
                SUCCESS=1
            fi
        fi
       
        # If all RMAN attempts failed, output a marker for detection
        if [ $SUCCESS -eq 0 ]; then
            echo "RMAN_AUTHENTICATION_FAILED" >&2
        fi
        """

    rman_stdout = ""
    rman_stderr = ""
    status = "SUCCESS"
    errs = ""

    # Execute SSH command
    try:
        cmd = cmd.replace('\r\n', '\n')  # Clean up Windows line endings if running Linux script
        stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=120)
        
        # Read outputs
        rman_stdout = stdout.read().decode("utf-8", errors="ignore")
        rman_stderr = stderr.read().decode("utf-8", errors="ignore")
        
        ssh_client.close()
    except Exception as e:
        status = "FAILED"
        errs = str(e)
        rman_stdout += f"\nCommand execution exception: {str(e)}"
        if ssh_client:
            try: ssh_client.close()
            except: pass

    # End metadata logging
    end_dt = datetime.now()
    end_time_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    duration = round((end_dt - start_dt).total_seconds(), 2)

    # 4. Parse deleted count and check for execution status failures
    deleted_count = 0
    if status == "SUCCESS":
        # Parse deleted logs count
        cumulative_deleted = 0
        for line in rman_stdout.splitlines():
            line_lower = line.lower()
            # Support both "deleted archive log" and "deleted archived log" or "deleted segment"
            if "deleted archived log" in line_lower or "deleted archive log" in line_lower or "deleted segment" in line_lower:
                deleted_count += 1
            
            # Match cumulative summaries like:
            # "Deleted 13 objects" or "Deleted 5 expired objects"
            import re
            m = re.search(r'deleted\s+(\d+)\s+(expired\s+)?objects', line_lower)
            if m:
                cumulative_deleted += int(m.group(1))

        if cumulative_deleted > deleted_count:
            deleted_count = cumulative_deleted
        
        # Check if RMAN/command failed
        if rman_stderr.strip():
            status = "FAILED"
            errs = rman_stderr.strip()
        elif "RMAN-" in rman_stdout or "ORA-" in rman_stdout:
            # If there's an RMAN or Oracle error in stdout, then mark as FAILED
            status = "FAILED"
            err_line = next((line.strip() for line in rman_stdout.splitlines() if "RMAN-" in line or "ORA-" in line), "")
            errs = err_line or "RMAN execution failed with database errors."

    run_record = {
        "db_id": db_id,
        "db_name": db_name,
        "archive_dest": info["archive_destination"],
        "archive_type": info["archive_type"],
        "method": "Oracle RMAN",
        "retention_days": retention_days,
        "start_time": start_time_str,
        "end_time": end_time_str,
        "duration_seconds": duration,
        "deleted_count": deleted_count,
        "rman_output": rman_stdout,
        "errors": errs or rman_stderr.strip() or None,
        "status": status
    }

    _save_cleanup_record(run_record)
    return run_record

def _save_cleanup_record(record):
    """Append a new execution record to JSON file database."""
    _initialize_history()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        
        history.append(record)
        
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=4)
    except Exception as e:
        logging.error(f"Error writing to cleanup history: {e}")
