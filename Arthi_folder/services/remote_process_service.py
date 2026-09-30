import paramiko
import logging
import threading
from flask import session
from services.config_service import get_db_config, get_all_db_configs
from services.ssh_service import get_ssh_connection_via_key

def _parse_ps_output(raw: str, sid: str = None) -> list:
    """
    Parse: ps -eo pid,user,rss,cputimes,vsz,comm,args --no-headers
    Columns: PID USER RSS(KB) CPUTIME(s) VSZ(KB) COMM ARGS...

    When `sid` is given, only Oracle shadow processes belonging to that
    specific instance are kept, matched via the SID embedded in the
    process image name (e.g. "oraclefa192"). This matters because a
    single host commonly runs several Oracle instances/databases, and
    `ps` has no built-in concept of "which database" a process belongs
    to - without this filter, every database on a shared host would see
    every other database's processes too.
    """
    import os
    processes = []
    sid_lower = str(sid).strip().lower() if sid else None
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 6:
            continue
        try:
            pid_str     = parts[0]
            user_str    = parts[1]
            rss_kb      = parts[2]
            cputime_str = parts[3]
            vsz_kb      = parts[4]
            comm_str    = parts[5]
            args_str    = parts[6] if len(parts) > 6 else comm_str

            vsz_mb = round(int(vsz_kb) / 1024, 1) if vsz_kb.isdigit() else 0.0
            memory_mb = round(int(rss_kb) / 1024, 1) if rss_kb.isdigit() else 0.0
            try:
                cpu_seconds = round(float(cputime_str), 1)
            except ValueError:
                cpu_seconds = 0.0

            # 1. We only want processes run by the OS user 'oracle'
            if user_str.lower() != 'oracle':
                continue

            # 2. Extract basename of the first word in args_str to determine binary name
            first_arg = args_str.split()[0] if args_str.strip() else ""
            binary_name = os.path.basename(first_arg).lower()

            # 3. Exclude background processes (starting with 'ora_')
            if binary_name.startswith('ora_'):
                continue

            # 4. Include only Oracle user shadow/server processes (starting with 'oracle')
            if not binary_name.startswith('oracle'):
                continue

            # 5. Scope to the requested database instance only
            if sid_lower and not binary_name.startswith(f"oracle{sid_lower}"):
                continue

            process_name = args_str.strip() if args_str else comm_str

            processes.append({
                "pid":         pid_str,
                "user":        user_str,
                "username":    user_str,
                "process":     process_name,
                "name":        process_name,
                "args":        args_str,
                "cpu_seconds": cpu_seconds,
                "memory_mb":   memory_mb,
                "vsz_mb":      vsz_mb,
            })
        except (ValueError, IndexError):
            continue
    return processes

