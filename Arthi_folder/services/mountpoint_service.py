import paramiko
import psutil
import json
import time
import logging
from flask import session
from services.config_service import get_db_config, get_all_db_configs

# Suppress noisy SSH banner logs
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

# Global cache for mount points (30s TTL)
_MOUNT_POINTS_CACHE = {
    "timestamp": 0,
    "db_id": None,
    "data": None
}

def format_bytes_human(bytes_val):
    if bytes_val is None:
        return "0G"
    if bytes_val >= 1024**4:
        return f"{round(bytes_val / (1024**4), 1)}T"
    elif bytes_val >= 1024**3:
        return f"{round(bytes_val / (1024**3), 1)}G"
    elif bytes_val >= 1024**2:
        return f"{round(bytes_val / (1024**2), 1)}M"
    elif bytes_val >= 1024:
        return f"{round(bytes_val / 1024, 1)}K"
    return f"{bytes_val}B"

def infer_filesystem_type(device, mount_point, fstype=None):
    if fstype and str(fstype).strip().lower() not in ("unknown", "n/a", "none", "", "null"):
        return str(fstype).strip()
    
    dev_lower = str(device or "").lower()
    mnt_lower = str(mount_point or "").lower()

    if "devtmpfs" in dev_lower or "devtmpfs" in mnt_lower:
        return "devtmpfs"
    if "tmpfs" in dev_lower or "tmpfs" in mnt_lower or mnt_lower.startswith("/run") or mnt_lower.startswith("/dev/shm"):
        return "tmpfs"
    if "efi" in dev_lower or "efi" in mnt_lower or "fat" in dev_lower or "boot/efi" in mnt_lower:
        return "vfat"
    if "swap" in dev_lower or "swap" in mnt_lower:
        return "swap"
    if "nfs" in dev_lower or ":" in dev_lower:
        return "nfs4"
    if "mapper" in dev_lower or "rhel" in dev_lower or "ol-" in dev_lower or "centos" in dev_lower or dev_lower.startswith("/dev/sd") or dev_lower.startswith("/dev/nvme") or dev_lower.startswith("/dev/vd"):
        return "xfs"
    if mnt_lower in ("/", "/boot", "/home", "/opt", "/usr", "/var", "/u01", "/u02", "/oradata"):
        return "xfs"
    
    return "xfs"

def parse_df_hT_output(output_text):
    """
    Parses output of 'df -hT' command into a list of mount point dicts.
    Line example:
    Filesystem     Type      Size  Used Avail Use% Mounted on
    /dev/sda2      xfs        70G   13G   57G  18% /
    /dev/sda1      xfs      1014M  512M  502M  51% /boot
    tmpfs          tmpfs     3.8G    0G  3.8G   0% /dev/shm
    """
    mounts = []
    if not output_text:
        return mounts

    lines = [line.strip() for line in output_text.strip().splitlines() if line.strip()]
    if not lines:
        return mounts

    start_idx = 0
    if lines[0].lower().startswith("filesystem"):
        start_idx = 1

    pending_filesystem = None

    for line in lines[start_idx:]:
        parts = line.split()
        if not parts:
            continue

        if len(parts) == 1:
            pending_filesystem = parts[0]
            continue

        if pending_filesystem:
            parts = [pending_filesystem] + parts
            pending_filesystem = None

        # df -hT outputs 7 fields: Filesystem Type Size Used Avail Use% Mounted_on
        if len(parts) >= 7:
            device = parts[0]
            raw_fstype = parts[1]
            total_size = parts[2]
            used_size = parts[3]
            avail_size = parts[4]
            usage_str = parts[5].replace('%', '')
            mount_point = " ".join(parts[6:])

            try:
                usage_percent = int(float(usage_str))
            except ValueError:
                usage_percent = 0

            fstype = infer_filesystem_type(device, mount_point, raw_fstype)

            mounts.append({
                "device": device,
                "mount_point": mount_point,
                "filesystem": fstype,
                "total_size": total_size,
                "used_size": used_size,
                "available_size": avail_size,
                "usage_percent": usage_percent
            })
        elif len(parts) >= 6:
            # Fallback if Type column was somehow missing (e.g. df -h)
            device = parts[0]
            total_size = parts[1]
            used_size = parts[2]
            avail_size = parts[3]
            usage_str = parts[4].replace('%', '')
            mount_point = " ".join(parts[5:])

            try:
                usage_percent = int(float(usage_str))
            except ValueError:
                usage_percent = 0

            fstype = infer_filesystem_type(device, mount_point, None)

            mounts.append({
                "device": device,
                "mount_point": mount_point,
                "filesystem": fstype,
                "total_size": total_size,
                "used_size": used_size,
                "available_size": avail_size,
                "usage_percent": usage_percent
            })

    return mounts

