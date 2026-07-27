"""
utils/ssh_mount_provider.py
───────────────────────────
Uses Paramiko (SSH) to retrieve ALL real disk/mount-point information from
remote Linux/Unix Oracle DB servers.

Works for ANY server — credentials are looked up from the Oracle DB registry
JSON file (host, username, password). No separate SSH config required.

Returned list format:
[
    {
        "drive":       "/dev/sda1",
        "mount_point": "/",
        "total":       95.0,      # GB
        "used":        40.2,      # GB
        "free":        54.8,      # GB
        "pct":         42.3,      # %
        "fs_type":     "xfs",
        "uuid":        "",
        "stype":       "Linux",
        "state":       "ACTIVE",
        "source":      "ssh",
    },
    ...
]
"""

import socket
import paramiko

# ── Pseudo / virtual file-system types to exclude ─────────────────────────────
PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "tmpfs", "cgroup", "cgroup2", "autofs",
    "configfs", "debugfs", "hugetlbfs", "mqueue", "pstore", "securityfs",
    "devpts", "binfmt_misc", "fusectl", "overlay", "aufs", "squashfs",
    "nsfs", "efivarfs", "tracefs", "ramfs", "nfsd", "sunrpc", "rpc_pipefs",
    "selinuxfs", "udev", "none",
}

# ── SSH / command timeouts (seconds) ──────────────────────────────────────────
SSH_TIMEOUT = 12
CMD_TIMEOUT = 20


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_df_output(raw: str) -> list:
    """
    Parse the stdout of `df -PkT` / `df -Pk`.
    Handles long device names wrapped onto multiple lines.
    Supports 7-column (df -PkT) and 6-column (df -Pk) output formats.
    """
    volumes = []
    lines = raw.strip().splitlines()
    if not lines:
        return volumes

    data_lines = []
    pending = ""
    for line in lines[1:]:          # skip header row
        if pending:
            line = pending + " " + line.strip()
            pending = ""
        parts = line.split()
        if len(parts) == 1:         # long device name wrapped → defer to next line
            pending = line.strip()
            continue
        if len(parts) < 6:
            continue
        data_lines.append(parts)

    for parts in data_lines:
        device = parts[0]

        # Detect df -PkT vs df -Pk
        fs_type_val = ""
        if not parts[1].isdigit() and not any(parts[1].endswith(x) for x in ["K", "M", "G", "T", "%"]):
            fs_type_val = parts[1]
            num_parts = parts[2:]
        else:
            num_parts = parts[1:]

        if len(num_parts) < 5:
            continue

        try:
            total_kb = int(num_parts[0])
            used_kb  = int(num_parts[1])
            avail_kb = int(num_parts[2])
            pct      = float(num_parts[3].rstrip("%"))
        except (ValueError, IndexError):
            continue

        mount_point = num_parts[4] if len(num_parts) > 4 else num_parts[-1]

        if total_kb == 0:           # skip zero-size pseudo mounts
            continue

        volumes.append({
            "drive":       device,
            "mount_point": mount_point,
            "total":       round(total_kb / (1024 ** 2), 3),   # GB
            "used":        round(used_kb  / (1024 ** 2), 3),
            "free":        round(avail_kb / (1024 ** 2), 3),
            "pct":         round(pct, 2),
            "fs_type":     fs_type_val,
            "uuid":        "",
            "stype":       "Linux",
            "state":       "ACTIVE",
            "source":      "ssh",
        })
    return volumes