def fetch_processes_from_oracle_db(db_id):
    conn = None
    cursor = None
    try:
        from services.db_service import get_connection_by_id
        conn = get_connection_by_id(db_id)
        if not conn:
            return None

        cursor = conn.cursor()
        
        # 1. Fetch top CPU processes (using subquery to avoid v$sesstat duplicate rows and FETCH FIRST for correct sorting)
        cpu_query = """
            SELECT p.spid        AS pid,
                   NVL(s.username, 'oracle') AS usr,
                   NVL(s.program, 'oracle') AS prog,
                   ROUND(NVL((SELECT ss.value FROM v$sesstat ss JOIN v$statname sn ON sn.statistic# = ss.statistic# WHERE ss.sid = s.sid AND sn.name = 'CPU used by this session'), 0) / 100, 2)  AS cpu_sec,
                   ROUND(p.pga_used_mem / 1024 / 1024, 1) AS mem_mb,
                   s.status
            FROM v$process  p
            JOIN v$session  s  ON s.paddr = p.addr
            WHERE s.type      = 'USER'
              AND p.spid     IS NOT NULL
              AND s.username IS NOT NULL
              AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
            ORDER BY cpu_sec DESC
            FETCH FIRST 15 ROWS ONLY
        """
        cursor.execute(cpu_query)
        cpu_rows = cursor.fetchall()

        # 2. Fetch top Memory processes (PGA memory)
        mem_query = """
            SELECT p.spid        AS pid,
                   NVL(s.username, 'oracle') AS usr,
                   NVL(s.program, 'oracle') AS prog,
                   ROUND(NVL((SELECT ss.value FROM v$sesstat ss JOIN v$statname sn ON sn.statistic# = ss.statistic# WHERE ss.sid = s.sid AND sn.name = 'CPU used by this session'), 0) / 100, 2)  AS cpu_sec,
                   ROUND(p.pga_used_mem / 1024 / 1024, 1) AS mem_mb,
                   s.status
            FROM v$process p
            JOIN v$session s ON s.paddr = p.addr
            WHERE s.type      = 'USER'
              AND p.spid     IS NOT NULL
              AND s.username IS NOT NULL
              AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
            ORDER BY p.pga_used_mem DESC
            FETCH FIRST 15 ROWS ONLY
        """
        cursor.execute(mem_query)
        mem_rows = cursor.fetchall()

        cpu_procs = []
        for r in cpu_rows:
            try: pid = int(r[0])
            except: pid = r[0]
            cpu_procs.append({
                "pid": pid,
                "user": str(r[1]),
                "username": str(r[1]),
                "process": str(r[2]),
                "name": str(r[2]),
                "cpu_seconds": float(r[3] or 0.0),
                "memory_mb": float(r[4] or 0.0)
            })

        mem_procs = []
        for r in mem_rows:
            try: pid = int(r[0])
            except: pid = r[0]
            mem_procs.append({
                "pid": pid,
                "user": str(r[1]),
                "username": str(r[1]),
                "process": str(r[2]),
                "name": str(r[2]),
                "cpu_seconds": float(r[3] or 0.0),
                "memory_mb": float(r[4] or 0.0)
            })

        return {
            "status": "success",
            "cpu_processes": cpu_procs,
            "memory_processes": mem_procs
        }
    except Exception as e:
        logging.error(f"Oracle DB process fallback failed for {db_id}: {e}")
        return None
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass

_instance_name_cache = {}
_instance_name_cache_lock = threading.Lock()

def _get_instance_name(db_id):
    """
    Returns the live Oracle instance_name for db_id - this is the value
    Oracle itself embeds in its OS process names (oracle<SID> (LOCAL=NO)),
    since instance_name is always equal to the ORACLE_SID the instance was
    started with. This is deliberately NOT service_name/db_id - a service
    name is an independent, DBA-configurable TNS alias that is not
    guaranteed to match the SID (see the fa191t investigation).

    Cached per db_id after the first successful lookup so v$instance isn't
    queried on every process-list poll. Returns None (never a fallback to
    service_name/db_id) if the live lookup fails - callers must treat that
    as "can't safely SID-match OS processes right now", not substitute
    anything else.
    """
    with _instance_name_cache_lock:
        cached = _instance_name_cache.get(db_id)
    if cached:
        return cached

    conn = None
    cursor = None
    try:
        from services.db_service import get_connection_by_id
        conn = get_connection_by_id(db_id)
        if not conn:
            logging.error(f"[Instance Name] Cannot resolve Oracle SID for db_id={db_id}: no database connection available.")
            return None
        cursor = conn.cursor()
        cursor.execute("SELECT instance_name FROM v$instance")
        row = cursor.fetchone()
        instance_name = str(row[0]).strip() if row and row[0] else None
        if not instance_name:
            logging.error(f"[Instance Name] v$instance returned no instance_name for db_id={db_id}.")
            return None
        with _instance_name_cache_lock:
            _instance_name_cache[db_id] = instance_name
        logging.info(f"[Instance Name] Resolved SID '{instance_name}' for db_id={db_id} via v$instance.")
        return instance_name
    except Exception as e:
        logging.error(f"[Instance Name] Failed to query v$instance for db_id={db_id}: {e}")
        return None
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass

import time

_ALL_PROCESSES_CACHE = {
    "timestamp": 0,
    "data": None
}

