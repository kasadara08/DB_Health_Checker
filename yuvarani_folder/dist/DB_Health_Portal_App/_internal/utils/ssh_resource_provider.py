"""
utils/ssh_resource_provider.py
──────────────────────────────
Queries host CPU and memory utilization directly from the remote Linux OS via SSH.
Provides accurate available RAM (accounting for buffer/cache) and current CPU usage.
"""

import socket

def get_system_resources_via_ssh(
    host: str,
    username: str,
    password: str = "",
    port: int = 22,
    timeout: int = 5,
    key_filename: str = None
) -> dict:
    """
    Connects to the host via SSH and queries real CPU & Memory info directly from Linux OS.
    Supports password and OCI key based authentication.
    Returns the same structure as get_system_resources.
    """
    import paramiko
    import os

    res = {
        "num_cpus": 1,
        "cpu_host_used_pct": 0.0,
        "cpu_host_free_pct": 100.0,
        "ram_total_gb": 0.0,
        "ram_used_gb": 0.0,
        "ram_free_gb": 0.0,
    }

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": timeout,
            "banner_timeout": timeout,
            "auth_timeout": timeout,
        }
        if key_filename and os.path.isfile(key_filename):
            connect_kwargs["key_filename"] = key_filename
            connect_kwargs["look_for_keys"] = True
            connect_kwargs["allow_agent"] = True
        elif password:
            connect_kwargs["password"] = password
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        else:
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False

        client.connect(**connect_kwargs)
    except Exception as e:
        print(f"[ssh_resources] Connection to {host} failed: {e}")
        return None

    try:
        # 1. Query CPU count
        _, stdout, _ = client.exec_command("nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo 1", timeout=5)
        nproc_raw = stdout.read().decode(errors="replace").strip()
        num_cpus = int(nproc_raw) if nproc_raw.isdigit() else 1
        res["num_cpus"] = num_cpus

        # 2. Query RAM via free -b (in bytes)
        # Columns: total, used, free, shared, buff/cache, available
        _, stdout, _ = client.exec_command("free -b 2>/dev/null || cat /proc/meminfo 2>/dev/null", timeout=5)
        mem_raw = stdout.read().decode(errors="replace").strip()

        # Check if free -b worked
        if "Mem:" in mem_raw:
            for line in mem_raw.splitlines():
                if line.startswith("Mem:"):
                    parts = line.split()
                    if len(parts) >= 7:
                        total_bytes = float(parts[1])
                        free_bytes = float(parts[6])  # use the "available" column (7th column)

                        res["ram_total_gb"] = round(total_bytes / (1024**3), 2)
                        res["ram_free_gb"] = round(free_bytes / (1024**3), 2)
                        res["ram_used_gb"] = round(max(0.0, total_bytes - free_bytes) / (1024**3), 2)
                    elif len(parts) >= 4:
                        # Fallback for old free commands without "available" column
                        total_bytes = float(parts[1])
                        used_bytes = float(parts[2])
                        free_bytes = float(parts[3])

                        res["ram_total_gb"] = round(total_bytes / (1024**3), 2)
                        res["ram_used_gb"] = round(used_bytes / (1024**3), 2)
                        res["ram_free_gb"] = round(free_bytes / (1024**3), 2)
                    break
        else:
            # Parse /proc/meminfo
            mem_info = {}
            for line in mem_raw.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    val_str = "".join(c for c in v if c.isdigit())
                    if val_str:
                        mem_info[k.strip()] = float(val_str) * 1024 # convert kB to bytes

            total_bytes = mem_info.get("MemTotal", 0)
            free_bytes = mem_info.get("MemAvailable", mem_info.get("MemFree", 0) + mem_info.get("Buffers", 0) + mem_info.get("Cached", 0))
            if total_bytes > 0:
                res["ram_total_gb"] = round(total_bytes / (1024**3), 2)
                res["ram_free_gb"] = round(free_bytes / (1024**3), 2)
                res["ram_used_gb"] = round(max(0.0, total_bytes - free_bytes) / (1024**3), 2)

        # 3. Query CPU load via vmstat 1 2
        _, stdout, _ = client.exec_command("vmstat 1 2 2>/dev/null", timeout=5)
        cpu_raw = stdout.read().decode(errors="replace").strip()
        lines = cpu_raw.splitlines()
        if len(lines) >= 3:
            # Last line contains the 1-second sample
            last_line = lines[-1].split()
            if len(last_line) >= 15:
                headers = lines[1].split()
                try:
                    id_idx = headers.index("id")
                    idle_pct = float(last_line[id_idx])
                    res["cpu_host_used_pct"] = round(100.0 - idle_pct, 2)
                    res["cpu_host_free_pct"] = round(idle_pct, 2)
                except Exception:
                    # fallback if "id" is not found in headers: typical column index for idle is -3
                    idle_pct = float(last_line[-3])
                    res["cpu_host_used_pct"] = round(100.0 - idle_pct, 2)
                    res["cpu_host_free_pct"] = round(idle_pct, 2)
        else:
            # Fallback to top command if vmstat failed
            _, stdout, _ = client.exec_command("top -bn1 | grep -i 'cpu(s)' 2>/dev/null", timeout=5)
            top_raw = stdout.read().decode(errors="replace").lower()
            if "id" in top_raw:
                try:
                    parts = top_raw.split(",")
                    for p in parts:
                        if "id" in p:
                            idle_val = float("".join(c for c in p if c.isdigit() or c == "."))
                            res["cpu_host_used_pct"] = round(100.0 - idle_val, 2)
                            res["cpu_host_free_pct"] = round(idle_val, 2)
                            break
                except Exception:
                    pass
    except Exception as e:
        print(f"[ssh_resources] Error executing resources queries on {host}: {e}")
    finally:
        client.close()

    return res