def _enrich_fs_types(client, volumes: list) -> list:
    """
    Fill in missing fs_type by reading /proc/mounts via SSH.
    """
    mount_map = {}
    try:
        _, stdout, _ = client.exec_command("cat /proc/mounts 2>/dev/null", timeout=CMD_TIMEOUT)
        for line in stdout.read().decode(errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                mount_map[parts[1]] = parts[2]
    except Exception:
        pass

    for v in volumes:
        if not v["fs_type"]:
            v["fs_type"] = mount_map.get(v["mount_point"], "xfs")
        v["stype"] = "Pseudo" if v["fs_type"].lower() in PSEUDO_FS else "Linux"

    return volumes


def _run_df(client) -> str:
    """
    Try several df variants in order of preference until we get output.
    Attempts: df -PkT → df -PT → df -Pk → df -Ph
    """
    commands = [
        "df -PkT 2>/dev/null",
        "df -PT  2>/dev/null",
        "df -Pk  2>/dev/null",
        "df -Ph  2>/dev/null",
    ]
    for cmd in commands:
        try:
            _, stdout, _ = client.exec_command(cmd, timeout=CMD_TIMEOUT)
            raw = stdout.read().decode(errors="replace")
            if raw.strip():
                return raw
        except Exception:
            continue
    return ""



def get_mounts_via_ssh(
    host: str,
    username: str,
    password: str,
    port: int = 22,
    timeout: int = SSH_TIMEOUT,
) -> dict:
    """
    Open an SSH session to *host* and collect ALL real mount points using df -h.

    Parameters
    ----------
    host      : Hostname or IP of the remote server.
    username  : OS-level username (from host_username column in registry).
    password  : OS-level password (from host_password column in registry).
    port      : SSH port (default 22).
    timeout   : TCP connection timeout in seconds.

    Returns
    -------
    dict with two keys:
      - "volumes": list of mount point dicts (empty list on failure)
      - "error":   None on success, or a human-readable error string on failure
    """
    # paramiko is now imported at the top level so PyInstaller can detect and bundle it.

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            look_for_keys=False,    # never use SSH key files
            allow_agent=False,      # never use SSH agent
            banner_timeout=30,
            auth_timeout=15,
        )
    except paramiko.AuthenticationException:
        msg = f"SSH Authentication Failed: Wrong username or password for '{username}@{host}:{port}'. Please check host_username and host_password in your registry file."
        print(f"[ssh_mount] {msg}")
        return {"volumes": [], "error": msg}
    except (paramiko.SSHException, socket.timeout, TimeoutError) as exc:
        msg = f"SSH Connection Failed to '{host}:{port}': {exc}. Check if SSH port {port} is open and the server is reachable."
        print(f"[ssh_mount] {msg}")
        return {"volumes": [], "error": msg}
    except OSError as exc:
        msg = f"SSH Network Error connecting to '{host}:{port}': {exc}. Check network/VPN connection."
        print(f"[ssh_mount] {msg}")
        return {"volumes": [], "error": msg}
    except Exception as exc:
        msg = f"SSH Unexpected Error connecting to '{host}:{port}': {exc}"
        print(f"[ssh_mount] {msg}")
        return {"volumes": [], "error": msg}

    try:
        raw = _run_df(client)

        if not raw.strip():
            msg = f"SSH connected to '{host}' successfully, but 'df -h' command returned no output. The OS user may not have permission to run df."
            print(f"[ssh_mount] {msg}")
            return {"volumes": [], "error": msg}

        volumes = _parse_df_output(raw)
        volumes = _enrich_fs_types(client, volumes)

        # Remove pseudo / virtual file systems and zero-size volumes
        volumes = [
            v for v in volumes
            if v["stype"] != "Pseudo"
            and v["total"] > 0
            and v["fs_type"].lower() not in PSEUDO_FS
        ]

        print(f"[ssh_mount] OK {host}: {len(volumes)} real mount point(s) retrieved")
        return {"volumes": volumes, "error": None}

    except Exception as exc:
        msg = f"SSH connected but failed while running df on '{host}': {exc}"
        print(f"[ssh_mount] {msg}")
        return {"volumes": [], "error": msg}
    finally:
        client.close()


def get_mounts_for_db(db_name: str, ssh_port: int = 22) -> list:
    """
    Convenience wrapper: look up the DB registry for *db_name*,
    extract host / username / password, then call get_mounts_via_ssh().

    Parameters
    ----------
    db_name  : Database name as it appears in the TXT registry file.
    ssh_port : SSH port on the DB server (default 22).

    Returns
    -------
    List of volume dicts, or [] on error.
    """
    try:
        from db_connection import get_config_for_db
        cfg = get_config_for_db(db_name)
        if not cfg:
            print(f"[ssh_mount] No registry config found for DB: {db_name}")
            return []

        host     = cfg.get("host", "").strip()
        host_user = cfg.get("host_username", "").strip()
        host_pass = cfg.get("host_password", "").strip()
        
        username = host_user
        password = host_pass

        if not host or not username:
            print(f"[ssh_mount] Missing host or username for DB: {db_name}")
            return []

        return get_mounts_via_ssh(
            host=host,
            username=username,
            password=password,
            port=ssh_port,
            timeout=SSH_TIMEOUT,
        )
    except Exception as exc:
        print(f"[ssh_mount] get_mounts_for_db({db_name}) error: {exc}")
        return []


