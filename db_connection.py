import os
import json
import oracledb
import pandas as pd
import streamlit as st

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
REGISTRY_COLUMNS = [
    "db_name", "host", "port", "service_name", "username", "password", "host_username", "host_password",
    "reporting_db_name", "reporting_host", "reporting_port", "reporting_service_name",
    "reporting_username", "reporting_password", "reporting_host_username", "reporting_host_password"
]

def get_txt_path():
    # Dynamically get the path of the database registry file (cross-platform)."""
    # 1. Check session state from a cache (custom_txt_path is already exits in session state)
    try:
        if "custom_txt_path" in st.session_state and st.session_state.custom_txt_path:
            p = st.session_state.custom_txt_path
            resolved_p = p if os.path.isabs(p) else os.path.join(_BASE_DIR, p)   # os.path.isabs(p) => true if p is absolute path, else false.  os.path.join(_BASE_DIR, p) => join the base dir with p to make it absolute path
            if os.path.exists(resolved_p):  #Checks whether the file actually exists.
                return resolved_p
            return p
    except Exception:
        pass

    # 2. Check JSON configuration file
    if os.path.exists(CONFIG_FILE):  #if the config file exists, read it and get the txt_path value
        try:
            with open(CONFIG_FILE, "r") as f: 
                cfg = json.load(f)  #cfg is the one  which reads and parses the JSON file into a Python dictionary.
                path = cfg.get("txt_path")  #txt_path is a KEY in the JSON file (file name is db_path_config.json)
                if path:
                    resolved_path = path if os.path.isabs(path) else os.path.join(_BASE_DIR, path)  # os.path.isabs(p) => true if p is absolute path, else false.  os.path.join(_BASE_DIR, p) => join the base dir with p to make it absolute path
                    if os.path.exists(resolved_path):   #Checks whether the file actually exists.
                        st.session_state.custom_txt_path = resolved_path    # (st.session_state.custom_txt_path is present in app.py, home.py)   st.session_state.custom_txt_path => User selects a file from History or last used registory  
                        return resolved_path
                    st.session_state.custom_txt_path = path # if file path is not exists, still store the path in session state (it may be a relative path or a non-existent file)
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
        return history[0]   # only one entry — fall back to same
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

        # ── Trigger fresh monitoring cycle on every registry change ──────
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
    # Position 2 is 'port' — if it's a number it's data, if text it's a header
    if len(first_row_values) >= 3:
        try:
            float(str(first_row_values[2]).strip())
            return False  # Port is numeric → this is a data row, no header
        except ValueError:
            return True   # Port field is text → this is likely a header row
    return True  # Fewer than 3 columns — assume header present

def derive_reporting_db_name(db_name):
    """
    Reporting DB Name rule (always lowercase r):
      ends with P or p -> replace last char with r   e.g. fa114p -> fa114r
      otherwise        -> append r                   e.g. fa114  -> fa114r
    """
    if not db_name:
        return "r"
    if db_name.endswith("p") or db_name.endswith("P"):
        return db_name[:-1] + "r"
    return db_name + "r"


def parse_db_line_parts(parts):
    res = {}
    n = len(parts)
    if n >= 14:
        res["db_name"]                 = parts[0]
        res["host"]                    = parts[1]
        res["port"]                    = parts[2]
        res["service_name"]            = parts[3]
        res["username"]                = parts[4]
        res["password"]                = parts[5]
        res["host_username"]           = parts[6]
        res["host_password"]           = parts[7]
        res["reporting_db_name"]       = parts[8]
        res["reporting_host"]          = parts[9]
        res["reporting_port"]          = parts[10]
        res["reporting_service_name"]  = parts[11]
        res["reporting_username"]      = parts[12]
        res["reporting_password"]      = parts[13]
    elif n >= 8:
        res["db_name"]                 = parts[0]
        res["host"]                    = parts[1]
        res["port"]                    = parts[2]
        res["service_name"]            = parts[3]
        res["username"]                = parts[4]
        res["password"]                = parts[5]
        res["host_username"]           = parts[6]
        res["host_password"]           = parts[7]
    elif n == 6:
        res["db_name"]                 = parts[0]
        res["host"]                    = parts[1]
        res["port"]                    = parts[2]
        res["service_name"]            = parts[3]
        res["username"]                = parts[4]
        res["password"]                = parts[5]
    elif n == 3:
        res["db_name"]                 = parts[0]
        res["password"]                = parts[1]
        res["reporting_password"]      = parts[2]
    elif n == 2:
        res["db_name"]                 = parts[0]
        res["password"]                = parts[1]
    elif n == 1:
        res["db_name"]                 = parts[0]
    return res


