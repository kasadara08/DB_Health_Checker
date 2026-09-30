import os
import socket
import paramiko
import logging
import threading
from flask import session
from services.config_service import get_db_config, get_all_db_configs, get_bundled_path

# Suppress noisy paramiko log outputs
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

_ssh_connections = {}
_ssh_lock = threading.Lock()

# Separate pool used by get_ssh_connection_via_key() (Mount Points, Oracle
# Server Process, and Standby-via-Production), so key-based auth never
# shares a cache slot with the password-based pool above and can't affect
# RMAN/archive-log SSH, which still uses get_ssh_connection().
_ssh_key_connections = {}
_ssh_key_lock = threading.Lock()

# Separate pool for get_grid_ssh_connection() (Grid/ASM disk group
# monitoring). Grid is server-level infra, not tied to any one database row,
# so this pool is keyed by (host, grid_user) directly rather than db_id, and
# is kept fully isolated from the "oracle" pools above - a Grid/ASM failure
# can never touch, invalidate, or close an Oracle SSH connection.
_ssh_grid_connections = {}
_ssh_grid_lock = threading.Lock()

# Key classes to try, in order, when loading the Mount Points private key.
# Covers both classic PEM ("-----BEGIN RSA/EC PRIVATE KEY-----") and the
# newer OpenSSH container format ("-----BEGIN OPENSSH PRIVATE KEY-----") -
# each of Paramiko's *Key classes accepts both containers for its own key
# type, so trying all three covers every format without any custom parsing.
# (DSSKey/DSA is not included - paramiko 5.x dropped it as insecure/removed.)
_SSH_KEY_CLASSES = (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey)


def _load_ssh_private_key(path, passphrase):
    """
    Load an SSH private key file via Paramiko's own key classes only
    (no custom key parsing). Tries each class in turn; a class whose
    loader reports "wrong key type for this file" is skipped so the next
    class can try, but any other error (bad passphrase, corrupt data) is
    the *real* failure for this file and is raised immediately instead of
    being masked by a later class's inevitable "wrong type" mismatch.

    Returns (pkey, key_class_name).
    """
    last_format_err = None
    for key_cls in _SSH_KEY_CLASSES:
        try:
            pkey = key_cls.from_private_key_file(path, password=passphrase)
            return pkey, key_cls.__name__
        except paramiko.PasswordRequiredException:
            raise
        except paramiko.SSHException as e:
            msg = str(e)
            if msg.startswith("encountered ") and " key, expected " in msg:
                # This class's loader doesn't match the file's key type -
                # not an error for THIS file, just the wrong class. Try the next one.
                last_format_err = last_format_err or e
                continue
            # Any other SSHException is a genuine problem with the key
            # file itself (bad passphrase, truncated/corrupt data, etc.).
            raise
    raise paramiko.SSHException(
        f"Unrecognized SSH private key format at '{path}' "
        f"(not RSA/Ed25519/ECDSA/DSA, PEM or OpenSSH format): {last_format_err}"
    )

def clear_ssh_connections():
    global _ssh_connections
    with _ssh_lock:
        for key, ssh in list(_ssh_connections.items()):
            try:
                if hasattr(ssh, '_orig_close'):
                    ssh._orig_close()
                else:
                    ssh.close()
            except:
                pass
        _ssh_connections.clear()
        logging.info("[SSH Pool] Cleared all pooled SSH connections.")

def invalidate_ssh_connection(hostname, os_user, os_password):
    global _ssh_connections
    if hostname and os_user and os_password:
        cache_key = f"{hostname}##{os_user}##{os_password}"
        with _ssh_lock:
            ssh = _ssh_connections.pop(cache_key, None)
            if ssh:
                try:
                    if hasattr(ssh, '_orig_close'):
                        ssh._orig_close()
                    else:
                        ssh.close()
                except:
                    pass
                logging.info(f"[SSH Pool] Invalidated and closed SSH connection for {cache_key}")

