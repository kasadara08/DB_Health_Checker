import os
import sys
import json
import datetime
import oracledb
import pandas as pd
import streamlit as st

# -- Compute exe/script directory once, used for log file & oracle_client path --
def _get_exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

_EXE_DIR = _get_exe_dir()
_LOG_FILE = os.path.join(_EXE_DIR, "oracle_thick_mode_debug.log")
_ORACLE_CONFIG_DIR = os.environ.get(
    "TNS_ADMIN",
    r"/u02/app/oracle/product/19.0.0.0/dbhome_1/network/admin/M5GRYHDP",
)

# -- Bundled OCI SSH keys (keys/ folder) ---------------------------------------
# In the PyInstaller one-folder build, bundled datas land in "_internal" next
# to the exe. When running from source, they live directly under the project.
def _get_keys_dir():
    if getattr(sys, "frozen", False):
        internal_candidate = os.path.join(_EXE_DIR, "_internal", "keys")
        if os.path.isdir(internal_candidate):
            return internal_candidate
        meipass_candidate = os.path.join(getattr(sys, "_MEIPASS", ""), "keys")
        if os.path.isdir(meipass_candidate):
            return meipass_candidate
        return internal_candidate
    return os.path.join(_EXE_DIR, "keys")

_KEYS_DIR = _get_keys_dir()
_KEY_EXTENSIONS = (".pem", ".key")

def get_bundled_oci_key(host: str = "") -> str:
    """
    Resolve the OCI SSH private key to use for a given host from the bundled
    keys/ folder. Preference order:
      1. A host-specific key: keys/<host>.pem or keys/<host>.key
      2. If the folder contains exactly one key file, use it for every host
         (typical when the same OCI key pair is shared across all instances).
    Returns "" if no bundled key is found.
    """
    try:
        if not os.path.isdir(_KEYS_DIR):
            return ""

        entries = [
            f for f in os.listdir(_KEYS_DIR)
            if f.lower().endswith(_KEY_EXTENSIONS)
        ]

        if host:
            host_lower = host.strip().lower()
            for f in entries:
                name_no_ext = os.path.splitext(f)[0].lower()
                if name_no_ext == host_lower:
                    return os.path.join(_KEYS_DIR, f)

        if len(entries) >= 1:
            for preferred in ("oci_key.pem", "oci_api_key.pem", "oci.pem", "id_rsa.pem", "id_rsa"):
                for f in entries:
                    if f.lower() == preferred.lower():
                        return os.path.join(_KEYS_DIR, f)
            return os.path.join(_KEYS_DIR, entries[0])
    except Exception as e:
        print(f"[config] Error resolving bundled OCI key: {e}")

    return ""

def _log(msg):
    """Write a timestamped message to the diagnostics log file."""
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except Exception:
        pass

def _log_normal_db(db_name: str, status: str, cfg: dict = None, error_details: str = ""):
    log_path = os.path.join(_EXE_DIR, "normal_db_debug.log")
    import datetime as _dt
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            if status == "UP":
                f.write(f"[{timestamp}] [UP] DB: {db_name}\n")
            else:
                f.write(f"[{timestamp}] [DOWN] DB: {db_name}\n")
                if cfg:
                    f.write(f"  Registry Details: host={cfg.get('host')}, port={cfg.get('port')}, service_name={cfg.get('service_name')}, user={cfg.get('user')}, password={cfg.get('password')}, host_username={cfg.get('host_username', '')}, host_password={cfg.get('host_password', '')}\n")
                if error_details:
                    f.write(f"  Error details:\n{error_details}\n")
                f.write("-" * 80 + "\n")
    except Exception:
        pass

def _log_reporting_db(db_name: str, rpt_name: str, status: str, cfg: dict = None, error_details: str = ""):
    log_path = os.path.join(_EXE_DIR, "normal_db_debug.log")
    import datetime as _dt
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            if status == "UP":
                f.write(f"[{timestamp}] [UP] Reporting DB: {rpt_name} (Parent: {db_name})\n")
            else:
                f.write(f"[{timestamp}] [DOWN] Reporting DB: {rpt_name} (Parent: {db_name})\n")
                if cfg:
                    f.write(f"  Registry Details: host={cfg.get('host')}, port={cfg.get('port')}, service_name={cfg.get('service_name')}, user={cfg.get('user')}, password={cfg.get('password')}, host_username={cfg.get('host_username', '')}, host_password={cfg.get('host_password', '')}\n")
                if error_details:
                    f.write(f"  Error details:\n{error_details}\n")
                f.write("-" * 80 + "\n")
    except Exception:
        pass

def _clean_stby_service_name(cfg):
    """Clean and extract pure Standby TNS alias / service name (no host:port prefix)."""
    if not cfg:
        return ""

    svc = cfg.get("service_name")
    if svc and ":" not in svc and "/" not in svc:
        return svc.strip()

    dsn = str(cfg.get("dsn") or "").strip()
    if "/" in dsn:
        return dsn.split("/")[-1].strip()
    if ":" in dsn:
        return dsn.split(":")[-1].strip()
    if dsn:
        return dsn

    return ""

def _log_standby_db(db_name: str, stby_name: str, status: str, cfg: dict = None, error_details: str = "", dg_info: dict = None, conn_method: str = None):
    """
    Logs detailed Standby DB status and connection details into normal_db_debug.log AND prints to console.
    If UP: shows connection details & replication metrics.
    If DOWN: shows only down reason.
    """
    log_path = os.path.join(_EXE_DIR, "normal_db_debug.log")
    import datetime as _dt
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    is_up = status.startswith("UP") or "SYNC" in status
    state_label = "SUCCESS / UP" if is_up else "DOWN / ERROR"
    
    _dsn = _clean_stby_service_name(cfg)
    _user = (cfg.get("user") if cfg else "") or "sys"
    method = conn_method or f"SSH via Primary Server (OCI Key) -> . {db_name}.env -> sqlplus {_user}/****@{_dsn} as sysdba"
    
    log_lines = []
    log_lines.append(f"[{timestamp}] [{state_label}] Standby (DR) DB Connection Details:")
    log_lines.append(f"  Primary DB Name  : {db_name}")
    log_lines.append(f"  Standby DB Name  : {stby_name}")
    
    if is_up:
        log_lines.append(f"  Connection Method: {method}")
        log_lines.append(f"  Overall Status   : {status}")
        if cfg:
            _user = cfg.get('user', 'sys')
            log_lines.append(f"  Standby Target DSN: {_dsn} (AuthUser={_user})")
            
        if dg_info and isinstance(dg_info, dict) and dg_info.get("configured"):
            max_gap = dg_info.get("max_gap", 0)
            sync_label = "SYNCHRONIZED ✅" if max_gap < 2 else f"NOT SYNCHRONIZED 🔴 (Gap: {max_gap} seqs)"
            log_lines.append(f"  Replication Sync : {sync_label}")
            
            threads = dg_info.get("threads", [])
            for t in threads:
                log_lines.append(f"    Thread #{t.get('thread', 1)}: Primary Generated={t.get('primary_generated', 0)} | Standby Received={t.get('standby_received', 0)} | Standby Applied={t.get('standby_applied', 0)} | Gap={t.get('gap', 0)}")
                
            dests = dg_info.get("standby_dests", [])
            for d in dests:
                err_part = f" | Error={d.get('error')}" if d.get('error') else ""
                log_lines.append(f"    Dest #{d.get('dest_id')}: DB_UNIQUE_NAME={d.get('db_unique_name')} | Status={d.get('status')}{err_part}")
    else:
        log_lines.append(f"  Overall Status   : {status}")
        clean_err = error_details.replace("Primary Data Guard check failed and direct Standby connection not available", "").strip()
        down_reason = clean_err if clean_err else status
        log_lines.append(f"  Down Reason      : {down_reason}")
        
    log_lines.append("-" * 80)
    log_entry = "\n".join(log_lines) + "\n"
    
    # 1. Output to Console Window
    print(f"\n[STANDBY DB LOG]\n{log_entry}")
    
    # 2. Append to normal_db_debug.log File
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception:
        pass

def _log_ssh_oci_key(host: str, username: str, key_path: str, status: str, error_details: str = ""):
    log_path = os.path.join(_EXE_DIR, "normal_db_debug.log")
    import datetime as _dt
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            state_label = "UP" if "UP" in status or "SUCCESS" in status else "DOWN"
            f.write(f"[{timestamp}] [{state_label}] SSH OCI Key Connectivity: Host={host}\n")
            f.write(f"  Status      : {status}\n")
            f.write(f"  User        : {username}\n")
            f.write(f"  OCI Key Path: {key_path or 'N/A'}\n")
            if error_details:
                f.write(f"  Error Details:\n{error_details}\n")
            f.write("-" * 80 + "\n")
    except Exception:
        pass

# -- Thick-mode initializer ----------------------------------------------------
import threading
_thick_mode_lock = threading.Lock()
_thick_mode_initialized = False