from services.ssh_service import get_ssh_connection_via_key

def get_remote_mount_points(db_id=None):
    ssh_client, db_cfg, ssh_err = get_ssh_connection_via_key(db_id=db_id, timeout=10, banner_timeout=10)

    if not ssh_client:
        return None, ssh_err or "SSH connection failed."

    try:
        stdin, stdout, stderr = ssh_client.exec_command("df -hT", timeout=10)
        output = stdout.read().decode('utf-8', errors='ignore')
        err_out = stderr.read().decode('utf-8', errors='ignore')
        ssh_client.close()

        if not output and err_out:
            return None, f"Command execution failed: {err_out.strip()}"

        mounts = parse_df_hT_output(output)
        server_name = db_cfg.get('service_name') or db_cfg.get('host') or "kasorcl"
        return (server_name, mounts), None
    except Exception as e:
        if ssh_client:
            try: ssh_client.close()
            except: pass
        return None, f"Command execution error: {str(e)}"

def get_oracle_db_mount_points(db_id):
    """
    Attempts to retrieve OS disk mount points via Oracle DB connection 
    (either Java df -h procedure or DBA datafile mount points).
    """
    try:
        from services.db_service import get_connection_by_id
        from services.os_service import get_remote_df_h, get_db_files_disks
        
        conn = get_connection_by_id(db_id)
        if not conn:
            return None
            
        try:
            # 1. Try Java stored procedure df -h via Oracle DB
            df_out = get_remote_df_h(conn)
            if df_out:
                mounts = parse_df_hT_output(df_out)
                if mounts:
                    conn.close()
                    return mounts
            
            # 2. Extract datafile mount points from v$datafile / dba_data_files
            db_disks = get_db_files_disks(conn, is_linux=True)
            conn.close()
            
            if db_disks:
                mounts = []
                for d in db_disks:
                    mounts.append({
                        "device": f"/dev/mapper/rhel-u01",
                        "mount_point": d["mount"],
                        "filesystem": "xfs",
                        "total_size": f"{d['total_gb']}G",
                        "used_size": f"{d['used_gb']}G",
                        "available_size": f"{d['free_gb']}G",
                        "usage_percent": int(round(d["percent"]))
                    })
                return mounts
        except Exception as e:
            logging.warning(f"Oracle DB mount points extraction warning: {e}")
            try: conn.close()
            except: pass
    except Exception as e:
        logging.error(f"Oracle DB connection error for mount points: {e}")
    return None

def get_fallback_linux_mount_points(server_name="kasorcl"):
    """
    Returns realistic Linux server storage partitions for remote database servers,
    matching standard RHEL/Oracle Linux server mount points.
    """
    return [
        {
            "device": "/dev/mapper/rhel-root",
            "mount_point": "/",
            "filesystem": "xfs",
            "total_size": "70.00 GB",
            "used_size": "15.00 GB",
            "available_size": "56.00 GB",
            "usage_percent": 21
        },
        {
            "device": "/dev/sda1",
            "mount_point": "/boot",
            "filesystem": "xfs",
            "total_size": "0.99 GB",
            "used_size": "0.66 GB",
            "available_size": "0.33 GB",
            "usage_percent": 67
        },
        {
            "device": "/dev/sda2",
            "mount_point": "/boot/efi",
            "filesystem": "vfat",
            "total_size": "0.58 GB",
            "used_size": "0.01 GB",
            "available_size": "0.58 GB",
            "usage_percent": 1
        },
        {
            "device": "/dev/mapper/rhel-home",
            "mount_point": "/home",
            "filesystem": "xfs",
            "total_size": "125.00 GB",
            "used_size": "33.00 GB",
            "available_size": "93.00 GB",
            "usage_percent": 26
        },
        {
            "device": "tmpfs",
            "mount_point": "/run/user/42",
            "filesystem": "tmpfs",
            "total_size": "0.35 GB",
            "used_size": "0.00 GB",
            "available_size": "0.35 GB",
            "usage_percent": 1
        },
        {
            "device": "tmpfs",
            "mount_point": "/run/user/54321",
            "filesystem": "tmpfs",
            "total_size": "0.39 GB",
            "used_size": "0.00 GB",
            "available_size": "0.39 GB",
            "usage_percent": 0
        }
    ]

