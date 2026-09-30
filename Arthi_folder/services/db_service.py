import oracledb
import logging
import sys

# Ensure root logger has a stream handler and is set to INFO
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if not root_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s'))
    root_logger.addHandler(handler)
else:
    for h in root_logger.handlers:
        h.setLevel(logging.INFO)

import json
import os
import threading
import time
from datetime import datetime, timedelta
from flask import session
from services.config_service import get_db_config, get_all_db_configs

_thick_client_initialized = False
_thick_client_error = None

# Dedicated logger for production database connection diagnostics, written to
# its own file (logs/production_db.log) instead of dashboard_monitor.log so
# full connection details - including host/port/username/password - are easy
# to find when troubleshooting a specific connection failure, without being
# buried among unrelated HTTP/app log lines.
#
# SECURITY NOTE: this logs the plaintext password for every connection
# attempt, at the user's explicit request, to make debugging easier. Treat
# logs/production_db.log as sensitive - restrict who can read it, and never
# paste it into a chat, ticket, or email without redacting passwords first.
def _get_production_db_logger():
    logger = logging.getLogger("production_db")
    if not logger.handlers:
        try:
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, 'production_db.log')
            handler = logging.FileHandler(log_file, encoding='utf-8')
            handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        except Exception as e:
            sys.stderr.write(f"Failed to initialize production_db logger: {e}\n")
    return logger

_production_db_logger = _get_production_db_logger()

def _log_production_connection(db_id, db_cfg, event, extra=None):
    """Writes a full-detail connection log line (including credentials) to
    logs/production_db.log, for end-to-end troubleshooting of a specific
    database's connection failures."""
    try:
        detail = (
            f"db_id={db_id}, host={db_cfg.get('host')}, port={db_cfg.get('port')}, "
            f"service_name={db_cfg.get('service_name')}, username={db_cfg.get('username')}, "
            f"password={db_cfg.get('password')}"
        )
        if extra:
            detail += f", {extra}"
        _production_db_logger.info(f"[{event}] {detail}")
    except Exception:
        pass

def _get_dll_arch(dll_path):
    try:
        with open(dll_path, 'rb') as f:
            f.seek(0x3c)
            pe_offset = int.from_bytes(f.read(4), byteorder='little')
            f.seek(pe_offset)
            signature = f.read(4)
            if signature != b'PE\x00\x00':
                return None
            machine = int.from_bytes(f.read(2), byteorder='little')
            if machine in (0x8664, 0xaa64):  # AMD64, ARM64/Aarch64
                return 64
            elif machine == 0x14c:           # i386 (32-bit)
                return 32
    except Exception:
        pass
    return None

def _shallow_search_for_client(root_dir, max_depth=3):
    if not os.path.isdir(root_dir):
        return []
    found_dirs = []
    queue = [(root_dir, 0)]
    while queue:
        current_dir, depth = queue.pop(0)
        try:
            if os.path.isfile(os.path.join(current_dir, "oci.dll")):
                found_dirs.append(current_dir)
                continue
        except Exception:
            continue
        if depth >= max_depth:
            continue
        try:
            for entry in os.scandir(current_dir):
                if entry.is_dir(follow_symlinks=False):
                    queue.append((entry.path, depth + 1))
        except Exception:
            pass
    return found_dirs

def init_thick_client():
    """
    Initialize Oracle python-oracledb Thick mode with robust verification and logging.
    Resolves ONLY from the project's bundled oracle_client directory
    to ensure a self-contained deployment.
    """
    global _thick_client_initialized, _thick_client_error
    if _thick_client_initialized:
        return True, None

    import sys
    import os
    import struct
    import traceback
    import ctypes

    # Print a header separating OCI diagnostics
    header_msg = "=== Oracle Client Thick Mode Diagnostic Check ==="
    print(header_msg)
    logging.info(header_msg)
    sys.stdout.flush()

    # 1. Determine Oracle Client path (strictly bundled client directory)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client_path = None
    
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', '')
        exe_dir = os.path.dirname(sys.executable)
        
        # Prioritize _MEIPASS, then app build root (exe_dir)
        check_bases = [
            os.path.join(meipass, "oracle_client") if meipass else None,
            os.path.join(meipass, "instantclient") if meipass else None,
            meipass if meipass else None,
            os.path.join(exe_dir, "oracle_client"),
            os.path.join(exe_dir, "instantclient"),
            exe_dir
        ]
        for base in check_bases:
            if not base or not os.path.isdir(base):
                continue
            # Try plain directory check
            if os.path.isfile(os.path.join(base, "oci.dll")):
                client_path = base
                break
            # Try subfolder search inside base directory
            found_dirs = _shallow_search_for_client(base)
            if found_dirs:
                client_path = found_dirs[0]
                break
                
        if not client_path:
            client_path = os.path.join(exe_dir, "oracle_client")
    else:
        # Development mode: strictly project's bundled oracle_client directory
        base_client = os.path.join(project_root, "oracle_client")
        if os.path.isfile(os.path.join(base_client, "oci.dll")):
            client_path = base_client
        else:
            # Search up to depth 3 for any subfolder containing oci.dll
            found_dirs = _shallow_search_for_client(base_client)
            if found_dirs:
                client_path = found_dirs[0]
            else:
                client_path = base_client

    # Normalize backslashes for display on Windows
    client_path = os.path.abspath(client_path).replace('/', '\\')

    # Verification 1: Verify Oracle Client directory exists
    dir_exists = os.path.isdir(client_path)
    client_dir_msg = f"[Verification 1] Oracle Client directory '{client_path}': {'EXISTS' if dir_exists else 'DOES NOT EXIST'}"
    print(client_dir_msg)
    logging.info(client_dir_msg)
    sys.stdout.flush()
    if not dir_exists:
        raise RuntimeError(f"Oracle Client directory does not exist: {client_path}")

    # Verification 2: Verify oci.dll exists
    oci_file_path = os.path.join(client_path, "oci.dll")
    oci_exists = os.path.isfile(oci_file_path)
    oci_msg = f"[Verification 2] oci.dll: {'FOUND' if oci_exists else 'NOT FOUND'} at '{oci_file_path}'"
    print(oci_msg)
    logging.info(oci_msg)
    sys.stdout.flush()
    if not oci_exists:
        raise RuntimeError(f"oci.dll not found in client directory: {client_path}")

    # Verification 3: Every required Oracle Client DLL exists
    # Let's list all files in directory and output their path, size, and architecture
    files_in_dir = []
    try:
        files_in_dir = os.listdir(client_path)
    except Exception as e:
        print(f"Error listing client directory: {e}")
        logging.error(f"Error listing client directory: {e}")
        sys.stdout.flush()

    dlls_in_dir = [f for f in files_in_dir if f.lower().endswith('.dll')]
    print(f"Total DLL files found: {len(dlls_in_dir)}")
    logging.info(f"Total DLL files found in Oracle client dir: {len(dlls_in_dir)}")
    for dll in sorted(dlls_in_dir):
        path = os.path.join(client_path, dll)
        try:
            size = os.path.getsize(path)
            arch = _get_dll_arch(path)
            dll_info_msg = f"  - {dll}: Size={size} bytes, Arch={arch if arch else 'Unknown'}-bit"
            print(dll_info_msg)
            logging.info(dll_info_msg)
        except Exception as ex:
            print(f"  - {dll}: Error getting info: {ex}")
            logging.error(f"  - {dll}: Error getting info: {ex}")
    sys.stdout.flush()

    # Core required DLLs for Oracle Instant Client 19c
    core_19c_dlls = [
        "oci.dll",
        "oraclient19.dll",
        "oracommon19.dll",
        "oracore19.dll",
        "oranls19.dll",
        "orannzsbb19.dll"
    ]
    missing_core = [d for d in core_19c_dlls if d not in dlls_in_dir]
    
    # We also check if at least one data engine library (oraociei19.dll or oraociicus19.dll) is present
    data_engine_present = "oraociei19.dll" in dlls_in_dir or "oraociicus19.dll" in dlls_in_dir
    
    required_dlls_status_msg = "[Verification 3] Checking required Oracle client DLLs status: "
    if missing_core:
        required_dlls_status_msg += f"MISSING CORE DLLS: {', '.join(missing_core)}. "
    if not data_engine_present:
        required_dlls_status_msg += "MISSING DATA ENGINE DLL (neither 'oraociei19.dll' nor 'oraociicus19.dll' found). "
    
    if not missing_core and data_engine_present:
        required_dlls_status_msg += "ALL REQUIRED CORE DLLS AND DATA ENGINE DETECTED."
        print(required_dlls_status_msg)
        logging.info(required_dlls_status_msg)
    else:
        print(required_dlls_status_msg)
        is_frozen = getattr(sys, 'frozen', False)
        if is_frozen:
            logging.error(required_dlls_status_msg)
            sys.stdout.flush()
            raise RuntimeError(f"Oracle Client directory is missing some required DLL dependencies. Status: {required_dlls_status_msg}")
        else:
            logging.warning(f"[Verification 3 WARNING] Non-self-contained client directory in development mode. "
                            f"Proceeding but may fail if system PATH fallback is not available: {required_dlls_status_msg}")
    sys.stdout.flush()

    # Verification 4: MSVC Runtime DLLs exist
    msvc_dlls = [
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll"
    ]
    all_msvc_found = True
    msvc_status = []
    for dll_name in msvc_dlls:
        if dll_name in dlls_in_dir:
            msvc_status.append(f"{dll_name}: FOUND (local)")
        else:
            # Fallback 1: App Root folder
            root_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else project_root
            root_dll = os.path.join(root_dir, dll_name)
            # Fallback 2: System32 folder
            sys32_p = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', dll_name)
            
            if os.path.isfile(root_dll):
                msvc_status.append(f"{dll_name}: FOUND (App Root)")
            elif os.path.isfile(sys32_p):
                msvc_status.append(f"{dll_name}: FOUND (System32)")
            else:
                msvc_status.append(f"{dll_name}: NOT FOUND")
                all_msvc_found = False
            
    msvc_msg = f"[Verification 4] MSVC Runtime DLLs: {'ALL FOUND' if all_msvc_found else 'SOME MISSING'} ({', '.join(msvc_status)})"
    print(msvc_msg)
    logging.info(msvc_msg)
    sys.stdout.flush()
    if not all_msvc_found:
        raise RuntimeError(f"Oracle Client directory is missing MSVC Runtime DLLs: {', '.join([s for s in msvc_status if 'NOT FOUND' in s])}")

    # Set up DLL search directory for ctypes loading
    dll_dir_cookie = None
    prev_path = os.environ.get("PATH", "")
    try:
        if hasattr(os, 'add_dll_directory'):
            dll_dir_cookie = os.add_dll_directory(client_path)
            logging.info("Modified DLL Search Path with os.add_dll_directory.")
        os.environ["PATH"] = f"{client_path};{prev_path}"
        logging.info("Prepended client directory to os.environ['PATH'].")
    except Exception as path_err:
        logging.warning(f"Warning setting DLL path variables: {path_err}")
    sys.stdout.flush()

    # Verification 5: oci.dll can actually be loaded with ctypes.WinDLL
    native_crash_warning = (
        "\n=========================================================================\n"
        "CRITICAL STARTUP POINT: About to attempt loading 'oci.dll' via ctypes.WinDLL.\n"
        "If the application exits abruptly right now WITHOUT logging the success message,\n"
        "this confirms a native Windows DLL/OCI initialization crash has occurred!\n"
        "Possible causes: missing system DLLs, wrong architecture (32-bit vs 64-bit),\n"
        "or corrupted/malformed DLL binaries.\n"
        "=========================================================================\n"
    )
    print(native_crash_warning)
    logging.info(native_crash_warning)
    sys.stdout.flush()

    try:
        print(f"[Verification 5] Attempting ctypes.WinDLL('{oci_file_path}')...")
        logging.info(f"[Verification 5] Attempting ctypes.WinDLL('{oci_file_path}')...")
        sys.stdout.flush()
        
        lib_oci = ctypes.WinDLL(oci_file_path)
        
        success_ctypes_msg = f"[Verification 5 SUCCESS] successfully loaded 'oci.dll' via ctypes. Handle={lib_oci._handle}"
        print(success_ctypes_msg)
        logging.info(success_ctypes_msg)
        sys.stdout.flush()
    except Exception as ctypes_err:
        fail_ctypes_msg = f"[Verification 5 FAILED] Exception raised while loading oci.dll via ctypes: {ctypes_err}"
        print(fail_ctypes_msg)
        logging.error(fail_ctypes_msg)
        logging.error(traceback.format_exc())
        sys.stdout.flush()
        if dll_dir_cookie:
            try: dll_dir_cookie.close()
            except: pass
        raise RuntimeError(f"ctypes.WinDLL failed to load oci.dll: {ctypes_err}") from ctypes_err

    # Verification 6: oracledb.init_oracle_client(lib_dir=...) succeeds
    native_crash_warning_oracledb = (
        "\n=========================================================================\n"
        "CRITICAL STARTUP POINT: About to call 'oracledb.init_oracle_client(lib_dir=...)'.\n"
        "If the application exits abruptly right now WITHOUT logging the success message,\n"
        "this confirms a native Oracle Client initialization crash has occurred within the driver!\n"
        "=========================================================================\n"
    )
    print(native_crash_warning_oracledb)
    logging.info(native_crash_warning_oracledb)
    sys.stdout.flush()

    thick_mode_status = "DISABLED"
    init_result = "FAILED"
    raw_error = None
    raw_traceback = None

    try:
        print(f"[Verification 6] Calling oracledb.init_oracle_client(lib_dir='{client_path}')...")
        logging.info(f"[Verification 6] Calling oracledb.init_oracle_client(lib_dir='{client_path}')...")
        sys.stdout.flush()
        
        oracledb.init_oracle_client(lib_dir=client_path)
        
        success_oracledb_msg = "[Verification 6 SUCCESS] oracledb.init_oracle_client() completed successfully."
        print(success_oracledb_msg)
        logging.info(success_oracledb_msg)
        sys.stdout.flush()
        
        # Verification 7: oracledb.is_thin_mode() returns the expected value after initialization
        print("[Verification 7] Testing is_thin_mode()...")
        logging.info("[Verification 7] Testing is_thin_mode()...")
        sys.stdout.flush()
        
        is_thin = oracledb.is_thin_mode()
        is_thin_msg = f"[Verification 7] oracledb.is_thin_mode() returned: {is_thin}"
        print(is_thin_msg)
        logging.info(is_thin_msg)
        sys.stdout.flush()
        
        if not is_thin:
            thick_mode_status = "ENABLED"
            init_result = "SUCCESS"
            _thick_client_initialized = True
        else:
            thick_mode_status = "DISABLED"
            init_result = "FAILED"
            raw_error = Exception("oracledb.is_thin_mode() returned True after init_oracle_client.")
    except Exception as e:
        thick_mode_status = "DISABLED"
        init_result = "FAILED"
        raw_error = e
        raw_traceback = traceback.format_exc()

    # Clean up dll path variables
    if dll_dir_cookie:
        try: dll_dir_cookie.close()
        except: pass
    os.environ["PATH"] = prev_path

    # Verification 8: Any exception/error from Thick Mode initialization is captured
    msg_thick = f"Thick Mode: {thick_mode_status}"
    print(f"{msg_thick}")
    logging.info(msg_thick)

    msg_init = f"Thick Mode Initialization: {init_result}"
    print(f"{msg_init}")
    logging.info(msg_init)
    sys.stdout.flush()

    if init_result == "FAILED":
        _thick_client_initialized = False
        _thick_client_error = str(raw_error)
        err_header = "--- [Verification 8 FAILED] Thick Mode Initialization Traceback/Error ---"
        print(err_header)
        logging.error(err_header)
        if raw_traceback:
            print(raw_traceback)
            logging.error(raw_traceback)
        else:
            print(f"Error: {raw_error}")
            logging.error(f"Error: {raw_error}")
        print("-------------------------------------------------------------------------\n")
        sys.stdout.flush()
        raise RuntimeError(f"Oracle Thick Mode is required but initialization failed: {raw_error}")

    footer_msg = "=== Oracle Client Thick Mode Diagnostics PASSED Successfully ===\n"
    print(footer_msg)
    logging.info(footer_msg)
    sys.stdout.flush()

    return True, None