_SINGLE_DB_PROCESS_CACHE = {}

def is_standby_or_reporting_db_id(db_id, configs):
    if not db_id:
        return False
    db_id_str = str(db_id).strip()
    db_id_lower = db_id_str.lower()
    
    # Check heuristic matching on DB name
    if any(k in db_id_lower for k in ("stby", "standby", "rep", "reporting")):
        return True

    # Check suffix matching: reporting SIDs end with 'r' and standby SIDs end with 'dr' in Aston/Fiat templates
    if db_id_lower.endswith('dr') or db_id_lower.endswith('r'):
        return True
        
    # Check if registered as standby/reporting db_id in any config row or if its service name indicates DR/Rep
    for cfg in configs:
        is_self_row = cfg.get("db_id") == db_id_str or (cfg.get("db_id") and cfg.get("db_id").lower() == db_id_lower)

        # A database's own rep_db_id/stby_db_id fields describe ITS secondary
        # databases - never compare them against its own db_id, or a row whose
        # test/placeholder secondary id happens to equal its own db_id would
        # falsely mark itself as standby/reporting.
        if not is_self_row:
            rep_db = str(cfg.get("rep_db_id") or "").strip()
            stby_db = str(cfg.get("stby_db_id") or "").strip()
            if db_id_str == rep_db or db_id_str == stby_db:
                return True
            if rep_db and db_id_lower == rep_db.lower():
                return True
            if stby_db and db_id_lower == stby_db.lower():
                return True

        if is_self_row:
            service_name = str(cfg.get("service_name") or "").lower()
            if "aston.csi.waynepa" in service_name or "fiat.csi.waynepa" in service_name:
                return True
            if any(k in service_name for k in ("stby", "standby", "rep", "reporting")):
                return True

    return False