def get_local_mount_points():
    mounts = []
    try:
        import platform
        # Only check local psutil if running on Linux
        if platform.system() != 'Windows':
            for p in psutil.disk_partitions(all=True):
                if p.fstype in ('proc', 'sysfs', 'devpts', 'configfs', 'pstore', 'securityfs', 'hugetlbfs', 'autofs'):
                    continue
                try:
                    u = psutil.disk_usage(p.mountpoint)
                    mounts.append({
                        "device": p.device,
                        "mount_point": p.mountpoint,
                        "filesystem": infer_filesystem_type(p.device, p.mountpoint, p.fstype),
                        "total_size": format_bytes_human(u.total),
                        "used_size": format_bytes_human(u.used),
                        "available_size": format_bytes_human(u.free),
                        "usage_percent": int(round(u.percent))
                    })
                except Exception:
                    pass
    except Exception as e:
        logging.error(f"psutil local mount points error: {e}")

    return mounts

def log_stage(msg):
    logging.info(f"[Mount Points Log] {msg}")

def map_ssh_error(ssh_err):
    if not ssh_err:
        return "SSH Connection Failed"
    err_lower = ssh_err.lower()
    
    # "SSH Authentication Failed"
    if "authentication failure" in err_lower or "authentication failed" in err_lower:
        return "SSH Authentication Failed"
    # "Invalid OS Username or Password"
    if "invalid os username" in err_lower or "os_user" in err_lower or "os_password" in err_lower or "missing" in err_lower:
        return "Invalid OS Username or Password"
    # "SSH Connection Timeout"
    if "timeout" in err_lower:
        return "SSH Connection Timeout"
    # "Port 22 Connection Refused"
    if "refused" in err_lower or "port 22" in err_lower:
        return "Port 22 Connection Refused"
    # "Host Unreachable"
    if "unreachable" in err_lower or "gaierror" in err_lower or "route" in err_lower or "resolve" in err_lower:
        return "Host Unreachable"
    # "Command Execution Failed"
    if "command execution failed" in err_lower:
        return "Command Execution Failed"
        
    return "SSH Connection Failed"

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
        
    # Check if registered as standby/reporting/standalone db_id in any config row or if its service name indicates DR/Rep
    for cfg in configs:
        is_self_row = cfg.get("db_id") == db_id_str or (cfg.get("db_id") and cfg.get("db_id").lower() == db_id_lower)

        # A database's own rep_db_id/stby_db_id/standalone_db_id fields describe
        # ITS secondary databases - they must never be compared against its own
        # db_id, or a production row whose test/placeholder secondary id happens
        # to equal its own db_id would falsely mark itself as standby/reporting.
        if not is_self_row:
            rep_db = str(cfg.get("rep_db_id") or "").strip()
            stby_db = str(cfg.get("stby_db_id") or "").strip()
            standalone_db = str(cfg.get("standalone_db_id") or "").strip()
            if db_id_str == rep_db or db_id_str == stby_db or db_id_str == standalone_db:
                return True
            if rep_db and db_id_lower == rep_db.lower():
                return True
            if stby_db and db_id_lower == stby_db.lower():
                return True
            if standalone_db and db_id_lower == standalone_db.lower():
                return True

        if is_self_row:
            service_name = str(cfg.get("service_name") or "").lower()
            if "aston.csi.waynepa" in service_name or "fiat.csi.waynepa" in service_name:
                return True
            if any(k in service_name for k in ("stby", "standby", "rep", "reporting")):
                return True

    return False

import threading

_single_db_mounts_cache = {}
_all_mount_points_cache = {
    "timestamp": 0,
    "data": None
}
_mount_lock = threading.Lock()
_mount_checking_tasks = set()

def clear_mountpoints_cache():
    with _mount_lock:
        _single_db_mounts_cache.clear()
        _all_mount_points_cache["timestamp"] = 0
        _all_mount_points_cache["data"] = None
        _mount_checking_tasks.clear()
        logging.info("[Mountpoints Cache] Cleared all mountpoints caches.")

def invalidate_mountpoints_cache(db_id):
    with _mount_lock:
        _single_db_mounts_cache.pop(db_id, None)
        _mount_checking_tasks.discard(db_id)
        # Also clear all mount points cache to force immediate recreation
        _all_mount_points_cache["timestamp"] = 0
        _all_mount_points_cache["data"] = None
        logging.info(f"[Mountpoints Cache] Invalidated mountpoints cache for {db_id}. Cleared all_mount_points_cache.")

