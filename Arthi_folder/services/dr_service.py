import oracledb
import socket
import logging
import os
from services.config_service import get_db_config

logging.basicConfig(level=logging.INFO)

import threading

_dr_pools = {}
_dr_pools_lock = threading.Lock()
_log_activity_lock = threading.Lock()

# Each reporting/standby check spawns its own OS subprocess, and every one of
# those subprocesses independently loads the Oracle Instant Client DLLs
# (ctypes.WinDLL + oracledb.init_oracle_client) from the same on-disk files.
# With dozens of databases configured, the frontend polls all of them at once,
# so without a cap here, dozens of subprocesses can hit that native DLL load
# simultaneously - this native-level contention is what was crashing subprocesses
# with STATUS_STACK_BUFFER_OVERRUN (exit code 3221225786) and raising
# "OSError: [Errno 22] Invalid argument" during init, and made some connection
# checks fail transiently with ORA-12514 under load. Bound concurrency instead.
_MAX_CONCURRENT_THICK_SUBPROCESSES = int(os.environ.get("MAX_CONCURRENT_ORACLE_CHECKS", "6"))
_thick_subprocess_semaphore = threading.Semaphore(_MAX_CONCURRENT_THICK_SUBPROCESSES)

# Serializes _get_standby_report_via_production() calls per db_id (the
# frontend polls the same db from more than one place). Rather than
# rejecting an overlapping call outright, it now queues behind whichever
# check for that db_id is already running on the shared pooled SSH
# connection, and runs once its turn comes up.
_standby_check_locks = {}
_standby_check_locks_guard = threading.Lock()

def _get_standby_check_lock(db_id):
    with _standby_check_locks_guard:
        lock = _standby_check_locks.get(db_id)
        if lock is None:
            lock = threading.Lock()
            _standby_check_locks[db_id] = lock
        return lock

def _is_host_reachable(host, port, timeout=1.0):
    try:
         s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
         s.settimeout(timeout)
         result = s.connect_ex((host, int(port)))
         s.close()
         return result == 0
    except Exception:
         return False