def get_remote_server_processes_single_raw(db_id):
    db_cfg = get_db_config(db_id)
    if db_cfg:
        configs = get_all_db_configs()
        if is_standby_or_reporting_db_id(db_id, configs):
            host = db_cfg.get("host")
            logging.info(f"Skipping server process details for Standby or Reporting database: {db_id}")
            single_res = {
                "status": "success",
                "db_name": db_cfg.get("service_name") or host or "unknown",
                "server": host or "unknown",
                "host": host or "unknown",
                "message": "Process monitoring is disabled for Standby/Reporting databases.",
                "cpu_processes": [],
                "memory_processes": []
            }
            res = {
                "status": "success",
                "databases": {
                    db_id: single_res
                }
            }
            return res
    if not db_cfg:
        return {
            "status": "error",
            "message": f"Database config not found for {db_id}",
            "databases": {}
        }

    host = db_cfg.get("host")
    db_name = db_cfg.get("service_name") or host or "unknown"
    
    if not host:
        single_res = {
            "status": "error",
            "db_name": db_name,
            "server": "unknown",
            "host": "unknown",
            "message": "Database host IP is missing in configuration.",
            "cpu_processes": [],
            "memory_processes": []
        }
    else:
        instance_name = _get_instance_name(db_id)
        if instance_name:
            ssh_client, _, ssh_err = get_ssh_connection_via_key(db_id=db_id, timeout=10, banner_timeout=10)
        else:
            # Can't safely SID-match OS processes without a confirmed instance
            # name - service_name/db_id are not reliable substitutes (see the
            # fa191t investigation). Skip the ps-based path entirely and drop
            # straight to the v$session-based DB fallback below, which is
            # already correctly scoped to this db_id's own connection and
            # doesn't need a SID string at all.
            logging.error(f"[Server Processes] db_id={db_id}: could not resolve Oracle instance name (SID) via v$instance - skipping OS process name matching, using DB-session fallback only.")
            ssh_client, ssh_err = None, None
        if ssh_client:
            try:
                cmd = "ps -eo pid,user,rss,cputimes,vsz,comm,args --no-headers --sort=-cputimes 2>/dev/null | head -200"
                stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=10)
                raw = stdout.read().decode('utf-8', errors='ignore')
                err_out = stderr.read().decode('utf-8', errors='ignore')
                ssh_client.close()

                procs = _parse_ps_output(raw, sid=instance_name)
                if procs:
                    cpu_procs = sorted(procs, key=lambda x: x['cpu_seconds'], reverse=True)[:15]
                    mem_procs = sorted(procs, key=lambda x: x['memory_mb'], reverse=True)[:15]
                    single_res = {
                        "status": "success",
                        "db_name": db_name,
                        "server": db_cfg.get('service_name') or host or "kasorcl",
                        "host": host,
                        "cpu_processes": cpu_procs,
                        "memory_processes": mem_procs
                    }
                else:
                    db_fallback = fetch_processes_from_oracle_db(db_id)
                    if db_fallback:
                        single_res = {
                            "status": "success",
                            "db_name": db_name,
                            "server": db_cfg.get('service_name') or host or "kasorcl",
                            "host": host,
                            "cpu_processes": db_fallback.get("cpu_processes", []),
                            "memory_processes": db_fallback.get("memory_processes", [])
                        }
                    else:
                        single_res = {
                            "status": "error",
                            "db_name": db_name,
                            "server": db_cfg.get('service_name') or host or "kasorcl",
                            "host": host,
                            "message": "Failed to parse SSH command output and DB fallback failed.",
                            "cpu_processes": [],
                            "memory_processes": []
                        }
            except Exception as e:
                db_fallback = fetch_processes_from_oracle_db(db_id)
                if db_fallback:
                    single_res = {
                        "status": "success",
                        "db_name": db_name,
                        "server": host,
                        "host": host,
                        "cpu_processes": db_fallback.get("cpu_processes", []),
                        "memory_processes": db_fallback.get("memory_processes", [])
                    }
                else:
                    single_res = {
                        "status": "error",
                        "db_name": db_name,
                        "server": host,
                        "host": host,
                        "message": f"SSH command execution error: {str(e)}",
                        "cpu_processes": [],
                        "memory_processes": []
                    }
        else:
            db_fallback = fetch_processes_from_oracle_db(db_id)
            if db_fallback:
                single_res = {
                    "status": "success",
                    "db_name": db_name,
                    "server": host,
                    "host": host,
                    "cpu_processes": db_fallback.get("cpu_processes", []),
                    "memory_processes": db_fallback.get("memory_processes", [])
                }
            else:
                if instance_name:
                    from services.mountpoint_service import map_ssh_error
                    message = map_ssh_error(ssh_err)
                else:
                    message = "Unable to resolve Oracle instance name (SID) via v$instance, and no session data available from v$session either."
                single_res = {
                    "status": "error",
                    "db_name": db_name,
                    "server": host,
                    "host": host,
                    "message": message,
                    "cpu_processes": [],
                    "memory_processes": []
                }

    res = {
        "status": "success",
        "databases": {
            db_id: single_res
        }
    }
    return res