def get_mount_points(db_id=None):
    now = time.time()
    try:
        from flask import session
        if not db_id:
            db_id = session.get('active_db_id')
    except Exception:
        pass
        
    if not db_id:
        try:
            from services.config_service import get_all_db_configs
            all_configs = get_all_db_configs()
            if all_configs:
                db_cfg = all_configs[0]
                db_id = db_cfg.get('db_id')
        except Exception:
            pass
            
    if not db_id:
        return {
            "status": "error",
            "server": "unknown",
            "message": "No active database selected.",
            "mount_points": []
        }
        
    cache_key = db_id
    with _mount_lock:
        cached = _single_db_mounts_cache.get(cache_key)
        # If cache is valid and not older than 60 seconds
        if cached and (now - cached["timestamp"] < 60):
            return cached["data"]
            
    # Trigger synchronous fetch to respond immediately without delay
    try:
        data = get_mount_points_raw(db_id)
        with _mount_lock:
            _single_db_mounts_cache[cache_key] = {
                "timestamp": time.time(),
                "data": data
            }
        return data
    except Exception as e:
        logging.error(f"Error fetching mount points for {db_id}: {e}")
        return {
            "status": "error",
            "server": "unknown",
            "message": str(e),
            "mount_points": []
        }

def get_all_servers_mount_points():
    now = time.time()
    
    with _mount_lock:
        cached = _all_mount_points_cache["data"]
        # If cache is valid and less than 60 seconds old
        if cached and (now - _all_mount_points_cache["timestamp"] < 60):
            return cached
            
    # Trigger synchronous fetch to respond immediately without delay
    try:
        data = get_all_servers_mount_points_raw()
        with _mount_lock:
            _all_mount_points_cache["timestamp"] = time.time()
            _all_mount_points_cache["data"] = data
        return data
    except Exception as e:
        logging.error(f"Error fetching all mount points: {e}")
        return {
            "status": "error",
            "message": str(e),
            "servers": {}
        }

def get_mount_points_raw(db_id=None):
    now = time.time()
    db_cfg = None
    if db_id:
        db_cfg = get_db_config(db_id)
    if not db_cfg:
        all_configs = get_all_db_configs()
        if all_configs:
            db_cfg = all_configs[0]
            db_id = db_cfg.get('db_id')

    if not db_cfg:
        log_stage("Active database selected: None")
        return {
            "status": "error",
            "server": "unknown",
            "message": "No active database selected.",
            "mount_points": []
        }

    configs = get_all_db_configs()
    if is_standby_or_reporting_db_id(db_id, configs):
        log_stage(f"Database {db_id} is Standby or Reporting database. Skipping OS mount points.")
        return {
            "status": "success",
            "server": db_cfg.get('service_name') or db_cfg.get('host') or "unknown",
            "message": "Mount point monitoring is disabled for Standby/Reporting databases.",
            "mount_points": []
        }

    # Stage 1: Active database selected
    log_stage(f"Active database selected: {db_id}")

    server_name = db_cfg.get('service_name') or db_cfg.get('host') or "kasorcl"
    host_ip = db_cfg.get('host') or "unknown"
    os_user = db_cfg.get('os_user') or ""
    
    # Stage 2: Host/IP
    log_stage(f"Target Host: {host_ip}")
    
    # Stage 3: SSH username
    log_stage(f"SSH Username: {os_user}")

    # Try Remote SSH (Paramiko df -hT)
    ssh_client, _, ssh_err = get_ssh_connection_via_key(db_id=db_id, timeout=10, banner_timeout=10)
    if not ssh_client:
        # Stage 4: SSH connection success/failure (failure case)
        log_stage(f"SSH Connection failed: {ssh_err}")
        return {
            "status": "error",
            "server": server_name,
            "message": map_ssh_error(ssh_err),
            "mount_points": []
        }

    # Stage 4: SSH connection success/failure (success case)
    log_stage("SSH Connection successful")

    try:
        cmd = "df -hT"
        # Stage 5: Command executed
        log_stage(f"Command executed: {cmd}")
        
        stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=10)
        output = stdout.read().decode('utf-8', errors='ignore')
        err_out = stderr.read().decode('utf-8', errors='ignore')
        ssh_client.close()

        # Stage 6: Command output
        log_stage("Command executed successfully, parsing output...")
        if err_out:
            log_stage(f"Command stderr: {err_out}")

        if not output and err_out:
            # Stage 7: Parsing status (failure)
            log_stage("Parsing status: Failed to parse output (no stdout content)")
            return {
                "status": "error",
                "server": server_name,
                "message": "Command Execution Failed",
                "mount_points": []
            }

        mounts = parse_df_hT_output(output)
        if not mounts:
            # Stage 7: Parsing status (failure)
            log_stage("Parsing status: Failed to parse output")
            return {
                "status": "error",
                "server": server_name,
                "message": "Command Execution Failed",
                "mount_points": []
            }

        # Stage 7: Parsing status (success)
        log_stage(f"Parsing status: Successfully parsed {len(mounts)} mount points")

        res = {
            "status": "success",
            "server": server_name,
            "mount_points": mounts
        }
        return res

    except Exception as e:
        log_stage(f"Exception during command execution: {str(e)}")
        if ssh_client:
            try: ssh_client.close()
            except: pass
        # Stage 7: Parsing status (failure)
        log_stage("Parsing status: Failed to parse output due to exception")
        return {
            "status": "error",
            "server": server_name,
            "message": "Command Execution Failed",
            "mount_points": []
        }

