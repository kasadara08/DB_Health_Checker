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
SSH_TIMEOUT = 6    # TCP connection timeout — reduced for fast fail
CMD_TIMEOUT = 8    # Command read timeout — reduced from 20s


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


def _enrich_fs_types_from_map(mount_map: dict, volumes: list) -> list:
    """
    Fill in missing fs_type from a pre-fetched /proc/mounts map.
    No extra SSH round-trip needed.
    """
    for v in volumes:
        if not v["fs_type"]:
            v["fs_type"] = mount_map.get(v["mount_point"], "xfs")
        v["stype"] = "Pseudo" if v["fs_type"].lower() in PSEUDO_FS else "Linux"
    return volumes


def _run_df_and_mounts(client) -> tuple:
    """
    Run df -PkT and cat /proc/mounts in a single batched SSH command
    to avoid two separate round-trips. Returns (df_raw, mount_map).
    Falls back to df -Pk if df -PkT returns nothing.
    """
    # Batch both commands into a single exec to eliminate second round-trip
    batch_cmd = "df -PkT 2>/dev/null; echo '---MOUNTS---'; cat /proc/mounts 2>/dev/null"
    try:
        _, stdout, _ = client.exec_command(batch_cmd, timeout=CMD_TIMEOUT)
        output = stdout.read().decode(errors="replace")
        if "---MOUNTS---" in output:
            df_raw, mounts_raw = output.split("---MOUNTS---", 1)
        else:
            df_raw, mounts_raw = output, ""

        # If df -PkT returned nothing, try df -Pk
        if not df_raw.strip():
            _, stdout2, _ = client.exec_command("df -Pk 2>/dev/null", timeout=CMD_TIMEOUT)
            df_raw = stdout2.read().decode(errors="replace")

        # Build mount map from /proc/mounts
        mount_map = {}
        for line in mounts_raw.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                mount_map[parts[1]] = parts[2]

        return df_raw, mount_map
    except Exception:
        return "", {}



import os

def get_mounts_via_ssh(
    host: str,
    username: str,
    password: str = "",
    port: int = 22,
    timeout: int = SSH_TIMEOUT,
    key_filename: str = None,
    **kwargs
) -> dict:
    """
    Open an SSH session to *host* and collect ALL real mount points using df -h.
    Supports OCI SSH Private Key authentication as well as Password authentication.

    Parameters
    ----------
    host         : Hostname or IP of the remote server.
    username     : OS-level username (from host_username column in registry).
    password     : OS-level password (from host_password column in registry).
    port         : SSH port (default 22).
    timeout      : TCP connection timeout in seconds.
    key_filename : Optional path to OCI SSH key / private key file (.pem).

    Returns
    -------
    dict with two keys:
      - "volumes": list of mount point dicts (empty list on failure)
      - "error":   None on success, or a human-readable error string on failure
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Only use key file if explicitly provided - no auto-search in system default locations
    key_file = (
        key_filename or
        kwargs.get("key_filename") or
        kwargs.get("ssh_key_path") or
        kwargs.get("oci_key_path") or
        kwargs.get("ssh_key") or
        kwargs.get("oci_key")
    )

    connect_kwargs = {
        "hostname": host,
        "port": port,
        "username": username,
        "timeout": timeout,
        "banner_timeout": 5,   # Reduced from 30s → 5s for fast failure
        "auth_timeout": 8,    # Reduced from 15s → 8s
    }
    if password:
        connect_kwargs["password"] = password

    if key_file and os.path.isfile(key_file):
        connect_kwargs["key_filename"] = key_file
        connect_kwargs["look_for_keys"] = True
        connect_kwargs["allow_agent"] = True
        print(f"[ssh_mount] Connecting to {host} using OCI SSH key file: '{key_file}'")
    elif not password:
        connect_kwargs["look_for_keys"] = True
        connect_kwargs["allow_agent"] = True
    else:
        connect_kwargs["look_for_keys"] = False
        connect_kwargs["allow_agent"] = False

    try:
        from db_connection import _log_ssh_oci_key
    except Exception:
        _log_ssh_oci_key = None

    try:
        client.connect(**connect_kwargs)
        if _log_ssh_oci_key:
            _log_ssh_oci_key(host, username, key_file, "UP (SSH Connected)", "")
    except paramiko.AuthenticationException:
        msg = f"SSH Authentication Failed: Wrong username or password for '{username}@{host}:{port}'. Please check host_username and host_password in your registry file."
        print(f"[ssh_mount] {msg}")
        if _log_ssh_oci_key:
            _log_ssh_oci_key(host, username, key_file, "DOWN (Auth Failed)", msg)
        return {"volumes": [], "error": msg}
    except (paramiko.SSHException, socket.timeout, TimeoutError) as exc:
        msg = f"SSH Connection Failed to '{host}:{port}': {exc}. Check if SSH port {port} is open and the server is reachable."
        print(f"[ssh_mount] {msg}")
        if _log_ssh_oci_key:
            _log_ssh_oci_key(host, username, key_file, "DOWN (Connection Failed)", msg)
        return {"volumes": [], "error": msg}
    except OSError as exc:
        msg = f"SSH Network Error connecting to '{host}:{port}': {exc}. Check network/VPN connection."
        print(f"[ssh_mount] {msg}")
        if _log_ssh_oci_key:
            _log_ssh_oci_key(host, username, key_file, "DOWN (Network Error)", msg)
        return {"volumes": [], "error": msg}
    except Exception as exc:
        msg = f"SSH Unexpected Error connecting to '{host}:{port}': {exc}"
        print(f"[ssh_mount] {msg}")
        if _log_ssh_oci_key:
            _log_ssh_oci_key(host, username, key_file, "DOWN (Error)", msg)
        return {"volumes": [], "error": msg}

    try:
        # Single batched SSH call — gets df output AND /proc/mounts in one shot
        raw, mount_map = _run_df_and_mounts(client)

        if not raw.strip():
            msg = f"SSH connected to '{host}' successfully, but 'df' command returned no output. The OS user may not have permission to run df."
            print(f"[ssh_mount] {msg}")
            return {"volumes": [], "error": msg}

        volumes = _parse_df_output(raw)
        volumes = _enrich_fs_types_from_map(mount_map, volumes)

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
        if _log_ssh_oci_key:
            _log_ssh_oci_key(host, username, key_file, "DOWN (Exec Error)", msg)
        return {"volumes": [], "error": msg}
    finally:
        client.close()


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