def _ensure_thick_mode():
    """
    Try to initialize oracledb in Thick mode.
    Pre-called at module import so it's ready before any connection attempt.
    Priority:
      1. Bundled oracle_client/_internal/oracle_client (PyInstaller one-folder builds)
      2. System PATH entries that contain oci.dll
      3. ORACLE_HOME environment variable
      4. Well-known install paths on Windows
    Returns True if Thick mode was enabled, False otherwise.
    """
    global _thick_mode_initialized
    if _thick_mode_initialized:
        return True

    with _thick_mode_lock:
        if _thick_mode_initialized:
            return True

        _log("--- _ensure_thick_mode starting ---")
        _log(f"sys.frozen: {getattr(sys, 'frozen', False)}")
        _log(f"sys.executable: {sys.executable}")
        _log(f"_EXE_DIR: {_EXE_DIR}")

        # -- 1. Paths relative to the exe / script --------------------------------
        # PyInstaller one-folder build: binaries are in _internal/
        internal_path  = os.path.join(_EXE_DIR, "_internal", "oracle_client")
        # Direct sibling of exe (older PyInstaller / manual copy)
        local_path     = os.path.join(_EXE_DIR, "oracle_client")
        # PyInstaller one-file build: extracted to sys._MEIPASS
        meipass_path   = os.path.join(getattr(sys, "_MEIPASS", ""), "oracle_client")

        candidate_paths = [internal_path, local_path, meipass_path]

        _log(f"Initial candidate paths:")
        for p in candidate_paths:
            _log(f"  '{p}' -> oci.dll exists: {os.path.exists(os.path.join(p, 'oci.dll')) if p else False}")

        # -- 2. PATH entries containing oci.dll -----------------------------------
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if p and os.path.exists(os.path.join(p, "oci.dll")):
                candidate_paths.append(p)
                _log(f"  Found oci.dll in PATH: {p}")

        # -- 3. ORACLE_HOME env var -----------------------------------------------
        oracle_home = os.environ.get("ORACLE_HOME", "")
        if oracle_home:
            candidate_paths.append(os.path.join(oracle_home, "bin"))
            _log(f"  ORACLE_HOME found: {oracle_home}")

        # -- 4. Well-known Windows install paths (machine-specific fallback) ------
        candidate_paths += [
            r"D:\Oracle_DB\product\bin",
            r"C:\Users\KTS\Downloads\WINDOWS.X64_193000_db_home\bin",
            r"C:\OracleForm\Middleware\OracleForm_Home\bin",
            
        ]

        for path in candidate_paths:
            if not path:
                continue
            oci_dll = os.path.join(path, "oci.dll")
            if not os.path.exists(oci_dll):
                continue
            _log(f"Attempting init_oracle_client(lib_dir='{path}')")
            try:
                if os.name == "nt":
                    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
                    if hasattr(os, "add_dll_directory"):
                        try:
                            os.add_dll_directory(path)
                        except Exception as add_dll_err:
                            _log(f"Warning: os.add_dll_directory failed: {add_dll_err}")
                init_kwargs = {"lib_dir": path}
                tns_file   = os.path.join(_ORACLE_CONFIG_DIR, "tnsnames.ora")
                dir_exists = os.path.isdir(_ORACLE_CONFIG_DIR)
                tns_exists = os.path.isfile(tns_file)

                if dir_exists and tns_exists:
                    init_kwargs["config_dir"] = _ORACLE_CONFIG_DIR
                    msg = f"[THICK MODE] config_dir ACCEPTED — tnsnames.ora found: '{tns_file}'"
                    _log(msg); print(msg)
                elif dir_exists and not tns_exists:
                    msg = f"[THICK MODE] WARNING: config_dir='{_ORACLE_CONFIG_DIR}' exists BUT tnsnames.ora NOT FOUND — connecting WITHOUT config_dir"
                    _log(msg); print(msg)
                else:
                    msg = f"[THICK MODE] WARNING: config_dir='{_ORACLE_CONFIG_DIR}' does NOT EXIST on this machine — connecting WITHOUT config_dir"
                    _log(msg); print(msg)

                oracledb.init_oracle_client(**init_kwargs)
                _thick_mode_initialized = True
                _log(f"SUCCESS - Thick mode enabled from: {path}")
                print(f"[db_connection] Thick mode enabled using: {path}")
                return True
            except Exception as e:
                err_msg = str(e)
                _log(f"FAILED - init_oracle_client error: {err_msg}")
                if "already been called" in err_msg.lower() or "already initialized" in err_msg.lower():
                    _thick_mode_initialized = True
                    _log("Already initialized - treating as success")
                    return True

        _log("Thick Mode initialization failed completely - no valid oci.dll path found")
        return False


# NOTE: Thick mode is initialized ON-DEMAND inside check_reporting_db_status()
# Do NOT call _ensure_thick_mode() here at import time - it will crash on
# machines where Oracle DLL dependencies (e.g. MSVC Redistributable) are missing.
_log("=== db_connection module imported ===")


def get_oracle_mode(username: str):
    #Determine the correct Oracle connection mode based on username (SYS, SYSBACKUP, etc) (it take the username and returns the appropriate mode for oracledb.connect())
    u = str(username).strip().lower() #converting username to lowercase and stripping whitespace
    if u == "sys":  # IT Checking if the username is sys or not sys means it return sys 
        return getattr(oracledb, "SYSDBA", oracledb.DEFAULT_AUTH)
    elif u == "sysoper":
        return getattr(oracledb, "SYSOPER", getattr(oracledb, "AUTH_SYSOPER", oracledb.DEFAULT_AUTH))
    elif u == "sysbackup":
        return getattr(oracledb, "SYSBKP", getattr(oracledb, "AUTH_SYSBACKUP", oracledb.DEFAULT_AUTH))
    elif u == "sysdg":
        return getattr(oracledb, "SYSDG", getattr(oracledb, "AUTH_SYSDG", oracledb.DEFAULT_AUTH))
    elif u == "syskm":
        return getattr(oracledb, "SYSKM", getattr(oracledb, "AUTH_SYSKM", oracledb.DEFAULT_AUTH))
    return oracledb.DEFAULT_AUTH

# Platform-safe config file path (works on both Windows and Linux)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 
       # __file__ is a special Python variable that contains the path of the currently running Python file.
       #  os.path.dirname=>it retrieve the directory of the file eg: C:\Projects\DBDashboard\config
       #  os.path.abspath=> Converts it into an absolute path.  eg: C:\Projects\DBDashboard\config\db_utils.py

CONFIG_FILE = os.path.join(_BASE_DIR, "db_path_config.json")  #now _BASE_DIR contains dbdbashboard/config and CONFIG_FILE contains db_path_config.json file path

# Required column order when the registry file has NO header row
# Columns 1-8:   Main DB credentials
# Columns 9-14:  Reporting DB credentials (no host SSH credentials)
# Columns 15-20: Standby DB credentials (no host SSH credentials)
REGISTRY_COLUMNS = [
    "db_name", "host", "port", "service_name", "username", "password", "host_username", "host_password",
    "reporting_db_name", "reporting_host", "reporting_port", "reporting_service_name",
    "reporting_username", "reporting_password",
    "standby_db_name", "standby_host", "standby_port", "standby_service_name",
    "standby_username", "standby_password",
]

# (start_index, column_count) for each of the 3 groups above, used by
# parse_smart_txt_to_df so a single "NC" token can stand in for an ENTIRE
# group instead of requiring one "NC" per column in that group.
_REGISTRY_GROUPS = [(0, 8), (8, 6), (14, 6)]

_thread_safe_txt_path = None

def get_txt_path():
    global _thread_safe_txt_path
    # Dynamically get the path of the database registry file (cross-platform).
    # 1. Check session state from a cache (custom_txt_path is already exists in session state)
    try:
        if "custom_txt_path" in st.session_state and st.session_state.custom_txt_path:
            p = st.session_state.custom_txt_path
            resolved_p = p if os.path.isabs(p) else os.path.join(_BASE_DIR, p)
            if os.path.exists(resolved_p):
                _thread_safe_txt_path = resolved_p
                return resolved_p
            _thread_safe_txt_path = p
            return p
    except Exception:
        pass

    # Fallback to thread-safe path if we are in a worker thread where session_state is inaccessible
    if _thread_safe_txt_path:
        return _thread_safe_txt_path

    # 2. Check JSON configuration file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f: 
                cfg = json.load(f)
                path = cfg.get("txt_path")
                if path:
                    resolved_path = path if os.path.isabs(path) else os.path.join(_BASE_DIR, path)
                    if os.path.exists(resolved_path):
                        _thread_safe_txt_path = resolved_path
                        try:
                            st.session_state.custom_txt_path = resolved_path
                        except Exception:
                            pass
                        return resolved_path
                    _thread_safe_txt_path = path
                    try:
                        st.session_state.custom_txt_path = path
                    except Exception:
                        pass
                    return path
        except Exception:
            pass

    return None

def get_registry_history():  #Retrieve the list of previously used registry file paths from the config file.
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)  #cfg is the one  which reads and parses the JSON file into a Python dictionary.
                history = cfg.get("history", []) #history is a KEY in the JSON file (file name is db_path_config.json)  if history key is not present, it will return an empty list
                txt_path = cfg.get("txt_path")  #txt_path is a KEY in the JSON file (file name is db_path_config.json)
                
                # it sending the registry file to history list if history is not exist that file  and txt_path is not empty.  It also updates the config file with the new history list.
                if not history and txt_path:
                    history = [txt_path] 
                    cfg["history"] = history 

                    try:
                        with open(CONFIG_FILE, "w") as w:
                            json.dump(cfg, w) # here it is writing the updated config dictionary back to the JSON file, effectively saving the new history list.
                    except Exception:
                        pass
                return history
        except Exception:
            pass
    return []


def get_previous_registry():  #Returns the previously confirmed registry path (second entry in history, i.e., before current).
    history = get_registry_history()
    if len(history) >= 2:
        return history[1]   # history[0] is current, history[1] is previous
    elif len(history) == 1:
        return history[0]   # only one entry - fall back to same
    return None