def get_ssh_connection(db_id=None, timeout=10, banner_timeout=10):
    """
    Retrieves database configuration dynamically based on db_id (or active session db_id),
    extracts host, os_user, os_password, and establishes an SSH connection using Paramiko.
    
    Strictly uses operating system credentials (os_user & os_password).
    Does NOT use Oracle DB username or password for SSH authentication.
    
    Returns:
        (ssh_client, db_cfg, error_message)
    """
    if not db_id:
        try:
            db_id = session.get('active_db_id')
        except Exception:
            pass

    db_cfg = None
    if db_id:
        db_cfg = get_db_config(db_id)

    if not db_cfg:
        all_configs = get_all_db_configs()
        if all_configs:
            db_cfg = all_configs[0]
            db_id = db_cfg.get('db_id')

    if not db_cfg:
        return None, None, "No database configuration found."

    hostname = str(db_cfg.get('host') or '').strip()
    if not hostname:
        return None, db_cfg, "Database host IP is missing in configuration."

    # Retrieve OS username and OS password STRICTLY from os_user and os_password fields
    # DO NOT fall back to Oracle DB username (username) or DB password (password)
    username = str(db_cfg.get('os_user') or '').strip()
    password = str(db_cfg.get('os_password') or '').strip()

    # Debug logging (excluding passwords)
    logging.info(f"[SSH DEBUG] Initiating SSH connection -> Host: {hostname}, OS Username: '{username or 'MISSING'}'")

    if not username:
        err_msg = f"OS Username (os_user) is missing in database configuration for host '{hostname}'. Please configure OS Username."
        logging.error(f"[SSH DEBUG] Error: {err_msg}")
        return None, db_cfg, err_msg
        
    if not password:
        err_msg = f"OS Password (os_password) is missing in database configuration for host '{hostname}'. Please configure OS Password."
        logging.error(f"[SSH DEBUG] Error: {err_msg}")
        return None, db_cfg, err_msg

    # SSH Pooling check
    cache_key = f"{hostname}##{username}##{password}"
    global _ssh_connections
    
    with _ssh_lock:
        if cache_key in _ssh_connections:
            ssh = _ssh_connections[cache_key]
            try:
                transport = ssh.get_transport()
                if transport and transport.is_active():
                    transport.send_ignore()
                    logging.info(f"[SSH Pool] Reusing active SSH connection to {hostname}")
                    return ssh, db_cfg, None
            except Exception:
                logging.info(f"[SSH Pool] Cached connection to {hostname} was closed or dead. Reconnecting...")
                try:
                    if hasattr(ssh, '_orig_close'):
                        ssh._orig_close()
                    else:
                        ssh.close()
                except:
                    pass
                _ssh_connections.pop(cache_key, None)

    # Fast reachability check for SSH (port 22) if not already cached
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        res = s.connect_ex((hostname, 22))
        s.close()
        if res != 0:
            err_msg = f"Host {hostname} on port 22 (SSH) is unreachable."
            logging.error(f"[SSH DEBUG] Error: {err_msg}")
            return None, db_cfg, err_msg
    except Exception as e:
        pass

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        logging.info(f"[SSH DEBUG] Connecting via Paramiko SSH to {hostname} with OS Username: '{username}'")
        ssh.connect(
            hostname=hostname,
            username=username,
            password=password,
            timeout=timeout,
            banner_timeout=banner_timeout
        )
        logging.info(f"[SSH DEBUG] Successfully connected via SSH to {hostname} as user '{username}'")
        
        # Override close with a no-op to retain the connection in our pool
        ssh._orig_close = ssh.close
        ssh.close = lambda: None
        _ssh_connections[cache_key] = ssh
        
        return ssh, db_cfg, None

    except paramiko.AuthenticationException:
        err = f"Authentication failure: Invalid OS Username ('{username}') or Password for host '{hostname}'."
        logging.error(f"[SSH DEBUG] {err}")
        return None, db_cfg, err
    except socket.timeout:
        err = f"SSH timeout: Connection timed out while connecting to host '{hostname}' (timeout={timeout}s)."
        logging.error(f"[SSH DEBUG] {err}")
        return None, db_cfg, err
    except ConnectionRefusedError:
        err = f"Connection refused: SSH service (port 22) is not accepting connections on host '{hostname}'."
        logging.error(f"[SSH DEBUG] {err}")
        return None, db_cfg, err
    except socket.gaierror:
        err = f"Host unreachable: Unable to resolve hostname/IP '{hostname}'."
        logging.error(f"[SSH DEBUG] {err}")
        return None, db_cfg, err
    except socket.error as e:
        err_msg = str(e).lower()
        logging.error(f"[SSH DEBUG] Socket error on '{hostname}': {e}")
        if "111" in err_msg or "refused" in err_msg:
            return None, db_cfg, f"Connection refused: SSH service (port 22) is not accepting connections on host '{hostname}'."
        elif "113" in err_msg or "route" in err_msg or "unreachable" in err_msg:
            return None, db_cfg, f"Host unreachable: Unable to route to host '{hostname}'."
        elif "timed out" in err_msg:
            return None, db_cfg, f"SSH timeout: Connection timed out while connecting to host '{hostname}'."
        else:
            return None, db_cfg, f"Network error connecting to host '{hostname}': {str(e)}"
    except Exception as e:
        err_msg = str(e)
        logging.error(f"[SSH DEBUG] General SSH Exception on '{hostname}': {err_msg}")
        if "Error reading SSH protocol banner" in err_msg:
            return None, db_cfg, f"SSH Banner Error: Failed to read SSH banner from host '{hostname}'."
        return None, db_cfg, f"SSH Connection Error on host '{hostname}': {err_msg}"