_pools = {}
_db_connection_errors = {}

# Set by graceful_shutdown() so background pollers stop kicking off new
# Oracle work once a shutdown has started - see graceful_shutdown() below.
_shutdown_in_progress = threading.Event()

class LoggedOracleConnection:
    """
    A connection wrapper that monitors and logs connection lifecycle events
    such as acquisition and release/return to the pool.
    """
    def __init__(self, conn, db_id, is_pooled=True):
        self._conn = conn
        self._db_id = db_id
        self._is_pooled = is_pooled
        self._released = False
        # Log connection acquisition without logging sensitive credentials
        logging.info(f"[Connection Lifecycle] Connection ACQUIRED for database '{db_id}' (Pooled: {is_pooled})")

    def __getattr__(self, name):
        # Delegate all other attributes to the underlying connection object
        return getattr(self._conn, name)

    def _log_release(self):
        if not self._released:
            self._released = True
            logging.info(f"[Connection Lifecycle] Connection RELEASED/RETURNED for database '{self._db_id}' (Pooled: {self._is_pooled})")

    def close(self):
        # Log connection release/return to pool
        self._log_release()
        try:
            self._conn.close()
        except Exception as e:
            logging.error(f"[Connection Lifecycle] Error closing connection for '{self._db_id}': {e}")

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._log_release()
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

def clear_db_service_caches():
    global _pools, _db_connection_errors, _db_summary_cache
    for db_id, pool in list(_pools.items()):
        try:
            logging.info(f"[Connection Lifecycle] Closing connection pool for {db_id} during cache clear.")
            pool.close()
        except Exception as e:
            logging.error(f"[Connection Lifecycle] Error closing pool for {db_id} during cache clear: {e}")
    _pools.clear()
    _db_connection_errors.clear()
    try:
        _db_summary_cache.clear()
    except:
        pass
    try:
        from services.dr_service import clear_dr_pools
        clear_dr_pools()
    except Exception as e:
        print(f"Error clearing DR pools: {e}")

_graceful_shutdown_done = threading.Event()

def graceful_shutdown(worker_timeout=10):
    """
    Best-effort, idempotent shutdown of every Oracle/SSH resource this
    process holds, run before the app terminates so Oracle server-side
    sessions (v$session) are released instead of left for SQL*Net's dead
    connection detection to clean up later. Safe to call more than once -
    a second call is a no-op.
    """
    if _graceful_shutdown_done.is_set():
        return
    _graceful_shutdown_done.set()

    logging.info("[Shutdown] Stopping background workers")
    # Stop poll_loop() and get_db_summary() from starting any new Oracle work.
    _shutdown_in_progress.set()

    # Wait for background workers currently mid-query on an Oracle connection
    # to finish on their own, up to worker_timeout, before pools are closed
    # out from under them.
    deadline = time.time() + worker_timeout
    while time.time() < deadline:
        with _poller_in_flight_lock:
            poller_busy = bool(_poller_in_flight)
        with _db_lock:
            summary_busy = bool(_db_summary_checking_tasks)
        if not poller_busy and not summary_busy:
            break
        time.sleep(0.2)

    for db_id, pool in list(_pools.items()):
        logging.info(f"[Shutdown] Closing Oracle pool for db_id={db_id}")
        try:
            pool.close()
        except Exception as e:
            logging.warning(f"[Shutdown] Oracle pool for db_id={db_id} already closed or failed to close: {e}")
    _pools.clear()

    try:
        from services.dr_service import clear_dr_pools
        clear_dr_pools()
    except Exception as e:
        logging.warning(f"[Shutdown] Error closing DR pools: {e}")

    logging.info("[Shutdown] Closing SSH connections")
    try:
        from services.ssh_service import clear_ssh_connections, clear_ssh_key_connections
        clear_ssh_connections()
        clear_ssh_key_connections()
    except Exception as e:
        logging.warning(f"[Shutdown] Error closing SSH connections: {e}")

    logging.info("[Shutdown] Database/SSH cleanup complete")

def invalidate_db_cache(db_id):
    global _pools, _db_connection_errors, _db_summary_cache, _db_summary_checking_tasks
    if db_id in _pools:
        try:
            logging.info(f"[Connection Lifecycle] Invalidating/closing connection pool for {db_id}.")
            _pools[db_id].close()
        except Exception as e:
            logging.error(f"[Connection Lifecycle] Error closing connection pool for {db_id} on invalidation: {e}")
        del _pools[db_id]
    if db_id in _db_connection_errors:
        del _db_connection_errors[db_id]
    
    # Use a lock to selectively delete from the summary cache
    if '_db_lock' in globals():
        with _db_lock:
            _db_summary_cache.pop(db_id, None)
            if '_db_summary_checking_tasks' in globals():
                _db_summary_checking_tasks.discard(db_id)
    else:
        _db_summary_cache.pop(db_id, None)
        if '_db_summary_checking_tasks' in globals():
            _db_summary_checking_tasks.discard(db_id)
            
    try:
        from services.dr_service import invalidate_dr_pool
        invalidate_dr_pool(db_id)
    except Exception as e:
        print(f"Error invalidating DR pool: {e}")

def _is_host_reachable(host, port, timeout=1.0):
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex((host, int(port)))
        s.close()
        return result == 0
    except:
        return False

def _check_and_deploy_package(conn):
    try:
        cursor = conn.cursor()
        print("Auto-deploy: Compiling/Updating GREENWORLD_MONITOR_PKG to ensure latest monitoring procedures...")
        from services.package_definition import PACKAGE_SPEC, PACKAGE_BODY
        cursor.execute(PACKAGE_SPEC)
        cursor.execute(PACKAGE_BODY)
        print("Auto-deploy: GREENWORLD_MONITOR_PKG compiled successfully.")
        cursor.close()
    except Exception as e:
        print("Auto-deploy warning: Failed to check/deploy GREENWORLD_MONITOR_PKG:", e)

# ✅ Create DB Connection
def get_connection():
    if _shutdown_in_progress.is_set():
        return None
    active_db_id = session.get('active_db_id')
    try:
        if not active_db_id:
            # Fallback to the first DB if none selected, without modifying session
            all_dbs = get_all_db_configs()
            if all_dbs:
                active_db_id = all_dbs[0].get('db_id')
        
        db_cfg = get_db_config(active_db_id)
        if not db_cfg:
            print(f"DB Error: config for {active_db_id} not found.")
            return None

        # Fail fast if host is unreachable ONLY when the pool hasn't been created yet
        if active_db_id not in _pools:
            if not _is_host_reachable(db_cfg['host'], db_cfg.get('port', 1521), timeout=2.5):
                print(f"DB Connection bypass: host {db_cfg['host']} port {db_cfg.get('port', 1521)} is unreachable.")
                return None

        dsn = f"{db_cfg['host']}:{db_cfg['port']}/{db_cfg['service_name']}"
        _log_production_connection(active_db_id, db_cfg, "Connection Attempt")

        # If username is SYS/sys, bypass the pool and create a standalone connection
        if db_cfg.get('username') and db_cfg['username'].strip().lower() == 'sys':
            oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
            logging.info(f"[Connection Lifecycle] Creating standalone SYSDBA connection for {active_db_id} (Mode: {oracle_mode}).")
            conn = oracledb.connect(
                user=db_cfg['username'],
                password=db_cfg['password'],
                dsn=dsn,
                mode=oracledb.SYSDBA
            )
            _log_production_connection(active_db_id, db_cfg, "Connection Success", extra="pooled=False")
            # Auto-deploy package check
            try:
                _check_and_deploy_package(conn)
            except Exception as pe:
                print("Auto-deploy warning during get_connection:", pe)
            return LoggedOracleConnection(conn, active_db_id, is_pooled=False)

        if active_db_id not in _pools:
            oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
            logging.info(f"[Connection Lifecycle] Creating connection pool for {active_db_id} (Mode: {oracle_mode}, ping_interval: 30s).")
            pool_kwargs = {
                "user": db_cfg['username'],
                "password": db_cfg['password'],
                "dsn": dsn,
                "min": 2,
                "max": 30,
                "increment": 2,
                "getmode": oracledb.POOL_GETMODE_WAIT,
                "ping_interval": 30
            }
            if db_cfg.get('username') and db_cfg['username'].strip().lower() == 'sys':
                pool_kwargs["mode"] = oracledb.SYSDBA
            _pools[active_db_id] = oracledb.create_pool(**pool_kwargs)
            
            # Auto-deploy package check
            try:
                temp_conn = _pools[active_db_id].acquire()
                _check_and_deploy_package(temp_conn)
                temp_conn.close()
            except Exception as pe:
                print("Auto-deploy warning during get_connection:", pe)
            
        raw_conn = _pools[active_db_id].acquire()
        _log_production_connection(active_db_id, db_cfg, "Connection Success", extra="pooled=True")
        return LoggedOracleConnection(raw_conn, active_db_id, is_pooled=True)
    except Exception as e:
        err_msg = str(e)
        if ("DPY-3001" in err_msg or "python-oracledb thick mode" in err_msg.lower()) and _thick_client_error:
            err_msg = f"{err_msg} (Thick mode initialization error: {_thick_client_error})"
        logging.error(f"[Connection Lifecycle] DB Error for {active_db_id}: {err_msg}")
        try:
            _log_production_connection(active_db_id, db_cfg, "Connection Failed", extra=f"error={err_msg}")
        except Exception:
            pass
        # If pool connection fails, cleanup pool to force recreation/recheck next time
        if active_db_id and active_db_id in _pools:
            logging.warning(f"[Connection Lifecycle] Connection failed, destroying pool for {active_db_id} to force recreation. Error: {err_msg}")
            try: _pools[active_db_id].close()
            except: pass
            del _pools[active_db_id]
        return None


# ✅ Create DB Connection by ID
def get_connection_by_id(db_id):
    if _shutdown_in_progress.is_set():
        return None
    try:
        if not db_id:
            return None
        
        db_cfg = get_db_config(db_id)
        if not db_cfg:
            _db_connection_errors[db_id] = "Configuration not found"
            print(f"DB Error: config for {db_id} not found.")
            return None

        # Fail fast if host is unreachable ONLY when the pool hasn't been created yet
        if db_id not in _pools:
            if not _is_host_reachable(db_cfg['host'], db_cfg.get('port', 1521), timeout=2.5):
                err_msg = f"Host {db_cfg['host']} on port {db_cfg.get('port', 1521)} is unreachable"
                print(f"DB Connection bypass: {err_msg}.")
                _db_connection_errors[db_id] = err_msg
                return None

        dsn = f"{db_cfg['host']}:{db_cfg['port']}/{db_cfg['service_name']}"
        _log_production_connection(db_id, db_cfg, "Connection Attempt")

        # If username is SYS/sys, bypass the pool and create a standalone connection
        if db_cfg.get('username') and db_cfg['username'].strip().lower() == 'sys':
            oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
            logging.info(f"[Connection Lifecycle] Creating standalone SYSDBA connection for {db_id} (Mode: {oracle_mode}).")
            conn = oracledb.connect(
                user=db_cfg['username'],
                password=db_cfg['password'],
                dsn=dsn,
                mode=oracledb.SYSDBA
            )
            _log_production_connection(db_id, db_cfg, "Connection Success", extra="pooled=False")
            # Auto-deploy package check
            try:
                _check_and_deploy_package(conn)
            except Exception as pe:
                print("Auto-deploy warning during get_connection_by_id:", pe)
            _db_connection_errors[db_id] = None
            return LoggedOracleConnection(conn, db_id, is_pooled=False)

        if db_id not in _pools:
            oracle_mode = "THICK" if not oracledb.is_thin_mode() else "THIN"
            logging.info(f"[Connection Lifecycle] Creating connection pool for {db_id} (Mode: {oracle_mode}, ping_interval: 30s).")
            pool_kwargs = {
                "user": db_cfg['username'],
                "password": db_cfg['password'],
                "dsn": dsn,
                "min": 2,
                "max": 30,
                "increment": 2,
                "getmode": oracledb.POOL_GETMODE_WAIT,
                "ping_interval": 30
            }
            if db_cfg.get('username') and db_cfg['username'].strip().lower() == 'sys':
                pool_kwargs["mode"] = oracledb.SYSDBA
            _pools[db_id] = oracledb.create_pool(**pool_kwargs)
            
            # Auto-deploy package check
            try:
                temp_conn = _pools[db_id].acquire()
                _check_and_deploy_package(temp_conn)
                temp_conn.close()
            except Exception as pe:
                print("Auto-deploy warning during get_connection_by_id:", pe)
            
        raw_conn = _pools[db_id].acquire()
        _log_production_connection(db_id, db_cfg, "Connection Success", extra="pooled=True")
        _db_connection_errors[db_id] = None  # Clear error on success
        return LoggedOracleConnection(raw_conn, db_id, is_pooled=True)
    except Exception as e:
        err_msg = str(e)
        if ("DPY-3001" in err_msg or "python-oracledb thick mode" in err_msg.lower()) and _thick_client_error:
            err_msg = f"{err_msg} (Thick mode initialization error: {_thick_client_error})"
        logging.error(f"[Connection Lifecycle] DB Connection Error for {db_id}: {err_msg}")
        try:
            _log_production_connection(db_id, db_cfg, "Connection Failed", extra=f"error={err_msg}")
        except Exception:
            pass
        _db_connection_errors[db_id] = err_msg
        # If pool connection fails, cleanup pool to force recreation/recheck next time
        if db_id and db_id in _pools:
            logging.warning(f"[Connection Lifecycle] Connection failed, destroying pool for {db_id} to force recreation. Error: {err_msg}")
            try: _pools[db_id].close()
            except: pass
            del _pools[db_id]
        return None