def get_pending_path(): #Returns the pending (unconfirmed) registry path, if any.
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f) # cfg is the one  which reads and parses the JSON file into a Python dictionary.
            return cfg.get("pending_txt_path")  #pending_txt_path is a KEY in the JSON file (file name is db_path_config.json)
        except Exception:
            pass
    return None


def save_pending_path(path):  # Save a new registry path as PENDING (not yet active).It will only become active after confirm_pending_path() is called.
   
    try:
        cfg = {}  #Starts with an empty dict that will hold the config data
        if os.path.exists(CONFIG_FILE):  #it read the existing config file if it exists, and load its contents into the cfg dictionary. If the file doesn't exist or can't be read, cfg remains an empty dict.
            try:
                with open(CONFIG_FILE, "r") as f:
                    cfg = json.load(f) #open the config file in read mode and load its contents into the cfg dictionary.
            except Exception:
                pass
        cfg["pending_txt_path"] = path # adding the new/updated path being the new registry path.
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)  #Overwrites the JSON config file with the updated cfg dictionary
    except Exception as e:
        print(f"Error saving pending path: {e}")


def confirm_pending_path():  # Promote the pending registry path to the active confirmed registry and Adds it to history, clears the pending entry, and triggers a fresh monitoring cycle.
    
    pending = get_pending_path() # Get the pending registry path from the db_connection itself
    if not pending:
        return False
    # Promote to active
    save_txt_path(pending)  # it call from db_connection in line of 212  (save_txt_path is the function that saves the path to a JSON configuration file, maintain history, and trigger fresh monitoring cycle.)
    # Clear the pending flag
    try:
        cfg = {}  #Starts with an empty dict that will hold the config data
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f) #open the config file in read mode and load its contents into the cfg dictionary.
        cfg.pop("pending_txt_path", None)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)  #Overwrites the JSON config file with the updated cfg dictionary
    except Exception as e:
        print(f"Error clearing pending path: {e}")
    return True


def discard_pending_path():  # Discard the pending registry path (e.g., when user closed the tab without confirming).Reverts to the most recent confirmed registry from history.
    
    try:
        cfg = {}   #Starts with an empty dict that will hold the config data
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f) #open the config file in read mode and load its contents into the cfg dictionary.
        cfg.pop("pending_txt_path", None)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)   #Overwrites the JSON config file with the updated cfg dictionary
    except Exception as e:
        print(f"Error discarding pending path: {e}")


def clear_active_registry(): # Remove the active registry path from config, but ensure it is stored in the history list first. Used when user wants to reconfigure or on fresh tab loads.
    
    try:
        cfg = {}  #Starts with an empty dict that will hold the config data
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)  #open the config file in read mode and load its contents into the cfg dictionary.
        
        active_path = cfg.get("txt_path")
        if active_path:
            history = cfg.get("history", [])
            norm_path = os.path.normpath(active_path)
            history = [p for p in history if os.path.normpath(p) != norm_path]
            history.insert(0, active_path)
            cfg["history"] = history[:10]  # Keep last 10 entries

        cfg["txt_path"] = None
        cfg.pop("pending_txt_path", None)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except Exception as e:
        print(f"Error clearing active registry: {e}")


def startup_registry_check(): # Call this ONCE at application startup (per server process). Behavior: - Any pending (unconfirmed) path is discarded.

    try:
        cfg = {}   #Starts with an empty dict that will hold the config data
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)  #open the config file in read mode and load its contents into the cfg dictionary.

       
        cfg.pop("pending_txt_path", None)   # Discard any leftover pending path

        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)  #Overwrites the JSON config file with the updated cfg dictionary

    except Exception as e:
        print(f"[startup] startup_registry_check error: {e}")


def save_txt_path(path):  #(this funtion is used by confirm_pending_path inside db_connection itself)  )
    """Save the path to a JSON configuration file, maintain history, and trigger fresh monitoring cycle."""
    st.session_state.custom_txt_path = path

    # A new registry file can reuse the same db_name as the previous one
    # (common with test/demo files) while pointing at a completely
    # different host/credentials. status_cache/server_cache are keyed
    # purely by db_name, so without this they'd keep showing the OLD
    # file's connection details under that name until a manual refresh.
    # Clear this session's cache so the next render is forced to fetch
    # live from the newly-active file for every database.
    for _key in ("status_cache", "server_cache", "home_load_complete",
                 "home_processes_cache", "home_mounts_cache"):
        if _key in st.session_state:
            del st.session_state[_key]
    # The per-DB monitoring dashboard cache (dashboard/monitoring.py:
    # "mon_cache_<db_name>") is keyed the same way and has the same problem
    # — purge every one of those too.
    for _key in [k for k in list(st.session_state.keys()) if str(k).startswith("mon_cache_")]:
        del st.session_state[_key]

    try:
        cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    cfg = json.load(f)
            except Exception:
                pass

        history = cfg.get("history", [])
        # Normalize paths to filter out duplicates
        norm_path = os.path.normpath(path)
        history = [p for p in history if os.path.normpath(p) != norm_path]
        history.insert(0, path)
        history = history[:10]  # Keep last 10 entries

        cfg["txt_path"] = path
        cfg["history"]  = history

        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)

        # -- Trigger fresh monitoring cycle on every registry change ------
        try:
            config_dir  = os.path.join(_BASE_DIR, "config")
            os.makedirs(config_dir, exist_ok=True)
            force_flag  = os.path.join(config_dir, "force_refresh.flag")
            with open(force_flag, "w") as ff:
                ff.write("1")
        except Exception:
            pass

    except Exception as e:
        print(f"Error saving config path: {e}")


def _is_header_row(first_row_values: list) -> bool:
    """
    Returns True if the first row looks like column headers (non-numeric strings),
    False if it looks like actual data (e.g., IP address in host column position).
    """
    # Position 2 is 'port' - if it's a number it's data, if text it's a header
    if len(first_row_values) >= 3:
        try:
            float(str(first_row_values[2]).strip())
            return False  # Port is numeric -> this is a data row, no header
        except ValueError:
            return True   # Port field is text -> this is likely a header row
    return True  # Fewer than 3 columns - assume header present

# Unused legacy helpers removed to keep codebase clean