def get_remote_server_processes_all_raw():
    configs = get_all_db_configs()
    # Filter out standby and reporting configurations
    configs = [c for c in configs if not is_standby_or_reporting_db_id(c.get("db_id"), configs)]
    
    # Map to track unique hosts/servers to avoid duplicate SSH connections
    host_map = {}
    for cfg in configs:
        host = cfg.get("host")
        if not host:
            continue
        if host not in host_map:
            host_map[host] = []
        host_map[host].append(cfg)

    # Dictionary to keep active SSH connections we create in this call
    host_connections = {}
    
    # Dictionary to store process results for each db_id
    databases_processes = {}

    for host, db_cfgs in host_map.items():
        # First, find a configuration on this host that has an OS username set
        # (key-based auth needs os_user to target the right account - os_password
        # is not used by get_ssh_connection_via_key)
        valid_cfg = None
        for cfg in db_cfgs:
            if cfg.get("os_user"):
                valid_cfg = cfg
                break

        # If no config has credentials, we can still use the first config for SSH attempt
        if not valid_cfg:
            valid_cfg = db_cfgs[0]

        target_db_id = valid_cfg.get("db_id")

        ssh_client, db_cfg, ssh_err = get_ssh_connection_via_key(db_id=target_db_id, timeout=10, banner_timeout=10)
        host_connections[host] = (ssh_client, ssh_err)

    # Now assign results or fallbacks for each DB config
    for cfg in configs:
        db_id = cfg.get("db_id")
        host = cfg.get("host")
        db_name = cfg.get("service_name") or host or "unknown"
        
        if not host:
            databases_processes[db_id] = {
                "status": "error",
                "db_name": db_name,
                "server": host or "unknown",
                "host": host or "unknown",
                "message": "Database host IP is missing in configuration.",
                "cpu_processes": [],
                "memory_processes": []
            }
            continue

        ssh_client, ssh_err = host_connections.get(host, (None, "SSH Connection failed"))
        sid_for_match = cfg.get("service_name") or db_id

        if ssh_client:
            try:
                # Execute the existing server process command
                cmd = "ps -eo pid,user,rss,cputimes,vsz,comm,args --no-headers --sort=-cputimes 2>/dev/null | head -200"
                stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=10)
                raw = stdout.read().decode('utf-8', errors='ignore')
                err_out = stderr.read().decode('utf-8', errors='ignore')

                procs = _parse_ps_output(raw, sid=sid_for_match)
                if procs:
                    cpu_procs = sorted(procs, key=lambda x: x['cpu_seconds'], reverse=True)[:15]
                    mem_procs = sorted(procs, key=lambda x: x['memory_mb'], reverse=True)[:15]
                    databases_processes[db_id] = {
                        "status": "success",
                        "db_name": db_name,
                        "server": cfg.get('service_name') or host or "kasorcl",
                        "host": host,
                        "cpu_processes": cpu_procs,
                        "memory_processes": mem_procs
                    }
                else:
                    # Let's try DB fallback for this specific database
                    db_fallback = fetch_processes_from_oracle_db(db_id)
                    if db_fallback:
                        databases_processes[db_id] = {
                            "status": "success",
                            "db_name": db_name,
                            "server": cfg.get('service_name') or host or "kasorcl",
                            "host": host,
                            "cpu_processes": db_fallback.get("cpu_processes", []),
                            "memory_processes": db_fallback.get("memory_processes", [])
                        }
                    else:
                        databases_processes[db_id] = {
                            "status": "error",
                            "db_name": db_name,
                            "server": cfg.get('service_name') or host or "kasorcl",
                            "host": host,
                            "message": "Failed to parse SSH command output and DB fallback failed.",
                            "cpu_processes": [],
                            "memory_processes": []
                        }
            except Exception as e:
                db_fallback = fetch_processes_from_oracle_db(db_id)
                if db_fallback:
                    databases_processes[db_id] = {
                        "status": "success",
                        "db_name": db_name,
                        "server": host,
                        "host": host,
                        "cpu_processes": db_fallback.get("cpu_processes", []),
                        "memory_processes": db_fallback.get("memory_processes", [])
                    }
                else:
                    databases_processes[db_id] = {
                        "status": "error",
                        "db_name": db_name,
                        "server": host,
                        "host": host,
                        "message": f"SSH command execution error: {str(e)}",
                        "cpu_processes": [],
                        "memory_processes": []
                    }
        else:
            # SSH failed. Attempt DB fallback for this specific database connection
            db_fallback = fetch_processes_from_oracle_db(db_id)
            if db_fallback:
                databases_processes[db_id] = {
                    "status": "success",
                    "db_name": db_name,
                    "server": host,
                    "host": host,
                    "cpu_processes": db_fallback.get("cpu_processes", []),
                    "memory_processes": db_fallback.get("memory_processes", [])
                }
            else:
                from services.mountpoint_service import map_ssh_error
                databases_processes[db_id] = {
                    "status": "error",
                    "db_name": db_name,
                    "server": host,
                    "host": host,
                    "message": map_ssh_error(ssh_err),
                    "cpu_processes": [],
                    "memory_processes": []
                }

    # Close all active SSH clients
    for host, (ssh_client, _) in host_connections.items():
        if ssh_client:
            try:
                ssh_client.close()
            except:
                pass

    res = {
        "status": "success",
        "databases": databases_processes
    }
    return res

