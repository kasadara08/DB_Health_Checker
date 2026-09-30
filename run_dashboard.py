import os
import sys

# -- stand-alone CLI check (Subprocess mode for Thick mode connection check) --
# Placing this at the absolute top prevents loading heavy modules like streamlit/pandas in subprocesses.
if "--check-reporting-db" in sys.argv:
    import json
    import socket
    import time
    import datetime
    import oracledb

    try:
        idx = sys.argv.index("--check-reporting-db")
        db_name = sys.argv[idx + 1]
        registry_path = sys.argv[idx + 2]
        result_file = sys.argv[idx + 3]
        debug_log_path = sys.argv[idx + 4]
    except Exception:
        sys.exit(1)

    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    _LOG_FILE = debug_log_path

    def _log_direct(msg):
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] {msg}\n")
        except Exception:
            pass

    _log_direct("=== db_connection module imported ===")

    def get_reporting_db_config_standalone(db_name: str, registry_path: str) -> dict:
        if not registry_path or not os.path.exists(registry_path):
            return None
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            REGISTRY_COLUMNS = [
                "db_name", "host", "port", "service_name", "username", "password", "host_username", "host_password",
                "reporting_db_name", "reporting_host", "reporting_port", "reporting_service_name",
                "reporting_username", "reporting_password",
                "standby_db_name", "standby_host", "standby_port", "standby_service_name",
                "standby_username", "standby_password",
            ]
            
            for line in lines:
                s_ln = line.strip()
                if not s_ln or s_ln.startswith("#"):
                    continue
                parts = s_ln.split()
                if not parts:
                    continue
                if parts[0].strip().lower() != db_name.strip().lower():
                    continue
                
                row = [""] * len(REGISTRY_COLUMNS)
                for idx, part in enumerate(parts):
                    if idx < len(REGISTRY_COLUMNS):
                        row[idx] = part.strip()
                
                row_dict = {}
                for idx, col in enumerate(REGISTRY_COLUMNS):
                    row_dict[col] = row[idx]
                
                def _first_val(keys):
                    for k in keys:
                        val = row_dict.get(k, "").strip()
                        if val and val.lower() not in ("none", "nan", "n/a", "-"):
                            return val
                    return ""
                
                r_db   = _first_val(["reporting_db_name", "reporting_db", "reporting db name", "reporting db", "rpt_db"])
                r_host = _first_val(["reporting_host", "reporting host", "rpt_host"])
                r_port = _first_val(["reporting_port", "reporting port", "rpt_port"])
                r_svc  = _first_val(["reporting_service_name", "reporting_server", "reporting server name", "reporting server", "reporting_service", "rpt_service", "server_name"])
                r_user = _first_val(["reporting_username", "reporting_user", "reporting username", "rpt_user"])
                r_pwd  = _first_val(["reporting_password", "reporting_pass", "reporting password", "rpt_password"])
                
                if not all([r_db, r_host, r_port, r_svc, r_user]):
                    return None
                
                if r_port.endswith(".0"):
                    r_port = r_port[:-2]
                
                r_host = r_host.strip().rstrip("/").rstrip(":")
                r_port = r_port.strip()
                r_svc  = r_svc.strip().strip("/").strip(".")
                r_user = r_user.strip()
                
                dsn = f"{r_host}:{r_port}/{r_svc}"
                return {
                    "db_name":      r_db,
                    "user":         r_user,
                    "password":     r_pwd,
                    "dsn":          dsn,
                    "host":         r_host,
                    "port":         r_port,
                    "service_name": r_svc,
                }
        except Exception:
            pass
        return None

    _log = _log_direct

    def check_connection(cfg: dict) -> dict:
        import traceback
        rpt_name = cfg["db_name"]
        user     = cfg["user"]
        password = cfg["password"]
        dsn      = cfg["dsn"]
        host     = cfg["host"]
        port     = cfg["port"]
        svc      = cfg["service_name"]
        
        _log("--- _ensure_thick_mode starting ---")
        _log(f"sys.frozen: {getattr(sys, 'frozen', False)}")
        _log(f"sys.executable: {sys.executable}")
        _log(f"_EXE_DIR: {_EXE_DIR}")
        
        internal_path  = os.path.join(_EXE_DIR, "_internal", "oracle_client")
        local_path     = os.path.join(_EXE_DIR, "oracle_client")
        meipass_path   = os.path.join(getattr(sys, "_MEIPASS", ""), "oracle_client")
        candidate_paths = [internal_path, local_path, meipass_path]
        
        _log("Initial candidate paths:")
        for p in candidate_paths:
            _log(f"  '{p}' -> oci.dll exists: {os.path.exists(os.path.join(p, 'oci.dll')) if p else False}")
            
        thick_ok = False
        for p in candidate_paths:
            if p and os.path.exists(os.path.join(p, 'oci.dll')):
                _log(f"Attempting init_oracle_client(lib_dir='{p}')")
                try:
                    if os.name == "nt":
                        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                        if hasattr(os, "add_dll_directory"):
                            try:
                                os.add_dll_directory(p)
                            except Exception as add_dll_err:
                                _log(f"Warning: os.add_dll_directory failed: {add_dll_err}")
                    oracledb.init_oracle_client(lib_dir=p)
                    thick_ok = True
                    _log(f"SUCCESS - Thick mode enabled from: {p}")
                    break
                except Exception as e:
                    err_msg = str(e)
                    if "already initialized" in err_msg.lower() or "already been called" in err_msg.lower():
                        thick_ok = True
                        _log("Already initialized - treating as success")
                        break
                    _log(f"FAILED - init_oracle_client error: {err_msg}")
                    
        _log("=" * 60)
        _log(f"_check_reporting_db_status_direct() called for db_name='{db_name}'")
        _log(f"Thick mode status at entry: _thick_mode_initialized={thick_ok}")
        _log(f"Registry txt path at entry : {registry_path}")
        _log(f"Registry file used  : {registry_path}")
        _log(f"Reporting DB name   : {rpt_name}")
        _log(f"Host                : {host}")
        _log(f"Port                : {port}")
        _log(f"Service name        : {svc}")
        _log(f"Username            : {user}")
        _log(f"Password            : *************")
        _log(f"DSN (full connect)  : {dsn}")
        _log("Forcing Thick mode initialization before connecting...")
        _log(f"Thick mode result: {thick_ok} (_thick_mode_initialized={thick_ok})")

        def _tcp_ping():
            try:
                s = socket.create_connection((host, int(port)), timeout=2)
                s.close()
                return True
            except Exception:
                return False

        failed_attempts_logs = []
        
        def _parse_error_details(e):
            tb = traceback.format_exc()
            failed_attempts_logs.append(tb)
            _log(f"[CHECK ERROR] Exception: {type(e).__name__}: {e}\n{tb}")
            if isinstance(e, oracledb.DatabaseError):
                try:
                    error_obj = e.args[0]
                    if hasattr(error_obj, "code"):
                        code = error_obj.code
                        detail = getattr(error_obj, "message", str(e)).strip()
                        ora_code = f"ORA-{code:05d}"
                        if code in (1017, 28000, 28001, 28009, 12514, 1034):
                            return "DOWN", "UP", ora_code, detail
                        if code in (12541, 12170, 12535, 12154, 12505):
                            return "DOWN", "DOWN", ora_code, detail
                        listener_status = "UP" if _tcp_ping() else "DOWN"
                        return "DOWN", listener_status, ora_code, detail
                except Exception:
                    pass
            listener_status = "UP" if _tcp_ping() else "DOWN"
            return "DOWN", listener_status, "CLIENT_ERROR", str(e)

        start_time = time.time()
        status = "DOWN"
        listener = "DOWN"
        error_code = None
        error_message = None
        
        # 1. Run lightweight TCP ping check first
        if not _tcp_ping():
            _log("TCP ping failed - host/listener is unreachable. Proceeding with connection attempt anyway...")

        try:
            if not thick_ok:
                raise RuntimeError("Oracle Thick mode failed to initialize (missing DLLs).")
            
            _log("--- Attempt 1: Using get_oracle_mode(user) ---")
            mode = 2 if str(user).lower() == "sys" else 0
            _log(f"Attempt 1 mode = {mode}")
            _log(f"[Attempt1/ServiceName] Trying oracledb.connect(user={user}, dsn={dsn}, mode={mode})")
            
            conn = oracledb.connect(user=user, password=password, dsn=dsn, mode=mode, tcp_connect_timeout=2)
            conn.close()
            _log("[Attempt1/ServiceName] SUCCESS - connection established!")
            _log("Attempt 1 SUCCESS -> Status = UP")
            status = "UP"
            listener = "UP"
        except Exception as e:
            tb_attempt1 = traceback.format_exc()
            failed_attempts_logs.append(f"Attempt 1 failed:\n{tb_attempt1}")
            _log(f"[Attempt1/ServiceName] FAILED - error: {e}")
            
            if mode == 0:
                # Mode is already 0, skip Attempt 2 (avoid duplicating the same connection attempt)
                _log("Mode is already 0. Skipping Attempt 2 fallback.")
                err_low = str(e).lower()
                if any(t in err_low for t in ["dpy-6001", "12505", "12154", "service name"]):
                    dsn_sid = f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port}))(CONNECT_DATA=(SID={svc})))"
                    _log(f"[Attempt 3: Trying SID fallback DSN={dsn_sid} ...]")
                    try:
                        conn = oracledb.connect(user=user, password=password, dsn=dsn_sid, mode=0, tcp_connect_timeout=2)
                        conn.close()
                        _log("SID fallback SUCCESS - connection established!")
                        status = "UP"
                        listener = "UP"
                    except Exception as esid:
                        _log(f"SID fallback FAILED - error: {esid}")
                        status, listener, error_code, error_message = _parse_error_details(esid)
                else:
                    status, listener, error_code, error_message = _parse_error_details(e)
            else:
                _log("--- Attempt 2: Trying DEFAULT_AUTH DSN=... ---")
                _log(f"[Attempt2/ServiceName] Trying oracledb.connect(user={user}, dsn={dsn}, mode=0)")
                try:
                    conn = oracledb.connect(user=user, password=password, dsn=dsn, mode=0, tcp_connect_timeout=2)
                    conn.close()
                    _log("[Attempt2/ServiceName] SUCCESS - connection established!")
                    _log("Attempt 2 SUCCESS -> Status = UP")
                    status = "UP"
                    listener = "UP"
                except Exception as e2:
                    tb_attempt2 = traceback.format_exc()
                    failed_attempts_logs.append(f"Attempt 2 failed:\n{tb_attempt2}")
                    _log(f"[Attempt2/ServiceName] FAILED - error: {e2}")
                    err_low = (str(e) + " " + str(e2)).lower()
                    if any(t in err_low for t in ["dpy-6001", "12505", "12154", "service name"]):
                        dsn_sid = f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={host})(PORT={port}))(CONNECT_DATA=(SID={svc})))"
                        _log(f"[Attempt 3: Trying SID fallback DSN={dsn_sid} ...]")
                        try:
                            conn = oracledb.connect(user=user, password=password, dsn=dsn_sid, mode=0, tcp_connect_timeout=2)
                            conn.close()
                            _log("SID fallback SUCCESS - connection established!")
                            status = "UP"
                            listener = "UP"
                        except Exception as esid:
                            _log(f"SID fallback FAILED - error: {esid}")
                            status, listener, error_code, error_message = _parse_error_details(esid)
                    else:
                        status, listener, error_code, error_message = _parse_error_details(e2)

        response_time_ms = int((time.time() - start_time) * 1000)
        checked_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _log(f"[CHECK END] Status={status} Listener={listener} Time={response_time_ms}ms Error={error_code}")
        
        return {
            "configured": True,
            "status": status,
            "listener": listener,
            "reporting_db_name": rpt_name,
            "error": error_message or "",
            "error_code": error_code,
            "error_message": error_message,
            "response_time_ms": response_time_ms,
            "checked_at": checked_at,
            "reporting_username": user,
            "reporting_password": password,
            "failed_attempts_logs": failed_attempts_logs,
            "host": host,
            "port": port,
            "service_name": svc
        }

    try:
        cfg = get_reporting_db_config_standalone(db_name, registry_path)
        if not cfg:
            res = {
                "configured": False,
                "status": "NOT_CONFIGURED",
                "listener": "NOT_CONFIGURED",
                "reporting_db_name": "",
                "error": "No config found in registry",
                "error_code": "NOT_CONFIGURED",
                "error_message": "No config found in registry",
                "response_time_ms": 0,
                "checked_at": "",
                "reporting_username": "",
                "reporting_password": ""
            }
        else:
            res = check_connection(cfg)
            
        with open(result_file, "w", encoding="utf-8") as rf:
            rf.write(json.dumps(res))
    except Exception as ex:
        pass
    sys.exit(0)