def _shell_single_quote(value):
    """Escapes an arbitrary string for safe embedding as one single-quoted
    POSIX shell argument (the standard '\\'' technique: close the quote, emit
    an escaped literal quote, reopen the quote). Handles every shell
    metacharacter, including '$', '`', '"', and spaces - not just quotes."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def clear_dr_pools():
    """Closes all Oracle connection pools for standby and reporting databases."""
    global _dr_pools
    with _dr_pools_lock:
        for key, pool in list(_dr_pools.items()):
            try:
                logging.info(f"[Connection Lifecycle] Closing DR pool {key} during cache clear.")
                pool.close()
            except Exception as e:
                logging.error(f"[Connection Lifecycle] Error closing DR pool {key}: {e}")
        _dr_pools.clear()
        logging.info("[DR Service] Cleared all Reporting and Standby connection pools.")

def invalidate_dr_pool(db_id):
    """Closes and deletes connection pools associated with a specific db_id."""
    global _dr_pools
    with _dr_pools_lock:
        for key in list(_dr_pools.keys()):
            if key[0] == db_id:
                pool = _dr_pools.pop(key, None)
                if pool:
                    try:
                        logging.info(f"[Connection Lifecycle] Invalidating/closing DR pool {key}.")
                        pool.close()
                    except Exception as e:
                        logging.error(f"[Connection Lifecycle] Error closing DR pool {key} on invalidation: {e}")
        logging.info(f"[DR Service] Invalidated DR pool for {db_id}")

def get_oracle_connection(cfg_dict, db_type_key):
    """
    Establish a connection to a DB using cached connection pools with the given key prefix (e.g. 'rep_' or 'stby_').
    Uses Oracle Session Pooling for high performance and tcp_connect_timeout to prevent hangs.
    """
    from services.db_service import LoggedOracleConnection
    try:
        db_id = cfg_dict.get("db_id")
        host = cfg_dict.get(f"{db_type_key}host")
        port_raw = cfg_dict.get(f"{db_type_key}port") or 1521
        service_name = cfg_dict.get(f"{db_type_key}service_name")
        username = cfg_dict.get(f"{db_type_key}username")
        password = cfg_dict.get(f"{db_type_key}password")
        
        if not db_id or not host or not username or not password or not service_name:
            return None, "Incomplete connection configuration details"
            
        port = int(port_raw)
        
        # Logging config before creating/acquiring Reporting connection
        if db_type_key == 'rep_':
            env_val = "Unknown"
            if service_name:
                if "aston" in service_name.lower():
                    env_val = "Aston"
                elif "fiat" in service_name.lower():
                    env_val = "Fiat"
            oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
            sysdba_val = "YES" if (username and username.strip().lower() == 'sys') else "NO"
            
            detailed_log = (
                f"\n[Reporting DB Config]\n"
                f"DB ID: {db_id}\n"
                f"Environment: {env_val}\n"
                f"Host: {host}\n"
                f"Port: {port_raw}\n"
                f"Service Name: {service_name}\n"
                f"Username: {username}\n"
                f"Oracle Mode: {oracle_mode}\n"
                f"SYSDBA: {sysdba_val}"
            )
            logging.info(detailed_log)
            print(detailed_log)

        # Fast reachability check
        if not _is_host_reachable(host, port, timeout=2.5):
            return None, f"Host {host} on port {port} is unreachable"
            
        dsn = f"{host}:{port}/{service_name}"

        # If username is SYS/sys, bypass the pool and create a standalone connection
        if username and username.strip().lower() == 'sys':
            oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
            logging.info(f"[Connection Lifecycle] Creating standalone SYSDBA connection for {db_id} ({db_type_key}) (Mode: {oracle_mode}).")
            try:
                conn = oracledb.connect(
                    user=username,
                    password=password,
                    dsn=dsn,
                    mode=oracledb.SYSDBA
                )
                return LoggedOracleConnection(conn, f"{db_id} ({db_type_key})", is_pooled=False), None
            except Exception as connect_err:
                err_msg = str(connect_err)
                from services.db_service import _thick_client_error
                if ("DPY-3001" in err_msg or "python-oracledb thick mode" in err_msg.lower()) and _thick_client_error:
                    err_msg = f"{err_msg} (Thick mode initialization error: {_thick_client_error})"
                return None, err_msg
        
        pool_key = (db_id, db_type_key)
        global _dr_pools
        
        with _dr_pools_lock:
            def make_pool():
                pool_kwargs = {
                    "user": username,
                    "password": password,
                    "dsn": dsn,
                    "min": 1,
                    "max": 5,
                    "increment": 1,
                    "getmode": oracledb.POOL_GETMODE_WAIT,
                    "tcp_connect_timeout": 3.0,
                    "ping_interval": 30
                }
                if username and username.strip().lower() == 'sys':
                    pool_kwargs["mode"] = oracledb.SYSDBA
                logging.info(f"[Connection Lifecycle] Creating DR/Standby Connection Pool for {db_id} ({db_type_key}) with ping_interval: 30s.")
                return oracledb.create_pool(**pool_kwargs)
                
            if pool_key not in _dr_pools:
                oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
                _dr_pools[pool_key] = make_pool()
                
            pool = _dr_pools[pool_key]
            
            try:
                conn = pool.acquire()
                return LoggedOracleConnection(conn, f"{db_id} ({db_type_key})", is_pooled=True), None
            except Exception as acquire_err:
                logging.warning(f"[Connection Lifecycle] DR Service: Pool acquire failed for {pool_key}, attempting to recreate pool: {acquire_err}")
                try:
                    logging.info(f"[Connection Lifecycle] Closing stale DR pool {pool_key}.")
                    pool.close()
                except Exception:
                    pass
                try:
                    oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
                    logging.info(f"[Connection Lifecycle] Recreating DR/Standby Connection Pool for {db_id} ({db_type_key}) in {oracle_mode} mode.")
                    new_pool = make_pool()
                    _dr_pools[pool_key] = new_pool
                    conn = new_pool.acquire()
                    return LoggedOracleConnection(conn, f"{db_id} ({db_type_key})", is_pooled=True), None
                except Exception as retry_err:
                    err_msg = str(retry_err)
                    from services.db_service import _thick_client_error
                    if ("DPY-3001" in err_msg or "python-oracledb thick mode" in err_msg.lower()) and _thick_client_error:
                        err_msg = f"{err_msg} (Thick mode initialization error: {_thick_client_error})"
                    logging.error(f"[Connection Lifecycle] DR Service: Recreated pool acquire failed for {pool_key}: {err_msg}")
                    return None, err_msg
                    
    except Exception as e:
        err_msg = str(e)
        from services.db_service import _thick_client_error
        if ("DPY-3001" in err_msg or "python-oracledb thick mode" in err_msg.lower()) and _thick_client_error:
            err_msg = f"{err_msg} (Thick mode initialization error: {_thick_client_error})"
        return None, err_msg

def get_reporting_status(db_id):
    """
    Check connectivity to the reporting database using an isolated subprocess in Thick Mode.
    Returns: (status, error_detail) where status is 'UP', 'DOWN', or 'Not Configured'.
    error_detail is None unless status is 'DOWN'.
    """
    cfg = get_db_config(db_id)
    if not cfg:
        return "Not Configured", None

    rep_db_id = cfg.get("rep_db_id")
    rep_host = cfg.get("rep_host")
    rep_port = cfg.get("rep_port") or 1521
    if not rep_db_id or not rep_host:
        return "Not Configured", None

    # Logging intent from main process
    logging.info(f"[Background Check] Querying Reporting DB status via subprocess for {db_id} on {rep_host}:{rep_port}...")

    res = _call_thick_subprocess("check_connection", cfg, 'rep_')
    if res.get("status") == "success" and res.get("connected"):
        return "UP", None
    else:
        err = res.get("error_detail", "Unknown connection failure")
        logging.warning(f"DR Service: Reporting DB check failed for {db_id}: {err}")
        return "DOWN", str(err)

def _get_standby_report_via_production(cfg):
    """
    Runs the Standby archive-log report FROM the Production server itself,
    over an SSH session authenticated the same way Mount Points already
    connects to Production (os_user + the shared OCI SSH private key, not
    os_password - see get_ssh_connection_via_key()) - no SSH tunnel/port-forward.

    Production's own SQL*Plus client resolves the Standby TNS alias itself
    (via TNS_ADMIN/tnsnames.ora, built below) and connects:

        sqlplus -L '<stby_username>/<stby_password>@<stby_tns_alias> as sysdba'

    Every one of those values, plus everything used to locate sqlplus itself
    on Production (the environment file name, ORACLE_HOME, TNS_ADMIN), comes
    from this row's own uploaded config fields or is discovered live on the
    server - nothing is hard-coded for one specific database. Specifically:

      - prod_db_name is this row's own Production db_id.
      - The environment file to source is named "<prod_db_name>.env",
        located wherever it actually is on Production rather than assumed to
        exist at a fixed path.
      - If the env file is missing or doesn't yield a working sqlplus,
        ORACLE_HOME falls back to the running instance's own pmon process
        environment, then /etc/oratab, then the known Production Oracle
        home - all keyed off this row's own service_name/db_id (or, for the
        last fallback, the server's own known base path convention).
      - TNS_ADMIN is always the known Production directory convention
        (".../dbhome_1/network/admin/<prod_db_name>") with only the final
        <prod_db_name> component substituted in - never another database's
        literal name.
      - stby_tns_alias is this row's own Standby stby_service_name, resolved
        against that TNS_ADMIN/tnsnames.ora on Production - never hard-coded.

    Output is parsed back into the same {"status", "threads", "dest_status",
    "error_detail"} shape the caller already expects from the old
    subprocess-based implementation, so nothing downstream of this call
    needed to change.
    """
    db_id = cfg.get("db_id")
    stby_db_id = cfg.get("stby_db_id")
    stby_host = cfg.get("stby_host")
    stby_port = cfg.get("stby_port") or 1521
    stby_service_name = cfg.get("stby_service_name")
    stby_username = cfg.get("stby_username")
    stby_password = cfg.get("stby_password")
    prod_service_name = cfg.get("service_name") or db_id

    if not stby_username or not stby_password or not stby_service_name:
        err_msg = "Incomplete Standby connection configuration (username/password/service_name)."
        _log_standby_activity(err_msg, "ERROR")
        return {"status": "error", "error_detail": err_msg}

    lock = _get_standby_check_lock(db_id)
    if lock.locked():
        _log_standby_activity(
            f"Standby check for {db_id} queued - waiting for the in-progress check "
            f"to finish before running this one...",
            "WARNING"
        )
    if not lock.acquire(timeout=90):
        err_msg = (
            f"Timed out waiting for the previous Standby check for {db_id} to finish "
            f"(queued longer than 90s) - not running another one on top of it."
        )
        _log_standby_activity(err_msg, "ERROR")
        return {"status": "error", "error_detail": err_msg}

    try:
        return _run_standby_report_via_production(
            db_id, stby_db_id, stby_host, stby_port,
            stby_service_name, stby_username, stby_password, prod_service_name
        )
    finally:
        lock.release()


def _run_standby_report_via_production(
    db_id, stby_db_id, stby_host, stby_port,
    stby_service_name, stby_username, stby_password, prod_service_name
):
    # The Production database name - always taken from this row's own
    # uploaded configuration (db_id), never hard-coded. Every path/filename
    # derived below (the environment file, TNS_ADMIN) is built from this one
    # value, so a differently-named database uploaded tomorrow needs no code
    # changes here.
    prod_db_name = db_id
    _log_standby_activity(
        f"Production database name (from uploaded configuration, db_id): {prod_db_name}"
    )

    from services.ssh_service import get_ssh_connection_via_key
    _log_standby_activity(
        f"Opening SSH session to Production ({db_id}) to run the Standby ({stby_db_id}) "
        f"TNS connection from there..."
    )
    ssh_client, _, ssh_err = get_ssh_connection_via_key(db_id=db_id, timeout=15, banner_timeout=15)
    if not ssh_client:
        err_msg = f"SSH connection to Production failed: {ssh_err}"
        _log_standby_activity(err_msg, "ERROR")
        return {"status": "error", "error_detail": err_msg}

    # The Standby TNS alias - always taken from this row's own uploaded
    # configuration (stby_service_name), never hard-coded. Production
    # resolves this alias itself via TNS_ADMIN/tnsnames.ora (built below), so
    # no host/port is embedded in the connect string.
    stby_tns_alias = stby_service_name
    _log_standby_activity(
        f"Standby TNS alias (from uploaded configuration, stby_service_name): {stby_tns_alias}"
    )

    connect_str = f"{stby_username}/{stby_password}@{stby_tns_alias} as sysdba"
    safe_connect_str = _shell_single_quote(connect_str)
    _log_standby_activity(
        f"Standby TNS connection (from Production): "
        f"{stby_username}/<redacted>@{stby_tns_alias} as sysdba"
    )

    sql_body = """SET PAGESIZE 0 LINESIZE 32767 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON TAB OFF TERMOUT ON