def clear_ssh_key_connections():
    """
    Closes and clears every cached key-based SSH connection (Mount Points,
    Oracle Server Process, Standby-via-Production). Mirrors
    clear_ssh_connections() above but for the separate key-auth pool.
    """
    global _ssh_key_connections
    with _ssh_key_lock:
        for key, ssh in list(_ssh_key_connections.items()):
            try:
                if hasattr(ssh, '_orig_close'):
                    ssh._orig_close()
                else:
                    ssh.close()
            except:
                pass
        _ssh_key_connections.clear()
        logging.info("[SSH Key Pool] Cleared all pooled SSH key connections.")

def invalidate_ssh_key_connection(hostname, username):
    global _ssh_key_connections
    if hostname and username:
        cache_key = f"{hostname}##{username}##key"
        with _ssh_key_lock:
            ssh = _ssh_key_connections.pop(cache_key, None)
            if ssh:
                try:
                    if hasattr(ssh, '_orig_close'):
                        ssh._orig_close()
                    else:
                        ssh.close()
                except:
                    pass
                logging.info(f"[SSH Key Pool] Invalidated and closed SSH key connection for {cache_key}")


def get_ssh_connection_via_key(db_id=None, timeout=10, banner_timeout=10):
    """
    Used by Mount Points, Oracle Server Process, and Standby-via-Production.
    Same host/os_user resolution as get_ssh_connection(), but authenticates
    with the single shared OCI SSH private key (path and passphrase read
    from env vars, never hard-coded) instead of os_password - on hosts where
    os_password isn't a valid/current SSH credential, only this key is.

    Kept entirely separate from get_ssh_connection() and its connection pool
    so this cannot affect RMAN/archive-log SSH, which still uses password
    auth via get_ssh_connection().

    Returns:
        (ssh_client, db_cfg, error_message)
    """
    # MOUNT_SSH_KEY_PATH overrides if set; otherwise default to keys/oci_prod.pem
    # bundled next to the app (_internal/keys/oci_prod.pem when frozen, or
    # <repo_root>/keys/oci_prod.pem in dev) - same resolution config_service.py
    # already uses for databases.csv's bundled default.
    key_path = os.environ.get("MOUNT_SSH_KEY_PATH") or get_bundled_path(os.path.join("keys", "oci_prod.pem"))
    if not key_path or not os.path.exists(key_path):
        return None, None, (
            f"OCI SSH private key not found at '{key_path}'. Place the key file there, "
            f"or set MOUNT_SSH_KEY_PATH to its actual location."
        )
    key_passphrase = os.environ.get("MOUNT_SSH_KEY_PASSPHRASE") or None

    if not db_id:
        try:
            db_id = session.get('active_db_id')
        except Exception:
            pass

    db_cfg = None
    if db_id:
        db_cfg = get_db_config(db_id)

    if not db_cfg:
        all_configs = get_all_db_configs()
        if all_configs:
            db_cfg = all_configs[0]
            db_id = db_cfg.get('db_id')

    if not db_cfg:
        return None, None, "No database configuration found."

    hostname = str(db_cfg.get('host') or '').strip()
    if not hostname:
        return None, db_cfg, "Database host IP is missing in configuration."

    # Key-based auth still needs a target OS username - only the password is
    # replaced by the private key, not os_user.
    username = str(db_cfg.get('os_user') or '').strip()
    if not username:
        err_msg = f"OS Username (os_user) is missing in database configuration for host '{hostname}'. Please configure OS Username."
        logging.error(f"[SSH Key DEBUG] Error: {err_msg}")
        return None, db_cfg, err_msg

    cache_key = f"{hostname}##{username}##key"
    global _ssh_key_connections

    with _ssh_key_lock:
        if cache_key in _ssh_key_connections:
            ssh = _ssh_key_connections[cache_key]
            try:
                transport = ssh.get_transport()
                if transport and transport.is_active():
                    transport.send_ignore()
                    logging.info(f"[SSH Key Pool] Reusing active SSH key connection to {hostname}")
                    return ssh, db_cfg, None
            except Exception:
                logging.info(f"[SSH Key Pool] Cached key connection to {hostname} was closed or dead. Reconnecting...")
                try:
                    if hasattr(ssh, '_orig_close'):
                        ssh._orig_close()
                    else:
                        ssh.close()
                except:
                    pass
                _ssh_key_connections.pop(cache_key, None)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        res = s.connect_ex((hostname, 22))
        s.close()
        if res != 0:
            err_msg = f"Host {hostname} on port 22 (SSH) is unreachable."
            logging.error(f"[SSH Key DEBUG] Error: {err_msg}")
            return None, db_cfg, err_msg
    except Exception:
        pass

    try:
        pkey, key_class_name = _load_ssh_private_key(key_path, key_passphrase)
        logging.info(f"[SSH Key DEBUG] Loaded private key '{key_path}' as {key_class_name} ({pkey.get_name()})")
    except paramiko.PasswordRequiredException:
        err = f"SSH private key at '{key_path}' is passphrase-protected; set MOUNT_SSH_KEY_PASSPHRASE to its passphrase."
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err
    except (paramiko.SSHException, IOError) as e:
        err = f"Failed to load/use SSH private key at '{key_path}': {e}"
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        logging.info(
            f"[SSH Key DEBUG] Connecting via Paramiko SSH (key auth, {key_class_name}, "
            f"look_for_keys=False, allow_agent=False - no other auth method will be tried) "
            f"to {hostname} with OS Username: '{username}'"
        )
        ssh.connect(
            hostname=hostname,
            username=username,
            pkey=pkey,
            look_for_keys=False,
            allow_agent=False,
            timeout=timeout,
            banner_timeout=banner_timeout
        )
        logging.info(f"[SSH Key DEBUG] Successfully connected via SSH key auth ({key_class_name}) to {hostname} as user '{username}'")

        ssh._orig_close = ssh.close
        ssh.close = lambda: None
        _ssh_key_connections[cache_key] = ssh

        return ssh, db_cfg, None

    except paramiko.AuthenticationException:
        err = f"Authentication failure: SSH key rejected for user '{username}' on host '{hostname}' (check that this key's public half is in authorized_keys for '{username}')."
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err
    except (paramiko.SSHException, IOError) as e:
        err = f"SSH connection error to '{hostname}' using key auth: {e}"
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err
    except socket.timeout:
        err = f"SSH timeout: Connection timed out while connecting to host '{hostname}' (timeout={timeout}s)."
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err
    except ConnectionRefusedError:
        err = f"Connection refused: SSH service (port 22) is not accepting connections on host '{hostname}'."
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err
    except socket.gaierror:
        err = f"Host unreachable: Unable to resolve hostname/IP '{hostname}'."
        logging.error(f"[SSH Key DEBUG] {err}")
        return None, db_cfg, err
    except Exception as e:
        err_msg = str(e)
        logging.error(f"[SSH Key DEBUG] General SSH Exception on '{hostname}': {err_msg}")
        return None, db_cfg, f"SSH Connection Error on host '{hostname}': {err_msg}"