def get_all_servers_mount_points_raw():
    now = time.time()
    configs = get_all_db_configs()
    # Filter out standby and reporting configurations
    configs = [c for c in configs if not is_standby_or_reporting_db_id(c.get("db_id"), configs)]

    # Group configs by host IP - a host is the single unit of identity here.
    # Multiple databases sharing a host must produce exactly ONE SSH query and
    # exactly ONE entry in the response (never one per database), since they
    # all report the same filesystem/mount-point telemetry.
    host_map = {}
    for cfg in configs:
        host = cfg.get("host")
        if not host:
            continue
        if host not in host_map:
            host_map[host] = []
        host_map[host].append(cfg)

    servers = {}
    servers_lock = threading.Lock()

    def process_host(host, db_cfgs):
        valid_cfg = None
        for cfg in db_cfgs:
            if cfg.get("os_user") and cfg.get("os_password"):
                valid_cfg = cfg
                break
        if not valid_cfg:
            valid_cfg = db_cfgs[0]

        target_db_id = valid_cfg.get("db_id")
        db_ids = [cfg.get("db_id") for cfg in db_cfgs]
        db_names = [cfg.get("service_name") or cfg.get("db_id") or host for cfg in db_cfgs]
        server_label = valid_cfg.get("service_name") or host or "kasorcl"

        ssh_client, _, ssh_err = get_ssh_connection_via_key(db_id=target_db_id, timeout=10, banner_timeout=10)

        if ssh_client:
            try:
                cmd = "df -hT"
                stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=10)
                output = stdout.read().decode('utf-8', errors='ignore')
                err_out = stderr.read().decode('utf-8', errors='ignore')
                ssh_client.close()

                if not output and err_out:
                    host_status = "error"
                    host_message = f"Command Execution Failed: {err_out.strip()}"
                    mounts = []
                else:
                    mounts = parse_df_hT_output(output)
                    if not mounts:
                        host_status = "error"
                        host_message = "Failed to parse partitions telemetry."
                    else:
                        host_status = "success"
                        host_message = None
            except Exception as e:
                host_status = "error"
                host_message = f"Command execution exception: {str(e)}"
                mounts = []
                try: ssh_client.close()
                except: pass
        else:
            host_status = "error"
            host_message = map_ssh_error(ssh_err)
            mounts = []

        with servers_lock:
            servers[host] = {
                "status": host_status,
                "host": host,
                "server": server_label,
                "db_ids": db_ids,
                "db_names": db_names,
                "message": host_message,
                "mount_points": mounts if host_status == "success" else []
            }

    # Parallel processing - one thread per unique host, never per database
    threads = []
    for host, db_cfgs in host_map.items():
        t = threading.Thread(target=process_host, args=(host, db_cfgs))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    # Databases with no host configured don't map to any real server, but
    # must still surface somewhere so they're not silently dropped.
    missing_host_dbs = [cfg for cfg in configs if not cfg.get("host")]
    if missing_host_dbs:
        servers["__missing_host__"] = {
            "status": "error",
            "host": "unknown",
            "server": "unknown",
            "db_ids": [cfg.get("db_id") for cfg in missing_host_dbs],
            "db_names": [cfg.get("service_name") or cfg.get("db_id") or "unknown" for cfg in missing_host_dbs],
            "message": "Database host IP is missing in configuration.",
            "mount_points": []
        }

    res = {
        "status": "success",
        "servers": servers
    }
    return res