SET COLSEP '|'
WHENEVER SQLERROR EXIT SQL.SQLCODE
WHENEVER OSERROR EXIT FAILURE
PROMPT ===CONN_CHECK_START===
SELECT 'CONNECTED_OK' FROM DUAL;
PROMPT ===CONN_CHECK_END===
PROMPT ===THREADS_START===
SELECT THREAD# || '|' || NVL(TO_CHAR(MAX(SEQUENCE#)), '') || '|' || NVL(TO_CHAR(MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END)), '') || '|' || NVL(TO_CHAR(MAX(SEQUENCE#) - MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END)), '')
FROM V$ARCHIVED_LOG
WHERE DEST_ID = 1
GROUP BY THREAD#
ORDER BY THREAD#;
PROMPT ===THREADS_END===
PROMPT ===DEST_START===
SELECT s.DEST_ID || '|' || NVL(s.STATUS,'') || '|' || NVL(d.TARGET,'') || '|' || NVL(d.DB_UNIQUE_NAME,'') || '|' || NVL(REPLACE(REPLACE(s.ERROR, CHR(10), ' '), '|', ' '),'')
FROM V$ARCHIVE_DEST_STATUS s
JOIN V$ARCHIVE_DEST d ON d.DEST_ID = s.DEST_ID
WHERE d.TARGET = 'STANDBY';
PROMPT ===DEST_END===
PROMPT ===DESTSTATUS_START===
SELECT DEST_ID || '|' || NVL(DEST_NAME,'') || '|' || NVL(STATUS,'') || '|' || NVL(REPLACE(REPLACE(ERROR, CHR(10), ' '), '|', ' '),'') || '|' || NVL(DB_UNIQUE_NAME,'') || '|' || NVL(SYNCHRONIZATION_STATUS,'')
FROM V$ARCHIVE_DEST_STATUS
WHERE DEST_ID IN (1, 2)
ORDER BY DEST_ID;
PROMPT ===DESTSTATUS_END===
EXIT
"""

    # Nothing below is hard-coded for one specific database: the env file
    # name and the TNS_ADMIN path's final component both come from this row's
    # own prod_db_name (its uploaded db_id) - see the module docstring above.
    env_file_name = f"{prod_db_name}.env"

    # The Production Oracle home's base path is the one thing that stays as
    # the server's own known convention (per requirement: "the base Oracle
    # path may remain as the known server convention, but the final <DB_NAME>
    # component must be dynamic"). TNS_ADMIN is always this base + "/network
    # /admin/" + prod_db_name - never any other database's literal name.
    prod_oracle_home_base = "/u02/app/oracle/product/19.0.0.0/dbhome_1"
    tns_admin_path = f"{prod_oracle_home_base}/network/admin/{prod_db_name}"
    _log_standby_activity(f"Dynamically generated TNS_ADMIN for {prod_db_name}: {tns_admin_path}")

    cmd = f"""
echo "===PROD_DB_NAME={prod_db_name}==="
echo "===ENV_FILE_NAME={env_file_name}==="

ORACLE_HOME=""
ORACLE_HOME_SRC=""

# 1) This row's own "<prod_db_name>.env" on Production, wherever it actually
# lives - located by searching, never assumed to exist or to be at a fixed
# path. Only accepted if sourcing it yields a working sqlplus; otherwise
# falls through to the pmon/oratab/known-home fallback below.
ORACLE_USER_HOME=$(getent passwd oracle 2>/dev/null | cut -d: -f6)
ENV_FILE=$(find $ORACLE_USER_HOME /home /u01 /u02 /etc/oracle -maxdepth 4 -type f -iname "{env_file_name}" 2>/dev/null | head -1)
if [ -n "$ENV_FILE" ]; then
    echo "===ENV_FILE_FOUND=$ENV_FILE==="
    . "$ENV_FILE" >/dev/null 2>&1
    if [ -n "$ORACLE_HOME" ] && [ -x "$ORACLE_HOME/bin/sqlplus" ]; then
        ORACLE_HOME_SRC="sourced $ENV_FILE"
        echo "===ENV_FILE_SOURCE_OK=$ORACLE_HOME==="
    else
        echo "===ENV_FILE_SOURCE_FAILED==="
        ORACLE_HOME=""
    fi
else
    echo "===ENV_FILE_NOT_FOUND==="
fi

# 2) Fallback: locate ORACLE_HOME from the running instance's own pmon
# process, then /etc/oratab, then the known Production Oracle home - all
# keyed off this row's own service_name/db_id, never a literal specific to
# one database.
if [ -z "$ORACLE_HOME" ]; then
    PID=$(pgrep -f -d, "pmon_{prod_service_name}" || pgrep -f -d, "pmon_$(echo {prod_service_name} | tr '[:upper:]' '[:lower:]')" || pgrep -f -d, "pmon_$(echo {prod_service_name} | tr '[:lower:]' '[:upper:]')")
    PID=$(echo $PID | cut -d, -f1)
    if [ -n "$PID" ]; then
        CAND=$(cat /proc/$PID/environ | tr '\\0' '\\n' | grep -a '^ORACLE_HOME=' | cut -d= -f2)
        if [ -n "$CAND" ] && [ -x "$CAND/bin/sqlplus" ]; then
            ORACLE_HOME="$CAND"
            ORACLE_HOME_SRC="pmon_{prod_service_name} process (pid=$PID)"
        fi
    fi
    if [ -z "$ORACLE_HOME" ] && [ -f /etc/oratab ]; then
        CAND=$(grep -a -i "^{prod_service_name}:" /etc/oratab | cut -d: -f2)
        if [ -n "$CAND" ] && [ -x "$CAND/bin/sqlplus" ]; then
            ORACLE_HOME="$CAND"
            ORACLE_HOME_SRC="/etc/oratab entry for {prod_service_name}"
        fi
    fi
    if [ -z "$ORACLE_HOME" ] && [ -x "{prod_oracle_home_base}/bin/sqlplus" ]; then
        ORACLE_HOME="{prod_oracle_home_base}"
        ORACLE_HOME_SRC="known Production Oracle home"
    fi
fi

if [ -n "$ORACLE_HOME" ]; then
    export ORACLE_HOME
    export PATH=$ORACLE_HOME/bin:$PATH
    export LD_LIBRARY_PATH=$ORACLE_HOME/lib:$LD_LIBRARY_PATH
    echo "===ORACLE_HOME_RESOLVED=$ORACLE_HOME ($ORACLE_HOME_SRC)==="