def clear_ssh_grid_connections():
    """Closes and clears every cached Grid/ASM SSH connection. Mirrors clear_ssh_key_connections()."""
    global _ssh_grid_connections
    with _ssh_grid_lock:
        for key, ssh in list(_ssh_grid_connections.items()):
            try:
                if hasattr(ssh, '_orig_close'):
                    ssh._orig_close()
                else:
                    ssh.close()
            except:
                pass
        _ssh_grid_connections.clear()
        logging.info("[SSH Grid Pool] Cleared all pooled Grid/ASM SSH connections.")


def invalidate_ssh_grid_connection(hostname):
    global _ssh_grid_connections
    if hostname:
        cache_key = f"{hostname}##{_GRID_SSH_LOGIN_USER}##grid"
        with _ssh_grid_lock:
            ssh = _ssh_grid_connections.pop(cache_key, None)
            if ssh:
                try:
                    if hasattr(ssh, '_orig_close'):
                        ssh._orig_close()
                    else:
                        ssh.close()
                except:
                    pass
                logging.info(f"[SSH Grid Pool] Invalidated and closed Grid/ASM SSH connection for {cache_key}")



# Fixed SSH login user for the Grid/ASM path only. Required flow is
# opc (SSH login) -> sudo su - <grid_user> (done per-command by
# asm_service.py's remote command wrapper) -> grid OS user -> sqlplus / as
# sysasm. This is intentionally NOT configurable and NOT the same as any
# database row's os_user - normal Oracle monitoring's get_ssh_connection()/
# get_ssh_connection_via_key() are untouched and keep using os_user exactly
# as before.
_GRID_SSH_LOGIN_USER = "opc"