def replace_in_template(template, old, new):
    if not template:
        return template
    if not old:
        return template
    if old in template:
        return template.replace(old, new, 1)
    lo_tmpl = template.lower()
    lo_old  = old.lower()
    if lo_old in lo_tmpl:
        idx = lo_tmpl.index(lo_old)
        return template[:idx] + new + template[idx + len(old):]
    return template


def parse_smart_txt_to_df(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [ln.rstrip() for ln in f]
    except Exception as e:
        print(f"[config] Error reading registry file: {e}")
        return pd.DataFrame(columns=REGISTRY_COLUMNS)

    global_key_map = {
        "db host": "host", "host": "host", "db port": "port", "port": "port",
        "db username": "username", "db user": "username", "username": "username", "user": "username",
        "host username": "host_username", "host user": "host_username", "os username": "host_username", "os user": "host_username",
        "host password": "host_password", "host pass": "host_password", "os password": "host_password", "os pass": "host_password",
        "reporting port": "reporting_port", "report port": "reporting_port", "rpt port": "reporting_port",
        "reporting username": "reporting_username", "reporting user": "reporting_username", "report username": "reporting_username",
        "report user": "reporting_username", "rpt username": "reporting_username", "rpt user": "reporting_username",
        "db service suffix": "service_suffix", "service suffix": "service_suffix",
    }

    reporting_key_map = {
        "reporting host": "reporting_host", "report host": "reporting_host", "rpt host": "reporting_host",
        "reporting server": "reporting_service_name", "report server": "reporting_service_name",
        "reporting service": "reporting_service_name", "reporting service name": "reporting_service_name",
        "reporting server name": "reporting_service_name", "rpt server": "reporting_service_name", "server": "reporting_service_name",
    }

    global_cfg = {
        "host": "", "port": "", "username": "", "host_username": "", "host_password": "",
        "reporting_port": "", "reporting_username": "", "service_suffix": "",
    }
    
    groups = []
    current_grp = None

    non_blank = []
    for i, ln in enumerate(lines):
        s_ln = ln.strip()
        if s_ln and not s_ln.startswith("#"):
            non_blank.append((i, s_ln))
            
    for pos, (orig_idx, line) in enumerate(non_blank):
        if "=" in line:
            key_raw, val = line.split("=", 1)
            key = key_raw.strip().lower()
            val = val.strip()

            if key in reporting_key_map:
                field = reporting_key_map[key]
                if field == "reporting_host":
                    current_grp = {
                        "reporting_host"        : val,
                        "reporting_service_name": "",
                        "reporting_port"        : "",
                        "reporting_username"    : "",
                        "reporting_password_template": "",
                        "dbs"                   : [],
                    }
                    groups.append(current_grp)
                elif current_grp is not None:
                    if field == "reporting_password":
                        current_grp["reporting_password_template"] = val
                    else:
                        current_grp[field] = val
                else:
                    global_cfg[field] = val
            elif key in global_key_map:
                global_cfg[global_key_map[key]] = val
        else:
            is_header = False
            # Check if this plain line is a section header (e.g. has spaces and contains header-like words)
            parts = line.split()
            line_lower = line.lower()
            if len(parts) > 1 and any(w in line_lower for w in ["server", "group", "reporting", "default", "class"]):
                is_header = True
            elif len(parts) > 1:
                # If it has spaces but no keyword, use lookahead to see if upcoming lines contain '='
                for upcoming_pos in range(pos + 1, min(pos + 10, len(non_blank))):
                    upcoming_line = non_blank[upcoming_pos][1]
                    if "=" in upcoming_line:
                        is_header = True
                        break
                    break

                
            if is_header:
                current_grp = {
                    "reporting_host"        : "",
                    "reporting_service_name": "",
                    "reporting_port"        : "",
                    "reporting_username"    : "",
                    "reporting_password_template": "",
                    "dbs"                   : [],
                }
                groups.append(current_grp)
            else:
                parts = line.split()
                if parts:
                    db_dict = parse_db_line_parts(parts)
                    if current_grp is None:
                        current_grp = {
                            "reporting_host"        : global_cfg.get("reporting_host", ""),
                            "reporting_service_name": global_cfg.get("reporting_service_name", ""),
                            "reporting_port"        : global_cfg.get("reporting_port", ""),
                            "reporting_username"    : global_cfg.get("reporting_username", ""),
                            "reporting_password_template": global_cfg.get("reporting_password", ""),
                            "dbs"                   : [],
                        }
                        groups.append(current_grp)
                    current_grp["dbs"].append(db_dict)

    rows = []
    
    first_db = None
    for group in groups:
        if group.get("dbs"):
            first_db = group["dbs"][0]
            break
            
    resolved_globals = {**global_cfg}
    db_tmpl = ""
    db_ref = ""
    rpt_tmpl = ""
    rpt_ref = ""
    svc_tmpl = ""
    svc_ref = ""
    
    if first_db:
        for key in ["host", "port", "username", "host_username", "host_password", "reporting_port", "reporting_username"]:
            if key in first_db and first_db[key]:
                resolved_globals[key] = first_db[key]
        
        db_tmpl = first_db.get("password", "")
        db_ref  = first_db.get("db_name", "")
        
        rpt_tmpl = first_db.get("reporting_password", "")
        rpt_ref  = first_db.get("reporting_db_name", "")
        if rpt_tmpl and not rpt_ref:
            rpt_ref = derive_reporting_db_name(db_ref)
            
        svc_tmpl = first_db.get("service_name", "")
        svc_ref  = db_ref

    # Fallbacks for globals
    g_host      = resolved_globals.get("host", "")
    g_port      = resolved_globals.get("port") or "1521"
    g_username  = resolved_globals.get("username") or "sys"
    g_host_user = resolved_globals.get("host_username", "")
    g_host_pass = resolved_globals.get("host_password", "")
    g_rpt_port  = resolved_globals.get("reporting_port") or "1521"
    g_rpt_user  = resolved_globals.get("reporting_username", "")
    g_svc_sfx   = resolved_globals.get("service_suffix", "")

    for group in groups:
        rpt_host = group.get("reporting_host", "")
        rpt_svc  = group.get("reporting_service_name", "")
        dbs      = group.get("dbs", [])

        if not dbs:
            continue

        first_entry = dbs[0]
        
        grp_db_tmpl = first_entry.get("password", "") or db_tmpl
        grp_db_ref  = first_entry.get("db_name", "") if first_entry.get("password") else db_ref
        
        grp_rpt_tmpl = first_entry.get("reporting_password", "")
        grp_rpt_ref  = ""
        if grp_rpt_tmpl:
            grp_rpt_ref = first_entry.get("reporting_db_name", "") or derive_reporting_db_name(first_entry["db_name"])
        else:
            grp_rpt_tmpl = group.get("reporting_password_template", "")
            if grp_rpt_tmpl:
                grp_rpt_ref = derive_reporting_db_name(first_entry["db_name"])
            else:
                grp_rpt_tmpl = rpt_tmpl
                grp_rpt_ref  = rpt_ref

        grp_svc_tmpl = first_entry.get("service_name", "") or svc_tmpl
        grp_svc_ref  = first_entry.get("db_name", "") if first_entry.get("service_name") else svc_ref

        for db_entry in dbs:
            db_name = db_entry["db_name"]

            host = db_entry.get("host") or g_host
            port = db_entry.get("port") or g_port
            
            service_name = db_entry.get("service_name")
            if not service_name:
                if grp_svc_tmpl and grp_svc_ref:
                    service_name = replace_in_template(grp_svc_tmpl, grp_svc_ref, db_name)
                else:
                    service_name = db_name + g_svc_sfx

            username = db_entry.get("username") or g_username
            
            password = db_entry.get("password")
            if not password:
                if grp_db_tmpl and grp_db_ref:
                    password = replace_in_template(grp_db_tmpl, grp_db_ref, db_name)
                else:
                    password = ""

            host_username = db_entry.get("host_username") or g_host_user
            host_password = db_entry.get("host_password") or g_host_pass

            reporting_db_name = db_entry.get("reporting_db_name")
            if not reporting_db_name:
                reporting_db_name = derive_reporting_db_name(db_name)

            reporting_host = db_entry.get("reporting_host") or rpt_host
            reporting_port = db_entry.get("reporting_port") or group.get("reporting_port") or g_rpt_port
            reporting_service_name = db_entry.get("reporting_service_name") or rpt_svc
            reporting_username = db_entry.get("reporting_username") or group.get("reporting_username") or g_rpt_user

            reporting_password = db_entry.get("reporting_password")
            if not reporting_password:
                if grp_rpt_tmpl and grp_rpt_ref:
                    reporting_password = replace_in_template(grp_rpt_tmpl, grp_rpt_ref, reporting_db_name)
                else:
                    reporting_password = ""

            row = {
                "db_name"                 : db_name,
                "host"                    : host,
                "port"                    : port,
                "service_name"            : service_name,
                "username"                : username,
                "password"                : password,
                "host_username"           : host_username,
                "host_password"           : host_password,
                "reporting_db_name"       : reporting_db_name,
                "reporting_host"          : reporting_host,
                "reporting_port"          : reporting_port,
                "reporting_service_name"  : reporting_service_name,
                "reporting_username"      : reporting_username,
                "reporting_password"      : reporting_password,
                "reporting_host_username" : "",
                "reporting_host_password" : "",
            }
            rows.append(row)

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
        # Read Excel — check if first row is header
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
                # No header — assign column names by position
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


def load_db_names():
    """Load database names dynamically from the registry file (Text or Excel)."""
    path = get_txt_path()
    if not path or not os.path.exists(path):
        return []
    
    try:
        df = read_db_file_to_df(path)
        if "db_name" in df.columns:
            db_names = df["db_name"].dropna().astype(str).str.strip().tolist()
            db_names = [name for name in db_names if name]
            if db_names:
                return db_names
        return []
    except Exception as e:
        st.error(f"Error reading database registry file: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# API-SAFE FUNCTIONS (No Streamlit session_state dependency)
# These work in FastAPI / uvicorn / CLI context on any OS.
# ─────────────────────────────────────────────────────────────

def get_txt_path_api() -> str:
    """
    Get the registry file path directly from the JSON config file.
    Does NOT use st.session_state — safe to call from FastAPI/uvicorn.
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
        df = read_db_file_to_df(path)

        # Check whether the DataFrame contains a 'db_name' column
        if "db_name" in df.columns:

            # Remove NULL values, convert all names to string,
            # remove leading/trailing spaces and convert into a list
            names = df["db_name"].dropna().astype(str).str.strip().tolist()

            # Return only non-empty database names
            return [n for n in names if n]

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

    # Get the path of the registry file — try session-aware path first
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
        df = read_db_file_to_df(path)

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

        def _clean(val, default=""):
            """Return clean string, replacing None/nan/empty with default."""
            v = str(val).strip() if val is not None else ""
            return default if v.lower() in ("", "none", "nan", "n/a", "-") else v

        # Read all fields with safe fallbacks
        user         = _clean(row.get("username",      row.get("user", "")))
        password     = _clean(row.get("password",      ""))
        host         = _clean(row.get("host",          ""))
        port         = _clean(row.get("port",          ""), "1521")
        service_name = _clean(row.get("service_name",  row.get("db_name", db_name)))
        host_username = _clean(row.get("host_username", row.get("os_user", "")))
        host_password = _clean(row.get("host_password", row.get("os_pass", "")))

        # Remove ".0" if Excel converted the port into decimal format (1521.0 → 1521)
        if port.endswith(".0"):
            port = port[:-2]

        # Validate essential fields — host, user, password must be present
        if not host:
            print(f"[config] '{db_name}': host is empty in registry — skipping row.")
            return None
        if not user:
            print(f"[config] '{db_name}': username is empty in registry — skipping row.")
            return None

        # Build Oracle DSN in host:port/service_name format (required for thin mode)
        # DPY-4027 occurs when DSN is a TNS alias (no host:port). Always use explicit format.
        dsn = f"{host}:{port}/{service_name}"

        # Return all database connection details as a dictionary
        return {
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
        df = read_db_file_to_df(path)
        if "db_name" not in df.columns:
            return None

        match = df[df["db_name"].astype(str).str.strip().str.lower() == str(db_name).strip().lower()]
        if match.empty:
            return None

        row = match.iloc[0]

        def _first_val(keys):
            for k in keys:
                if k in row:
                    val = str(row[k]).strip()
                    if val and val.lower() not in ("none", "nan", "n/a", "-"):
                        return val
            return ""

        r_db   = _first_val(["reporting_db_name", "reporting_db", "reporting db name", "reporting db", "rpt_db"])
        r_host = _first_val(["reporting_host", "reporting host", "rpt_host"])
        r_port = _first_val(["reporting_port", "reporting port", "rpt_port"])
        r_svc  = _first_val(["reporting_service_name", "reporting_server", "reporting server name", "reporting server", "reporting_service", "rpt_service", "server_name"])
        r_user = _first_val(["reporting_username", "reporting_user", "reporting username", "rpt_user"])
        r_pwd  = _first_val(["reporting_password", "reporting_pass", "reporting password", "rpt_password"])

        # All essential fields must be present
        if not all([r_db, r_host, r_port, r_svc, r_user, r_pwd]):
            return None

        if r_port.endswith(".0"):
            r_port = r_port[:-2]

        return {
            "db_name":      r_db,
            "user":         r_user,
            "password":     r_pwd,
            "dsn":          f"{r_host}:{r_port}/{r_svc}",
            "host":         r_host,
            "port":         r_port,
            "service_name": r_svc,
        }
    except Exception as e:
        print(f"[reporting] Error reading reporting config for {db_name}: {e}")
        return None


def check_reporting_db_status(db_name: str) -> dict:
    """
    Attempt a lightweight connection to the reporting DB configured for `db_name`.
    Tries three methods in order:
      1. SYSDBA mode (correct for sys users)
      2. DEFAULT_AUTH mode (fallback if remote SYSDBA is disabled on target DB)
      3. TCP listener ping (checks if listener port is reachable at all)
    Returns:
        { "configured": bool, "status": "UP"/"DOWN"/"NOT_CONFIGURED",
          "reporting_db_name": str, "error": str }
    """
    cfg = get_reporting_db_config(db_name)
    if cfg is None:
        return {"configured": False, "status": "NOT_CONFIGURED", "reporting_db_name": "", "error": ""}

    rpt_name = cfg["db_name"]
    user     = cfg["user"]
    password = cfg["password"]
    dsn      = cfg["dsn"]
    host     = cfg["host"]
    port     = cfg["port"]
    svc      = cfg["service_name"]

    def _parse_ora_error(e):
        """Return a human-readable error string from an oracledb.DatabaseError."""
        try:
            error_obj = e.args[0]
            if hasattr(error_obj, "code"):
                code   = error_obj.code
                detail = getattr(error_obj, "message", str(e)).strip()
                ora    = f"ORA-{code:05d}"
                if code == 1017:
                    return f"[{ora}] Invalid username or password", code
                elif code == 12541:
                    return f"[{ora}] Listener not running at {host}:{port}", code
                elif code in (12170, 12535):
                    return f"[{ora}] Connection timed out to {host}:{port}", code
                elif code == 12154:
                    return f"[{ora}] Service '{svc}' not found in TNS", code
                elif code == 12505:
                    return f"[{ora}] Listener at {host} doesn't know service '{svc}'", code
                elif code == 1034:
                    return f"[{ora}] Database instance is DOWN or not started", code
                elif code == 28000:
                    return f"[{ora}] Account '{user}' is locked", code
                elif code == 28001:
                    return f"[{ora}] Password for '{user}' has expired", code
                elif code == 28009:
                    return f"[{ora}] SYS must connect as SYSDBA — remote SYSDBA may be disabled on target", code
                return f"[{ora}] {detail}", code
        except Exception:
            pass
        return str(e), 0

    # Codes that mean the DB listener IS reachable but it's an auth/config issue
    # — no point retrying with a different mode for these
    AUTH_CODES = {1017, 28000, 28001, 1034, 12505, 12154}

    last_error = ""

    # ── Attempt 1: Correct mode for username (SYSDBA for sys, DEFAULT otherwise)
    try:
        mode = get_oracle_mode(user)
        conn = oracledb.connect(
            user=user, password=password, dsn=dsn,
            mode=mode, tcp_connect_timeout=5
        )
        conn.close()
        return {"configured": True, "status": "UP", "reporting_db_name": rpt_name, "error": ""}
    except oracledb.DatabaseError as e:
        last_error, code = _parse_ora_error(e)
        if code in AUTH_CODES:
            return {"configured": True, "status": "DOWN", "reporting_db_name": rpt_name, "error": last_error}
    except Exception as e:
        last_error = str(e)

    # ── Attempt 2: DEFAULT_AUTH mode (fallback if remote SYSDBA is disabled) ──
    try:
        conn = oracledb.connect(
            user=user, password=password, dsn=dsn,
            mode=oracledb.DEFAULT_AUTH, tcp_connect_timeout=5
        )
        conn.close()
        return {"configured": True, "status": "UP", "reporting_db_name": rpt_name, "error": ""}
    except oracledb.DatabaseError as e:
        last_error, code = _parse_ora_error(e)
        if code in AUTH_CODES:
            return {"configured": True, "status": "DOWN", "reporting_db_name": rpt_name, "error": last_error}
    except Exception as e:
        last_error = str(e)

    # ── Attempt 3: TCP listener ping only (check if port is reachable at all) ─
    try:
        import socket as _sock
        s = _sock.create_connection((host, int(port)), timeout=3)
        s.close()
        # Listener port is open but Oracle login blocked — still DOWN
        last_error = f"Listener UP at {host}:{port} but Oracle login failed. {last_error}"
    except Exception:
        last_error = f"Cannot reach {host}:{port} — {last_error}"

    return {"configured": True, "status": "DOWN", "reporting_db_name": rpt_name, "error": last_error}


def get_api_connection(db_name: str):
    """
    Open and return a direct Oracle DB connection for a named database.
    Uses thin mode (no Oracle Client libs needed on Linux).
    Does NOT use st.session_state — safe to call from FastAPI/uvicorn.
    Returns (connection, None) on success or (None, error_message) on failure.
    """
    cfg = get_config_for_db(db_name)
    if cfg is None:
        return None, f"Database '{db_name}' not found in registry."
    try:
        mode = get_oracle_mode(cfg["user"])
        conn = oracledb.connect(
            user=cfg["user"],
            password=cfg["password"],
            dsn=cfg["dsn"],
            mode=mode,
            tcp_connect_timeout=5
        )
        return conn, None
    except oracledb.DatabaseError as e:
        error_obj = e.args[0]
        # Parse Oracle error code for meaningful messages
        if hasattr(error_obj, "code"):
            code = error_obj.code
            ora_prefix = f"ORA-{code:05d}"
            detail_msg = getattr(error_obj, "message", str(e)).strip()
            if code == 1017:
                return None, f"[{ora_prefix}] Authentication failed: Invalid username or password for '{db_name}'. Detail: {detail_msg}"
            elif code == 12541:
                return None, f"[{ora_prefix}] Listener not running: Cannot reach '{db_name}' at {cfg['host']}:{cfg['port']}. Detail: {detail_msg}"
            elif code == 12170 or code == 12535:
                return None, f"[{ora_prefix}] Connection timed out: Host {cfg['host']} is unreachable or firewall is blocking port {cfg['port']}. Detail: {detail_msg}"
            elif code == 12154:
                return None, f"[{ora_prefix}] Service unknown: TNS could not resolve service '{cfg['service_name']}'. Detail: {detail_msg}"
            elif code == 12505:
                return None, f"[{ora_prefix}] Service not registered: Listener at {cfg['host']} does not know service '{cfg['service_name']}'. Detail: {detail_msg}"
            elif code == 1034:
                return None, f"[{ora_prefix}] Database not open: Oracle instance '{db_name}' is down or requires startup. Detail: {detail_msg}"
            elif code == 28000:
                return None, f"[{ora_prefix}] Account locked: The user '{cfg['user']}' is locked. Detail: {detail_msg}"
            elif code == 28001:
                return None, f"[{ora_prefix}] Password expired: The password for '{cfg['user']}' has expired. Detail: {detail_msg}"
            elif code == 28009:
                return None, f"[{ora_prefix}] SYSDBA required: Connection as SYS should be as SYSDBA. Detail: {detail_msg}"
            return None, f"[{ora_prefix}] Oracle error for '{db_name}': {detail_msg}"
        return None, f"Oracle error for '{db_name}': {str(e)}"
    except Exception as e:
        return None, f"Connection error for '{db_name}': {str(e)}"


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