import threading
_process_lock = threading.Lock()
_process_checking_tasks = set()

def clear_remote_process_cache():
    with _process_lock:
        _SINGLE_DB_PROCESS_CACHE.clear()
        _ALL_PROCESSES_CACHE["timestamp"] = 0
        _ALL_PROCESSES_CACHE["data"] = None
        _process_checking_tasks.clear()
        logging.info("[Remote Processes Cache] Cleared all remote process caches.")

def invalidate_remote_process_cache(db_id):
    with _process_lock:
        _SINGLE_DB_PROCESS_CACHE.pop(db_id, None)
        _process_checking_tasks.discard(db_id)
        logging.info(f"[Remote Processes Cache] Invalidated process cache for {db_id}.")

def get_remote_server_processes(db_id=None):
    now = time.time()
    
    if db_id:
        with _process_lock:
            cached = _SINGLE_DB_PROCESS_CACHE.get(db_id)
            if cached and (now - cached["timestamp"] < 15):
                logging.info(f"[Remote Processes] Returning cached process data for single database: {db_id}")
                return cached["data"]
            
            # If not in cache, prepare placeholder
            if db_id not in _SINGLE_DB_PROCESS_CACHE:
                placeholder = {
                    "status": "checking",
                    "databases": {
                        db_id: {
                            "status": "checking",
                            "db_name": "Checking...",
                            "server": "Checking...",
                            "host": "Checking...",
                            "message": "Fetching remote server processes...",
                            "cpu_processes": [],
                            "memory_processes": []
                        }
                    }
                }
                _SINGLE_DB_PROCESS_CACHE[db_id] = {
                    "timestamp": 0,
                    "data": placeholder
                }
            
            # Trigger background get, if not already running
            if db_id not in _process_checking_tasks:
                _process_checking_tasks.add(db_id)
                
                def async_fetch():
                    try:
                        res = get_remote_server_processes_single_raw(db_id)
                        with _process_lock:
                            _SINGLE_DB_PROCESS_CACHE[db_id] = {
                                "timestamp": time.time(),
                                "data": res
                            }
                    except Exception as e:
                        logging.error(f"Error in async fetch processes for {db_id}: {e}")
                    finally:
                        with _process_lock:
                            _process_checking_tasks.discard(db_id)
                            
                try:
                    from services.config_service import telemetry_executor
                    telemetry_executor.submit(async_fetch)
                except Exception:
                    threading.Thread(target=async_fetch, daemon=True).start()
                
            return _SINGLE_DB_PROCESS_CACHE[db_id]["data"]

    else:
        # Global overview / homepage
        cache_key = "all"
        with _process_lock:
            cached = _ALL_PROCESSES_CACHE["data"]
            if cached and (now - _ALL_PROCESSES_CACHE["timestamp"] < 15):
                logging.info("[Remote Processes] Returning cached all-servers process data")
                return cached
                
            if not _ALL_PROCESSES_CACHE["data"] or _ALL_PROCESSES_CACHE["data"].get("databases") == {}:
                _ALL_PROCESSES_CACHE["data"] = {
                    "status": "checking",
                    "databases": {}
                }
                
            if cache_key not in _process_checking_tasks:
                _process_checking_tasks.add(cache_key)
                
                def async_fetch_all():
                    try:
                        res = get_remote_server_processes_all_raw()
                        with _process_lock:
                            _ALL_PROCESSES_CACHE["timestamp"] = time.time()
                            _ALL_PROCESSES_CACHE["data"] = res
                    except Exception as e:
                        logging.error(f"Error in async fetch all processes: {e}")
                    finally:
                        with _process_lock:
                            _process_checking_tasks.discard(cache_key)
                            
                try:
                    from services.config_service import telemetry_executor
                    telemetry_executor.submit(async_fetch_all)
                except Exception:
                    threading.Thread(target=async_fetch_all, daemon=True).start()
                
            return _ALL_PROCESSES_CACHE["data"]