def get_mounts_for_all_dbs(ssh_port: int = 22) -> dict:
    """
    Iterate over ALL databases in the registry and collect mount points
    for every unique server host.

    Returns
    -------
    dict: { "host": [volume_dicts, ...], ... }
          Hosts that fail SSH are silently skipped.
    """
    results = {}
    try:
        from db_connection import get_db_names, get_config_for_db
        seen_hosts = set()

        for db_name in get_db_names():
            cfg = get_config_for_db(db_name)
            if not cfg:
                continue

            host     = cfg.get("host", "").strip()
            
            # ONLY use host_username/password — never fallback to DB user/password
            host_user = cfg.get("host_username", "").strip()
            host_pass = cfg.get("host_password", "").strip()
            
            username = host_user
            password = host_pass

            if not host or not username or not password or host in seen_hosts:
                continue

            seen_hosts.add(host)
            volumes = get_mounts_via_ssh(
                host=host,
                username=username,
                password=password,
                port=ssh_port,
                timeout=SSH_TIMEOUT,
            )
            if volumes:
                results[host] = volumes

    except Exception as exc:
        print(f"[ssh_mount] get_mounts_for_all_dbs error: {exc}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Oracle Scheduler-based mount retrieval  (works when SSH credentials = DB user)
# ─────────────────────────────────────────────────────────────────────────────
PSEUDO_FS_TYPES = {
    "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2",
    "autofs", "configfs", "debugfs", "devpts", "hugetlbfs",
    "mqueue", "pstore", "securityfs", "binfmt_misc", "fusectl",
    "overlay", "squashfs", "nsfs", "efivarfs", "tracefs",
    "ramfs", "nfsd", "sunrpc", "rpc_pipefs", "selinuxfs", "udev",
}

def get_mounts_via_oracle_scheduler(conn) -> list:
    """
    Run `df -Pk` on the Oracle server via DBMS_SCHEDULER EXECUTABLE job.
    Writes output to /tmp/dash_df.txt, then reads it back with UTL_FILE.

    This works even when the DB user is 'sys' (not a Linux OS user)
    because the Oracle process itself runs the shell command.

    Returns a parsed list of volume dicts identical to get_mounts_via_ssh().
    Returns [] if the scheduler or UTL_FILE approach is not available.
    """
    if not conn:
        return []
    try:
        import time
        cursor = conn.cursor()

        # Step 1: Ensure /tmp directory object exists
        try:
            cursor.execute(
                "CREATE OR REPLACE DIRECTORY DASH_TMP_DIR AS '/tmp'"
            )
        except Exception:
            pass  # Already exists or no privilege — try anyway

        # Step 2: Drop old job if exists, create fresh df -PkT (includes FS type)
        cursor.execute("""
            BEGIN
              BEGIN
                DBMS_SCHEDULER.DROP_JOB('DASH_DF_JOB', force => TRUE);
              EXCEPTION WHEN OTHERS THEN NULL;
              END;
              DBMS_SCHEDULER.CREATE_JOB(
                job_name            => 'DASH_DF_JOB',
                job_type            => 'EXECUTABLE',
                job_action          => '/bin/bash',
                number_of_arguments => 2,
                enabled             => FALSE,
                auto_drop           => TRUE
              );
              DBMS_SCHEDULER.SET_JOB_ARGUMENT_VALUE('DASH_DF_JOB', 1, '-c');
              DBMS_SCHEDULER.SET_JOB_ARGUMENT_VALUE('DASH_DF_JOB', 2,
                'df -PkT 2>/dev/null > /tmp/dash_df.txt');
              DBMS_SCHEDULER.RUN_JOB('DASH_DF_JOB', use_current_session => FALSE);
            END;
        """)
        conn.commit()
        time.sleep(2)   # Give the OS job time to complete

        # Step 3: Read output file back via UTL_FILE
        result_var = cursor.var(str)
        cursor.execute("""
            DECLARE
              l_file  UTL_FILE.file_type;
              l_line  VARCHAR2(4000);
              l_out   VARCHAR2(32767) := '';
            BEGIN
              l_file := UTL_FILE.FOPEN('DASH_TMP_DIR', 'dash_df.txt', 'r', 4000);
              LOOP
                BEGIN
                  UTL_FILE.GET_LINE(l_file, l_line);
                  l_out := l_out || l_line || CHR(10);
                EXCEPTION WHEN NO_DATA_FOUND THEN EXIT;
                END;
              END LOOP;
              UTL_FILE.FCLOSE(l_file);
              :out := l_out;
            END;
        """, [result_var])
        raw = result_var.getvalue() or ""

        if not raw.strip():
            print("[oracle_sched] df output is empty")
            return []

        # Step 4: Parse using the existing _parse_df_output helper
        volumes = _parse_df_output(raw)

        # Step 5: Filter — keep only real physical filesystems
        #   Check fs_type field AND device name AND mount path prefix
        PSEUDO_DEVICES = {"tmpfs", "devtmpfs", "proc", "sysfs", "cgroup",
                          "none", "udev", "overlay", "aufs", "ramfs"}
        SKIP_MOUNT_PREFIXES = ("/sys", "/proc", "/dev/shm", "/run/user",
                               "/run/lock", "/snap")

        real = []
        for v in volumes:
            fs   = v.get("fs_type", "").lower()
            dev  = v.get("drive", "").lower()
            mp   = v.get("mount_point", "")

            # Skip by fs_type if available
            if fs and fs in PSEUDO_FS_TYPES:
                continue
            # Skip by device name (df -Pk has empty fs_type)
            if dev in PSEUDO_DEVICES:
                continue
            # Skip well-known pseudo mount paths
            if any(mp.startswith(pfx) for pfx in SKIP_MOUNT_PREFIXES):
                continue
            # Skip pure /dev entries (not /dev/mapper/...)
            if mp == "/dev":
                continue
            # Skip zero-size
            if v.get("total", 0) <= 0:
                continue
            real.append(v)

        print(f"[oracle_sched] Returned {len(real)} real volumes from df")
        return real

    except Exception as exc:
        print(f"[oracle_sched] get_mounts_via_oracle_scheduler error: {exc}")
        return []