def get_grid_ssh_connection(host, timeout=10, banner_timeout=10):
    """
    SSH connection to a Grid Infrastructure / ASM server, used only by
    services/asm_service.py for the Home Page ASM disk group widget. Takes a
    raw host directly (not a db_id) - the host is the Production DB server's
    own host. Always logs in as the fixed OS user "opc" (_GRID_SSH_LOGIN_USER)
    - deliberately DIFFERENT from that row's os_user (e.g. "oracle") and never
    the Grid OS user itself. asm_service.py is responsible for switching to
    the actual Grid OS user (e.g. "grid") via `sudo su - <grid_user>` on a
    per-command basis once this session is open; this function only
    establishes the opc SSH session.

    Authenticates with the same shared OCI SSH private key as
    get_ssh_connection_via_key() (path/passphrase from env vars, never
    hard-coded) - there is no SYSASM password in this application; sqlplus
    connects with OS-authenticated "/ as sysasm" once the remote command has
    switched to the Grid OS user.

    Kept in its own connection pool (_ssh_grid_connections), fully separate
    from every other SSH pool in this module, so a Grid/ASM failure can never
    invalidate, close, or otherwise affect an Oracle "oracle"-user SSH
    connection.

    Returns:
        (ssh_client, error_message)
    """
    key_path = os.environ.get("MOUNT_SSH_KEY_PATH") or get_bundled_path(os.path.join("keys", "oci_prod.pem"))
    if not key_path or not os.path.exists(key_path):
        return None, (
            f"OCI SSH private key not found at '{key_path}'. Place the key file there, "
            f"or set MOUNT_SSH_KEY_PATH to its actual location."
        )
    key_passphrase = os.environ.get("MOUNT_SSH_KEY_PASSPHRASE") or None

    hostname = str(host or '').strip()
    if not hostname:
        return None, "Grid server host is missing."

    username = _GRID_SSH_LOGIN_USER

    cache_key = f"{hostname}##{username}##grid"
    global _ssh_grid_connections

    with _ssh_grid_lock:
        if cache_key in _ssh_grid_connections:
            ssh = _ssh_grid_connections[cache_key]
            try:
                transport = ssh.get_transport()
                if transport and transport.is_active():
                    transport.send_ignore()
                    logging.info(f"[SSH Grid Pool] Reusing active Grid/ASM SSH connection to {hostname}")
                    return ssh, None
            except Exception:
                logging.info(f"[SSH Grid Pool] Cached Grid/ASM connection to {hostname} was closed or dead. Reconnecting...")
                try:
                    if hasattr(ssh, '_orig_close'):
                        ssh._orig_close()
                    else:
                        ssh.close()
                except:
                    pass
                _ssh_grid_connections.pop(cache_key, None)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        res = s.connect_ex((hostname, 22))
        s.close()
        if res != 0:
            err_msg = f"Host {hostname} on port 22 (SSH) is unreachable."
            logging.error(f"[SSH Grid DEBUG] Error: {err_msg}")
            return None, err_msg
    except Exception:
        pass

    try:
        pkey, key_class_name = _load_ssh_private_key(key_path, key_passphrase)
        logging.info(f"[SSH Grid DEBUG] Loaded private key '{key_path}' as {key_class_name} ({pkey.get_name()})")
    except paramiko.PasswordRequiredException:
        err = f"SSH private key at '{key_path}' is passphrase-protected; set MOUNT_SSH_KEY_PASSPHRASE to its passphrase."
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err
    except (paramiko.SSHException, IOError) as e:
        err = f"Failed to load/use SSH private key at '{key_path}': {e}"
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        logging.info(
            f"[SSH Grid DEBUG] Connecting via Paramiko SSH (key auth, {key_class_name}, "
            f"look_for_keys=False, allow_agent=False) to {hostname} with Grid OS Username: '{username}'"
        )
        ssh.connect(
            hostname=hostname,
            username=username,
            pkey=pkey,
            look_for_keys=False,
            allow_agent=False,
            timeout=timeout,
            banner_timeout=banner_timeout
        )
        logging.info(f"[SSH Grid DEBUG] Successfully connected via SSH key auth ({key_class_name}) to {hostname} as user '{username}'")

        ssh._orig_close = ssh.close
        ssh.close = lambda: None
        _ssh_grid_connections[cache_key] = ssh

        return ssh, None

    except paramiko.AuthenticationException:
        err = f"Authentication failure: SSH key rejected for OS user '{username}' on host '{hostname}' (check that this key's public half is in authorized_keys for '{username}')."
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err
    except (paramiko.SSHException, IOError) as e:
        err = f"SSH connection error to '{hostname}' using key auth: {e}"
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err
    except socket.timeout:
        err = f"SSH timeout: Connection timed out while connecting to host '{hostname}' (timeout={timeout}s)."
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err
    except ConnectionRefusedError:
        err = f"Connection refused: SSH service (port 22) is not accepting connections on host '{hostname}'."
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err
    except socket.gaierror:
        err = f"Host unreachable: Unable to resolve hostname/IP '{hostname}'."
        logging.error(f"[SSH Grid DEBUG] {err}")
        return None, err
    except Exception as e:
        err_msg = str(e)
        logging.error(f"[SSH Grid DEBUG] General SSH Exception on '{hostname}': {err_msg}")
        return None, f"SSH Connection Error on host '{hostname}': {err_msg}"
