import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor

telemetry_executor = ThreadPoolExecutor(max_workers=3)

def get_bundled_path(relative_path):
    if getattr(sys, "frozen", False):
        # PyInstaller bundled files are in _MEIPASS
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

if getattr(sys, "frozen", False):
    # Dynamic configurations should live next to the .exe, not in read-only _MEIPASS
    CSV_PATH = os.path.join(os.path.dirname(sys.executable), "data", "databases.csv")
    
    # If the database CSV does not exist next to the .exe, copy the bundled default
    if not os.path.exists(CSV_PATH):
        try:
            import shutil
            os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
            default_csv = get_bundled_path(os.path.join("data", "databases.csv"))
            if os.path.exists(default_csv):
                shutil.copyfile(default_csv, CSV_PATH)
                print(f"Copied default database config to: {CSV_PATH}")
        except Exception as e:
            print(f"Failed to copy default database config: {e}")
else:
    CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "databases.csv")

print("CSV PATH =", CSV_PATH)

def get_all_db_configs():
    """Reads the CSV and returns a list of dictionaries with DB configs."""
    configs = []
    if not os.path.exists(CSV_PATH):
        print(f"Warning: CSV file not found at {CSV_PATH}")
        return configs

    with open(CSV_PATH, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Strip whitespace from keys and values just in case
            clean_row = {k.strip(): v.strip() for k, v in row.items() if k}
            if clean_row.get('db_id'):
                configs.append(clean_row)
    return configs

def get_databases():
    """Returns a list of databases excluding sensitive information like passwords."""
    configs = get_all_db_configs()
    safe_configs = []
    for config in configs:
        safe_configs.append({
            "db_id": config.get("db_id"),
            "host": config.get("host"),
            "port": config.get("port"),
            "service_name": config.get("service_name"),
            "os_user": config.get("os_user"),
            "os_password": config.get("os_password"),
            "grid_user": config.get("grid_user"),
            "rep_db_id": config.get("rep_db_id"),
            "rep_host": config.get("rep_host"),
            "rep_port": config.get("rep_port"),
            "rep_service_name": config.get("rep_service_name"),
            "rep_username": config.get("rep_username"),
            "rep_password": config.get("rep_password"),
            "stby_db_id": config.get("stby_db_id"),
            "stby_host": config.get("stby_host"),
            "stby_port": config.get("stby_port"),
            "stby_service_name": config.get("stby_service_name")
        })
    return safe_configs

def get_db_config(db_id):
    """Returns the full configuration for a specific database ID."""
    configs = get_all_db_configs()
    for config in configs:
        if config.get("db_id") == db_id:
            return config
    return None

# ---------------------------------------------------------------------------
# Grid Infrastructure / ASM: no separate configuration.
# ---------------------------------------------------------------------------
# ASM disk groups are discovered directly on the Production DB server(s)
# already present in databases.csv (host + os_user) - see
# services/asm_service.py's own host resolution. There is no separate Grid
# config file and no second upload; a server simply shows Grid/ASM as
# "Not Available" if ASM can't be discovered/queried there.

# ---------------------------------------------------------------------------
# Reporting environment (ASTON / FIAT) sequential detection
# ---------------------------------------------------------------------------
# The upload file is ordered by Reporting environment: every record is ASTON
# until the first Reporting Service Name containing "fiat" is encountered,
# after which every subsequent record is FIAT. This must be derived ONLY from
# that literal marker, in file order - never from db_id family (M5 vs FA),
# the P/R suffix, the Production DB name, or which template scores best by
# prefix match (that scoring is for the Production side only and must not be
# used to guess the Reporting environment).
ASTON_REPORTING_HOST = "10.10.4.184"
FIAT_REPORTING_HOST = "10.10.4.100"


def new_reporting_environment_state():
    """Starting state for advance_reporting_environment: always ASTON."""
    return {"env": "ASTON"}


def advance_reporting_environment(state, rep_service_name):
    """
    Determines the Reporting environment/host for the record currently being
    processed, and advances `state` in place (a dict from
    new_reporting_environment_state()) for every subsequent call.

    The transition is a one-way latch: once a Reporting Service Name
    containing "fiat" (case-insensitive) is seen, `state` permanently becomes
    FIAT for the rest of the file, even if a later record's service name
    happens to contain "aston" again.

    Returns (environment, reporting_host) for the current record as a tuple,
    e.g. ("ASTON", "10.10.4.184") or ("FIAT", "10.10.4.100").
    """
    if rep_service_name and 'fiat' in str(rep_service_name).lower():
        state['env'] = 'FIAT'
    env = state['env']
    host = ASTON_REPORTING_HOST if env == 'ASTON' else FIAT_REPORTING_HOST
    return env, host


def find_longest_common_prefix(s1, s2):
    s1_lower = s1.lower()
    s2_lower = s2.lower()
    comm_len = 0
    for c1, c2 in zip(s1_lower, s2_lower):
        if c1 == c2:
            comm_len += 1
        else:
            break
    return comm_len

def find_best_matching_template(db_id, existing_configs):
    if not existing_configs:
        return None
    
    best_match = None
    longest_prefix_len = -1
    
    for cfg in existing_configs:
        exist_id = cfg.get("db_id", "")
        if not exist_id:
            continue
        
        # Calculate length of common prefix
        comm_len = find_longest_common_prefix(db_id, exist_id)
        
        if comm_len > longest_prefix_len:
            longest_prefix_len = comm_len
            best_match = cfg
            
    if longest_prefix_len <= 0:
        return None
    return best_match

def derive_similar_id(tmpl_db_id, tmpl_target_id, new_db_id):
    if not tmpl_db_id or not tmpl_target_id:
        return tmpl_target_id or ""
    
    tmpl_db_id_lower = tmpl_db_id.lower()
    tmpl_target_id_lower = tmpl_target_id.lower()
    
    # Find longest common prefix
    common_prefix_len = find_longest_common_prefix(tmpl_db_id, tmpl_target_id)
    
    suffix_db = tmpl_db_id[common_prefix_len:]
    suffix_target = tmpl_target_id[common_prefix_len:]
    
    # Apply suffix transformation to new_db_id
    new_db_id_lower = new_db_id.lower()
    if suffix_db and new_db_id_lower.endswith(suffix_db.lower()):
        base = new_db_id[:-len(suffix_db)]
        return base + suffix_target
    else:
        return new_db_id + suffix_target

def case_insensitive_replace(text, old, new):
    if not text or not old:
        return text
    import re
    escaped_old = re.escape(old)
    return re.sub(escaped_old, lambda m: new, text, flags=re.IGNORECASE)

def derive_config_from_template(new_row, tmpl, pw_tmpl=None):
    """
    Enriches new_row (dict) with values derived from tmpl (dict).
    Keeps values in new_row if they are already present and non-empty.

    pw_tmpl (defaults to tmpl) supplies the password/rep_password pattern.
    It is kept separate from tmpl because environment fields (host,
    service_name) can be chosen positionally/sequentially by file order,
    while a password's fixed prefix is still tied to the SID's actual
    family — so the two templates may legitimately differ (e.g. an FA
    SID inheriting a positionally-active M5 environment template must
    still get its password pattern from an FA-family template).
    """
    new_db_id = new_row.get('db_id', '').strip()
    if not new_db_id:
        return new_row

    if pw_tmpl is None:
        pw_tmpl = tmpl

    tmpl_db_id = tmpl.get('db_id', '').strip()

    # Determine reporting and standby database IDs first
    rep_db_key = 'rep_db_id'
    stby_db_key = 'stby_db_id'

    new_rep_db_id = new_row.get(rep_db_key, '').strip()
    if not new_rep_db_id:
        # Rule 6: derive Reporting DB ID
        if new_db_id.lower().endswith('p'):
            new_rep_db_id = new_db_id[:-1] + ('r' if new_db_id[-1] == 'p' else 'R')
        else:
            new_rep_db_id = new_db_id + ('R' if new_db_id[-1].isupper() else 'r')
        new_row[rep_db_key] = new_rep_db_id

    new_stby_db_id = new_row.get(stby_db_key, '').strip()
    if not new_stby_db_id:
        new_stby_db_id = derive_similar_id(tmpl_db_id, tmpl.get(stby_db_key, ''), new_db_id)
        if new_stby_db_id:
            new_row[stby_db_key] = new_stby_db_id

    # Build replacements map
    replacements = []
    if tmpl_db_id and new_db_id:
        replacements.append((tmpl_db_id, new_db_id))
    
    tmpl_rep_db_id = tmpl.get(rep_db_key, '').strip()
    if tmpl_rep_db_id and new_rep_db_id:
        replacements.append((tmpl_rep_db_id, new_rep_db_id))

    tmpl_stby_db_id = tmpl.get(stby_db_key, '').strip()
    if tmpl_stby_db_id and new_stby_db_id:
        replacements.append((tmpl_stby_db_id, new_stby_db_id))

    # Sort replacements descending by template string length
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    # Derive missing fields (password/rep_password are handled separately
    # below via pw_tmpl, not tmpl).
    for field_name in tmpl:
        if field_name in ('db_id', 'password', 'rep_password'):
            continue

        uploaded_val = new_row.get(field_name, '').strip()
        if not uploaded_val:
            tmpl_val = tmpl.get(field_name, '')
            if isinstance(tmpl_val, str) and tmpl_val:
                derived_val = tmpl_val
                for old_str, new_str in replacements:
                    derived_val = case_insensitive_replace(derived_val, old_str, new_str)
                new_row[field_name] = derived_val
            else:
                new_row[field_name] = tmpl_val or ''

    # Derive password from pw_tmpl's own pattern (its db_id substituted with
    # the new db_id), not tmpl's — so a cross-family environment template
    # can't leak its password prefix onto an unrelated family's database.
    if not new_row.get('password', '').strip():
        pw_tmpl_password = pw_tmpl.get('password', '')
        pw_tmpl_db_id = pw_tmpl.get('db_id', '').strip()
        if pw_tmpl_password and pw_tmpl_db_id:
            new_row['password'] = case_insensitive_replace(pw_tmpl_password, pw_tmpl_db_id, new_db_id)
        else:
            new_row['password'] = pw_tmpl_password or ''

    if not new_row.get('rep_password', '').strip():
        pw_tmpl_rep_password = pw_tmpl.get('rep_password', '')
        pw_tmpl_rep_db_id = pw_tmpl.get(rep_db_key, '').strip()
        if pw_tmpl_rep_password and pw_tmpl_rep_db_id:
            new_row['rep_password'] = case_insensitive_replace(pw_tmpl_rep_password, pw_tmpl_rep_db_id, new_rep_db_id)
        else:
            new_row['rep_password'] = pw_tmpl_rep_password or ''

    return new_row

def save_database_configs(configs):
    """Writes a list of config dictionaries to the CSV file using 33 columns (32 Production/Reporting/Standby/Standalone fields, plus the optional Grid/ASM OS user)."""
    # 1. Load the existing configurations before writing
    old_configs = {}
    try:
        old_list = get_all_db_configs()
        for cfg in old_list:
            db_id = cfg.get("db_id")
            if db_id:
                old_configs[db_id] = cfg
    except Exception as e:
        print(f"Error loading old configs: {e}")

    # 2. Save the new configs to databases.csv
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    fieldnames = [
        'db_id', 'host', 'port', 'service_name', 'username', 'password', 'os_user', 'os_password',
        'rep_db_id', 'rep_host', 'rep_port', 'rep_service_name', 'rep_username', 'rep_password', 'rep_os_user', 'rep_os_password',
        'stby_db_id', 'stby_host', 'stby_port', 'stby_service_name', 'stby_username', 'stby_password', 'stby_os_user', 'stby_os_password',
        'standalone_db_id', 'standalone_host', 'standalone_port', 'standalone_service_name', 'standalone_username', 'standalone_password', 'standalone_os_user', 'standalone_os_password',
        # Grid/ASM SSHes into the Production host as a DIFFERENT OS user than
        # os_user (e.g. 'grid' vs 'oracle') - see services/asm_service.py.
        # Optional: blank falls back to the single "grid" default there, never
        # to os_user/oracle. Appended at the end so existing 32-column files
        # keep working unchanged.
        'grid_user'
    ]

    old_configs_list = list(old_configs.values())
    new_configs_list = []
    with open(CSV_PATH, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for config in configs:
            row = {}
            for field in fieldnames:
                row[field] = str(config.get(field, '')).strip()

            # BUG 3 FIX: Do NOT re-derive from old disk configs here.
            # All fields are correctly and completely populated from the parse step.
            # Re-deriving with find_best_matching_template(old_configs_list) picks
            # stale/mismatched disk configs and fills empty fields with wrong values
            # (e.g., wrong host, wrong environment, wrong rep_service_name).
            # The parse step already used the correct active_template to derive everything.

            if row.get('db_id'):
                writer.writerow(row)
                new_configs_list.append(row)

    # 3. Determine invalidated database IDs
    new_configs_dict = {cfg.get("db_id"): cfg for cfg in new_configs_list if cfg.get("db_id")}
    invalidated_db_ids = set()

    for db_id in old_configs:
        if db_id not in new_configs_dict:
            invalidated_db_ids.add(db_id)

    for db_id, new_cfg in new_configs_dict.items():
        if db_id not in old_configs:
            invalidated_db_ids.add(db_id)
        else:
            old_cfg = old_configs[db_id]
            fields_to_check = [
                'host', 'port', 'service_name', 'username', 'password', 'os_user', 'os_password',
                'rep_db_id', 'rep_host', 'rep_port', 'rep_service_name', 'rep_username', 'rep_password', 'rep_os_user', 'rep_os_password',
                'stby_db_id', 'stby_host', 'stby_port', 'stby_service_name', 'stby_username', 'stby_password', 'stby_os_user', 'stby_os_password',
                'standalone_db_id', 'standalone_host', 'standalone_port', 'standalone_service_name', 'standalone_username', 'standalone_password', 'standalone_os_user', 'standalone_os_password'
            ]
            modified = False
            for f in fields_to_check:
                if str(new_cfg.get(f, '')).strip() != str(old_cfg.get(f, '')).strip():
                    modified = True
                    break
            if modified:
                invalidated_db_ids.add(db_id)

    # 4. Perform selective cache invalidations
    if invalidated_db_ids:
        print(f"Selectively invalidating caches for: {invalidated_db_ids}")

        try:
            from services.mountpoint_service import invalidate_mountpoints_cache
            for db_id in invalidated_db_ids:
                invalidate_mountpoints_cache(db_id)
        except Exception as e:
            print(f"Error invalidating mountpoints: {e}")

        try:
            from services.remote_process_service import invalidate_remote_process_cache
            for db_id in invalidated_db_ids:
                invalidate_remote_process_cache(db_id)
        except Exception as e:
            print(f"Error invalidating remote processes: {e}")

        try:
            from services.db_service import invalidate_db_cache
            for db_id in invalidated_db_ids:
                invalidate_db_cache(db_id)
        except Exception as e:
            print(f"Error invalidating db service: {e}")

        try:
            from services.ssh_service import invalidate_ssh_connection
            for db_id in invalidated_db_ids:
                old_cfg = old_configs.get(db_id)
                if old_cfg:
                    h = str(old_cfg.get('host') or '').strip()
                    u = str(old_cfg.get('os_user') or '').strip()
                    p = str(old_cfg.get('os_password') or '').strip()
                    invalidate_ssh_connection(h, u, p)
        except Exception as e:
            print(f"Error invalidating ssh connections: {e}")

        # Also reset global display telemetry caches to force them to reload
        try:
            from services.remote_process_service import _ALL_PROCESSES_CACHE
            _ALL_PROCESSES_CACHE["timestamp"] = 0
            _ALL_PROCESSES_CACHE["data"] = None
        except Exception:
            pass
        try:
            from services.mountpoint_service import _ALL_MOUNT_POINTS_CACHE
            _ALL_MOUNT_POINTS_CACHE["timestamp"] = 0
            _ALL_MOUNT_POINTS_CACHE["data"] = None
        except Exception:
            pass
        try:
            # So the homepage's Oracle Server Processes summary doesn't serve
            # up-to-30s-stale data (missing newly uploaded databases) on the
            # immediate post-upload fetch.
            from services.db_service import _all_oracle_processes_cache
            _all_oracle_processes_cache["timestamp"] = 0
            _all_oracle_processes_cache["data"] = None
        except Exception:
            pass

    # 5. Background pre-warming (Asynchronous daemon thread to not block API return)
    def async_prewarm():
        try:
            from services.db_service import get_db_summary, get_all_servers_oracle_processes
            from services.mountpoint_service import get_mount_points, get_all_servers_mount_points
            from services.remote_process_service import get_remote_server_processes

            for config in new_configs_list:
                p_db = config.get("db_id")
                if p_db:
                    get_db_summary(p_db)
                    get_mount_points(p_db)
                    get_remote_server_processes(p_db)

            get_all_servers_mount_points()
            get_remote_server_processes(None)
            # Pre-warm the homepage's per-server Oracle process summary so it's
            # ready by the time the frontend's immediate post-upload fetch arrives.
            get_all_servers_oracle_processes()
        except Exception as e:
            print(f"Error pre-warming database telemetry caches: {e}")

    import threading
    threading.Thread(target=async_prewarm, daemon=True).start()