else
    echo "===ORACLE_HOME_NOT_FOUND==="
fi

# 3) TNS_ADMIN is always this row's own Production database name substituted
# into the known Production directory convention - never hard-coded to one
# specific database's name.
TNS_ADMIN="{tns_admin_path}"
export TNS_ADMIN
echo "===TNS_ADMIN_PATH=$TNS_ADMIN==="
if [ -d "$TNS_ADMIN" ]; then
    echo "===TNS_ADMIN_DIR_EXISTS==="
else
    echo "===TNS_ADMIN_DIR_MISSING==="
fi
if [ -f "$TNS_ADMIN/tnsnames.ora" ]; then
    echo "===TNSNAMES_FOUND==="
else
    echo "===TNSNAMES_NOT_FOUND==="
fi

echo "===STANDBY_TNS_ALIAS={stby_tns_alias}==="
timeout -k 5 30 sqlplus -s -L {safe_connect_str} <<'SQLEOF'
{sql_body}SQLEOF
echo "===SQLPLUS_EXIT_CODE=$?==="
"""

    # TEMPORARY DIAGNOSTIC INSTRUMENTATION - split into separate statements
    # (instead of one try wrapping all three calls) so a failure records
    # exactly which step it happened at, plus the exception's real type/repr
    # (str(e) has been observed empty in production for this failure) and the
    # live transport/channel state at the moment of failure. No retry logic;
    # remove once the real root cause is confirmed.
    stdout = None
    stderr = None
    failed_step = None
    try:
        failed_step = "exec_command"
        _, stdout, stderr = ssh_client.exec_command(cmd, timeout=45)
        failed_step = "stdout.read"
        out = stdout.read().decode("utf-8", errors="ignore")
        failed_step = "stderr.read"
        err_out = stderr.read().decode("utf-8", errors="ignore")
    except Exception as e:
        transport = None
        transport_active = None
        try:
            transport = ssh_client.get_transport()
            transport_active = transport.is_active() if transport else None
        except Exception as transport_err:
            transport_active = f"<error checking transport: {transport_err!r}>"

        channel_state = "n/a (exec_command itself did not return a channel)"
        if stdout is not None:
            try:
                chan = stdout.channel
                channel_state = (
                    f"closed={chan.closed}, "
                    f"exit_status_ready={chan.exit_status_ready()}, "
                    f"active={chan.active}, "
                    f"eof_received={getattr(chan, 'eof_received', 'n/a')}"
                )
            except Exception as chan_err:
                channel_state = f"<error reading channel state: {chan_err!r}>"

        diag = (
            f"failed_at_step={failed_step}, "
            f"exception_type={type(e).__name__}, "
            f"exception_repr={e!r}, "
            f"exception_str={str(e)!r}, "
            f"ssh_transport_active={transport_active}, "
            f"channel_state=[{channel_state}]"
        )
        err_msg = f"SSH exec of Standby TNS connection failed: {e}"
        _log_standby_activity(f"{err_msg} | DIAGNOSTIC: {diag}", "ERROR")
        return {"status": "error", "error_detail": err_msg}

    _log_standby_activity(f"Standby sqlplus raw stdout:\n{out}")
    if err_out.strip():
        _log_standby_activity(f"Standby sqlplus stderr:\n{err_out}", "DEBUG")

    import re

    def _extract_marker(pattern):
        m = re.search(pattern, out)
        return m.group(1) if m else None

    exit_code_match = re.search(r"===SQLPLUS_EXIT_CODE=(-?\d+)===", out)
    exit_code = int(exit_code_match.group(1)) if exit_code_match else None

    env_file_found = _extract_marker(r"===ENV_FILE_FOUND=(.+?)===")
    env_file_source_ok = "===ENV_FILE_SOURCE_OK=" in out
    oracle_home_resolved = _extract_marker(r"===ORACLE_HOME_RESOLVED=(.+?)===")
    tns_admin_dir_exists = "===TNS_ADMIN_DIR_EXISTS===" in out
    tnsnames_found = "===TNSNAMES_FOUND===" in out

    _log_standby_activity(
        f"Standby Oracle environment discovery for {db_id} (Production database "
        f"name '{prod_db_name}', from uploaded configuration): "
        f"env_file={env_file_name} "
        f"({('found at ' + env_file_found) if env_file_found else 'not found'}"
        f"{', sourced OK' if (env_file_found and env_file_source_ok) else ''}), "
        f"oracle_home={oracle_home_resolved or 'NOT FOUND'}, "
        f"tns_admin={tns_admin_path} "
        f"(directory {'exists' if tns_admin_dir_exists else 'MISSING'}), "
        f"tnsnames.ora={'found' if tnsnames_found else 'NOT found'}, "
        f"standby_tns_alias={stby_tns_alias} (from uploaded configuration, stby_service_name)"
    )

    required_markers = (
        "===CONN_CHECK_START===", "===CONN_CHECK_END===",
        "===THREADS_START===", "===THREADS_END===",
        "===DEST_START===", "===DEST_END===",
        "===DESTSTATUS_START===", "===DESTSTATUS_END==="
    )
    connected_ok = "CONNECTED_OK" in out

    if exit_code != 0 or not connected_ok or not all(m in out for m in required_markers):
        # standby_unreachable distinguishes "the SSH/Production side of this
        # check worked, sqlplus actually attempted the Standby TNS connection,
        # and THAT is what failed" from every other failure mode here (SSH,
        # missing sqlplus, missing TNS_ADMIN/tnsnames.ora) - those mean the
        # check itself couldn't run, not that the Standby DB is unreachable.
        # Only these two causes are Standby-connection-specific:
        #   - exit_code 124: sqlplus was actually launched against
        #     stby_host:stby_port and never got a response.
        #   - the final "else" below: sqlplus ran, found ORACLE_HOME/TNS_ADMIN/
        #     tnsnames.ora, but the TNS connection itself failed (ORA-/SP2-).
        standby_unreachable = False
        if exit_code == 124:
            # 124 is the standard exit code from the "timeout -k 5 30 sqlplus ..."
            # wrapper when it had to kill sqlplus itself for running too long -
            # i.e. Production started sqlplus, but it never returned (most
            # likely no network path from Production to stby_host:stby_port,
            # or the standby listener/instance isn't responding).
            standby_unreachable = True
            err_msg = (
                f"sqlplus did not return within 30s on Production and was killed by the "
                f"remote 'timeout' wrapper - likely no network path from Production to "
                f"{stby_host}:{stby_port}, or the Standby listener is not responding."
            )
        elif "===ORACLE_HOME_NOT_FOUND===" in out or exit_code == 127:
            # 127 is the shell's "command not found" - neither <env_file_name>,
            # the running pmon process, /etc/oratab, nor the known Production
            # Oracle home yielded a directory with an executable bin/sqlplus,
            # so sqlplus was never on PATH.
            err_msg = (
                f"Could not locate an Oracle sqlplus binary on Production for database "
                f"'{prod_db_name}': {env_file_name} was not found (or didn't provide a "
                f"working ORACLE_HOME), and neither the running pmon process, /etc/oratab, "
                f"nor the known Production Oracle home yielded one either. Verify the Oracle "
                f"environment file exists on Production for this database, or that the "
                f"instance is running and registered in /etc/oratab."
            )
        elif not tns_admin_dir_exists:
            err_msg = (
                f"TNS_ADMIN directory does not exist on Production: {tns_admin_path} "
                f"(derived from Production database name '{prod_db_name}'). Verify the "
                f"Oracle network/admin directory for this database exists at this path."
            )
        elif not tnsnames_found:
            err_msg = (
                f"tnsnames.ora not found under TNS_ADMIN ({tns_admin_path}) on Production. "
                f"Verify the Standby TNS alias '{stby_tns_alias}' is defined there."
            )
        else:
            # sqlplus was actually able to run on Production (ORACLE_HOME,
            # TNS_ADMIN, and tnsnames.ora all resolved OK) and the TNS
            # connection to the Standby itself is what produced this error.
            standby_unreachable = True
            ora_line = next((ln.strip() for ln in out.splitlines() if "ORA-" in ln), None)
            if not connected_ok and ora_line is None:
                ora_line = next((ln.strip() for ln in out.splitlines() if "SP2-" in ln), None)
            err_msg = ora_line or err_out.strip() or f"Standby TNS connection or query failed (exit_code={exit_code})."
        _log_standby_activity(f"Standby report FAILED: {err_msg}", "ERROR")
        return {"status": "error", "error_detail": err_msg, "standby_unreachable": standby_unreachable}

    def _extract_block(text, start_marker, end_marker):
        try:
            block = text.split(start_marker, 1)[1].split(end_marker, 1)[0]
        except IndexError:
            return []
        return [ln.strip() for ln in block.splitlines() if ln.strip()]

    threads = []
    for line in _extract_block(out, "===THREADS_START===", "===THREADS_END==="):
        parts = line.split("|")
        if len(parts) < 4:
            continue
        thread_no, last_received, last_applied, log_gap = parts[0], parts[1], parts[2], parts[3]
        threads.append({
            "thread": int(thread_no) if thread_no.strip().lstrip('-').isdigit() else None,
            "last_received": int(last_received) if last_received.strip().lstrip('-').isdigit() else None,
            "last_applied": int(last_applied) if last_applied.strip().lstrip('-').isdigit() else None,
            "log_gap": int(log_gap) if log_gap.strip().lstrip('-').isdigit() else None
        })

    dest_status = []
    for line in _extract_block(out, "===DEST_START===", "===DEST_END==="):
        parts = line.split("|")
        if len(parts) < 5:
            continue
        dest_status.append({
            "dest_id": parts[0].strip() or None,
            "status": parts[1].strip() or None,
            "target": parts[2].strip() or None,
            "db_unique_name": parts[3].strip() or None,
            "error": parts[4].strip() or None
        })

    destination_status = []
    for line in _extract_block(out, "===DESTSTATUS_START===", "===DESTSTATUS_END==="):
        parts = line.split("|")
        if len(parts) < 6:
            continue
        destination_status.append({
            "dest_id": parts[0].strip() or None,
            "dest_name": parts[1].strip() or None,
            "status": parts[2].strip() or None,
            "error": parts[3].strip() or None,
            "db_unique_name": parts[4].strip() or None,
            "synchronization_status": parts[5].strip() or None
        })

    _log_standby_activity(
        f"Standby report parsed: {len(threads)} thread(s), {len(dest_status)} destination row(s), "
        f"{len(destination_status)} raw V$ARCHIVE_DEST_STATUS row(s)."
    )
    return {
        "status": "success",
        "threads": threads,
        "dest_status": dest_status,
        "destination_status": destination_status
    }


def get_standby_archive_report(db_id):
    """
    Full standby archive-log sync report, per thread, using Thin Mode for the
    local Primary DB and an isolated Thick Mode subprocess for the Standby DB:

      Primary (in-process, Thin Mode):
        SELECT THREAD#, MAX(SEQUENCE#) AS PRIMARY_LAST_GENERATED
        FROM V$ARCHIVED_LOG GROUP BY THREAD# ORDER BY THREAD#

      Standby (isolated Thick subprocess):
        SELECT THREAD#, MAX(SEQUENCE#) AS LAST_RECEIVED,
               MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LAST_APPLIED,
               MAX(SEQUENCE#) - MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LOG_GAP
        FROM V$ARCHIVED_LOG WHERE DEST_ID = 1 GROUP BY THREAD# ORDER BY THREAD#

        SELECT s.DEST_ID, s.STATUS, d.TARGET, d.DB_UNIQUE_NAME, s.ERROR
        FROM V$ARCHIVE_DEST_STATUS s JOIN V$ARCHIVE_DEST d ON d.DEST_ID = s.DEST_ID
        WHERE d.TARGET = 'STANDBY'
        (TARGET/DB_UNIQUE_NAME live on V$ARCHIVE_DEST, not _STATUS)

    A thread's LOG_GAP > 1 is a Warning. Any destination row with a non-empty
    ERROR (or STATUS = 'ERROR') is also surfaced as an error.

    Returns a dict with the per-thread ("threads") and destination-status
    ("dest_status") breakdowns for the dashboard's detailed Standby card, plus
    the same sync_status/archive_gap/error_detail/generated_sequence/
    applied_sequence shape the previous implementation returned (still used
    by the homepage quick-connect card and the DB summary) - here representing
    the worst-gap thread.
    """
    empty_result = {
        "status": "success",
        "sync_status": "Not Configured",
        "generated_sequence": None,
        "applied_sequence": None,
        "archive_gap": None,
        "error_detail": None,
        "threads": [],
        "dest_status": [],
        "destination_status": [],
        "has_warning": False,
        "has_dest_error": False
    }

    cfg = get_db_config(db_id)
    if not cfg:
        return empty_result

    stby_db_id = cfg.get("stby_db_id")
    stby_host = cfg.get("stby_host")
    if not stby_db_id or not stby_host:
        return empty_result

    from services.db_service import get_connection_by_id

    # 1. Primary side - per thread, in-process Thin connection
    prod_conn = None
    try:
        prod_conn = get_connection_by_id(db_id)
    except Exception as pe:
        logging.error(f"DR Service: Failed to get connection for Production DB {db_id}: {pe}")

    if not prod_conn:
        _log_standby_activity(f"Standby report for {db_id}/{stby_db_id} aborted: Production database connection failed.", "ERROR")
        return {
            **empty_result,
            "status": "error",
            "sync_status": "DOWN",
            "error_detail": "Production database connection failed"
        }

    primary_by_thread = {}
    try:
        cursor = prod_conn.cursor()
        cursor.execute("""
            SELECT THREAD#, MAX(SEQUENCE#) AS PRIMARY_LAST_GENERATED
            FROM V$ARCHIVED_LOG
            GROUP BY THREAD#
            ORDER BY THREAD#
        """)
        for row in cursor.fetchall():
            if row[0] is not None:
                primary_by_thread[int(row[0])] = row[1]
        cursor.close()
    except Exception as e:
        logging.error(f"DR Service: Error fetching Production per-thread archive-log sequence for {db_id}: {e}")
        _log_standby_activity(f"Standby report for {db_id}/{stby_db_id} aborted: Production archive-log query failed: {e}", "ERROR")
        return {
            **empty_result,
            "status": "error",
            "sync_status": "DOWN",
            "error_detail": f"Failed to query latest log sequence from Production database: {e}"
        }
    finally:
        try:
            prod_conn.close()
        except Exception:
            pass

    if not primary_by_thread:
        _log_standby_activity(f"Standby report for {db_id}/{stby_db_id} aborted: Production returned no archive-log sequence data.", "ERROR")
        return {
            **empty_result,
            "status": "error",
            "sync_status": "DOWN",
            "error_detail": "Failed to query latest log sequence from Production database"
        }

    _log_standby_activity(f"Production per-thread sequence for {db_id}: {primary_by_thread}")

    # 2. Standby side - isolated Thick Mode subprocess (received/applied/gap
    # per thread, plus archive destination status/error)
    logging.info(f"[Background Check] Querying Standby DB archive-log report via subprocess for {db_id} on {stby_host}...")
    _log_standby_activity(f"Querying Standby DB ({stby_db_id}) archive-log report for {db_id} on {stby_host}...")

    res = _get_standby_report_via_production(cfg)
    if res.get("status") != "success":
        err = res.get("error_detail", "Unknown standby connection failure")
        logging.error(f"DR Service: Failed to connect to Standby DB for {db_id}: {err}")
        _log_standby_activity(f"Standby report for {db_id}/{stby_db_id} FAILED: {err}", "ERROR")
        if res.get("standby_unreachable"):
            # SSH to Production succeeded and the check actually ran the
            # Standby TNS connection - THAT is what failed, so this is the
            # Standby DB itself being unreachable, not an SSH/Production
            # environment problem. Every other failure mode (SSH, missing
            # sqlplus, missing TNS_ADMIN/tnsnames.ora) keeps the "DOWN"
            # classification below unchanged.
            return {
                **empty_result,
                "status": "error",
                "sync_status": "Not Available",
                "error_detail": f"Standby database is unreachable: {err}"
            }
        return {
            **empty_result,
            "status": "error",
            "sync_status": "DOWN",
            "error_detail": f"Standby database connection failed: {err}"
        }

    threads = []
    has_warning = False
    for row in (res.get("threads") or []):
        thread_no = row.get("thread")
        last_received = row.get("last_received")
        last_applied = row.get("last_applied")
        log_gap = row.get("log_gap")
        if log_gap is None and last_received is not None:
            log_gap = last_received - (last_applied or 0)
        warning = bool(log_gap is not None and log_gap > 1)
        has_warning = has_warning or warning
        threads.append({
            "thread": thread_no,
            "primary_last_generated": primary_by_thread.get(thread_no),
            "last_received": last_received,
            "last_applied": last_applied,
            "log_gap": log_gap,
            "warning": warning
        })

    dest_status = res.get("dest_status") or []
    dest_errors = [
        d for d in dest_status
        if str(d.get("error") or "").strip() or str(d.get("status") or "").strip().upper() == "ERROR"
    ]
    has_dest_error = bool(dest_errors)

    # Worst-gap thread stands in for the single-number fields the homepage
    # quick-connect card and DB summary expect.
    worst_thread = max(threads, key=lambda t: (t.get("log_gap") or 0)) if threads else None
    generated_sequence = worst_thread.get("primary_last_generated") if worst_thread else None
    applied_sequence = worst_thread.get("last_applied") if worst_thread else None
    archive_gap = worst_thread.get("log_gap") if worst_thread else None

    error_detail = None
    if has_warning:
        gap_threads = ", ".join(f"THREAD#{t['thread']} (gap={t['log_gap']})" for t in threads if t["warning"])
        error_detail = f"Standby database is lagging behind Primary: {gap_threads}."
    if dest_errors:
        dest_msgs = "; ".join(f"DEST_ID {d.get('dest_id')}: {d.get('error')}" for d in dest_errors if d.get('error'))
        if dest_msgs:
            error_detail = (error_detail + " " if error_detail else "") + f"Archive destination error(s): {dest_msgs}"

    sync_status = "Synced" if not has_warning and not has_dest_error else "Not Synced"
    _log_standby_activity(
        f"Standby report for {db_id}/{stby_db_id} SUCCESS: sync_status={sync_status}, "
        f"threads={len(threads)}, has_warning={has_warning}, has_dest_error={has_dest_error}"
        + (f", detail={error_detail}" if error_detail else "")
    )

    return {
        "status": "success",
        "sync_status": sync_status,
        "generated_sequence": generated_sequence,
        "applied_sequence": applied_sequence,
        "archive_gap": archive_gap,
        "error_detail": error_detail,
        "threads": threads,
        "dest_status": dest_status,
        "destination_status": res.get("destination_status") or [],
        "has_warning": has_warning,
        "has_dest_error": has_dest_error
    }


def get_standalone_status(db_id):
    """
    Check connectivity to the optional Standalone database.
    Returns: (status, error_detail) where status is 'UP', 'DOWN', or 'Not
    Configured'. error_detail is None unless status is 'DOWN'.

    Status is based ONLY on whether a connection to the Standalone database
    can be established (via the shared get_oracle_connection() helper below,
    same as Reporting/Standby) - it does not depend on the archive-log gap
    check performed by _check_standalone_archive_log_gap(), which is purely
    internal monitoring and never affects this UP/DOWN result.
    """
    cfg = get_db_config(db_id)
    if not cfg:
        return "Not Configured", None

    standalone_db_id = cfg.get("standalone_db_id")
    standalone_host = cfg.get("standalone_host")
    if not standalone_db_id or not standalone_host:
        return "Not Configured", None

    conn, err = get_oracle_connection(cfg, 'standalone_')
    if not conn:
        logging.warning(f"DR Service: Standalone DB connection check failed for {db_id}: {err}")
        return "DOWN", str(err)

    try:
        conn.close()
    except Exception:
        pass

    # Best-effort internal log-gap monitoring only - any failure here is
    # logged and swallowed, and never changes the UP status returned above.
    try:
        _check_standalone_archive_log_gap(db_id, cfg)
    except Exception as gap_err:
        logging.warning(f"DR Service: Standalone archive-log gap check failed for {db_id}: {gap_err}")

    return "UP", None


def _check_standalone_archive_log_gap(db_id, cfg):
    """
    Internal-only log-gap monitoring for the Standalone database. Not
    surfaced on the dashboard (which only shows Standalone UP/DOWN) - this
    exists purely so the received/applied gap can be logged per THREAD# for
    internal monitoring, per the Standalone Database spec:

      Production DB:
        SELECT THREAD#, MAX(SEQUENCE#) AS PRIMARY_LAST_GENERATED
        FROM V$ARCHIVED_LOG GROUP BY THREAD# ORDER BY THREAD#

      Standalone DB:
        SELECT THREAD#, MAX(SEQUENCE#) AS LAST_RECEIVED,
               MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LAST_APPLIED,
               MAX(SEQUENCE#) - MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LOG_GAP
        FROM V$ARCHIVED_LOG WHERE DEST_ID = 1 GROUP BY THREAD# ORDER BY THREAD#

    LOG_GAP <= 1 -> Healthy, LOG_GAP > 1 -> Warning (logged per thread).
    """
    from services.db_service import get_connection_by_id

    prod_conn = None
    try:
        prod_conn = get_connection_by_id(db_id)
        if not prod_conn:
            logging.warning(f"DR Service: Could not open Production connection for Standalone archive-log gap check ({db_id}).")
            return

        cursor = prod_conn.cursor()
        cursor.execute("""
            SELECT THREAD#, MAX(SEQUENCE#) AS PRIMARY_LAST_GENERATED
            FROM V$ARCHIVED_LOG
            GROUP BY THREAD#
            ORDER BY THREAD#
        """)
        primary_last_generated_by_thread = {
            int(row[0]): row[1] for row in cursor.fetchall() if row[0] is not None
        }
        cursor.close()
    except Exception as e:
        logging.warning(f"DR Service: Production archive-log query failed for {db_id}: {e}")
        return
    finally:
        if prod_conn:
            try:
                prod_conn.close()
            except Exception:
                pass

    standalone_conn, conn_err = get_oracle_connection(cfg, 'standalone_')
    if not standalone_conn:
        logging.warning(f"DR Service: Standalone archive-log query skipped for {db_id}: {conn_err}")
        return

    try:
        cursor = standalone_conn.cursor()
        cursor.execute("""
            SELECT THREAD#,
                   MAX(SEQUENCE#) AS LAST_RECEIVED,
                   MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LAST_APPLIED,
                   MAX(SEQUENCE#) - MAX(CASE WHEN APPLIED = 'YES' THEN SEQUENCE# END) AS LOG_GAP
            FROM V$ARCHIVED_LOG
            WHERE DEST_ID = 1
            GROUP BY THREAD#
            ORDER BY THREAD#
        """)
        for row in cursor.fetchall():
            thread_no = int(row[0]) if row[0] is not None else None
            last_received = row[1]
            last_applied = row[2]
            log_gap = row[3]
            if log_gap is None and last_received is not None:
                log_gap = last_received - (last_applied or 0)

            condition = "Warning" if (log_gap or 0) > 1 else "Healthy"
            primary_last_generated = primary_last_generated_by_thread.get(thread_no)

            log_line = (
                f"[Standalone Archive Log Gap] db_id={db_id} THREAD#={thread_no} "
                f"PRIMARY_LAST_GENERATED={primary_last_generated} LAST_RECEIVED={last_received} "
                f"LAST_APPLIED={last_applied} LOG_GAP={log_gap} CONDITION={condition}"
            )
            if condition == "Warning":
                logging.warning(log_line)
            else:
                logging.info(log_line)
        cursor.close()
    except Exception as e:
        logging.warning(f"DR Service: Standalone archive-log query failed for {db_id}: {e}")
    finally:
        try:
            standalone_conn.close()
        except Exception:
            pass


def _call_thick_subprocess(action, db_cfg, db_type_key):
    """Spawns an isolated helper instance of the application in Thick Mode to run SQL queries."""
    import subprocess
    import sys
    import json
    import logging
    import os
    import time
    import uuid

    request_id = uuid.uuid4().hex[:8]
    prod_db_id = db_cfg.get('db_id', '?')
    target_db_id = db_cfg.get(f"{db_type_key}db_id", prod_db_id)
    tag = f"[req={request_id}] [pid={os.getpid()}]"
    start_time = time.time()

    if getattr(sys, 'frozen', False):
        # Frozen EXE environment: run the EXE directly with target CLI flag
        cmd = [sys.executable, "--thick-subprocess"]
    else:
        # Development Python mode: run interpreter pointing to app.py in project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_script = os.path.join(project_root, "app.py")
        cmd = [sys.executable, main_script, "--thick-subprocess"]

    input_payload = {
        "action": action,
        "db_cfg": db_cfg,
        "db_type_key": db_type_key,
        "request_id": request_id
    }

    # This helper is only ever invoked for Reporting (db_type_key='rep_') now -
    # Standby runs through _get_standby_report_via_production() instead, which
    # logs to its own logs/standby_db.log. Reporting keeps logging to
    # reporting_db.log exactly as it always has.
    log_fn = _log_background_activity

    log_fn(
        f"{tag} Invocation started for db_id={prod_db_id} (target={target_db_id}), action={action}"
    )

    # Bound how many of these Oracle thick-mode subprocesses run at once so
    # they don't all race to load the same Instant Client DLLs simultaneously
    # (see _thick_subprocess_semaphore comment above).
    with _thick_subprocess_semaphore:
        try:
            # Prevent CMD popup window from flashing on Windows when frozen
            startupinfo = None
            creationflags = 0
            if sys.platform == 'win32':
                # Put the child in its own process group so it's detached from the
                # parent's console. Without this, a Ctrl+C/Ctrl+Break or console-close
                # event delivered to the parent's console is broadcast to every
                # process still attached to that console - including this child -
                # and since the child installs no handler for it, Python raises
                # KeyboardInterrupt in its main thread wherever it happens to be
                # executing (observed landing inside oracledb.init_oracle_client()).
                # That is an external-interrupt crash, not an Oracle Client/DLL fault.
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
                if getattr(sys, 'frozen', False):
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                startupinfo=startupinfo,
                creationflags=creationflags
            )
            log_fn(f"{tag} Child subprocess started (child_pid={process.pid})")

            stdout, stderr = process.communicate(input=json.dumps(input_payload), timeout=25.0)
            elapsed_ms = int((time.time() - start_time) * 1000)

            if process.returncode != 0:
                # Distinguish an external interrupt (KeyboardInterrupt reaching the
                # child - a process-isolation/signal issue) from a real crash inside
                # the subprocess's own logic (an actual Oracle Client/init failure),
                # since both produce a non-zero exit code and a stderr traceback.
                was_interrupted = "KeyboardInterrupt" in stderr
                interrupt_note = " [external interrupt/signal - NOT an Oracle Client or DLL failure]" if was_interrupted else ""
                err_msg = f"Subprocess (child_pid={process.pid}) exited with code {process.returncode}{interrupt_note}. Stderr: {stderr.strip()}"
                logging.error(f"[Thick Subprocess Error] {err_msg}")
                log_fn(
                    f"{tag} {err_msg} (elapsed={elapsed_ms}ms, interrupted={was_interrupted})",
                    "ERROR"
                )
                return {"status": "error", "error_detail": err_msg, "interrupted": was_interrupted}

            # Parse stdout line-by-line to extract the returned JSON payload (ignores any extra print outs)
            json_line = None
            for line in stdout.splitlines():
                line_s = line.strip()
                if line_s.startswith("{") and line_s.endswith("}"):
                    json_line = line_s
                    break

            if not json_line:
                err_msg = f"Malformed output (no JSON found). Output: {stdout.strip()}. Stderr: {stderr.strip()}"
                logging.error(f"[Thick Subprocess Error] {err_msg}")
                log_fn(f"{tag} {err_msg} (elapsed={elapsed_ms}ms)", "ERROR")
                return {"status": "error", "error_detail": err_msg}

            log_fn(f"{tag} Invocation completed successfully (elapsed={elapsed_ms}ms)")
            return json.loads(json_line)

        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except:
                pass
            elapsed_ms = int((time.time() - start_time) * 1000)
            err_msg = "Database connection query check timed out (25s limit)"
            logging.error(f"[Thick Subprocess Timeout] {err_msg}")
            log_fn(f"{tag} {err_msg} (elapsed={elapsed_ms}ms)", "ERROR")
            return {"status": "error", "error_detail": err_msg}
        except Exception as ex:
            elapsed_ms = int((time.time() - start_time) * 1000)
            err_msg = f"Subprocess invocation failure: {ex}"
            logging.error(f"[Thick Subprocess Call Load Exception] {err_msg}")
            log_fn(f"{tag} {err_msg} (elapsed={elapsed_ms}ms)", "ERROR")
            return {"status": "error", "error_detail": err_msg}


def _log_background_activity(message, level="INFO", log_file_name="reporting_db.log"):
    """Appends a detailed activity log entry to logs/<log_file_name> in background.
    Defaults to reporting_db.log (unchanged behavior for every existing caller -
    Reporting's log file/content stays exactly as it always was)."""
    import sys
    import os
    import datetime

    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        # Project workspace root
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    log_dir = os.path.join(base_dir, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, log_file_name)
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        log_line = f"[{timestamp}] {level}: {message}\n"
        # Each call may run in its own OS subprocess (see _call_thick_subprocess),
        # so a threading.Lock alone can't prevent interleaved/torn writes between
        # processes. Use an OS-level file lock (msvcrt on Windows) to serialize
        # writes across processes as well as threads within one process.
        with _log_activity_lock:
            with open(log_file, 'a', encoding='utf-8') as f:
                try:
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        f.seek(0, os.SEEK_END)
                        f.write(log_line)
                        f.flush()
                    finally:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except ImportError:
                    # Non-Windows fallback: no cross-process lock available here.
                    f.write(log_line)
    except Exception as ex:
        sys.stderr.write(f"Failed to log background OCI activity to file: {ex}\n")


def _log_standby_activity(message, level="INFO"):
    """Appends a detailed activity log entry to logs/standby_db.log - a
    dedicated log file for the Standby database's own lifecycle (SSH session
    to Production, the SQL*Plus TNS connection run there, queries,
    success/failure - no tunnel/port-forward involved), kept separate from
    reporting_db.log so Standby's log is self-contained and doesn't intermix
    with Reporting's entries.

    Also echoes the same line to stdout (visible in the console window when
    running as the built EXE) so the Production SSH/TNS and Standby
    connection details can be watched live without opening the log file."""
    try:
        print(f"[Standby] {level}: {message}", flush=True)
    except Exception:
        pass
    _log_background_activity(message, level, log_file_name="standby_db.log")


def run_thick_subprocess():
    """CLI Entry point for the isolated subprocess to run Oracle Thick queries and print JSON."""
    import sys
    import os
    import json
    import time
    import uuid
    import oracledb
    import traceback

    process_start = time.time()

    def elapsed_ms():
        return int((time.time() - process_start) * 1000)

    # 1. Read input payload from stdin
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        err_msg = f"Failed to parse stdin payload: {e}"
        _log_background_activity(err_msg, "ERROR")
        sys.stdout.write(json.dumps({"status": "error", "error_detail": err_msg}) + "\n")
        sys.stdout.flush()
        sys.exit(1)

    action = input_data.get("action")
    db_cfg = input_data.get("db_cfg") or {}
    db_type_key = input_data.get("db_type_key") or "rep_"
    request_id = input_data.get("request_id") or uuid.uuid4().hex[:8]

    # Extract config parameters
    host = db_cfg.get(f"{db_type_key}host")
    port_raw = db_cfg.get(f"{db_type_key}port") or 1521
    port = int(port_raw)
    service = db_cfg.get(f"{db_type_key}service_name")
    user = db_cfg.get(f"{db_type_key}username")
    password = db_cfg.get(f"{db_type_key}password")
    prod_db_id = db_cfg.get('db_id', '?')
    target_db_id = db_cfg.get(f"{db_type_key}db_id", prod_db_id)

    tag = f"[req={request_id}] [pid={os.getpid()}]"

    # This subprocess entry point now only ever runs for Reporting
    # (action="check_connection", db_type_key='rep_') - Standby runs via
    # _get_standby_report_via_production() instead (SSH exec + SQL*Plus from
    # Production, no subprocess, no tunnel). Reporting logs to
    # reporting_db.log exactly as it always has.
    log_fn = _log_background_activity

    log_fn("=========================================================================")
    log_fn(f"{tag} Subprocess started for db_id={prod_db_id} (target={target_db_id}), action={action}")
    log_fn(
        f"{tag} Connection parameters: db_id={prod_db_id}, target_db_id={target_db_id}, "
        f"host={host}, port={port}, service_name={service}, username={user}, password={password}"
    )

    from services.db_service import init_thick_client, _thick_client_error
    try:
        log_fn(f"{tag} Establishing Oracle Client (Thick mode)...")
        init_thick_client()
        log_fn(f"{tag} Oracle Client Thick mode ready. (elapsed={elapsed_ms()}ms)")
    except Exception as init_err:
        err_msg = f"Thick Mode initialization failed: {init_err} (details: {_thick_client_error})"
        log_fn(f"{tag} {err_msg} (elapsed={elapsed_ms()}ms)", "ERROR")
        log_fn(f"{tag} {traceback.format_exc()}", "ERROR")
        sys.stdout.write(json.dumps({"status": "error", "error_detail": err_msg}) + "\n")
        sys.stdout.flush()
        log_fn(f"{tag} Process completed with error. (total_elapsed={elapsed_ms()}ms)\n")
        sys.exit(0)

    dsn = f"{host}:{port}/{service}"

    if action == "check_connection":
        try:
            mode_label = "SYSDBA" if user and user.strip().lower() == 'sys' else "normal"
            log_fn(f"{tag} Establishing connection to reporting DB at {dsn} as user {user} (mode={mode_label})...")
            conn_kwargs = {
                "user": user,
                "password": password,
                "dsn": dsn
            }
            if user and user.strip().lower() == 'sys':
                conn_kwargs["mode"] = oracledb.SYSDBA

            connect_start = time.time()
            conn = oracledb.connect(**conn_kwargs)
            connect_ms = int((time.time() - connect_start) * 1000)
            log_fn(f"{tag} Connection established successfully to {dsn}. (connect_time={connect_ms}ms, elapsed={elapsed_ms()}ms)")
            conn.close()
            log_fn(f"{tag} Connection closed cleanly.")

            sys.stdout.write(json.dumps({"status": "success", "connected": True}) + "\n")
            sys.stdout.flush()
        except Exception as conn_err:
            err_msg = str(conn_err)
            log_fn(f"{tag} Connection failed to reporting DB at {dsn}. Error: {err_msg} (elapsed={elapsed_ms()}ms)", "ERROR")
            log_fn(f"{tag} {traceback.format_exc()}", "DEBUG")
            sys.stdout.write(json.dumps({"status": "error", "error_detail": err_msg}) + "\n")
            sys.stdout.flush()

    else:
        err_msg = f"Unknown action requested: {action}"
        log_fn(f"{tag} {err_msg}", "WARNING")
        sys.stdout.write(json.dumps({"status": "error", "error_detail": err_msg}) + "\n")
        sys.stdout.flush()

    log_fn(f"{tag} Process completed successfully. (total_elapsed={elapsed_ms()}ms)\n")
    sys.exit(0)