import socket
import webbrowser
import time
import streamlit.web.cli as stcli

# === Explicit Imports for PyInstaller ===
# By placing these here, PyInstaller statically analyzes and bundles them.
import monitor_thread
import db_connection
import dashboard.home
import dashboard.monitoring
import queries.queries
import utils.alerts
import utils.storage_provider
import utils.ssh_mount_provider

PORT = 8501

def is_port_in_use(port: int) -> bool:
    """Check if a given port is already listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0

def open_browser(port: int):
    """Open Firefox (or default browser) to the dashboard URL."""
    url = f"http://127.0.0.1:{port}"
    ff_paths = [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
    ]
    import subprocess
    for path in ff_paths:
        if os.path.exists(path):
            subprocess.Popen([path, url])
            return
    # Fallback to system default browser
    webbrowser.open(url)

if __name__ == "__main__":
    # -- If dashboard is already running and healthy, just open browser --------------
    if is_port_in_use(PORT):
        import urllib.request
        is_healthy = False
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/_stcore/health", timeout=2)
            if req.getcode() == 200:
                is_healthy = True
        except Exception:
            pass

        if is_healthy:
            print(f"[LAUNCHER] Dashboard is already active on http://127.0.0.1:{PORT}. Opening browser...")
            open_browser(PORT)
            time.sleep(2)
            sys.exit(0)
        else:
            print(f"[LAUNCHER] Port {PORT} is held by an inactive process. Finding available port...")
            for p in range(8502, 8520):
                if not is_port_in_use(p):
                    PORT = p
                    break
            print(f"[LAUNCHER] Starting Dashboard on fallback port {PORT}...")

    # -- Determine base directory -----------------------------------------
    if getattr(sys, "frozen", False):
        current_dir = sys._MEIPASS
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))

    app_path = os.path.join(current_dir, "app.py")

    # Allow external app.py override when frozen
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        external = os.path.join(exe_dir, "app.py")
        if os.path.exists(external):
            app_path = external

    # -- Write force_refresh flag so the monitor thread runs immediately --
    try:
        config_dir = os.path.join(current_dir, "config")
        if getattr(sys, "frozen", False):
            config_dir = os.path.join(os.path.dirname(sys.executable), "config")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "force_refresh.flag"), "w") as frf:
            frf.write("1")
    except Exception:
        pass

    # -- Configure Streamlit ----------------------------------------------
    # disconnectedSessionTTL=2 → Streamlit drops a session 2 s after the
    # browser tab is closed, allowing the session-monitor thread to detect
    # the empty runtime and call os._exit(0) automatically.
    sys.argv = [
        "streamlit", "run", app_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        f"--server.port={PORT}",
        "--server.address=127.0.0.1",
        "--server.disconnectedSessionTTL=2"
    ]

    # Open browser after short delay
    import threading
    def _open():
        time.sleep(3)
        open_browser(PORT)
    threading.Thread(target=_open, daemon=True).start()

    # -- Start Streamlit (blocking) ---------------------------------------
    sys.exit(stcli.main())