def parse_smart_txt_to_df(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [ln.rstrip() for ln in f]
    except Exception as e:
        print(f"[config] Error reading registry file: {e}")
        return pd.DataFrame(columns=REGISTRY_COLUMNS)

    rows = []
    for line in lines:
        s_ln = line.strip()
        if not s_ln or s_ln.startswith("#"):
            continue
            
        parts = s_ln.split()
        if not parts:
            continue

        row = [""] * len(REGISTRY_COLUMNS)

        # Walk the 3 column groups (production/reporting/standby) in order.
        # If a group's next token is exactly "NC", that ONE token stands in
        # for the whole group — every column in it is set to "NC" and only
        # that single token is consumed, so whatever is typed right after
        # lines up with the NEXT group instead of shifting into this one.
        # Otherwise, consume one token per column in the group as before.
        p_idx = 0
        for start, length in _REGISTRY_GROUPS:
            if p_idx < len(parts) and parts[p_idx].strip().lower() == "nc":
                for offset in range(length):
                    row[start + offset] = "NC"
                p_idx += 1
            else:
                for offset in range(length):
                    if p_idx < len(parts):
                        row[start + offset] = parts[p_idx].strip()
                        p_idx += 1

        row_dict = {}
        for idx, col in enumerate(REGISTRY_COLUMNS):
            row_dict[col] = row[idx]
            
        rows.append(row_dict)

    df = pd.DataFrame(rows, columns=REGISTRY_COLUMNS)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
    
    return df

      


def read_db_file_to_df(path):
    """
    Reads either a text file (txt, csv) or an Excel sheet (xlsx, xls).
    Supports files WITH or WITHOUT a header row.
    If no header is detected, assigns columns in order:
       db_name, host, port, service_name, username, password
    Works cross-platform (Windows & Linux).
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".txt":
        # Always use the smart auto-fill parser for txt config files
        return parse_smart_txt_to_df(path)

    if ext in [".xlsx", ".xls"]:
        # Read Excel - check if first row is header
        df_raw = pd.read_excel(path, header=None)
        if not df_raw.empty:
            first_row = df_raw.iloc[0].tolist()
            if _is_header_row(first_row):
                df = pd.read_excel(path)
            else:
                df = pd.read_excel(path, header=None)
                df.columns = REGISTRY_COLUMNS[:len(df.columns)]
        else:
            df = df_raw
    else:
        # Try whitespace-separated first, then comma
        try:
            df_raw = pd.read_csv(path, sep=r'\s+', engine='python', header=None)
            has_commas = False
            if not df_raw.empty:
                first_row_str = "".join(df_raw.iloc[0].astype(str).tolist())
                if ',' in first_row_str:
                    has_commas = True
            if df_raw.shape[1] <= 1 or has_commas:
                df_raw = pd.read_csv(path, sep=',', header=None)
        except Exception:
            df_raw = pd.read_csv(path, sep=',', header=None)

        if not df_raw.empty:
            first_row = df_raw.iloc[0].tolist()
            if _is_header_row(first_row):
                # Re-read with header
                try:
                    df = pd.read_csv(path, sep=r'\s+', engine='python')
                    has_commas = False
                    if not df.empty:
                        first_row_str = "".join(df.iloc[0].astype(str).tolist())
                        if ',' in first_row_str:
                            has_commas = True
                    if df.shape[1] <= 1 or has_commas:
                        df = pd.read_csv(path, sep=',')
                except Exception:
                    df = pd.read_csv(path, sep=',')
            else:
                # No header - assign column names by position
                df = df_raw.copy()
                df.columns = REGISTRY_COLUMNS[:len(df.columns)]
        else:
            df = df_raw

    # Clean up column values: strip whitespace and trailing commas
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().str.strip(',')

    df.columns = [col.strip().lower() for col in df.columns]
    return df


# -- In-memory registry cache -------------------------------------------------
# get_config_for_db/get_reporting_db_config/get_standby_db_config/load_db_names*
# are each called once per database, per refresh cycle (and again from the
# reporting-db subprocess). Without caching, every one of those calls re-opens
# and re-parses the whole registry file from disk. Cache the parsed DataFrame
# per path and only re-parse when the file's mtime changes.
_registry_df_cache = {}
_registry_cache_lock = threading.Lock()

def _read_db_file_to_df_cached(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    with _registry_cache_lock:
        cached = _registry_df_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    df = read_db_file_to_df(path)
    with _registry_cache_lock:
        _registry_df_cache[path] = (mtime, df)
    return df


def load_db_names():
    """Load database names dynamically from the registry file (Text or Excel)."""
    path = get_txt_path()
    if not path or not os.path.exists(path):
        return []
    
    try:
        df = _read_db_file_to_df_cached(path)
        if "db_name" in df.columns:
            db_names = df["db_name"].dropna().astype(str).str.strip().tolist()
            # "nc" in db_name means the row has no production config at all
            # — exclude it from the list entirely, same as a blank db_name.
            db_names = [name for name in db_names if name and name.strip().lower() != "nc"]
            if db_names:
                # Deduplicate database names while keeping their original order (case-insensitive check)
                seen = set()
                unique_names = []
                for name in db_names:
                    norm = name.strip().lower()
                    if norm not in seen:
                        seen.add(norm)
                        unique_names.append(name)
                return unique_names
        return []
    except Exception as e:
        st.error(f"Error reading database registry file: {e}")
        return []

# -------------------------------------------------------------
# API-SAFE FUNCTIONS (No Streamlit session_state dependency)
# These work in FastAPI / uvicorn / CLI context on any OS.
# -------------------------------------------------------------

def get_txt_path_api() -> str:
    """
    Get the registry file path directly from the JSON config file.
    Does NOT use st.session_state - safe to call from FastAPI/uvicorn.
    """
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                path = cfg.get("txt_path", "")
                if path and os.path.exists(path):
                    return path
        except Exception:
            pass
    return ""

def load_db_names_api() -> list:
    """
    Load all database names from the registry file without session_state.
    Safe for FastAPI / Linux server use.
    """

    # Get the path of the database registry (.txt/.csv) file
    path = get_txt_path_api()

    # If path is not available, return an empty list
    if not path:
        return []

    try:
        # Read the registry file into a Pandas DataFrame
        df = _read_db_file_to_df_cached(path)

        # Check whether the DataFrame contains a 'db_name' column
        if "db_name" in df.columns:

            # Remove NULL values, convert all names to string,
            # remove leading/trailing spaces and convert into a list
            names = df["db_name"].dropna().astype(str).str.strip().tolist()

            # Return only non-empty, unique database names (preserving order).
            # "nc" means the row has no production config at all — exclude
            # it entirely, same as a blank db_name.
            seen = set()
            unique_names = []
            for n in names:
                if n and n.strip().lower() != "nc":
                    norm = n.strip().lower()
                    if norm not in seen:
                        seen.add(norm)
                        unique_names.append(n)
            return unique_names

    except Exception as e:

        # Print error if reading the file fails
        print(f"[API] Error reading db names: {e}")

    # Return an empty list if any error occurs
    return []


def get_config_for_db(db_name: str) -> dict:
    """
    Load Oracle credentials for a specific database name from the registry file.
    Tries session-aware path first (get_txt_path), then JSON config (get_txt_path_api).
    Returns a dict with keys: user, password, dsn, host, port, service_name
    Returns None if not found or file missing.
    """

    # Get the path of the registry file - try session-aware path first
    path = ""
    try:
        path = get_txt_path()
    except Exception:
        pass
    if not path or not os.path.exists(path):
        path = get_txt_path_api()

    # Check whether the file exists
    if not path or not os.path.exists(path):
        return None

    try:
        # Read the registry file into a DataFrame
        df = _read_db_file_to_df_cached(path)

        if "db_name" not in df.columns:
            return None

        # Find the row where database name matches (case-insensitive)
        match = df[
            df["db_name"]
            .astype(str)
            .str.strip()
            .str.lower()
            == str(db_name).strip().lower()
        ]

        # Return None if database name is not found
        if match.empty:
            return None

        # Get the first matching row
        row = match.iloc[0]

        # "nc" ("Not Configured") in db_name — the first of the production
        # column group — means this row has no production config at all.
        # In practice load_db_names()/load_db_names_api() already exclude
        # such rows from the list of databases shown in the UI, so this is
        # mainly a defense-in-depth guard against a direct lookup.
        if str(row.get("db_name", "")).strip().lower() == "nc":
            return None

        def _clean(val, default=""):
            """Return clean string, replacing None/nan/empty with default."""
            v = str(val).strip() if val is not None else ""
            return default if v.lower() in ("", "none", "nan", "n/a", "-", "nc") else v

        # Read all fields with safe fallbacks
        user         = _clean(row.get("username",      row.get("user", "")))
        password     = _clean(row.get("password",      ""))
        host         = _clean(row.get("host",          ""))
        port         = _clean(row.get("port",          ""), "1521")
        service_name = _clean(row.get("service_name",  row.get("db_name", db_name)))
        host_username = _clean(row.get("host_username", row.get("os_user", "")))
        host_password = _clean(row.get("host_password", row.get("os_pass", "")))

        # Remove ".0" if Excel converted the port into decimal format (1521.0 -> 1521)
        if port.endswith(".0"):
            port = port[:-2]

        # Validate essential fields - host, user, password must be present
        if not host:
            print(f"[config] '{db_name}': host is empty in registry - skipping row.")
            return None
        if not user:
            print(f"[config] '{db_name}': username is empty in registry - skipping row.")
            return None

        # Build Oracle DSN in host:port/service_name format (required for thin mode)
        # DPY-4027 occurs when DSN is a TNS alias (no host:port). Always use explicit format.
        dsn = f"{host}:{port}/{service_name}"

        # Return all database connection details as a dictionary
        return {
            "db_name":       str(db_name).strip(),
            "user":          user,
            "password":      password,
            "dsn":           dsn,
            "host":          host,
            "port":          port,
            "service_name":  service_name,
            "host_username": host_username,
            "host_password": host_password,
        }

    except Exception as e:
        print(f"[config] Error loading config for '{db_name}': {e}")
        return None


def get_reporting_db_config(db_name: str) -> dict:
    """
    Returns reporting DB credentials for a given main db_name.
    Supports flexible column names / aliases and UI session path.
    Returns None if no reporting DB is configured for that row.
    """
    path = ""
    try:
        path = get_txt_path()
    except Exception:
        pass
    if not path or not os.path.exists(path):
        path = get_txt_path_api()

    if not path or not os.path.exists(path):
        return None

    try:
        df = _read_db_file_to_df_cached(path)
        if "db_name" not in df.columns:
            return None

        match = df[df["db_name"].astype(str).str.strip().str.lower() == str(db_name).strip().lower()]
        if match.empty:
            return None

        row = match.iloc[0]

        # "nc" ("Not Configured") in the reporting_db_name column — the
        # first of the reporting column group — means the whole reporting
        # group is unconfigured, regardless of what's in the other
        # reporting_* columns to its right.
        if str(row.get("reporting_db_name", "")).strip().lower() == "nc":
            return None

        def _first_val(keys):
            for k in keys:
                if k in row:
                    val = str(row[k]).strip()
                    if val and val.lower() not in ("none", "nan", "n/a", "-", "nc"):
                        return val
            return ""

        r_db   = _first_val(["reporting_db_name", "reporting_db", "reporting db name", "reporting db", "rpt_db"])
        r_host = _first_val(["reporting_host", "reporting host", "rpt_host"])
        r_port = _first_val(["reporting_port", "reporting port", "rpt_port"])
        r_svc  = _first_val(["reporting_service_name", "reporting_server", "reporting server name", "reporting server", "reporting_service", "rpt_service", "server_name"])
        r_user = _first_val(["reporting_username", "reporting_user", "reporting username", "rpt_user"])
        r_pwd  = _first_val(["reporting_password", "reporting_pass", "reporting password", "rpt_password"])

        # # Fall back to main DB host/port if reporting host/port are missing
        # if not r_host:
        #     r_host = _first_val(["host"])
        # if not r_port:
        #     r_port = _first_val(["port"])

        # Essential fields: db_name, host, port, service_name, username are required.
        # Password is optional (some DBs use OS/wallet auth) - allow empty password.
        if not all([r_db, r_host, r_port, r_svc, r_user]):
            return None

        if r_port.endswith(".0"):
            r_port = r_port[:-2]

        # Sanitize values to prevent DPY-6001 (invalid DSN format)
        r_host = r_host.strip().rstrip("/").rstrip(":")
        r_port = r_port.strip()
        r_svc  = r_svc.strip().strip("/").strip(".")
        r_user = r_user.strip()
        
        # Final safety check after sanitization
        if not all([r_db, r_host, r_port, r_svc, r_user]):
            return None

        dsn = f"{r_host}:{r_port}/{r_svc}"
        # print(f"[reporting] DSN for '{db_name}' -> reporting_db='{r_db}' user='{r_user}' dsn='{dsn}'")

        return {
            "db_name":      r_db,
            "user":         r_user,
            "password":     r_pwd,
            "dsn":          dsn,
            "host":         r_host,
            "port":         r_port,
            "service_name": r_svc,
        }
    except Exception as e:
        print(f"[reporting] Error reading reporting config for {db_name}: {e}")
        return None


def get_standby_db_config(db_name: str) -> dict:
    """
    Returns standby DB credentials for a given main db_name.
    Standby DB is a read-only Data Guard replica — no SSH credentials needed.
    Returns None if no standby DB is configured for that row.
    """
    path = ""
    try:
        path = get_txt_path()
    except Exception:
        pass
    if not path or not os.path.exists(path):
        path = get_txt_path_api()

    if not path or not os.path.exists(path):
        return None

    try:
        df = _read_db_file_to_df_cached(path)
        if "db_name" not in df.columns:
            return None

        match = df[df["db_name"].astype(str).str.strip().str.lower() == str(db_name).strip().lower()]
        if match.empty:
            return None

        row = match.iloc[0]

        def _clean(val, default=""):
            v = str(val).strip() if val is not None else ""
            return default if v.lower() in ("", "none", "nan", "n/a", "-", "nc") else v

        # "nc" ("Not Configured") in the standby_db_name column — the first
        # of the standby column group — means the whole standby/DR group is
        # unconfigured, regardless of what's in standby_host/username/etc.
        # to its right. Check the RAW cell (not just via _clean) so this
        # short-circuits even if someone left stray values in later columns.
        if str(row.get("standby_db_name", "")).strip().lower() == "nc":
            return None

        s_db   = _clean(row.get("standby_db_name",   ""))
        s_host = _clean(row.get("standby_host",       ""))
        s_port = _clean(row.get("standby_port",       ""), "1521")
        s_svc  = _clean(row.get("standby_service_name", ""))
        s_user = _clean(row.get("standby_username",   ""))
        s_pwd  = _clean(row.get("standby_password",   ""))

        # Standby is only configured if standby_host and standby_username are explicitly provided.
        # Do NOT borrow from primary database configuration under any circumstance.
        if not s_host or not s_user:
            return None

        # If service name is missing, default to standby db_name or main db_name
        if not s_svc:
            s_svc = s_db or db_name

        if s_port.endswith(".0"):
            s_port = s_port[:-2]

        s_host = s_host.strip().rstrip("/").rstrip(":")

        dsn = f"{s_host}:{s_port}/{s_svc}"

        return {
            "db_name":      s_db or db_name,
            "user":         s_user,
            "password":     s_pwd,
            "dsn":          dsn,
            "host":         s_host,
            "port":         s_port,
            "service_name": s_svc,
        }
    except Exception as e:
        print(f"[standby] Error reading standby config for {db_name}: {e}")
        return None


def check_reporting_db_status(db_name: str) -> dict:
    """
    Checks the reporting DB connection status using an isolated, fast Python subprocess.
    Avoids DPY-2019 by initializing thick mode in a separate process.
    """
    import subprocess
    import json
    import sys
    
    creds = get_reporting_db_config(db_name)
    if creds is None:
        return {
            "configured": False,
            "status": "NOT_CONFIGURED",
            "listener": "NOT_CONFIGURED",
            "reporting_db_name": "",
            "error": "reporting db is not able to cofig without their information",
            "error_code": "NOT_CONFIGURED",
            "error_message": "reporting db is not able to cofig without their information",
            "response_time_ms": 0,
            "checked_at": "",
            "reporting_username": "",
            "reporting_password": ""
        }

    registry_path = get_txt_path()
    if not registry_path or not os.path.exists(registry_path):
        return {
            "configured": False,
            "status": "NOT_CONFIGURED",
            "listener": "NOT_CONFIGURED",
            "reporting_db_name": "",
            "error": "No active registry file.",
            "error_code": "NOT_CONFIGURED",
            "error_message": "No active registry file.",
            "response_time_ms": 0,
            "checked_at": "",
            "reporting_username": "",
            "reporting_password": ""
        }
        
    scratch_dir = os.path.join(_EXE_DIR, "scratch")
    if not os.path.exists(scratch_dir):
        os.makedirs(scratch_dir, exist_ok=True)
        
    import uuid
    uid = str(uuid.uuid4())[:8]
    result_file = os.path.join(scratch_dir, f"rpt_{db_name}_{uid}.json")
    subprocess_log = os.path.join(scratch_dir, f"rpt_{db_name}_{uid}.log")
    
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--check-reporting-db", db_name, registry_path, result_file, subprocess_log]
    else:
        cmd = [sys.executable, os.path.join(_EXE_DIR, "run_dashboard.py"), "--check-reporting-db", db_name, registry_path, result_file, subprocess_log]

    try:
        _log(f"Spawning subprocess to check reporting DB '{db_name}'. Registry path: {registry_path}")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        _log(f"Subprocess exited with code {p.returncode}. stdout len={len(p.stdout) if p.stdout else 0}")
        if p.stderr:
            _log(f"Subprocess stderr: {p.stderr.strip()}")
            
        # Read the subprocess log file and write its contents to our main oracle_thick_mode_debug.log
        if os.path.exists(subprocess_log):
            try:
                with open(subprocess_log, "r", encoding="utf-8") as f:
                    sub_log_content = f.read()
                if sub_log_content:
                    with open(_LOG_FILE, "a", encoding="utf-8") as main_f:
                        main_f.write(sub_log_content)
            except Exception:
                pass
        
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                res = json.load(f)
            
            status = res.get("status", "DOWN")
            rpt_name = res.get("reporting_db_name", "")
            _log(f"Result read from temp file: status={status}")
            
            parent_cfg = get_config_for_db(db_name)
            cfg_log = {
                "host": res.get("host", ""),
                "port": res.get("port", ""),
                "service_name": res.get("service_name", ""),
                "user": res.get("reporting_username", ""),
                "password": res.get("reporting_password", ""),
                "host_username": parent_cfg.get("host_username", "") if parent_cfg else "",
                "host_password": parent_cfg.get("host_password", "") if parent_cfg else ""
            }
            
            if status == "UP":
                _log_reporting_db(db_name, rpt_name, "UP", cfg_log)
            else:
                err_details = f"Status: {status}, Listener: {res.get('listener', 'DOWN')}, Error: {res.get('error', '')} [{res.get('error_code', '')}]\n"
                failed_logs = res.get("failed_attempts_logs", [])
                if failed_logs:
                    err_details += "Collected Tracebacks:\n" + "\n".join(failed_logs)
                _log_reporting_db(db_name, rpt_name, "DOWN", cfg_log, err_details)
                
            return res
        else:
            err_output = p.stderr or p.stdout or "Subprocess exited without writing result."
            raise RuntimeError(err_output)
            
    except Exception as e:
        err_msg = f"Subprocess check failed: {e}"
        _log(f"Subprocess check failed: {e}")
        _log_reporting_db(db_name, "", "DOWN", None, err_msg)
        return {
            "configured": True,
            "status": "DOWN",
            "listener": "DOWN",
            "reporting_db_name": "",
            "error": err_msg,
            "error_code": "SUBPROCESS_ERROR",
            "error_message": err_msg,
            "response_time_ms": 0,
            "checked_at": "",
            "reporting_username": "",
            "reporting_password": ""
        }
    finally:
        try:
            if os.path.exists(result_file):
                os.remove(result_file)
            if os.path.exists(subprocess_log):
                os.remove(subprocess_log)
        except Exception:
            pass






def get_api_connection(db_name: str):
    """
    Open and return a direct Oracle DB connection for a named database.
    Uses thin mode (no Oracle Client libs needed on Linux).
    Does NOT use st.session_state - safe to call from FastAPI/uvicorn.
    Returns (connection, None) on success or (None, error_message) on failure.
    """
    cfg = get_config_for_db(db_name)
    if cfg is None:
        path = ""
        try:
            path = get_txt_path()
        except Exception:
            pass
        if not path or not os.path.exists(path):
            path = get_txt_path_api()
        if path and os.path.exists(path):
            try:
                df = _read_db_file_to_df_cached(path)
                if "db_name" in df.columns:
                    match = df[df["db_name"].astype(str).str.strip().str.lower() == str(db_name).strip().lower()]
                    if not match.empty:
                        err_msg = "txt file is blank for that particular db"
                        _log_normal_db(db_name, "DOWN", None, err_msg)
                        return None, err_msg
            except Exception:
                pass
        err_msg = f"Database '{db_name}' not found in registry."
        _log_normal_db(db_name, "DOWN", None, err_msg)
        return None, err_msg
        
    import traceback
    try:
        c_dsn = str(cfg.get("dsn", ""))
        c_user = cfg.get("user", "")
        c_pass = cfg.get("password", "")
        mode = get_oracle_mode(c_user)

        try:
            conn_kwargs = {
                "user": c_user,
                "password": c_pass,
                "dsn": c_dsn,
                "mode": mode,
                "tcp_connect_timeout": 2
            }
            
            if os.path.isdir(_ORACLE_CONFIG_DIR):
                conn_kwargs["config_dir"] = _ORACLE_CONFIG_DIR
                
            conn = oracledb.connect(**conn_kwargs)
            _log_normal_db(db_name, "UP", cfg)
            return conn, None
        except Exception as e:
            tb_attempt1 = traceback.format_exc()
            err_str = str(e)
            is_svc_err = any(term in err_str.lower() for term in ["dpy-6001", "12505", "12154", "service name", "sid"])
            if is_svc_err:
                try:
                    dsn_sid = f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={cfg['host']})(PORT={cfg['port']}))(CONNECT_DATA=(SID={cfg['service_name']})))"

                    conn_kwargs_sid = {
                        "user": c_user,
                        "password": c_pass,
                        "dsn": dsn_sid,
                        "mode": mode,
                        "tcp_connect_timeout": 2
                    }
                    if os.path.isdir(_ORACLE_CONFIG_DIR):
                        conn_kwargs_sid["config_dir"] = _ORACLE_CONFIG_DIR
                        
                    conn = oracledb.connect(**conn_kwargs_sid)
                    _log_normal_db(db_name, "UP", cfg)
                    return conn, None
                except Exception as e_sid:
                    tb_attempt2 = traceback.format_exc()
                    raise RuntimeError(f"First attempt failed:\n{tb_attempt1}\nSID fallback failed:\n{tb_attempt2}")
            else:
                raise e
    except Exception as full_e:
        tb_final = traceback.format_exc()
        err_msg = str(full_e)
        if isinstance(full_e, oracledb.DatabaseError):
            try:
                error_obj = full_e.args[0]
                if hasattr(error_obj, "code"):
                    code = error_obj.code
                    ora_prefix = f"ORA-{code:05d}"
                    detail_msg = getattr(error_obj, "message", str(full_e)).strip()
                    err_msg = f"[{ora_prefix}] {detail_msg}"
            except Exception:
                pass
        _log_normal_db(db_name, "DOWN", cfg, f"Error: {err_msg}\nTraceback:\n{tb_final}")
        return None, err_msg


def load_db_config_for_selected():
    """Load database credentials dynamically from Registry matching the selected db."""
    selected_db = st.session_state.get("selected_db")
    
    txt_path = get_txt_path()
    if not txt_path or not os.path.exists(txt_path):
        print(f"[db_connection] load_db_config_for_selected: registry file not found (selected_db={selected_db})")
        return None
        
    try:
        df = read_db_file_to_df(txt_path)
        
        if not selected_db:
            row = df.iloc[0]
        else:
            match = df[df["db_name"].astype(str).str.strip().str.lower() == str(selected_db).strip().lower()]
            if not match.empty:
                row = match.iloc[0]
            else:
                row = df.iloc[0]
                
        user = str(row["username"]).strip()
        password = str(row["password"]).strip()
        host = str(row["host"]).strip()
        port = str(row["port"]).strip()
        service_name = str(row["service_name"]).strip()
        
        # Strip trailing floating point representation (e.g. 1521.0 -> 1521)
        if port.endswith(".0"):
            port = port[:-2]
            
        dsn = f"{host}:{port}/{service_name}"

        return {
            "user": user,
            "password": password,
            "dsn": dsn,
            "host": host,
            "port": port,
            "service_name": service_name
        }
    except Exception as e:
        st.error(f"Error loading credentials from registry: {e}")
        return None

# Connection pool state
_pool = None
_current_pool_db = None

def get_connection_pool():
    """Retrieve or create an Oracle connection pool for the selected database."""
    global _pool, _current_pool_db
    selected_db = st.session_state.get("selected_db")
    
    # Reset pool if the selected database has changed
    if _pool is not None and _current_pool_db != selected_db:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None
        
    if _pool is None:
        cfg = load_db_config_for_selected()
        if cfg is None:
            return None
        try:
            mode = get_oracle_mode(cfg["user"])
            _pool = oracledb.create_pool(
                user=cfg["user"],
                password=cfg["password"],
                dsn=cfg["dsn"],
                mode=mode,
                min=1,
                max=5,
                increment=1,
                tcp_connect_timeout=3
            )
            _current_pool_db = selected_db
        except Exception as e:
            print(f"Error creating connection pool: {e}")
            _pool = None
    return _pool


class SSHCursor:
    def __init__(self, cfg, is_standby=False, standby_cfg=None):
        self.cfg = cfg
        self.is_standby = is_standby
        self.standby_cfg = standby_cfg
        self.description = None
        self._records = []
        self._index = 0

    def execute(self, query, params=None):
        records, cols = _execute_query_via_ssh(self.cfg, query, self.is_standby, self.standby_cfg)
        if records is None:
            raise RuntimeError(cols or "SSH query execution failed")
        self.description = [(col, None, None, None, None, None, None) for col in cols]
        self._records = records
        self._index = 0

    def fetchall(self):
        return self._records

    def fetchone(self):
        if self._index < len(self._records):
            r = self._records[self._index]
            self._index += 1
            return r
        return None

    def close(self):
        pass


class SSHConnection:
    def __init__(self, cfg, is_standby=False, standby_cfg=None):
        self.cfg = cfg
        self.is_standby = is_standby
        self.standby_cfg = standby_cfg

    def cursor(self):
        return SSHCursor(self.cfg, self.is_standby, self.standby_cfg)

    def close(self):
        pass


# -- Standby remote script -----------------------------------------------------
# Everything below sourcing the Production db's own .env, resolving TNS_ADMIN,
# validating tnsnames.ora and running sqlplus happens INSIDE ONE remote shell
# (single SSH exec_command call) so the sourced environment / TNS_ADMIN is not
# lost between steps. Nothing here is database-specific - the Production db
# name, Standby TNS alias and Standby credentials all come from the uploaded
# registry config (see get_config_for_db / get_standby_db_config).
_STANDBY_ENV_DISCOVERY_SCRIPT = r"""
echo "##LOG##[Standby] Production DB from uploaded configuration: $PROD_DB"
ENV_FILE_NAME="${PROD_DB}.env"
echo "##LOG##[Standby] Derived environment file: $ENV_FILE_NAME"

FOUND_ENV=""
for f in "/home/oracle/${PROD_DB}.env" "$HOME/${PROD_DB}.env" "/u01/app/oracle/${PROD_DB}.env" "/oracle/${PROD_DB}.env"; do
    if [ -f "$f" ]; then FOUND_ENV="$f"; break; fi
done
if [ -z "$FOUND_ENV" ]; then
    FOUND_ENV=$(find /home /u01 /oracle /etc -maxdepth 3 -iname "${PROD_DB}.env" 2>/dev/null | head -n 1)
fi

# A missing/unsourceable .env is not fatal by itself - ORACLE_HOME/TNS_ADMIN
# can still be discovered below (running pmon process -> /etc/oratab ->
# known default). Only the later hard checks (TNS_ADMIN dir, tnsnames.ora)
# actually stop the flow.
if [ -z "$FOUND_ENV" ] || [ ! -f "$FOUND_ENV" ]; then
    echo "##LOG##[Standby] WARNING: Could not find ${ENV_FILE_NAME} on Production - will try to discover ORACLE_HOME another way"
else
    echo "##LOG##[Standby] Environment file found: $FOUND_ENV"
    set +u
    # shellcheck disable=SC1090
    . "$FOUND_ENV" >/dev/null 2>&1
    SOURCE_RC=$?
    set -u
    if [ "$SOURCE_RC" -ne 0 ]; then
        echo "##LOG##[Standby] WARNING: Failed to source $FOUND_ENV (exit $SOURCE_RC) - will try to discover ORACLE_HOME another way"
    else
        echo "##LOG##[Standby] Environment sourced successfully"
    fi
fi

# Priority for TNS_ADMIN: 1) already set by the sourced .env (never overwrite
# a valid value) 2) derive from ORACLE_HOME, discovered in turn from: the
# sourced env -> the already-running pmon_<db> process -> /etc/oratab -> the
# known default Oracle home path.
if [ -z "${TNS_ADMIN:-}" ]; then
    echo "##LOG##[Standby] TNS_ADMIN not set by environment - discovering ORACLE_HOME"
    if [ -z "${ORACLE_HOME:-}" ]; then
        PMON_PID=$(pgrep -f -d, "pmon_${PROD_DB}" 2>/dev/null | cut -d, -f1)
        if [ -z "$PMON_PID" ]; then
            PMON_PID=$(pgrep -f -d, "pmon_$(echo "$PROD_DB" | tr '[:upper:]' '[:lower:]')" 2>/dev/null | cut -d, -f1)
        fi
        if [ -z "$PMON_PID" ]; then
            PMON_PID=$(pgrep -f -d, "pmon_$(echo "$PROD_DB" | tr '[:lower:]' '[:upper:]')" 2>/dev/null | cut -d, -f1)
        fi
        if [ -n "$PMON_PID" ] && [ -r "/proc/$PMON_PID/environ" ]; then
            ORACLE_HOME=$(tr '\0' '\n' < "/proc/$PMON_PID/environ" 2>/dev/null | grep '^ORACLE_HOME=' | cut -d= -f2-)
            if [ -n "$ORACLE_HOME" ]; then
                echo "##LOG##[Standby] ORACLE_HOME discovered from running pmon_${PROD_DB} process: $ORACLE_HOME"
            fi
        fi
        if [ -z "${ORACLE_HOME:-}" ] && [ -f /etc/oratab ]; then
            ORACLE_HOME=$(grep -i "^${PROD_DB}:" /etc/oratab 2>/dev/null | head -n 1 | cut -d: -f2)
            if [ -n "$ORACLE_HOME" ]; then
                echo "##LOG##[Standby] ORACLE_HOME discovered from /etc/oratab: $ORACLE_HOME"
            fi
        fi
        if [ -z "${ORACLE_HOME:-}" ]; then
            ORACLE_HOME="/u02/app/oracle/product/19.0.0.0/dbhome_1"
            echo "##LOG##[Standby] ORACLE_HOME not discovered - using known default: $ORACLE_HOME"
        fi
        export ORACLE_HOME
        export PATH="$ORACLE_HOME/bin:$PATH"
    fi
    TNS_ADMIN="${ORACLE_HOME}/network/admin/${PROD_DB}"
else
    echo "##LOG##[Standby] TNS_ADMIN already set by sourced environment"
fi
export TNS_ADMIN
echo "##LOG##[Standby] TNS_ADMIN resolved to: $TNS_ADMIN"

if [ -z "${TNS_ADMIN:-}" ]; then
    echo "##STAGE_ERROR##TNS_ADMIN_MISSING##TNS_ADMIN could not be determined after sourcing the environment."
    exit 92
fi

if [ -d "$TNS_ADMIN" ]; then
    echo "##LOG##[Standby] TNS_ADMIN directory exists: YES"
else
    echo "##LOG##[Standby] TNS_ADMIN directory exists: NO"
    echo "##STAGE_ERROR##TNS_ADMIN_DIR_MISSING##TNS_ADMIN directory does not exist: $TNS_ADMIN"
    exit 93
fi

if [ -f "$TNS_ADMIN/tnsnames.ora" ]; then
    echo "##LOG##[Standby] tnsnames.ora found: YES"
else
    echo "##LOG##[Standby] tnsnames.ora found: NO"
    echo "##STAGE_ERROR##TNSNAMES_MISSING##tnsnames.ora was not found under: $TNS_ADMIN"
    exit 94
fi

echo "##LOG##[Standby] Standby TNS alias: $STBY_TNS"

if ! command -v sqlplus >/dev/null 2>&1; then
    echo "##STAGE_ERROR##SQLPLUS_NOT_FOUND##sqlplus executable could not be found on Production."
    exit 95
fi

echo "##LOG##[Standby] Executing SQL*Plus on Production"
echo "##LOG##[Standby] Username: $STBY_USER"
echo "##LOG##[Standby] Mode: SYSDBA"
"""

# Single-query tail: used by _run_standby_ssh_query() for an arbitrary
# caller-supplied query (generic SSHCursor.execute() path).
# Wrapped in `timeout` so a hung sqlplus (e.g. stuck on an unexpected prompt)
# can never hang the whole SSH session forever - SQLPLUS_TIMEOUT is reported
# as its own distinct stage error.
_STANDBY_SINGLE_QUERY_TAIL = r"""
timeout -k 5 60 sqlplus -L -S "$STBY_USER/$STBY_PASS@$STBY_TNS as sysdba" <<'SQLEOF'
set markup csv on delimiter | quote off
set feedback off verify off heading on pagesize 50000 linesize 32767
prompt ##VERIFY_START##
SELECT SYS_CONTEXT('USERENV','DB_NAME') AS DB_NAME, SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM dual;
prompt ##VERIFY_END##
prompt ##DATA_START##
__QUERY__
prompt ##DATA_END##
exit;
SQLEOF
SQLPLUS_RC=$?
if [ "$SQLPLUS_RC" -eq 124 ] || [ "$SQLPLUS_RC" -eq 137 ]; then
    echo "##STAGE_ERROR##SQLPLUS_TIMEOUT##SQL*Plus did not complete within the timeout window on Production."
fi
"""

# Oracle errors that get a specific, actionable message instead of the raw
# sqlplus text - see _run_standby_ssh_query().
_STANDBY_ORA_MESSAGES = [
    ("ORA-01017", lambda ctx: "Standby Oracle authentication failed. Verify the Standby SYS credentials and TNS alias."),
    ("ORA-12154", lambda ctx: f"Standby TNS alias '{ctx.get('stby_tns','')}' could not be resolved. Resolved TNS_ADMIN: {ctx.get('tns_admin') or 'unknown'}"),
    ("ORA-12514", lambda ctx: "Standby listener does not know of the requested service (ORA-12514). Check the Standby TNS alias/service name."),
    ("ORA-12541", lambda ctx: "No listener reachable on the Standby target (ORA-12541). Check the Standby host/port and listener status."),
    ("ORA-12545", lambda ctx: "Standby target host/address could not be resolved or reached (ORA-12545)."),
]


def _parse_csv_block(lines):
    """Turn a list of '|'-delimited CSV lines (first row = header) into (records, cols)."""
    import io
    import csv
    cleaned = [ln.strip() for ln in lines if ln.strip() and "oracle base" not in ln.lower() and "oracle_home" not in ln.lower()]
    if not cleaned:
        return [], []
    reader = csv.reader(io.StringIO("\n".join(cleaned)), delimiter='|')
    rows = list(reader)
    if not rows:
        return [], []
    cols = [c.strip().upper() for c in rows[0]]
    records = [[val.strip() for val in r] for r in rows[1:]]
    return records, cols


def _build_standby_script(prod_db_name, stby_tns, stby_user, stby_pass, tail):
    """Prefix the shared discovery script with the shlex-quoted dynamic values, then append `tail`."""
    import shlex
    return (
        f"PROD_DB={shlex.quote(prod_db_name)}\n"
        f"STBY_TNS={shlex.quote(stby_tns)}\n"
        f"STBY_USER={shlex.quote(stby_user)}\n"
        f"STBY_PASS={shlex.quote(stby_pass)}\n"
        + _STANDBY_ENV_DISCOVERY_SCRIPT
        + tail
    )


def _terminate_sql(query):
    """Make sure a SQL statement is self-terminated so it always runs regardless
    of what SQL*Plus command (prompt/exit) follows it in the script."""
    q = query.strip()
    return q if q.endswith(";") else q + ";"


def _extract_ora_line(text):
    """Return the first raw ORA-/SP2- error line found in `text`, or ''."""
    import re
    m = re.search(r'((?:ORA|SP2)-\d{4,5}[^\n\r]*)', text)
    return m.group(1).strip() if m else ""


def _parse_standby_script_output(out_content, err_content, stby_tns):
    """
    Shared marker/error parsing for the standby remote script's stdout: logs
    every ##LOG## line, and checks for a ##STAGE_ERROR## marker or a known
    ORA- code, then parses the ##VERIFY_START##/##VERIFY_END## block.
    Returns (error_message_or_None, verify_records, verify_cols).
    A non-None error means the caller must stop and return that error - do
    not use verify_records/verify_cols in that case (they will be None).
    """
    combined = out_content + "\n" + err_content
    tns_admin_seen = ""
    stage_error = None
    for raw_line in out_content.splitlines():
        line = raw_line.strip()
        if line.startswith("##STAGE_ERROR##"):
            parts = line.split("##")
            # ['', 'STAGE_ERROR', '<CODE>', '<message>']
            stage_error = parts[3] if len(parts) > 3 else "Standby connection failed."
        elif line.startswith("##LOG##"):
            log_msg = line[len("##LOG##"):]
            _log(log_msg)
            print(log_msg)
            if "TNS_ADMIN resolved to:" in log_msg:
                tns_admin_seen = log_msg.split("TNS_ADMIN resolved to:", 1)[1].strip()

    if stage_error:
        return f"Standby connection failed: {stage_error}", None, None

    ctx = {"stby_tns": stby_tns, "tns_admin": tns_admin_seen}
    for ora_code, make_msg in _STANDBY_ORA_MESSAGES:
        if ora_code in combined:
            return f"Standby connection failed: {make_msg(ctx)}", None, None

    verify_lines = []
    if "##VERIFY_START##" in out_content and "##VERIFY_END##" in out_content:
        verify_block = out_content.split("##VERIFY_START##", 1)[1].split("##VERIFY_END##", 1)[0]
        verify_lines = verify_block.splitlines()
    verify_records, verify_cols = _parse_csv_block(verify_lines)

    if not verify_records:
        ora_line = _extract_ora_line(combined)
        if ora_line:
            return f"Standby connection failed: {ora_line}", None, None
        tail = "\n".join([l.strip() for l in out_content.splitlines() if l.strip()][-5:])
        return f"Standby connection failed: SQL*Plus did not return a valid connection on Production.\n{tail}", None, None

    try:
        db_name_idx = verify_cols.index("DB_NAME")
        connected_db_name = verify_records[0][db_name_idx]
        _log("[Standby] SQL*Plus connection successful")
        _log(f"[Standby] Connected DB_NAME: {connected_db_name}")
        print(f"[Standby] Connected DB_NAME: {connected_db_name}")
    except (ValueError, IndexError):
        pass

    return None, verify_records, verify_cols


def _run_standby_ssh_query(client, prod_db_name, stby_tns, stby_user, stby_pass, query):
    """
    Run the full Production -> SSH -> source .env -> resolve TNS_ADMIN ->
    verify tnsnames.ora -> sqlplus -> Standby flow in ONE remote shell, for a
    single arbitrary query (generic SSHCursor.execute() path).
    Returns (records, cols) on success, or (None, error_message) on failure.
    """
    tail = _STANDBY_SINGLE_QUERY_TAIL.replace("__QUERY__", _terminate_sql(query))
    script = _build_standby_script(prod_db_name, stby_tns, stby_user, stby_pass, tail)

    # Longer than the remote `timeout -k 5 60` wrapper around sqlplus, so the
    # SSH channel itself never cuts off before that remote timeout can fire.
    stdin, stdout, stderr = client.exec_command(script, timeout=75)
    out_content = stdout.read().decode(errors="replace")
    err_content = stderr.read().decode(errors="replace")

    error, verify_records, verify_cols = _parse_standby_script_output(out_content, err_content, stby_tns)
    if error:
        return None, error

    # -- Parse the actual query result -------------------------------------
    if "##DATA_START##" not in out_content or "##DATA_END##" not in out_content:
        return None, "Standby connection failed: Standby query did not return a result on Production."
    data_block = out_content.split("##DATA_START##", 1)[1].split("##DATA_END##", 1)[0]
    _log("[Standby] Executing Standby replication query")
    records, cols = _parse_csv_block(data_block.splitlines())
    return records, cols


def _open_ssh_client(cfg):
    """
    Resolve the OCI key and open an authenticated SSH connection to
    cfg['host'] as cfg['host_username']. Raises on failure (missing
    host/username, or the connection itself failing). Never sends a
    password - key-based auth only (see get_bundled_oci_key()).
    """
    import paramiko

    ssh_host = cfg.get("host")
    ssh_user = cfg.get("host_username")
    if not ssh_host or not ssh_user:
        raise RuntimeError("SSH host or username not configured on primary host")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    key_file = (
        cfg.get("key_filename") or
        cfg.get("ssh_key_path") or
        cfg.get("oci_key_path") or
        cfg.get("ssh_key") or
        cfg.get("oci_key")
    )
    if not key_file:
        key_file = get_bundled_oci_key(ssh_host)
    if not key_file:
        for default_key in [
            os.path.expanduser("~/.oci/oci_api_key.pem"),
            os.path.expanduser("~/.oci/id_rsa"),
            os.path.expanduser("~/.ssh/id_rsa"),
            os.path.expanduser("~/.ssh/id_ed25519"),
            os.path.expanduser("~/.ssh/oci_id_rsa"),
            os.path.expanduser("~/.ssh/id_ecdsa"),
        ]:
            if os.path.isfile(default_key):
                key_file = default_key
                break

    connect_kwargs = {
        "hostname": ssh_host,
        "port": 22,
        "username": ssh_user,
        "timeout": 5,
        "look_for_keys": True,
        "allow_agent": True,
    }
    if key_file and os.path.isfile(key_file):
        connect_kwargs["key_filename"] = key_file

    client.connect(**connect_kwargs)
    return client


def _execute_query_via_ssh(cfg, query, is_standby=False, standby_cfg=None):
    """Execute a query via SSH using SQL*Plus on the host, returning (records, cols)."""
    import io
    import csv

    ssh_host = cfg.get("host")
    ssh_user = cfg.get("host_username")

    if not ssh_host or not ssh_user:
        return None, "SSH host or username not configured on primary host"

    if is_standby:
        # Standby is reached ONLY via: PC -> SSH (OCI key) -> Production ->
        # source <PROD_DB>.env -> resolve/verify TNS_ADMIN -> sqlplus -> Standby.
        # See _run_standby_ssh_query() for the full staged flow.
        if not standby_cfg:
            return None, "Standby connection failed: Standby configuration missing"

        db_name = str(cfg.get("db_name") or "").strip()
        if not db_name:
            return None, "Standby connection failed: Primary db_name not available - cannot locate its .env file on the primary host"

        stby_tns = _clean_stby_service_name(standby_cfg)
        stby_user = str(standby_cfg.get("user") or "").strip()
        stby_pass = str(standby_cfg.get("password") or "")
        if not stby_tns:
            return None, "Standby connection failed: Standby TNS alias/service name is not configured in the registry for this database"
        if not stby_user:
            return None, "Standby connection failed: Standby username is not configured in the registry for this database"
        if not stby_pass:
            return None, "Standby connection failed: Standby password is not configured in the registry for this database"
    else:
        # Connect to primary locally on the host
        user = cfg.get("user")
        password = cfg.get("password")
        if user.lower() == "sys":
            login_str = '"/ as sysdba"'
        else:
            login_str = f'"{user}/{password}"'

        svc = cfg.get("service_name")
        env_setup = (
            f"export ORACLE_SID={svc}; "
            "export ORAENV_ASK=NO; "
            "if [ -f /usr/local/bin/oraenv ]; then . /usr/local/bin/oraenv; "
            "elif [ -f /opt/oracle/dcs/bin/oraenv ]; then . /opt/oracle/dcs/bin/oraenv; fi; "
        )
        # Escape dollar signs in query for bash shell execution
        escaped_query = query.replace("$", "\\$")
        cmd = (
            env_setup +
            f"sqlplus -S {login_str} <<EOF\n"
            "set markup csv on delimiter | quote off\n"
            "set feedback off verify off heading on pagesize 50000 linesize 32767;\n"
            f"{escaped_query}\n"
            "exit;\n"
            "EOF"
        )

    try:
        client = _open_ssh_client(cfg)
    except Exception as e:
        print(f"[SSH FALLBACK] SSH connect failed: {e}")
        if is_standby:
            return None, f"Standby connection failed: SSH connection to Production failed. ({e})"
        return None, str(e)

    try:
        if is_standby:
            records, cols = _run_standby_ssh_query(client, db_name, stby_tns, stby_user, stby_pass, query)
            client.close()
            return records, cols

        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
        out_content = stdout.read().decode(errors='replace').strip()
        client.close()

        lines = []
        for line in out_content.splitlines():
            if "oracle base" in line.lower() or "oracle_home" in line.lower():
                continue
            if line.strip():
                lines.append(line.strip())

        if not lines:
            return [], []

        csv_data = "\n".join(lines)
        f_in = io.StringIO(csv_data)
        reader = csv.reader(f_in, delimiter='|')
        rows = list(reader)
        if not rows:
            return [], []

        cols = [c.strip().upper() for c in rows[0]]
        records = []
        for r in rows[1:]:
            records.append([val.strip() for val in r])

        return records, cols
    except Exception as e:
        try:
            client.close()
        except Exception:
            pass
        print(f"[SSH FALLBACK] SSH Query execution failed: {e}")
        if is_standby:
            return None, f"Standby connection failed: {e}"
        return None, str(e)


def get_db_connection():
    """Get a connection from the pool, or fallback to direct connection."""
    pool = get_connection_pool()
    if pool is not None:
        try:
            return pool.acquire()
        except Exception:
            pass # Fall through to direct connection if pool acquire fails
            
    # Direct connection attempt
    cfg = load_db_config_for_selected()
    if cfg is None:
        return None
    try:
        mode = get_oracle_mode(cfg["user"])
        return oracledb.connect(
            user=cfg["user"],
            password=cfg["password"],
            dsn=cfg["dsn"],
            mode=mode,
            tcp_connect_timeout=3
        )
    except Exception as e:
        print(f"Failed to establish direct connection: {e}")
        if cfg.get("host_username") and cfg.get("host_password"):
            print(f"[SSH FALLBACK] Returning SSHConnection for selected DB")
            return SSHConnection(cfg)
        return None

def execute_query(query, params=None, conn=None):
    """Safely execute a query, returning records and description."""
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True
        
    if conn is None:
        return None, "Database Connection Failed"
    
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        records = cursor.fetchall()
        cols = [col[0] for col in cursor.description]
        cursor.close()
        if should_close:
            conn.close()
        return records, cols
    except Exception as e:
        if should_close:
            try:
                conn.close()
            except Exception:
                pass
        return None, str(e)

def execute_query_to_df(query, params=None, conn=None):
    """Safely execute a query and return it as a pandas DataFrame with normalized columns."""
    records, cols = execute_query(query, params, conn=conn)
    if records is None:
        # Return empty DataFrame
        return pd.DataFrame()
    
    df = pd.DataFrame(records, columns=cols)
    # Normalize column names to uppercase
    df.columns = [col.upper() for col in df.columns]
    return df


def execute_proc_to_df(proc_name, params=None, conn=None):
    """Safely execute a stored procedure returning a SYS_REFCURSOR as a DataFrame."""
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True
        
    if conn is None:
        return pd.DataFrame()
    
    try:
        import oracledb
        cursor = conn.cursor()
        out_var = cursor.var(oracledb.CURSOR)
        
        if params:
            call_params = params + [out_var]
            cursor.callproc(proc_name, call_params)
        else:
            cursor.callproc(proc_name, [out_var])
            
        out_cursor = out_var.getvalue()
        if out_cursor is None:
            cursor.close()
            if should_close: conn.close()
            return pd.DataFrame()
            
        records = out_cursor.fetchall()
        cols = [col[0] for col in out_cursor.description]
        
        out_cursor.close()
        cursor.close()
        if should_close:
            conn.close()
            
        df = pd.DataFrame(records, columns=cols)
        df.columns = [col.upper() for col in df.columns]
        return df
    except Exception as e:
        print(f"Error executing procedure {proc_name}: {e}")
        if should_close:
            try:
                conn.close()
            except Exception:
                pass
        return pd.DataFrame()

def execute_non_query(query, params=None):
    """Safely execute a DDL/DML query (e.g. ALTER SYSTEM, UPDATE) without fetching records."""
    conn = get_db_connection()
    if conn is None:
        return False, "Database Connection Failed"
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        cursor.close()
        conn.close()
        return True, None
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return False, str(e)