def get_db_summary_raw(db_id):
    summary = {
        "db_id": db_id,
        "db_status": "Not Connected",
        "listener_status": "Stopped",
        "active_sessions": 0,
        "tablespace_used_percent": 0.0,
        "has_full_tablespace": False,
        "full_tablespaces": [],
        "above_90_tablespaces": [],
        "last_backup_status": "Unknown",
        "last_backup_time": "N/A",
        "uptime": "N/A",
        "db_error": "",
        "cpu_used_percent": 0,
        "memory_used_percent": 0,
        "disk_used_percent": 0,
        "archive_log_full": False,
        "blocking_sessions_count": 0,
        "startup_time": None,
        "has_critical_mount": False,
        "critical_mount_points": []
    }
    
    # 1. Check Listener first via TCP ping
    try:
        db_cfg = get_db_config(db_id)
        if db_cfg:
            import socket
            host = db_cfg.get('host', 'localhost')
            port = int(db_cfg.get('port', 1521))
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            result = s.connect_ex((host, port))
            s.close()
            if result == 0:
                summary["listener_status"] = "Running"
    except Exception as e:
        print(f"Listener summary check error for {db_id}: {e}")

    # 2. Attempt DB Connection
    if summary["listener_status"] != "Running":
        summary["db_error"] = f"TNS: Listener is stopped or unreachable on port {port if 'port' in locals() else 1521}"
        return summary
        
    conn = get_connection_by_id(db_id)
    if not conn:
        summary["db_error"] = _db_connection_errors.get(db_id, "Connection failed")
        return summary
    
    summary["db_status"] = "Connected"
    
    # 3. Query Sessions, Tablespace, and Backup using procedures if possible, or direct fallback
    try:
        cursor = conn.cursor()

        # Fetch CPU, Memory, Disk, Archive Log, Blocking Sessions, and Startup Time
        try:
            cursor.execute("SELECT value FROM v$sysmetric WHERE metric_name = 'Host CPU Utilization (%)' AND group_id = 2")
            row = cursor.fetchone()
            if row and row[0] is not None:
                summary["cpu_used_percent"] = int(round(float(row[0])))
            else:
                cursor.execute("SELECT value FROM v$sysmetric WHERE metric_name = 'Host CPU Utilization (%)'")
                rows = cursor.fetchall()
                if rows:
                    summary["cpu_used_percent"] = int(round(sum(float(r[0]) for r in rows) / len(rows)))
        except:
            pass

        try:
            cursor.execute("SELECT stat_name, value FROM v$osstat WHERE stat_name IN ('PHYSICAL_MEMORY_BYTES', 'FREE_MEMORY_BYTES', 'INACTIVE_MEMORY_BYTES')")
            stats = dict(cursor.fetchall())
            total_mem = stats.get('PHYSICAL_MEMORY_BYTES', 0)
            free_mem = stats.get('FREE_MEMORY_BYTES', 0)
            inactive_mem = stats.get('INACTIVE_MEMORY_BYTES', 0)
            if total_mem > 0:
                available_mem = free_mem + inactive_mem if inactive_mem > 0 else free_mem
                used_mem = max(0, total_mem - available_mem)
                summary["memory_used_percent"] = int((used_mem / total_mem * 100))
        except:
            pass

        try:
            cursor.execute("SELECT ROUND(SUM(used_space) / SUM(tablespace_size) * 100, 2) FROM dba_tablespace_usage_metrics")
            row = cursor.fetchone()
            if row and row[0] is not None:
                summary["disk_used_percent"] = int(row[0])
            else:
                summary["disk_used_percent"] = int(round(summary.get("tablespace_used_percent", 0)))
        except:
            pass

        try:
            cursor.execute("""
                SELECT 
                    (SELECT log_mode FROM v$database) AS log_mode,
                    (SELECT ROUND(space_limit / 1024 / 1024 / 1024, 2) FROM v$recovery_file_dest WHERE rownum = 1) AS fra_limit_gb, 
                    (SELECT ROUND(space_used / 1024 / 1024 / 1024, 2) FROM v$recovery_file_dest WHERE rownum = 1) AS fra_used_gb
                FROM dual
            """)
            row = cursor.fetchone()
            if row:
                log_mode = str(row[0] or "UNKNOWN").upper()
                limit_gb = float(row[1] or 0.0)
                used_gb = float(row[2] or 0.0)
                percent_used = (used_gb / limit_gb * 100) if limit_gb > 0 else 0.0
                if log_mode == "ARCHIVELOG" and percent_used >= 90.0:
                    summary["archive_log_full"] = True
        except:
            pass

        try:
            cursor.execute("SELECT COUNT(*) FROM v$session WHERE blocking_session IS NOT NULL")
            row = cursor.fetchone()
            if row:
                summary["blocking_sessions_count"] = int(row[0])
        except:
            pass

        try:
            cursor.execute("SELECT startup_time FROM v$instance")
            row = cursor.fetchone()
            if row and row[0]:
                if hasattr(row[0], 'strftime'):
                    summary["startup_time"] = row[0].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    summary["startup_time"] = str(row[0])
        except:
            pass
        
        # A. Get Active Sessions Count
        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            out_max = cursor.var(oracledb.NUMBER)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_SESSIONS', [None, out_cursor, out_max])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
            
            total = 0
            active_count = 0
            for count, status in rows:
                if status and str(status).strip().upper() == 'ACTIVE':
                    active_count += (count or 0)
                total += (count or 0)
            summary["active_sessions"] = active_count
        except Exception as e:
            print(f"Summary sessions fetch error: {e}")
            try:
                cursor.execute("SELECT count(*) FROM v$session WHERE status = 'ACTIVE' AND username IS NOT NULL")
                res = cursor.fetchone()
                summary["active_sessions"] = res[0] if res else 0
            except:
                pass
        
        # B. Get Tablespace Usage Percent
        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_TABLESPACE_USED', [out_cursor])
            results = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
            
            total_used = 0
            total_allocated = 0
            has_full = False
            full_list = []
            above_90 = []
            for row in results:
                used_gb = row[1] or 0
                total_gb = row[2] or 0
                used_percent = row[3] or 0
                total_allocated += total_gb
                total_used += used_gb
                if used_percent >= 80.0:
                    has_full = True
                    full_list.append(f"{row[0]} ({used_percent}%)")
                if used_percent >= 90.0:
                    above_90.append(row[0])
            
            summary["tablespace_used_percent"] = round((total_used / total_allocated * 100), 2) if total_allocated > 0 else 0.0
            summary["has_full_tablespace"] = has_full
            summary["full_tablespaces"] = full_list
            summary["above_90_tablespaces"] = above_90
        except Exception as e:
            print(f"Summary tablespace fetch error: {e}")
            try:
                cursor.execute("""
                    SELECT
                        df.tablespace_name,
                        ROUND(((df.allocated_mb - NVL(fs.free_mb,0)) / df.max_mb) * 100, 2) AS used_percent
                    FROM
                    (
                        SELECT
                            tablespace_name,
                            SUM(bytes)/1024/1024 allocated_mb,
                            SUM(CASE WHEN autoextensible='YES' THEN maxbytes ELSE bytes END)/1024/1024 max_mb
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
                    ) fs ON df.tablespace_name = fs.tablespace_name
                """)
                rows = cursor.fetchall()
                has_full = False
                full_list = []
                above_90 = []
                for tablespace_name, used_percent in rows:
                    if used_percent >= 80.0:
                        has_full = True
                        full_list.append(f"{tablespace_name} ({used_percent}%)")
                    if used_percent >= 90.0:
                        above_90.append(tablespace_name)
                summary["has_full_tablespace"] = has_full
                summary["full_tablespaces"] = full_list
                summary["above_90_tablespaces"] = above_90
                
                cursor.execute("SELECT nvl(round(avg(used_percent), 2), 0.0) FROM dba_tablespace_usage_metrics")
                summary["tablespace_used_percent"] = cursor.fetchone()[0]
            except:
                pass

        # C. Get Last Backup Status
        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            out_yesterday = cursor.var(oracledb.NUMBER)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_LAST_BACKUP', [out_cursor, out_yesterday])
            row = out_cursor.getvalue().fetchone() if out_cursor.getvalue() else None
            yesterday_count = out_yesterday.getvalue()
            backup_yesterday = True if (yesterday_count and yesterday_count > 0) else False
            if row:
                summary["last_backup_status"] = row[2] or "Unknown"
                start_time = row[3]
                summary["last_backup_time"] = start_time.strftime("%d %b %H:%M") if start_time else "N/A"
                
                # Verify if backup is from yesterday or today
                from datetime import timedelta
                yesterday_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
                if isinstance(start_time, datetime):
                    if start_time.tzinfo is None and yesterday_midnight.tzinfo is not None:
                        yesterday_midnight = yesterday_midnight.replace(tzinfo=None)
                    summary["backup_yesterday"] = backup_yesterday or (start_time >= yesterday_midnight)
                else:
                    summary["backup_yesterday"] = backup_yesterday
        except Exception as e:
            print(f"Summary backup fetch error: {e}")
            try:
                cursor.execute("""
                    SELECT status, start_time 
                    FROM (SELECT status, start_time FROM v$rman_status ORDER BY start_time DESC) 
                    WHERE rownum = 1
                """)
                r = cursor.fetchone()
                if r:
                    summary["last_backup_status"] = r[0]
                    summary["last_backup_time"] = r[1].strftime("%d %b %H:%M") if r[1] else "N/A"
                    
                    # Verify if backup is from yesterday or today in fallback
                    start_time = r[1]
                    from datetime import timedelta
                    yesterday_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
                    if isinstance(start_time, datetime):
                        if start_time.tzinfo is None and yesterday_midnight.tzinfo is not None:
                            yesterday_midnight = yesterday_midnight.replace(tzinfo=None)
                        summary["backup_yesterday"] = start_time >= yesterday_midnight
                    else:
                        summary["backup_yesterday"] = False
            except:
                pass

        # D. Get Uptime
        try:
            cursor.execute("SELECT (sysdate - startup_time) FROM v$instance")
            row = cursor.fetchone()
            if row and row[0] is not None:
                uptime_days = float(row[0])
                total_minutes = int(uptime_days * 24 * 60)
                days = total_minutes // (24 * 60)
                hours = (total_minutes % (24 * 60)) // 60
                mins = total_minutes % 60
                
                if days > 0:
                    summary["uptime"] = f"{days}d {hours}h {mins}m"
                elif hours > 0:
                    summary["uptime"] = f"{hours}h {mins}m"
                else:
                    summary["uptime"] = f"{mins}m"
        except Exception as e:
            print(f"Summary uptime fetch error: {e}")
            try:
                # Fallback: estimate uptime from sysdate - MIN(logon_time) in v$session
                cursor.execute("SELECT (sysdate - MIN(logon_time)) FROM v$session")
                r = cursor.fetchone()
                if r and r[0] is not None:
                    total_minutes = int(float(r[0]) * 24 * 60)
                    days = total_minutes // (24 * 60)
                    hours = (total_minutes % (24 * 60)) // 60
                    mins = total_minutes % 60
                    if days > 0:
                        summary["uptime"] = f"{days}d {hours}h {mins}m"
                    elif hours > 0:
                        summary["uptime"] = f"{hours}h {mins}m"
                    else:
                        summary["uptime"] = f"{mins}m"
            except:
                pass

        cursor.close()
    except Exception as e:
        print(f"Error fetching DB summary details for {db_id}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass

    # Check remote OS mount points for space usage above 90% (ignoring tmpfs & devtmpfs)
    try:
        from services.mountpoint_service import get_mount_points
        mp_res = get_mount_points(db_id)
        if mp_res and mp_res.get("status") == "success":
            critical_mps = []
            for mp in mp_res.get("mount_points", []):
                fs = str(mp.get("filesystem") or "").lower().strip()
                dev = str(mp.get("device") or "").lower().strip()
                if fs in ("tmpfs", "devtmpfs") or dev in ("tmpfs", "devtmpfs"):
                    continue
                pct = mp.get("usage_percent", 0)
                if pct >= 90:
                    critical_mps.append(f"{mp.get('mount_point')} ({pct}%)")
            if critical_mps:
                summary["has_critical_mount"] = True
                summary["critical_mount_points"] = critical_mps
    except Exception as mp_ex:
        print(f"Error checking mount points in summary for {db_id}: {mp_ex}")
            
    # Check Standby and Reporting sync/status
    try:
        from services.dr_service import get_reporting_status, get_standby_archive_report
        summary["reporting_status"], summary["reporting_error"] = get_reporting_status(db_id)

        stby_res = get_standby_archive_report(db_id)
        summary["standby_status"] = stby_res.get("sync_status", "Not Configured")
        summary["archive_gap"] = stby_res.get("archive_gap")
        summary["standby_error"] = stby_res.get("error_detail")
        summary["standby_primary_seq"] = stby_res.get("generated_sequence")
        summary["standby_applied_seq"] = stby_res.get("applied_sequence")
        summary["standby_threads"] = stby_res.get("threads", [])
        summary["standby_dest_status"] = stby_res.get("dest_status", [])
        summary["standby_destination_status"] = stby_res.get("destination_status", [])
    except Exception as dr_ex:
        print(f"Error checking DR status in summary for {db_id}: {dr_ex}")
        summary["reporting_status"] = "Not Configured"
        summary["reporting_error"] = str(dr_ex)
        summary["standby_status"] = "Not Configured"
        summary["archive_gap"] = None
        summary["standby_error"] = str(dr_ex)
        summary["standby_primary_seq"] = None
        summary["standby_applied_seq"] = None
        summary["standby_threads"] = []
        summary["standby_dest_status"] = []
        summary["standby_destination_status"] = []

    return summary


# ✅ Caching Wrapper & Background Thread Poller for Database Summaries
_db_summary_cache = {}
_db_summary_checking_tasks = set()
_db_lock = threading.Lock()

def get_db_summary(db_id):
    # Check if this is the active database and quickly override the cache if it's connected
    active_db_id = None
    try:
        active_db_id = session.get('active_db_id')
    except Exception:
        pass # Context may not have Flask session if called outside request
        
    if db_id == active_db_id:
        conn = get_connection()
        if conn:
            conn.close()
            with _db_lock:
                if db_id in _db_summary_cache:
                    _db_summary_cache[active_db_id]["db_status"] = "Connected"
                    _db_summary_cache[active_db_id]["listener_status"] = "Running"
                else:
                    _db_summary_cache[active_db_id] = get_db_summary_raw(active_db_id)
                    _db_summary_cache[active_db_id]["db_status"] = "Connected"
                    _db_summary_cache[active_db_id]["listener_status"] = "Running"
        else:
            with _db_lock:
                if db_id in _db_summary_cache:
                    _db_summary_cache[db_id]["db_status"] = "Not Connected"

    with _db_lock:
        if db_id not in _db_summary_cache:
            # Initialize placeholder summary to prevent blocking Flask thread
            _db_summary_cache[db_id] = {
                "db_id": db_id,
                "db_status": "Checking...",
                "listener_status": "Checking...",
                "active_sessions": 0,
                "tablespace_used_percent": 0.0,
                "has_full_tablespace": False,
                "full_tablespaces": [],
                "above_90_tablespaces": [],
                "last_backup_status": "Unknown",
                "last_backup_time": "N/A",
                "uptime": "N/A",
                "db_error": "",
                "cpu_used_percent": 0,
                "memory_used_percent": 0,
                "disk_used_percent": 0,
                "archive_log_full": False,
                "blocking_sessions_count": 0,
                "startup_time": None,
                "has_critical_mount": False,
                "critical_mount_points": []
            }
            
            # Trigger async poll if not already running (and not shutting down)
            if db_id not in _db_summary_checking_tasks and not _shutdown_in_progress.is_set():
                _db_summary_checking_tasks.add(db_id)
                
                def async_poll_db():
                    try:
                        summary = get_db_summary_raw(db_id)
                        with _db_lock:
                            _db_summary_cache[db_id] = summary
                        from services.config_service import get_db_config
                        cfg = get_db_config(db_id)
                        if cfg:
                            evaluate_database_alerts(cfg, summary)
                    except Exception as e:
                        print(f"Error in async init poll for {db_id}: {e}")
                    finally:
                        with _db_lock:
                            _db_summary_checking_tasks.discard(db_id)
                            
                try:
                    from services.config_service import telemetry_executor
                    telemetry_executor.submit(async_poll_db)
                except Exception:
                    # Fallback to raw thread
                    import threading
                    threading.Thread(target=async_poll_db, daemon=True).start()
        
        return _db_summary_cache[db_id]

_alert_tracker = {}

def evaluate_database_alerts(cfg, summary):
    try:
        from services.email_service import load_global_email_settings, send_alert_email
        from datetime import datetime

        db_id = cfg.get("db_id")
        if not db_id:
            return

        settings = load_global_email_settings()
        alert_types = settings.get("alert_types", {})
        custom_rules = settings.get("custom_alert_rules", [])

        recipients_data = settings.get("recipients", [])
        if not recipients_data or not settings.get("smtp_host"):
            return

        # Collect recipient emails
        recipient_emails = []
        for r in recipients_data:
            if isinstance(r, dict) and r.get("email"):
                recipient_emails.append(r["email"])
            elif isinstance(r, str) and r.strip():
                recipient_emails.append(r.strip())

        if not recipient_emails:
            return

        frequency = str(settings.get("frequency", "once"))
        now = datetime.now()

        # Initialize tracker for db_id if not present
        if db_id not in _alert_tracker:
            _alert_tracker[db_id] = {
                "last_db_status": "Unknown",
                "last_startup_time": None,
                "states": {}
            }

        tracker = _alert_tracker[db_id]
        
        def check_alert_trigger(alert_key, is_currently_triggered):
            alert_state = tracker["states"].setdefault(alert_key, {"sent": False, "last_sent_time": None})
            
            if not is_currently_triggered:
                # Reset sent state when condition resolves
                alert_state["sent"] = False
                alert_state["last_sent_time"] = None
                return False
                
            # Triggered! Check frequency
            if frequency == "once":
                if not alert_state["sent"]:
                    return True
            else:
                try:
                    mins = int(frequency)
                except:
                    mins = 15
                last_time = alert_state["last_sent_time"]
                if last_time is None or (now - last_time).total_seconds() >= mins * 60:
                    return True
            return False

        def mark_alert_sent(alert_key):
            alert_state = tracker["states"].setdefault(alert_key, {"sent": False, "last_sent_time": None})
            alert_state["sent"] = True
            alert_state["last_sent_time"] = now

        def send_email_notification(alert_desc):
            host = cfg.get("host", "Unknown")
            date_time_str = now.strftime("%Y-%m-%d %H:%M:%S")

            subject_tmpl = settings.get("template_subject") or "[CRITICAL] Database Alert - {DATABASE_NAME}"
            message_tmpl = settings.get("template_message") or "Hello Team,\n\nDatabase : {DATABASE_NAME}\nHost : {HOST}\nStatus : {STATUS}\nTime : {DATE_TIME}\n\nPlease investigate immediately.\n\nRegards,\nGreenWorld Database Monitoring"

            subject = subject_tmpl.replace("{DATABASE_NAME}", db_id).replace("{HOST}", host).replace("{STATUS}", alert_desc).replace("{DATE_TIME}", date_time_str)
            message = message_tmpl.replace("{DATABASE_NAME}", db_id).replace("{HOST}", host).replace("{STATUS}", alert_desc).replace("{DATE_TIME}", date_time_str)

            send_alert_email(settings, subject, message, recipient_emails, db_id=db_id)

        # Get current states
        current_db_status = summary.get("db_status", "Not Connected")
        current_listener_status = summary.get("listener_status", "Stopped")
        current_startup = summary.get("startup_time")
        last_startup = tracker.get("last_startup_time")
        last_db_status = tracker.get("last_db_status")
        current_standby_status = summary.get("standby_status", "Not Configured")
        current_reporting_status = summary.get("reporting_status", "Not Configured")

        # 1. Evaluate Default (Standard) Alerts
        # A. Database Down
        if alert_types.get("db_down", True):
            is_triggered = (current_db_status != "Connected")
            if check_alert_trigger("db_down", is_triggered):
                send_email_notification(f"Database is down or offline. Error details: {summary.get('db_error', 'N/A')}")
                mark_alert_sent("db_down")
        else:
            check_alert_trigger("db_down", False)

        # B. Listener Down
        if alert_types.get("listener_down", True):
            is_triggered = (current_listener_status != "Running")
            if check_alert_trigger("listener_down", is_triggered):
                send_email_notification("Database Listener is stopped or unreachable.")
                mark_alert_sent("listener_down")
        else:
            check_alert_trigger("listener_down", False)

        # C. Tablespace 90
        if alert_types.get("tablespace_90", True):
            pct = summary.get("tablespace_used_percent", 0.0)
            above_90 = summary.get("above_90_tablespaces", [])
            is_triggered = (pct >= 90.0 or len(above_90) > 0)
            if check_alert_trigger("tablespace_90", is_triggered):
                ts_list = ", ".join(above_90) if above_90 else f"Overall ({pct}%)"
                send_email_notification(f"Tablespace usage exceeds 90%. Tablespaces: {ts_list}")
                mark_alert_sent("tablespace_90")
        else:
            check_alert_trigger("tablespace_90", False)

        # D. Disk 90
        if alert_types.get("disk_90", True):
            pct = summary.get("disk_used_percent", 0)
            is_triggered = (pct >= 90)
            if check_alert_trigger("disk_90", is_triggered):
                send_email_notification(f"Host Disk usage exceeds 90% (Current: {pct}%).")
                mark_alert_sent("disk_90")
        else:
            check_alert_trigger("disk_90", False)

        # E. CPU 90
        if alert_types.get("cpu_90", True):
            pct = summary.get("cpu_used_percent", 0)
            is_triggered = (pct >= 90)
            if check_alert_trigger("cpu_90", is_triggered):
                send_email_notification(f"Host CPU utilization exceeds 90% (Current: {pct}%).")
                mark_alert_sent("cpu_90")
        else:
            check_alert_trigger("cpu_90", False)

        # F. Memory 90
        if alert_types.get("mem_90", True):
            pct = summary.get("memory_used_percent", 0)
            is_triggered = (pct >= 90)
            if check_alert_trigger("mem_90", is_triggered):
                send_email_notification(f"Host Memory usage exceeds 90% (Current: {pct}%).")
                mark_alert_sent("mem_90")
        else:
            check_alert_trigger("mem_90", False)

        # G. RMAN Failed
        if alert_types.get("rman_failed", True):
            backup_status = str(summary.get("last_backup_status", "")).upper()
            is_triggered = ("FAIL" in backup_status or "ERR" in backup_status or "WARN" in backup_status)
            if check_alert_trigger("rman_failed", is_triggered):
                send_email_notification(f"RMAN Backup completed with warnings or errors. Status: {backup_status}")
                mark_alert_sent("rman_failed")
        else:
            check_alert_trigger("rman_failed", False)

        # H. Archive Full
        if alert_types.get("archive_full", True):
            is_triggered = summary.get("archive_log_full", False)
            if check_alert_trigger("archive_full", is_triggered):
                send_email_notification("Archive Log space is full or recovery area (FRA) usage exceeds 90%.")
                mark_alert_sent("archive_full")
        else:
            check_alert_trigger("archive_full", False)

        # I. Blocking Sessions
        if alert_types.get("blocking_sessions", True):
            cnt = summary.get("blocking_sessions_count", 0)
            is_triggered = (cnt > 0)
            if check_alert_trigger("blocking_sessions", is_triggered):
                send_email_notification(f"Blocking sessions detected in database (Active blocker count: {cnt}).")
                mark_alert_sent("blocking_sessions")
        else:
            check_alert_trigger("blocking_sessions", False)

        # J. Database Startup
        if alert_types.get("db_startup", True):
            if current_startup and last_startup and last_startup != current_startup:
                startup_key = f"db_startup_{current_startup}"
                alert_state = tracker["states"].setdefault(startup_key, {"sent": False, "last_sent_time": None})
                if not alert_state["sent"]:
                    send_email_notification(f"Database Startup Event Detected (Startup Time: {current_startup}).")
                    alert_state["sent"] = True
                    alert_state["last_sent_time"] = now

        # K. Database Shutdown
        if alert_types.get("db_shutdown", True):
            if last_db_status == "Connected" and current_db_status == "Not Connected":
                alert_state = tracker["states"].setdefault("db_shutdown_event", {"sent": False, "last_sent_time": None})
                if not alert_state["sent"]:
                    send_email_notification("Database Shutdown or Disconnect Event Detected.")
                    alert_state["sent"] = True
                    alert_state["last_sent_time"] = now
            elif current_db_status == "Connected":
                # reset shutdown state
                alert_state = tracker["states"].setdefault("db_shutdown_event", {"sent": False, "last_sent_time": None})
                alert_state["sent"] = False

        # L. Standby Database Down (SSH-to-Production / TNS connection failure).
        # "Not Available" is the same underlying condition this alert has
        # always covered (the Standby DB itself couldn't be reached) - it's
        # just now reported under its own status instead of being lumped
        # into "DOWN" together with SSH/Production-side failures. Included
        # here so genuinely-unreachable Standby DBs keep alerting exactly as
        # before; SSH/Production failures still classify as "DOWN" and still
        # trigger this alert unchanged.
        if alert_types.get("standby_down", True):
            is_triggered = (current_standby_status in ("DOWN", "Not Available"))
            if check_alert_trigger("standby_down", is_triggered):
                send_email_notification(f"Standby database is unreachable. Error details: {summary.get('standby_error', 'N/A')}")
                mark_alert_sent("standby_down")
        else:
            check_alert_trigger("standby_down", False)

        # M. Standby Not Synced (replication lag or destination error)
        if alert_types.get("standby_not_synced", True):
            is_triggered = (current_standby_status == "Not Synced")
            if check_alert_trigger("standby_not_synced", is_triggered):
                send_email_notification(f"Standby database is out of sync with Primary. Error details: {summary.get('standby_error', 'N/A')}")
                mark_alert_sent("standby_not_synced")
        else:
            check_alert_trigger("standby_not_synced", False)

        # N. Standby Archive Log Gap Critical (gap > 2)
        if alert_types.get("standby_log_gap", True):
            gap = summary.get("archive_gap")
            is_triggered = (gap is not None and gap > 2)
            if check_alert_trigger("standby_log_gap", is_triggered):
                send_email_notification(f"Standby archive log gap exceeds 2 (Current gap: {gap}).")
                mark_alert_sent("standby_log_gap")
        else:
            check_alert_trigger("standby_log_gap", False)

        # O. Standby Archive Destination Error (V$ARCHIVE_DEST_STATUS ERROR column)
        if alert_types.get("standby_destination_error", True):
            dest_rows = summary.get("standby_dest_status", []) or []
            error_dests = [d for d in dest_rows if str(d.get("error") or "").strip()]
            is_triggered = len(error_dests) > 0
            if check_alert_trigger("standby_destination_error", is_triggered):
                dest_msgs = "; ".join(f"DEST_ID {d.get('dest_id')}: {d.get('error')}" for d in error_dests)
                send_email_notification(f"Standby archive destination reporting error(s): {dest_msgs}")
                mark_alert_sent("standby_destination_error")
        else:
            check_alert_trigger("standby_destination_error", False)

        # P. Reporting Database Down
        if alert_types.get("reporting_db_down", True):
            is_triggered = (current_reporting_status == "DOWN")
            if check_alert_trigger("reporting_db_down", is_triggered):
                send_email_notification(f"Reporting database is unreachable. Error details: {summary.get('reporting_error', 'N/A')}")
                mark_alert_sent("reporting_db_down")
        else:
            check_alert_trigger("reporting_db_down", False)

        # Q. Critical Mount Point Usage (>= 90%)
        if alert_types.get("mount_point_critical", True):
            is_triggered = summary.get("has_critical_mount", False)
            if check_alert_trigger("mount_point_critical", is_triggered):
                mp_list = ", ".join(summary.get("critical_mount_points", []))
                send_email_notification(f"Mount point usage critical (>=90%). Mount points: {mp_list}")
                mark_alert_sent("mount_point_critical")
        else:
            check_alert_trigger("mount_point_critical", False)

        # 2. Evaluate Custom Alert Rules
        for rule in custom_rules:
            if not rule.get("enabled", True):
                continue
            rtype = rule.get("type")
            rthresh = rule.get("threshold")
            try:
                threshold_val = float(rthresh) if rthresh is not None else 0.0
            except:
                threshold_val = 0.0

            # Normalize rtype string to support typed inputs
            norm_type = str(rtype or "").lower().strip()
            alert_key = f"custom_{norm_type}_{threshold_val}"
            
            is_triggered = False
            desc = ""

            if "cpu" in norm_type:
                pct = summary.get("cpu_used_percent", 0)
                is_triggered = (pct >= threshold_val)
                desc = f"Custom CPU usage alert: current CPU usage is {pct}% (Threshold: {threshold_val}%)."
            elif "mem" in norm_type or "ram" in norm_type:
                pct = summary.get("memory_used_percent", 0)
                is_triggered = (pct >= threshold_val)
                desc = f"Custom Memory usage alert: current Memory usage is {pct}% (Threshold: {threshold_val}%)."
            elif "disk" in norm_type or "mount" in norm_type or "storage" in norm_type:
                pct = summary.get("disk_used_percent", 0)
                is_triggered = (pct >= threshold_val)
                desc = f"Custom Disk usage alert: current Disk usage is {pct}% (Threshold: {threshold_val}%)."
            elif "tablespace" in norm_type or "tb" in norm_type:
                pct = summary.get("tablespace_used_percent", 0.0)
                is_triggered = (pct >= threshold_val)
                desc = f"Custom Tablespace usage alert: current usage is {pct}% (Threshold: {threshold_val}%)."
            elif "block" in norm_type or "lock" in norm_type or "session" in norm_type:
                cnt = summary.get("blocking_sessions_count", 0)
                is_triggered = (cnt > threshold_val)
                desc = f"Custom Blocking Sessions alert: current blocker count is {cnt} (Threshold: > {threshold_val})."
            else:
                # If they typed something else entirely, try to match keys inside summary
                matched_key = None
                for key in summary.keys():
                    if norm_type in key.lower() or key.lower() in norm_type:
                        matched_key = key
                        break
                if matched_key:
                    val = summary.get(matched_key)
                    try:
                        val_num = float(val)
                        is_triggered = (val_num >= threshold_val)
                        desc = f"Custom Alert for {matched_key}: current value is {val} (Threshold: {threshold_val})."
                    except:
                        pass

            if desc and check_alert_trigger(alert_key, is_triggered):
                send_email_notification(desc)
                mark_alert_sent(alert_key)

        # Update tracking values
        tracker["last_db_status"] = current_db_status
        if current_startup:
            tracker["last_startup_time"] = current_startup

    except Exception as e:
        print(f"Error evaluating alert for {cfg.get('db_id')}: {e}")

_poller_in_flight = set()
_poller_in_flight_lock = threading.Lock()

def _background_summary_poller():
    import time
    import threading

    def poll_db(cfg):
        db_id = cfg.get('db_id')
        if not db_id:
            return
        try:
            summary = get_db_summary_raw(db_id)
            _db_summary_cache[db_id] = summary
            evaluate_database_alerts(cfg, summary)
        except Exception as e:
            print(f"Error polling db {db_id}: {e}")
        finally:
            with _poller_in_flight_lock:
                _poller_in_flight.discard(db_id)

    def poll_loop():
        # Delay startup briefly to allow server initialization
        time.sleep(2)
        while not _shutdown_in_progress.is_set():
            try:
                from services.config_service import get_all_db_configs
                configs = get_all_db_configs()
                threads = []
                for cfg in configs:
                    if _shutdown_in_progress.is_set():
                        break
                    db_id = cfg.get('db_id')
                    if not db_id:
                        continue
                    with _poller_in_flight_lock:
                        # Skip this DB if its previous poll cycle hasn't finished yet,
                        # so a slow subprocess can't stack overlapping polls on top of itself.
                        if db_id in _poller_in_flight:
                            continue
                        _poller_in_flight.add(db_id)
                    t = threading.Thread(target=poll_db, args=(cfg,))
                    t.start()
                    threads.append(t)

                # Wait for all threads to finish with a maximum timeout of 15 seconds
                for t in threads:
                    t.join(timeout=15.0)
            except Exception as e:
                print("Error in background summary poller:", e)
            # Wake up immediately once shutdown starts instead of sleeping out
            # the full interval, so poll_loop exits promptly.
            if _shutdown_in_progress.wait(25):
                break

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

# Start background poller immediately (only in parent process, not in subprocess runtime)
if "--thick-subprocess" not in sys.argv:
    _background_summary_poller()




# ✅ Check DB Status
def check_db_connection():
    conn = get_connection()
    if conn:
        conn.close()
        return "Connected"
    else:
        return "Not Connected"


# ✅ Get Tablespace Usage (Oracle metrics-based)
def get_tablespace_used():
    try:
        conn = get_connection()
        if not conn:
            return {
                "used_percent": 0,
                "status": "Error",
                "error": "Cannot connect to database"
            }

        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_TABLESPACE_USED', [out_cursor])
            results = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
        except Exception as pkg_err:
            print(f"Package GET_TABLESPACE_USED failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT
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
                ORDER BY df.tablespace_name
            """)
            results = cursor.fetchall()

        cursor.close()
        conn.close()

        if not results:
            return {
                "used_percent": 0,
                "status": "No Data",
                "error": "No tablespace data found"
            }

        tablespaces = []
        total_used = 0
        total_allocated = 0
        total_free = 0

        for row in results:
            tablespace_name = row[0]
            used_gb = row[1] or 0
            total_gb = row[2] or 0
            used_percent = row[3] or 0
            free_gb = row[4] or 0

            # Protect against small rounding errors
            if used_percent < 0:
                used_percent = 0.0
            if used_percent > 100:
                used_percent = 100.0

            tablespaces.append({
                "tablespace_name": tablespace_name,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "used_percent": used_percent
            })

            total_allocated += total_gb
            total_used += used_gb
            total_free += free_gb

        overall_percent = round((total_used / total_allocated * 100), 2) if total_allocated > 0 else 0

        if overall_percent < 40:
            status = "Low Usage"
        elif overall_percent < 80:
            status = "Moderate"
        else:
            status = "Critical"

        return {
            "tablespace_name": "ALL_TABLESPACES",
            "total_gb": round(total_allocated, 2),
            "used_gb": round(total_used, 2),
            "free_gb": round(total_free, 2),
            "used_percent": overall_percent,
            "status": status,
            "tablespaces": tablespaces
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        print("Tablespace Error:", e)
        return {
            "used_percent": 0,
            "status": "Error",
            "error": str(e)
        }


# ✅ Get Last RMAN Backup
# ✅ Get Last RMAN Backup
def get_last_backup():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database", "backup_yesterday": False}
        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            out_yesterday = cursor.var(oracledb.NUMBER)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_LAST_BACKUP', [out_cursor, out_yesterday])
            row = out_cursor.getvalue().fetchone() if out_cursor.getvalue() else None
            yesterday_count = out_yesterday.getvalue()
            backup_yesterday = True if (yesterday_count and yesterday_count > 0) else False
        except Exception as pkg_err:
            print(f"Package GET_LAST_BACKUP failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT session_key, input_type, status, start_time, end_time, ROUND(input_bytes / 1024 / 1024, 2) AS size_mb
                FROM (
                    SELECT session_key, input_type, status, start_time, end_time, input_bytes
                    FROM v$rman_backup_job_details
                    ORDER BY start_time DESC
                )
                WHERE ROWNUM = 1
            """)
            row = cursor.fetchone()
            
            cursor.execute("""
                SELECT COUNT(*) FROM v$rman_backup_job_details 
                WHERE start_time >= TRUNC(SYSDATE) - 1 
                  AND start_time < TRUNC(SYSDATE) 
                  AND status IN ('COMPLETED', 'SUCCESS')
            """)
            y_count = cursor.fetchone()
            yesterday_count = y_count[0] if y_count else 0
            backup_yesterday = True if yesterday_count > 0 else False

        cursor.close()
        conn.close()

        if row:
            start_time = row[3]
            end_time = row[4]

            formatted_start = start_time.strftime("%d %b %Y %H:%M") if isinstance(start_time, datetime) else start_time
            formatted_end = end_time.strftime("%d %b %Y %H:%M") if isinstance(end_time, datetime) else end_time

            duration_minutes = 0
            if isinstance(start_time, datetime) and isinstance(end_time, datetime):
                duration = end_time - start_time
                duration_minutes = round(duration.total_seconds() / 60, 2)

            return {
                "session_key": row[0],
                "input_type": row[1],
                "status": row[2],
                "start_time": formatted_start,
                "end_time": formatted_end,
                "duration_minutes": duration_minutes,
                "size_mb": float(row[5]) if row[5] else 0,
                "backup_yesterday": backup_yesterday
            }

        return {"error": "No Backup Found", "backup_yesterday": False}

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Backup History (Past 7 Days)
def get_backup_history():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database", "backups": []}
        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_BACKUP_HISTORY', [out_cursor])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
        except Exception as pkg_err:
            print(f"Package GET_BACKUP_HISTORY failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT
                    SESSION_KEY,
                    INPUT_TYPE,
                    STATUS,
                    TO_CHAR(START_TIME, 'DD-MON HH24:MI') AS START_TIME,
                    TO_CHAR(END_TIME, 'DD-MON HH24:MI') AS END_TIME,
                    ROUND(OUTPUT_BYTES / 1024 / 1024, 2) AS SIZE_MB
                FROM
                    V$RMAN_BACKUP_JOB_DETAILS
                WHERE
                    START_TIME >= SYSDATE - 14
                ORDER BY
                    START_TIME ASC
            """)
            rows = cursor.fetchall()

        cursor.close()
        conn.close()

        backups = []
        for row in rows:
            backups.append({
                "session_key": row[0],
                "input_type": row[1],
                "status": row[2],
                "start_time": row[3],
                "end_time": row[4],
                "size_mb": float(row[5]) if row[5] else 0
            })

        return {"backups": backups}

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Daily Backup Statistics (Past 7 Days) - For Graphs
def get_daily_backup_stats():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_DAILY_BACKUP_STATS', [out_cursor])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
        except Exception as pkg_err:
            print(f"Package GET_DAILY_BACKUP_STATS failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT
                    TO_CHAR(START_TIME, 'DD-MON') AS DAY,
                    COUNT(*) AS TOTAL_BACKUPS,
                    ROUND(SUM(OUTPUT_BYTES)/1024/1024, 2) AS TOTAL_MB
                FROM
                    V$RMAN_BACKUP_JOB_DETAILS
                WHERE
                    START_TIME >= SYSDATE - 14
                GROUP BY
                    TO_CHAR(START_TIME, 'DD-MON')
                ORDER BY
                    DAY
            """)
            rows = cursor.fetchall()
        
        cursor.close()
        conn.close()

        stats = []
        for row in rows:
            stats.append({
                "day": row[0],
                "total_backups": int(row[1]) if row[1] else 0,
                "total_mb": float(row[2]) if row[2] else 0
            })

        return {"stats": stats}

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Backup Sessions by Status with Cumulative Data
def get_backup_sessions_chart():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_BACKUP_SESSIONS_CHART', [out_cursor])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
        except Exception as pkg_err:
            print(f"Package GET_BACKUP_SESSIONS_CHART failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT
                    TO_CHAR(START_TIME, 'DD MON') AS DAY,
                    ROWNUM AS SESSION_NUM,
                    STATUS,
                    ROUND(OUTPUT_BYTES/1024/1024, 2) AS SIZE_MB,
                    ROUND(SUM(OUTPUT_BYTES) OVER (ORDER BY START_TIME)/1024/1024, 2) AS CUMULATIVE_MB
                FROM
                    V$RMAN_BACKUP_JOB_DETAILS
                WHERE
                    START_TIME >= SYSDATE - 14
                ORDER BY
                    START_TIME
            """)
            rows = cursor.fetchall()
        
        cursor.close()
        conn.close()

        # Group by day and status
        daily_data = {}
        all_sessions = []
        
        for row in rows:
            day = row[0]
            session_num = int(row[1])
            status = (row[2] or '').upper()
            size_mb = float(row[3]) if row[3] else 0
            cumulative_mb = float(row[4]) if row[4] else 0
            
            # Store session data for table
            all_sessions.append({
                "day": day,
                "session": f"#{session_num}",
                "status": status,
                "size_mb": size_mb,
                "cumulative_mb": cumulative_mb
            })
            
            # Group by day for chart
            if day not in daily_data:
                daily_data[day] = {
                    "completed": 0,
                    "running": 0,
                    "failed": 0,
                    "cumulative_mb": cumulative_mb
                }
            
            if status == 'COMPLETED':
                daily_data[day]["completed"] += size_mb
            elif status in ['RUNNING', 'IN PROGRESS', 'STARTED', 'ACTIVE', 'QUEUED']:
                daily_data[day]["running"] += size_mb
            else:
                daily_data[day]["failed"] += size_mb
            
            daily_data[day]["cumulative_mb"] = cumulative_mb

        # Convert to list for chart
        chart_data = []
        for day in sorted(daily_data.keys()):
            chart_data.append({
                "day": day,
                "completed": daily_data[day]["completed"],
                "running": daily_data[day]["running"],
                "failed": daily_data[day]["failed"],
                "cumulative_mb": daily_data[day]["cumulative_mb"]
            })

        return {
            "chart": chart_data,
            "sessions": all_sessions
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Session Log Details
def get_session_log():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_SESSION_LOG', [out_cursor])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
        except Exception as pkg_err:
            print(f"Package GET_SESSION_LOG failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT
                    TO_CHAR(START_TIME, 'DD MON') AS DATE_STR,
                    SESSION_KEY AS SESSION_NUM,
                    STATUS,
                    ROUND(OUTPUT_BYTES/1024/1024, 2) AS SIZE_MB,
                    ROUND((END_TIME - START_TIME) * 24 * 60, 0) AS DURATION_MINUTES
                FROM
                    V$RMAN_BACKUP_JOB_DETAILS
                WHERE
                    START_TIME >= SYSDATE - 14
                ORDER BY
                    START_TIME DESC
            """)
            rows = cursor.fetchall()
        
        cursor.close()
        conn.close()

        sessions = []
        for row in rows:
            date = row[0]
            session_num = int(row[1]) if row[1] else 0
            status = (row[2] or '').upper()
            size_mb = float(row[3]) if row[3] else 0
            duration_minutes = int(row[4]) if row[4] else 0
            
            # Format duration
            if duration_minutes is None or duration_minutes < 0:
                duration = "—"
            else:
                hours = duration_minutes // 60
                minutes = duration_minutes % 60
                if hours > 0:
                    duration = f"{hours}h {minutes}m"
                else:
                    duration = f"{minutes}m" if minutes > 0 else "—"
            
            sessions.append({
                "date": date,
                "session": f"#{session_num}",
                "status": status,
                "size_mb": size_mb,
                "duration": duration
            })

        return {"sessions": sessions}

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Active Sessions
def get_sessions(status_filter=None):
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()

        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            out_max = cursor.var(oracledb.NUMBER)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_SESSIONS', [status_filter, out_cursor, out_max])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
            max_total = int(out_max.getvalue()) if out_max.getvalue() is not None else None
        except Exception as pkg_err:
            print(f"Package GET_SESSIONS failed: {pkg_err}. Falling back to direct SQL.")
            if status_filter:
                cursor.execute("""
                    SELECT count(1) AS Session_Count, status
                    FROM v$session
                    WHERE username IS NOT NULL AND status = :1
                    GROUP BY status
                """, [status_filter])
            else:
                cursor.execute("""
                    SELECT count(1) AS Session_Count, status
                    FROM v$session
                    WHERE username IS NOT NULL
                    GROUP BY status
                """)
            rows = cursor.fetchall()
            
            try:
                cursor.execute("SELECT TO_NUMBER(max_utilization) FROM v$resource_limit WHERE resource_name = 'sessions'")
                max_total = cursor.fetchone()[0]
            except:
                max_total = None

        cursor.close()
        conn.close()

        total = 0
        breakdown = []

        for count, status in rows:
            total += count or 0

            breakdown.append({
                "status": status,
                "count": count
            })

        return {
            "total": total,
            "breakdown": breakdown,
            "max_total": max_total if max_total is not None else total
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Active Sessions with CPU Usage (Top 10)
def get_active_sessions_cpu():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()
        rows = []
        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_ACTIVE_SESSIONS_CPU', [out_cursor])
            rows = out_cursor.getvalue().fetchall() if out_cursor.getvalue() else []
        except Exception as pkg_err:
            pass

        if not rows:
            # Fallback to direct SQL query using s.sql_id and s.prev_sql_id
            query = """
                SELECT s.sid, s.serial#, s.username, s.osuser, s.machine,
                       p.spid AS unix_process,
                       (se.value / 100) AS cpu_usage_seconds,
                       COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
                       COALESCE(q1.sql_text, q2.sql_text) AS sql_text,
                       s.blocking_session,
                       s.program,
                       s.module,
                       COALESCE(s.last_call_et, 0) AS active_seconds,
                       s.status
                FROM v$session s
                JOIN v$sesstat se ON s.sid = se.sid
                JOIN v$statname sn ON se.statistic# = sn.statistic#
                LEFT JOIN v$process p ON s.paddr = p.addr
                LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
                LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
                WHERE sn.name = 'CPU used by this session'
                  AND s.username IS NOT NULL
                  AND UPPER(s.username) NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
                ORDER BY cpu_usage_seconds DESC
            """
            cursor.execute(query)
            rows = cursor.fetchall()[:10]

        sessions = []
        max_cpu = 0
        
        for row in rows:
            sid = int(row[0])
            serial = int(row[1])
            username = row[2] or "SYS"
            osuser = row[3] or "oracle"
            machine = row[4] or "N/A"
            unix_process = row[5] or "N/A"
            cpu_usage = float(row[6]) if row[6] else 0.0
            sql_id = row[7]
            raw_sql_text = row[8]
            blocking_session = row[9] if len(row) > 9 else None
            program = row[10] if len(row) > 10 and row[10] else ""
            module = row[11] if len(row) > 11 and row[11] else ""
            is_deadlock = True if blocking_session else False
            
            if cpu_usage > max_cpu:
                max_cpu = cpu_usage

            sql_text = ""
            if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                sql_text = str(raw_sql_text)[:1000]

            # Try secondary lookups if sql_text missing but sql_id exists
            if not sql_text and sql_id and sql_id != "N/A":
                try:
                    cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_fulltext, 1000, 1) FROM v$sql WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                    r = cursor.fetchone()
                    if r and r[0]:
                        sql_text = str(r[0])[:1000]
                    else:
                        cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_text, 1000, 1) FROM dba_hist_sqltext WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                        r2 = cursor.fetchone()
                        if r2 and r2[0]:
                            sql_text = str(r2[0])[:1000]
                except Exception:
                    pass

            if not sql_text or sql_text.strip() in ("", "N/A", "None"):
                if sql_id and sql_id != "N/A":
                    sql_text = f"-- SQL_ID: {sql_id}\n-- Session: SID {sid} (Serial #{serial}) | User: {username}\n-- Machine: {machine} | OS User: {osuser}\n-- Statement executed by session (SQL_ID: {sql_id}):\nSELECT * FROM v$sql WHERE sql_id = '{sql_id}';"
                else:
                    prog_info = f"Program: {program}" if program else "Oracle System Process"
                    mod_info = f" | Module: {module}" if module else ""
                    sql_text = f"-- Session Telemetry Information --\n-- SID: {sid} | Serial#: {serial} | User: {username}\n-- Machine: {machine} | OS User: {osuser} | SPID: {unix_process}\n-- Execution: {prog_info}{mod_info}\n-- CPU Usage: {cpu_usage:.2f} seconds."

            active_sec = float(row[12]) if len(row) > 12 and row[12] is not None else cpu_usage
            if active_sec <= 0:
                active_str = "< 1s"
            elif active_sec < 60:
                active_str = f"{active_sec:.1f}s" if active_sec < 10 else f"{int(active_sec)}s"
            elif active_sec < 3600:
                active_str = f"{int(active_sec // 60)}m {int(active_sec % 60)}s"
            else:
                active_str = f"{int(active_sec // 3600)}h {int((active_sec % 3600) // 60)}m"

            status = str(row[13]).strip().upper() if len(row) > 13 and row[13] else "N/A"

            sessions.append({
                "sid": sid,
                "serial": serial,
                "username": username,
                "osuser": osuser,
                "machine": machine,
                "unix_process": unix_process,
                "cpu_usage": cpu_usage,
                "active_time": active_str,
                "sql_id": sql_id or "N/A",
                "sql_text": sql_text,
                "is_deadlock": is_deadlock,
                "status": status
            })

        cursor.close()
        conn.close()

        return {
            "sessions": sessions,
            "max_cpu": max_cpu
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Top CPU Sessions in 24 Hours
def get_active_sessions_cpu_24h():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()
        rows = []
        
        # 1. Try querying v$active_session_history for 24h metrics
        try:
            # v$active_session_history only ever contains samples of sessions
            # that were ON CPU or actively waiting at the moment of each
            # sample - a truly idle/inactive session is never sampled into
            # it, so ASH itself has no notion of "inactive". s2.status below
            # is instead the session's CURRENT status (right now, ACTIVE or
            # INACTIVE) looked up by rejoining to v$session on sid+serial# -
            # it's NULL if that session has since disconnected.
            ash_query = """
                SELECT h.session_id as sid, h.session_serial# as serial#,
                       NVL(u.username, 'SYS') as username,
                       'oracle' as osuser,
                       NVL(h.machine, 'N/A') as machine,
                       'N/A' as unix_process,
                       ROUND(COUNT(*) * 10 / 100, 2) AS cpu_usage_seconds,
                       h.sql_id,
                       COALESCE(q.sql_text, (SELECT DBMS_LOB.SUBSTR(sql_text, 1000, 1) FROM dba_hist_sqltext WHERE sql_id = h.sql_id AND ROWNUM = 1)) as sql_text,
                       NULL as blocking_session,
                       h.program,
                       h.module,
                       ROUND(COUNT(*) * 10 / 100, 2) as active_seconds,
                       MAX(s2.status) as status
                FROM v$active_session_history h
                LEFT JOIN dba_users u ON h.user_id = u.user_id
                LEFT JOIN v$sqlarea q ON h.sql_id = q.sql_id
                LEFT JOIN v$session s2 ON s2.sid = h.session_id AND s2.serial# = h.session_serial#
                WHERE h.sample_time >= SYSDATE - 1
                  AND UPPER(NVL(u.username, 'SYS')) NOT IN ('SYS', 'SYSTEM')
                GROUP BY h.session_id, h.session_serial#, u.username, h.machine, h.sql_id, q.sql_text, h.program, h.module
                ORDER BY cpu_usage_seconds DESC
            """
            cursor.execute(ash_query)
            rows = cursor.fetchall()[:10]
        except Exception as ash_err:
            pass

        # 2. Fallback to v$session + v$sesstat with 24h filter
        if not rows:
            try:
                fallback_query = """
                    SELECT s.sid, s.serial#, s.username, s.osuser, s.machine,
                           p.spid AS unix_process,
                           (se.value / 100) AS cpu_usage_seconds,
                           COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
                           COALESCE(q1.sql_text, q2.sql_text) AS sql_text,
                           s.blocking_session,
                           s.program,
                           s.module,
                           COALESCE(s.last_call_et, 0) AS active_seconds,
                           s.status
                    FROM v$session s
                    JOIN v$sesstat se ON s.sid = se.sid
                    JOIN v$statname sn ON se.statistic# = sn.statistic#
                    LEFT JOIN v$process p ON s.paddr = p.addr
                    LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
                    LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
                    WHERE sn.name = 'CPU used by this session'
                      AND s.username IS NOT NULL
                      AND UPPER(s.username) NOT IN ('SYS', 'SYSTEM')
                      AND (s.logon_time >= SYSDATE - 1 OR s.last_call_et <= 86400)
                    ORDER BY cpu_usage_seconds DESC
                """
                cursor.execute(fallback_query)
                rows = cursor.fetchall()[:10]
            except Exception as fb_err:
                pass

        sessions = []
        max_cpu = 0
        
        for row in rows:
            sid = int(row[0])
            serial = int(row[1])
            username = row[2] or "SYS"
            osuser = row[3] or "oracle"
            machine = row[4] or "N/A"
            unix_process = row[5] or "N/A"
            cpu_usage = float(row[6]) if row[6] else 0.0
            sql_id = row[7]
            raw_sql_text = row[8]
            blocking_session = row[9] if len(row) > 9 else None
            program = row[10] if len(row) > 10 and row[10] else ""
            module = row[11] if len(row) > 11 and row[11] else ""
            is_deadlock = True if blocking_session else False
            
            if cpu_usage > max_cpu:
                max_cpu = cpu_usage

            sql_text = ""
            if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                sql_text = str(raw_sql_text)[:1000]

            # Try secondary lookups if sql_text missing but sql_id exists
            if not sql_text and sql_id and sql_id != "N/A":
                try:
                    cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_fulltext, 1000, 1) FROM v$sql WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                    r = cursor.fetchone()
                    if r and r[0]:
                        sql_text = str(r[0])[:1000]
                    else:
                        cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_text, 1000, 1) FROM dba_hist_sqltext WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                        r2 = cursor.fetchone()
                        if r2 and r2[0]:
                            sql_text = str(r2[0])[:1000]
                except Exception:
                    pass

            if not sql_text or sql_text.strip() in ("", "N/A", "None"):
                if sql_id and sql_id != "N/A":
                    sql_text = f"-- SQL_ID: {sql_id}\n-- Session: SID {sid} (Serial #{serial}) | User: {username}\n-- Machine: {machine} | OS User: {osuser}\n-- Statement executed by session (SQL_ID: {sql_id}):\nSELECT * FROM v$sql WHERE sql_id = '{sql_id}';"
                else:
                    prog_info = f"Program: {program}" if program else "Oracle System Process"
                    mod_info = f" | Module: {module}" if module else ""
                    sql_text = f"-- Session Telemetry Information --\n-- SID: {sid} | Serial#: {serial} | User: {username}\n-- Machine: {machine} | OS User: {osuser} | SPID: {unix_process}\n-- Execution: {prog_info}{mod_info}\n-- CPU Usage: {cpu_usage:.2f} seconds over past 24 hours."

            active_sec = float(row[12]) if len(row) > 12 and row[12] is not None else cpu_usage
            if active_sec <= 0:
                active_str = "< 1s"
            elif active_sec < 60:
                active_str = f"{active_sec:.1f}s" if active_sec < 10 else f"{int(active_sec)}s"
            elif active_sec < 3600:
                active_str = f"{int(active_sec // 60)}m {int(active_sec % 60)}s"
            else:
                active_str = f"{int(active_sec // 3600)}h {int((active_sec % 3600) // 60)}m"

            # For the ASH path this is the session's CURRENT status (it may
            # have logged off since the 24h sample), not a historical value -
            # "N/A" means it's no longer connected.
            status = str(row[13]).strip().upper() if len(row) > 13 and row[13] else "N/A"

            sessions.append({
                "sid": sid,
                "serial": serial,
                "username": username,
                "osuser": osuser,
                "machine": machine,
                "unix_process": unix_process,
                "cpu_usage": cpu_usage,
                "active_time": active_str,
                "sql_id": sql_id or "N/A",
                "sql_text": sql_text,
                "is_deadlock": is_deadlock,
                "status": status
            })

        cursor.close()
        conn.close()

        return {
            "sessions": sessions,
            "max_cpu": max_cpu
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Active Sessions Memory (Top SGA/UGA & PGA - Non-SYS Users)
def get_active_sessions_memory():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()
        top_sga = []
        top_pga = []

        try:
            uga_cursor = cursor.var(oracledb.CURSOR)
            pga_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_TOP_MEMORY_SESSIONS', [uga_cursor, pga_cursor])
            
            uga_rows = uga_cursor.getvalue().fetchall() if uga_cursor.getvalue() else []
            for row in uga_rows:
                sid = row[0]
                uname = str(row[1] or "").strip()
                if not uname or uname.upper() in ('SYS', 'SYSTEM'):
                    continue
                mem = float(row[2]) if row[2] else 0.0
                sql_id = row[3] if len(row) > 3 else None
                raw_sql_text = row[4] if len(row) > 4 else None
                status = str(row[5]).strip().upper() if len(row) > 5 and row[5] else "N/A"

                sql_text = ""
                if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                    sql_text = str(raw_sql_text)[:1000]

                if not sql_text and sql_id and sql_id != "N/A":
                    try:
                        cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_fulltext, 1000, 1) FROM v$sql WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                        r = cursor.fetchone()
                        if r and r[0]:
                            sql_text = str(r[0])[:1000]
                    except Exception:
                        pass

                if not sql_text:
                    if sql_id and sql_id != "N/A":
                        sql_text = f"-- SQL_ID: {sql_id}\nSELECT * FROM v$sql WHERE sql_id = '{sql_id}';"
                    else:
                        sql_text = f"-- Session: SID {sid} | User: {uname}"

                top_sga.append({
                    "sid": sid,
                    "username": uname,
                    "label": f"{sid} {uname}",
                    "memory_mb": round(mem, 1),
                    "sql_text": sql_text,
                    "status": status
                })

            pga_rows = pga_cursor.getvalue().fetchall() if pga_cursor.getvalue() else []
            for row in pga_rows:
                sid = row[0]
                uname = str(row[1] or "").strip()
                if not uname or uname.upper() in ('SYS', 'SYSTEM'):
                    continue
                mem = float(row[2]) if row[2] else 0.0
                sql_id = row[3] if len(row) > 3 else None
                raw_sql_text = row[4] if len(row) > 4 else None
                status = str(row[5]).strip().upper() if len(row) > 5 and row[5] else "N/A"

                sql_text = ""
                if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                    sql_text = str(raw_sql_text)[:1000]

                if not sql_text and sql_id and sql_id != "N/A":
                    try:
                        cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_fulltext, 1000, 1) FROM v$sql WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                        r = cursor.fetchone()
                        if r and r[0]:
                            sql_text = str(r[0])[:1000]
                    except Exception:
                        pass

                if not sql_text:
                    if sql_id and sql_id != "N/A":
                        sql_text = f"-- SQL_ID: {sql_id}\nSELECT * FROM v$sql WHERE sql_id = '{sql_id}';"
                    else:
                        sql_text = f"-- Session: SID {sid} | User: {uname}"

                top_pga.append({
                    "sid": sid,
                    "username": uname,
                    "label": f"{sid} {uname}",
                    "memory_mb": round(mem, 1),
                    "sql_text": sql_text,
                    "status": status
                })
        except Exception:
            pass

        # Fallback queries if package is not present or cursors returned empty/SYS-only
        if not top_sga:
            try:
                uga_fallback_query = """
                    SELECT s.sid, s.username,
                           ROUND(sn.value / (1024 * 1024), 2) AS memory_mb,
                           COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
                           COALESCE(q1.sql_text, q2.sql_text) AS sql_text,
                           s.status
                    FROM v$session s
                    JOIN v$sesstat sn ON s.sid = sn.sid
                    JOIN v$statname n ON sn.statistic# = n.statistic#
                    LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
                    LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
                    WHERE n.name = 'session uga memory'
                      AND s.username IS NOT NULL
                      AND UPPER(s.username) NOT IN ('SYS', 'SYSTEM')
                      AND sn.value > 0
                    ORDER BY sn.value DESC
                """
                cursor.execute(uga_fallback_query)
                for row in cursor.fetchall()[:3]:
                    sid = row[0]
                    uname = str(row[1] or "").strip()
                    if not uname or uname.upper() in ('SYS', 'SYSTEM'):
                        continue
                    mem = float(row[2]) if row[2] else 0.0
                    sql_id = row[3]
                    raw_sql_text = row[4]
                    status = str(row[5]).strip().upper() if len(row) > 5 and row[5] else "N/A"

                    sql_text = ""
                    if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                        sql_text = str(raw_sql_text)[:1000]

                    if not sql_text and sql_id and sql_id != "N/A":
                        try:
                            cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_fulltext, 1000, 1) FROM v$sql WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                            r = cursor.fetchone()
                            if r and r[0]:
                                sql_text = str(r[0])[:1000]
                        except Exception:
                            pass

                    if not sql_text:
                        if sql_id and sql_id != "N/A":
                            sql_text = f"-- SQL_ID: {sql_id}\nSELECT * FROM v$sql WHERE sql_id = '{sql_id}';"
                        else:
                            sql_text = f"-- Session: SID {sid} | User: {uname}"

                    top_sga.append({
                        "sid": sid,
                        "username": uname,
                        "label": f"{sid} {uname}",
                        "memory_mb": round(mem, 1),
                        "sql_text": sql_text,
                        "status": status
                    })
            except Exception:
                pass

        if not top_pga:
            try:
                pga_fallback_query = """
                    SELECT s.sid, s.username,
                           ROUND(p.pga_alloc_mem / (1024 * 1024), 2) AS memory_mb,
                           COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
                           COALESCE(q1.sql_text, q2.sql_text) AS sql_text,
                           s.status
                    FROM v$session s
                    JOIN v$process p ON s.paddr = p.addr
                    LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
                    LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
                    WHERE s.username IS NOT NULL
                      AND UPPER(s.username) NOT IN ('SYS', 'SYSTEM')
                      AND p.pga_alloc_mem > 0
                    ORDER BY p.pga_alloc_mem DESC
                """
                cursor.execute(pga_fallback_query)
                for row in cursor.fetchall()[:3]:
                    sid = row[0]
                    uname = str(row[1] or "").strip()
                    if not uname or uname.upper() in ('SYS', 'SYSTEM'):
                        continue
                    mem = float(row[2]) if row[2] else 0.0
                    sql_id = row[3]
                    raw_sql_text = row[4]
                    status = str(row[5]).strip().upper() if len(row) > 5 and row[5] else "N/A"

                    sql_text = ""
                    if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                        sql_text = str(raw_sql_text)[:1000]
                    
                    if not sql_text and sql_id and sql_id != "N/A":
                        try:
                            cursor.execute("SELECT DBMS_LOB.SUBSTR(sql_fulltext, 1000, 1) FROM v$sql WHERE sql_id = :1 AND ROWNUM = 1", [sql_id])
                            r = cursor.fetchone()
                            if r and r[0]:
                                sql_text = str(r[0])[:1000]
                        except Exception:
                            pass
                    
                    if not sql_text:
                        if sql_id and sql_id != "N/A":
                            sql_text = f"-- SQL_ID: {sql_id}\nSELECT * FROM v$sql WHERE sql_id = '{sql_id}';"
                        else:
                            sql_text = f"-- Session: SID {sid} | User: {uname}"

                    top_pga.append({
                        "sid": sid,
                        "username": uname,
                        "label": f"{sid} {uname}",
                        "memory_mb": round(mem, 1),
                        "sql_text": sql_text,
                        "status": status
                    })
            except Exception:
                pass

        cursor.close()
        conn.close()

        if not top_pga and not top_sga:
            return {
                "status": "no_data",
                "message": "No session memory statistics available.",
                "sessions": []
            }

        return {
            "top_sga": top_sga,
            "top_pga": top_pga
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ✅ Get Database Growth
def get_database_growth():
    try:
        # First get current database info
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()
        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_DATABASE_GROWTH', [out_cursor])
            row = out_cursor.getvalue().fetchone() if out_cursor.getvalue() else None
        except Exception as pkg_err:
            print(f"Package GET_DATABASE_GROWTH failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                 SELECT
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
                  GROUP BY d.name
            """)
            row = cursor.fetchone()

        cursor.close()
        conn.close()

        if not row:
            return {"error": "No growth data found"}

        creation_time, database_name, database_size_mb, used_space_mb, used_percent, free_space_mb, free_percent = row

        # Now calculate growth rates from historical data
        growth_history_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'growth_history.json')
        
        growth_rates = {"growth_day_mb": 0, "growth_day_percent": 0, "growth_week_mb": 0, "growth_week_percent": 0, 
                       "growth_month_mb": 0, "growth_month_percent": 0, "growth_year_mb": 0, "growth_year_percent": 0}
        
        historical_data = {"7_days": {"labels": [], "total": []}, "30_days": {"labels": [], "total": []}, 
                          "90_days": {"labels": [], "total": []}, "1_year": {"labels": [], "total": []}}
        
        if os.path.exists(growth_history_file):
            try:
                with open(growth_history_file, 'r') as f:
                    history_data = json.load(f)
                
                if history_data:
                    # Sort timestamps
                    timestamps = sorted(history_data.keys())
                    
                    # Process data for different time periods
                    now = datetime.now()
                    
                    # 7 days
                    seven_days_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
                    seven_days_data = []
                    for ts in timestamps:
                        ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        if ts_dt >= seven_days_ago:
                            seven_days_data.append((ts_dt, history_data[ts]['used_gb']))
                    
                    if len(seven_days_data) > 1:
                        seven_days_data.sort(key=lambda x: x[0])
                        historical_data["7_days"]["labels"] = [dt.strftime("%m/%d") for dt, _ in seven_days_data]
                        historical_data["7_days"]["total"] = [gb for _, gb in seven_days_data]
                    
                    # 30 days
                    thirty_days_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
                    thirty_days_data = []
                    for ts in timestamps:
                        ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        if ts_dt >= thirty_days_ago:
                            thirty_days_data.append((ts_dt, history_data[ts]['used_gb']))
                    
                    if len(thirty_days_data) > 1:
                        thirty_days_data.sort(key=lambda x: x[0])
                        historical_data["30_days"]["labels"] = [dt.strftime("%m/%d") for dt, _ in thirty_days_data]
                        historical_data["30_days"]["total"] = [gb for _, gb in thirty_days_data]
                    
                    # 90 days
                    ninety_days_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=90)
                    ninety_days_data = []
                    for ts in timestamps:
                        ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        if ts_dt >= ninety_days_ago:
                            ninety_days_data.append((ts_dt, history_data[ts]['used_gb']))
                    
                    if len(ninety_days_data) > 1:
                        ninety_days_data.sort(key=lambda x: x[0])
                        historical_data["90_days"]["labels"] = [dt.strftime("%m/%d") for dt, _ in ninety_days_data]
                        historical_data["90_days"]["total"] = [gb for _, gb in ninety_days_data]
                    
                    # 1 year
                    one_year_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=365)
                    one_year_data = []
                    for ts in timestamps:
                        ts_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        if ts_dt >= one_year_ago:
                            one_year_data.append((ts_dt, history_data[ts]['used_gb']))
                    
                    if len(one_year_data) > 1:
                        one_year_data.sort(key=lambda x: x[0])
                        historical_data["1_year"]["labels"] = [dt.strftime("%Y-%m") for dt, _ in one_year_data]
                        historical_data["1_year"]["total"] = [gb for _, gb in one_year_data]
                    
                    # Calculate growth rates from the last two data points
                    if len(timestamps) >= 2:
                        first_timestamp = timestamps[0]
                        last_timestamp = timestamps[-1]
                        
                        first_time = datetime.fromisoformat(first_timestamp.replace('Z', '+00:00'))
                        last_time = datetime.fromisoformat(last_timestamp.replace('Z', '+00:00'))
                        
                        time_diff_days = (last_time - first_time).total_seconds() / (24 * 3600)
                        
                        if time_diff_days > 0:
                            first_used_gb = history_data[first_timestamp].get('used_gb', 0)
                            last_used_gb = history_data[last_timestamp].get('used_gb', 0)
                            
                            growth_gb_per_day = (last_used_gb - first_used_gb) / time_diff_days
                            growth_mb_per_day = growth_gb_per_day * 1024
                            
                            if first_used_gb > 0:
                                growth_percent_per_day = (growth_gb_per_day / first_used_gb) * 100
                            else:
                                growth_percent_per_day = 0
                            
                            growth_rates["growth_day_mb"] = round(growth_mb_per_day, 2)
                            growth_rates["growth_day_percent"] = round(growth_percent_per_day, 3)
                            growth_rates["growth_week_mb"] = round(growth_mb_per_day * 7, 2)
                            growth_rates["growth_week_percent"] = round(growth_percent_per_day * 7, 3)
                            growth_rates["growth_month_mb"] = round(growth_mb_per_day * 30, 2)
                            growth_rates["growth_month_percent"] = round(growth_percent_per_day * 30, 3)
                            growth_rates["growth_year_mb"] = round(growth_mb_per_day * 365, 2)
                            growth_rates["growth_year_percent"] = round(growth_percent_per_day * 365, 3)
            
            except Exception as e:
                if 'conn' in locals() and conn:
                    try: conn.close()
                    except: pass
                print(f"Error reading growth history: {e}")
                # Fall back to creation time based calculation
                if creation_time:
                    days_since_creation = (datetime.now() - creation_time).days
                    if days_since_creation > 0:
                        growth_mb_per_day = used_space_mb / days_since_creation
                        growth_rates["growth_day_mb"] = round(growth_mb_per_day, 2)
                        growth_rates["growth_day_percent"] = round((growth_mb_per_day / database_size_mb) * 100, 3) if database_size_mb > 0 else 0
                        growth_rates["growth_week_mb"] = round(growth_mb_per_day * 7, 2)
                        growth_rates["growth_week_percent"] = round(growth_rates["growth_day_percent"] * 7, 3)
                        growth_rates["growth_month_mb"] = round(growth_mb_per_day * 30, 2)
                        growth_rates["growth_month_percent"] = round(growth_rates["growth_day_percent"] * 30, 3)
                        growth_rates["growth_year_mb"] = round(growth_mb_per_day * 365, 2)
                        growth_rates["growth_year_percent"] = round(growth_rates["growth_day_percent"] * 365, 3)

        creation_time_str = creation_time.strftime("%d %b %Y") if creation_time else None

        return {
            "creation_time": creation_time_str,
            "database_name": database_name,
            "database_size_mb": float(database_size_mb) if database_size_mb is not None else 0,
            "used_space_mb": float(used_space_mb) if used_space_mb is not None else 0,
            "used_percent": float(used_percent) if used_percent is not None else 0,
            "free_space_mb": float(free_space_mb) if free_space_mb is not None else 0,
            "free_percent": float(free_percent) if free_percent is not None else 0,
            **growth_rates,
            "historical_data": historical_data
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


def get_db_growth():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()
        
        try:
            out_cursor = cursor.var(oracledb.CURSOR)
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_DB_GROWTH', [out_cursor])
            row = out_cursor.getvalue().fetchone() if out_cursor.getvalue() else None
        except Exception as pkg_err:
            print(f"Package GET_DB_GROWTH failed: {pkg_err}. Falling back to direct SQL.")
            cursor.execute("""
                SELECT
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
                 GROUP BY d.name
            """)
            row = cursor.fetchone()

        cursor.close()
        conn.close()

        if not row:
            return {"error": "No growth data found"}

        database_name, total_mb, used_mb, growth_day_mb = row
        total_mb = float(total_mb or 0)
        used_mb = float(used_mb or 0)
        growth_day_mb = float(growth_day_mb or 0)

        used_percent = round((used_mb / total_mb * 100), 2) if total_mb > 0 else 0.0
        growth_week_mb = round(growth_day_mb * 7, 2)
        growth_month_mb = round(growth_day_mb * 30, 2)
        growth_year_mb = round(growth_day_mb * 365, 2)

        return {
            "database_name": database_name,
            "total_mb": round(total_mb, 2),
            "used_mb": round(used_mb, 2),
            "used_percent": used_percent,
            "growth_day_mb": round(growth_day_mb, 2),
            "growth_week_mb": growth_week_mb,
            "growth_month_mb": growth_month_mb,
            "growth_year_mb": growth_year_mb
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)} 

# ✅ Get SGA and PGA Memory Info
def get_memory_info():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()

        try:
            out_sga_info = cursor.var(oracledb.CURSOR)
            out_sga_free = cursor.var(oracledb.NUMBER)
            out_pga_stats = cursor.var(oracledb.CURSOR)
            out_pga_components = cursor.var(oracledb.CURSOR)
            
            cursor.callproc('GREENWORLD_MONITOR_PKG.GET_MEMORY_INFO', [
                out_sga_info, out_sga_free, out_pga_stats, out_pga_components
            ])
            
            sga_info = {}
            if out_sga_info.getvalue():
                for row in out_sga_info.getvalue().fetchall():
                    sga_info[row[0]] = float(row[1])
                    
            sga_free_val = out_sga_free.getvalue()
            sga_free = float(sga_free_val) if sga_free_val else 0
            
            pga_stats = {}
            if out_pga_stats.getvalue():
                for row in out_pga_stats.getvalue().fetchall():
                    pga_stats[row[0]] = float(row[1])
                    
            pga_components = {}
            if out_pga_components.getvalue():
                for row in out_pga_components.getvalue().fetchall():
                    pga_components[row[0]] = float(row[1])
        except Exception as pkg_err:
            print(f"Package GET_MEMORY_INFO failed: {pkg_err}. Falling back to direct SQL.")
            sga_info = {}
            try:
                cursor.execute("SELECT name, bytes FROM v$sgainfo")
                for row in cursor.fetchall():
                    sga_info[row[0]] = float(row[1])
            except Exception as e:
                print("Direct SGA info query failed:", e)

            sga_free = 0.0
            try:
                cursor.execute("SELECT SUM(bytes) FROM v$sgastat WHERE name = 'free memory'")
                row = cursor.fetchone()
                if row and row[0] is not None:
                    sga_free = float(row[0])
            except Exception as e:
                print("Direct SGA free query failed:", e)

            pga_stats = {}
            try:
                cursor.execute("SELECT name, value FROM v$pgastat")
                for row in cursor.fetchall():
                    pga_stats[row[0]] = float(row[1])
            except Exception as e:
                print("Direct PGA stats query failed:", e)

            pga_components = {}
            try:
                cursor.execute("SELECT category, sum(allocated) as allocated FROM v$process_memory GROUP BY category")
                for row in cursor.fetchall():
                    pga_components[row[0]] = float(row[1])
            except Exception as e:
                print("Direct PGA components query failed:", e)

        cursor.close()
        conn.close()

        # Format SGA
        sga_total = sga_info.get("Maximum SGA Size", 0)
        sga_used = max(0, sga_total - sga_free)
        sga_data = {
            "total_bytes": sga_total,
            "free_bytes": sga_free,
            "used_bytes": sga_used,
            "components": {
                "Buffer cache": sga_info.get("Buffer Cache Size", 0),
                "Shared pool": sga_info.get("Shared Pool Size", 0),
                "Large pool": sga_info.get("Large Pool Size", 0),
                "Java pool": sga_info.get("Java Pool Size", 0),
                "Redo log buffer": sga_info.get("Redo Buffers", 0),
                "Fixed SGA": sga_info.get("Fixed SGA Size", 0)
            }
        }

        # Format PGA
        pga_total = pga_stats.get("aggregate PGA target parameter", 0)
        pga_allocated = pga_stats.get("total PGA allocated", 0)
        pga_used = pga_stats.get("total PGA inuse", 0)
        pga_free = pga_stats.get("total freeable PGA memory", 0)
        
        if pga_total < pga_allocated:
            pga_total = pga_allocated

        pga_free_calculated = max(0, pga_total - pga_allocated + pga_free)
        
        pga_data = {
            "total_bytes": pga_total,
            "free_bytes": pga_free_calculated,
            "used_bytes": pga_used,
            "components": {
                "SQL": pga_components.get("SQL", 0),
                "PL/SQL": pga_components.get("PL/SQL", 0),
                "Freeable": pga_components.get("Freeable", 0),
                "Other": pga_components.get("Other", 0)
            }
        }

        return {
            "sga": sga_data,
            "pga": pga_data
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        print(f"Memory info error: {e}")
        return {"error": str(e)}

# ✅ Get Archive Log Info
def get_archive_log_info():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database"}

        cursor = conn.cursor()
        
        # Default empty dictionary structure matching existing API contract
        archive_info = {
            "total_gb": 0.0,
            "total_count": 0,
            "archive_logs": 0,
            "size_gb": 0.0,
            "last_24h_gb": 0.0,
            "last_24h_count": 0,
            "fra_dest_param": "N/A",
            "fra_limit_gb": 0.0,
            "fra_used_gb": 0.0,
            "fra_percent": 0.0,
            "fra_free_gb": 0.0,
            "log_mode": "UNKNOWN",
            "archive_location": "N/A"
        }

        try:
            # 1. Detection Query
            cursor.execute("""
                SELECT destination
                FROM v$archive_dest
                WHERE dest_id = 1
                ORDER BY dest_id
            """)
            dest_row = cursor.fetchone()
            destination = str(dest_row[0] or "").strip() if dest_row else ""
            
            archive_info["archive_location"] = destination if destination else "N/A"

            if destination.upper() == "USE_DB_RECOVERY_FILE_DEST":
                # Case 2 - Fast Recovery Area (FRA)
                cursor.execute("""
                    SELECT
                        name,
                        ROUND(space_limit / 1024 / 1024 / 1024, 2) AS allocated_gb,
                        ROUND(space_used / 1024 / 1024 / 1024, 2) AS used_gb,
                        ROUND(space_reclaimable / 1024 / 1024 / 1024, 2) AS reclaimable_gb,
                        ROUND((space_used / space_limit) * 100, 2) AS pct_used
                    FROM v$recovery_file_dest
                """)
                fra_row = cursor.fetchone()
                if fra_row:
                    name = str(fra_row[0] or "").strip()
                    allocated_gb = float(fra_row[1] or 0.0)
                    used_gb = float(fra_row[2] or 0.0)
                    reclaimable_gb = float(fra_row[3] or 0.0)
                    pct_used = float(fra_row[4] or 0.0)
                    
                    archive_info["fra_dest_param"] = name if name else "USE_DB_RECOVERY_FILE_DEST"
                    archive_info["fra_limit_gb"] = allocated_gb
                    archive_info["fra_used_gb"] = used_gb
                    archive_info["fra_percent"] = pct_used
                    archive_info["fra_free_gb"] = round(max(0.0, allocated_gb - used_gb + reclaimable_gb), 2)
                    
                    # Resolve to the actual folder path of the Fast Recovery Area instead of displaying parameter keyword
                    if name:
                        archive_info["archive_location"] = name

                # Additionally query archived logs details
                cursor.execute("""
                    SELECT
                        COUNT(*) AS archive_logs,
                        ROUND(SUM(blocks * block_size) / 1024 / 1024 / 1024, 2) AS size_gb
                    FROM v$archived_log
                    WHERE deleted = 'NO'
                """)
                fs_row = cursor.fetchone()
                if fs_row:
                    archive_logs = int(fs_row[0] or 0)
                    size_gb = float(fs_row[1] or 0.0)
                    
                    archive_info["archive_logs"] = archive_logs
                    archive_info["total_count"] = archive_logs
                    archive_info["size_gb"] = size_gb
                    archive_info["total_gb"] = size_gb
            else:
                # Case 1 - Filesystem Archive Destination
                cursor.execute("""
                    SELECT
                        COUNT(*) AS archive_logs,
                        ROUND(SUM(blocks * block_size) / 1024 / 1024 / 1024, 2) AS size_gb
                    FROM v$archived_log
                    WHERE deleted = 'NO'
                """)
                fs_row = cursor.fetchone()
                if fs_row:
                    archive_logs = int(fs_row[0] or 0)
                    size_gb = float(fs_row[1] or 0.0)
                    
                    archive_info["archive_logs"] = archive_logs
                    archive_info["total_count"] = archive_logs
                    archive_info["size_gb"] = size_gb
                    archive_info["total_gb"] = size_gb

        except Exception as query_err:
            cursor.close()
            conn.close()
            return {"error": str(query_err)}

        cursor.close()
        conn.close()
        return archive_info

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}

# ✅ Get Blocking/Waiting Sessions Info
def get_blocking_sessions():
    try:
        conn = get_connection()
        if not conn:
            return {"error": "Cannot connect to database", "blocks": []}

        cursor = conn.cursor()
        blocks = []
        try:
            cursor.execute("""
                SELECT 
                    s2.username AS waiting_username, 
                    s2.sid AS waiting_sid, 
                    s1.username AS blocking_username, 
                    s1.sid AS blocking_sid,
                    s2.seconds_in_wait
                FROM v$session s1
                JOIN v$session s2 ON s1.sid = s2.blocking_session
                WHERE s2.blocking_session IS NOT NULL
            """)
            for row in cursor.fetchall():
                blocks.append({
                    "waiting_username": row[0] or "SYSTEM",
                    "waiting_sid": int(row[1]),
                    "blocking_username": row[2] or "SYSTEM",
                    "blocking_sid": int(row[3]),
                    "seconds_in_wait": int(row[4] or 0)
                })
        except Exception as query_err:
            print(f"Blocking sessions query failed: {query_err}")
            
        cursor.close()
        conn.close()
        return {"blocks": blocks}

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        print(f"Blocking sessions error: {e}")
        return {"error": str(e), "blocks": []}


# ✅ Get Oracle Database Processes (V$PROCESS & V$SESSION User Processes)
def get_oracle_database_processes(db_id=None):
    """
    Returns V$SESSION/V$PROCESS user telemetry for a database.

    db_id=None (default) preserves the EXACT existing behavior used by the
    dashboard's Oracle Database Processes widget: it operates on the
    currently active/selected database via get_connection(). Passing an
    explicit db_id (used by the homepage's per-server aggregation) queries
    that specific database via get_connection_by_id() instead - same query,
    same filtering rules, just a different connection target.
    """
    try:
        conn = get_connection_by_id(db_id) if db_id else get_connection()
        if not conn:
            return {
                "status": "error",
                "message": "Unable to retrieve Oracle Database Processes.",
                "processes": []
            }

        cursor = conn.cursor()
        processes = []
        try:
            query = """
                SELECT
                    TRIM(p.addr) AS process_addr,
                    TRIM(p.spid) AS oracle_pid,
                    s.username AS username,
                    COALESCE(s.osuser, p.username, 'N/A') AS os_user,
                    COALESCE(s.machine, 'N/A') AS machine,
                    COALESCE(s.program, p.program, 'N/A') AS program,
                    COALESCE(s.module, 'N/A') AS module,
                    COALESCE(s.status, 'INACTIVE') AS status,
                    COALESCE(s.sid, 0) AS sid,
                    COALESCE(s.serial#, 0) AS serial,
                    NVL(TO_CHAR(s.logon_time, 'YYYY-MM-DD HH24:MI:SS'), 'N/A') AS logon_time,
                    COALESCE(s.sql_id, s.prev_sql_id) AS sql_id,
                    COALESCE(q1.sql_text, q2.sql_text) AS sql_text,
                    COALESCE(cpu.value, 0) AS cpu_centiseconds,
                    COALESCE(p.pga_used_mem, 0) AS pga_used_mem
                FROM v$process p
                LEFT JOIN v$session s ON p.addr = s.paddr
                LEFT JOIN v$sqlarea q1 ON s.sql_id = q1.sql_id
                LEFT JOIN v$sqlarea q2 ON s.prev_sql_id = q2.sql_id
                LEFT JOIN v$sesstat cpu ON cpu.sid = s.sid
                    AND cpu.statistic# = (SELECT statistic# FROM v$statname WHERE name = 'CPU used by this session')
                WHERE p.spid IS NOT NULL
                  AND (p.pname IS NULL OR UPPER(p.pname) NOT IN ('PMON','DBW0','DBWR','LGWR','CKPT','SMON','RECO','MMON','MMNL','VKTM','DIA0'))
                  AND (p.pname IS NULL OR NOT REGEXP_LIKE(p.pname, '^[DS][0-9]{3}$'))
                  AND (s.type IS NULL OR UPPER(s.type) != 'BACKGROUND')
                  AND (s.username IS NULL OR UPPER(s.username) NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC'))
                ORDER BY TO_NUMBER(REGEXP_SUBSTR(p.spid, '^[0-9]+')) ASC NULLS LAST, p.spid ASC
            """
            cursor.execute(query)
            seen_identities = set()
            for row in cursor.fetchall():
                addr_val = str(row[0]).strip() if row[0] is not None else ""
                spid_val = str(row[1]).strip() if row[1] is not None else "N/A"
                # A NULL username means v$process had no matching v$session row
                # (its client-side session is already gone but the OS process
                # hasn't been reaped). That row is still a real process worth
                # showing, so it displays like any other row with missing data -
                # only an actual SYS/SYSTEM-family username gets excluded here.
                uname_raw = str(row[2]).strip() if row[2] is not None else ""
                if uname_raw and uname_raw.upper() in ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC'):
                    continue
                uname = uname_raw if uname_raw else "N/A"

                sid_val = int(row[8]) if row[8] is not None else 0
                serial_val = int(row[9]) if row[9] is not None else 0
                logon_val = str(row[10]) if row[10] is not None else "N/A"
                sql_id_val = str(row[11]) if len(row) > 11 and row[11] else "N/A"
                raw_sql_text = row[12] if len(row) > 12 and row[12] else None
                cpu_centiseconds = float(row[13]) if len(row) > 13 and row[13] is not None else 0.0
                pga_used_bytes = float(row[14]) if len(row) > 14 and row[14] is not None else 0.0
                cpu_seconds = round(cpu_centiseconds / 100.0, 2)
                memory_mb = round(pga_used_bytes / (1024 * 1024), 2)

                # A single OS process address (p.addr) can legitimately match more
                # than one v$session row under shared-server, so PID/SID/Serial#
                # alone are each too weak to dedupe on. The process address plus
                # SID+Serial# together identify one true process/session pairing;
                # only an exact repeat of that composite key is a real duplicate.
                identity_key = (addr_val, sid_val, serial_val)
                if identity_key in seen_identities:
                    continue
                seen_identities.add(identity_key)

                sql_text = ""
                if raw_sql_text and str(raw_sql_text).strip() not in ("", "N/A", "None"):
                    sql_text = str(raw_sql_text)[:1000]

                if not sql_text or sql_text.strip() in ("", "N/A", "None"):
                    if sql_id_val and sql_id_val != "N/A":
                        sql_text = f"-- SQL_ID: {sql_id_val}\n-- Process PID: {spid_val} | Session: SID {sid_val} (Serial #{serial_val}) | User: {uname}\n-- Machine: {row[4]} | OS User: {row[3]}\nSELECT * FROM v$sql WHERE sql_id = '{sql_id_val}';"
                    else:
                        sql_text = f"-- Oracle Process Telemetry Information --\n-- Process PID: {spid_val} | SID: {sid_val} | Serial#: {serial_val}\n-- User: {uname} | OS User: {row[3]} | Machine: {row[4]}\n-- Program: {row[5]} | Module: {row[6]}\n-- Status: {row[7]} | Logon Time: {logon_val}\n-- No active SQL statement executing at this moment."

                processes.append({
                    "oracle_pid": spid_val,
                    "username": uname,
                    "os_user": str(row[3]) if row[3] is not None else "N/A",
                    "machine": str(row[4]) if row[4] is not None else "N/A",
                    "program": str(row[5]) if row[5] is not None else "N/A",
                    "module": str(row[6]) if row[6] is not None else "N/A",
                    "status": str(row[7] or "INACTIVE").upper(),
                    "sid": sid_val,
                    "serial": serial_val,
                    "logon_time": logon_val,
                    "sql_id": sql_id_val,
                    "sql_text": sql_text,
                    "cpu_seconds": cpu_seconds,
                    "memory_mb": memory_mb
                })
        except Exception as query_err:
            print(f"Oracle database processes query failed: {query_err}")
            cursor.close()
            conn.close()
            return {
                "status": "error",
                "message": "Unable to retrieve Oracle Database Processes.",
                "processes": []
            }

        cursor.close()
        conn.close()
        return {
            "status": "success",
            "processes": processes
        }

    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        print(f"Oracle database processes error: {e}")
        return {
            "status": "error",
            "message": "Unable to retrieve Oracle Database Processes.",
            "processes": []
        }


# ✅ Oracle Server Processes - Homepage aggregation, grouped by unique host
#
# Unlike the dashboard's Oracle Database Processes widget (get_oracle_database_
# processes / V$SESSION & V$PROCESS, untouched above), the homepage widget
# shows OS-level "top Oracle processes by CPU" for each host, fetched over
# SSH with EXACTLY this command (see get_top_oracle_os_processes_for_host):
#   ps -eo pid,user,ni,vsz,rss,state,pcpu,pmem,time,comm --sort=-pcpu |
#   awk 'NR==1 {print; next} $2=="oracle" {print; count++; if(count==10) exit}'
# This is run once per unique host (OS processes aren't per-database), and
# already returns the header line plus at most the top 10 rows owned by the
# "oracle" OS user, pre-sorted by %CPU - no further client-side Top N
# slicing is needed.
_all_oracle_processes_cache = {"timestamp": 0, "data": None}
_all_oracle_processes_lock = threading.Lock()

def get_all_servers_oracle_processes():
    now = time.time()
    with _all_oracle_processes_lock:
        cached = _all_oracle_processes_cache["data"]
        if cached and (now - _all_oracle_processes_cache["timestamp"] < 30):
            return cached

    try:
        data = get_all_servers_oracle_processes_raw()
        with _all_oracle_processes_lock:
            _all_oracle_processes_cache["timestamp"] = time.time()
            _all_oracle_processes_cache["data"] = data
        return data
    except Exception as e:
        logging.error(f"Error fetching all-servers Oracle processes: {e}")
        return {"status": "error", "message": str(e), "servers": {}}


def _parse_top_oracle_ps_output(raw):
    """
    Parses the output of the EXACT command run by
    get_top_oracle_os_processes_for_host:
        ps -eo pid,user,ni,vsz,rss,state,pcpu,pmem,time,comm --sort=-pcpu |
        awk 'NR==1 {print; next} $2=="oracle" {print; count++; if(count==10) exit}'

    Line 1 is always the `ps` column header (kept by the awk NR==1 clause
    regardless of its own content), every following line has already been
    filtered by awk to OS user 'oracle' ($2) and capped at 10 rows, ranked
    by %CPU descending (from `ps --sort=-pcpu`). The USER check below is
    kept as a defensive no-op in case the header line's shape ever changes.
    """
    processes = []
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        return processes

    data_lines = lines[1:] if lines[0].strip().split()[:1] == ["PID"] else lines
    for line in data_lines:
        parts = line.strip().split(None, 9)
        if len(parts) < 10:
            continue
        pid, user, ni, vsz, rss, state, pcpu, pmem, cpu_time, comm = parts
        if user.strip().lower() != "oracle":
            continue
        try:
            cpu_percent = float(pcpu)
        except ValueError:
            cpu_percent = 0.0
        try:
            mem_percent = float(pmem)
        except ValueError:
            mem_percent = 0.0

        processes.append({
            "oracle_pid": pid,
            "os_user": user,
            "niceness": ni,
            "vsz_kb": vsz,
            "rss_kb": rss,
            "state": state,
            "cpu_percent": cpu_percent,
            "memory_percent": mem_percent,
            "cpu_time": cpu_time,
            "program": comm
        })
    return processes


def get_top_oracle_os_processes_for_host(db_id):
    """
    Opens (or reuses a pooled) SSH connection using the given db_id's OS
    username and the shared private key (same mechanism as Mount Points -
    see get_ssh_connection_via_key()) and runs EXACTLY this top-10-by-CPU
    Oracle OS process command on that host - no other ps/top/pgrep variant:
        ps -eo pid,user,ni,vsz,rss,state,pcpu,pmem,time,comm --sort=-pcpu |
        awk 'NR==1 {print; next} $2=="oracle" {print; count++; if(count==10) exit}'
    Returns {"status": "success", "processes": [...]} or
    {"status": "error", "message": "..."}.
    """
    from services.ssh_service import get_ssh_connection_via_key
    from services.mountpoint_service import map_ssh_error

    ssh_client, db_cfg, ssh_err = get_ssh_connection_via_key(db_id=db_id, timeout=10, banner_timeout=10)
    if not ssh_client:
        return {"status": "error", "message": map_ssh_error(ssh_err), "processes": []}

    try:
        cmd = (
            'ps -eo pid,user,ni,vsz,rss,state,pcpu,pmem,time,comm --sort=-pcpu | '
            'awk \'NR==1 {print; next} $2=="oracle" {print; count++; if(count==10) exit}\''
        )
        stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=10)
        raw = stdout.read().decode('utf-8', errors='ignore')
        processes = _parse_top_oracle_ps_output(raw)
        return {"status": "success", "processes": processes}
    except Exception as e:
        return {"status": "error", "message": f"SSH command execution error: {str(e)}", "processes": []}


def get_all_servers_oracle_processes_raw():
    from services.mountpoint_service import is_standby_or_reporting_db_id

    configs = get_all_db_configs()
    configs = [c for c in configs if not is_standby_or_reporting_db_id(c.get("db_id"), configs)]

    host_map = {}
    for cfg in configs:
        host = cfg.get("host")
        if not host:
            continue
        host_map.setdefault(host, []).append(cfg)

    servers = {}
    servers_lock = threading.Lock()

    def process_host(host, db_cfgs):
        db_ids = [c.get("db_id") for c in db_cfgs]
        db_names = [c.get("service_name") or c.get("db_id") for c in db_cfgs]

        # OS processes aren't per-database - use whichever config on this
        # host has an OS username set to open one SSH connection for the host
        # (key-based auth needs os_user only - os_password is not used).
        valid_cfg = next((c for c in db_cfgs if c.get("os_user")), db_cfgs[0])
        res = get_top_oracle_os_processes_for_host(valid_cfg.get("db_id"))

        is_success = res.get("status") == "success"
        processes = res.get("processes") or []

        with servers_lock:
            servers[host] = {
                "status": "Running" if is_success else "Down",
                "host": host,
                "server": db_cfgs[0].get("service_name") or host,
                "db_ids": db_ids,
                "db_names": db_names,
                # Already the top 10 Oracle OS processes by CPU usage for
                # this host - no further Top N slicing needed on the frontend.
                "processes": processes,
                "total_process_count": len(processes),
                "message": None if is_success else (res.get("message") or "Unable to retrieve Oracle server processes for this host.")
            }

    host_threads = [threading.Thread(target=process_host, args=(host, db_cfgs)) for host, db_cfgs in host_map.items()]
    for t in host_threads:
        t.start()
    for t in host_threads:
        t.join()

    return {"status": "success", "servers": servers}


# ✅ Kill Database Session (ALTER SYSTEM KILL SESSION 'sid,serial' IMMEDIATE)
def kill_database_session(sid, serial, db_id=None):
    try:
        conn = get_connection_by_id(db_id) if db_id else get_connection()
        if not conn:
            return {"status": "error", "message": "Cannot connect to database"}

        cursor = conn.cursor()
        sid_val = int(sid)
        serial_val = int(serial)
        
        kill_sql = f"ALTER SYSTEM KILL SESSION '{sid_val},{serial_val}' IMMEDIATE"
        cursor.execute(kill_sql)
        cursor.close()
        conn.close()

        return {
            "status": "success",
            "message": f"Session (SID: {sid_val}, Serial#: {serial_val}) killed successfully."
        }
    except Exception as e:
        if 'conn' in locals() and conn:
            try: conn.close()
            except: pass
        err_msg = str(e)
        if "ORA-00030" in err_msg or "00030" in err_msg:
            return {"status": "error", "message": f"Session SID {sid}, Serial# {serial} does not exist or is already terminated."}
        elif "ORA-00031" in err_msg or "00031" in err_msg:
            return {"status": "success", "message": f"Session (SID: {sid}, Serial#: {serial}) marked for kill."}
        return {"status": "error", "message": f"Failed to kill session: {err_msg}"}