"""
utils/ssh_process_provider.py
─────────────────────────────
Fetches top CPU and memory consuming processes from a remote Oracle DB
server via Paramiko SSH.

Option B filtering:
  - SHOW  : Oracle server/user processes  e.g. oracle@hostname (W005)
  - EXCLUDE: Oracle background daemons    e.g. ora_pmon_ORCL, ora_lgwr_ORCL
  - EXCLUDE: Unrelated system processes   e.g. kernel threads, init, cron

Process data is cached per-host for CACHE_TTL seconds to avoid hammering
SSH on every Streamlit rerender.
"""

import socket
import time
import threading

# ── Cache ──────────────────────────────────────────────────────────────────────
_proc_cache: dict = {}          # {host: {"ts": float, "procs": list}}
_cache_lock = threading.Lock()
CACHE_TTL   = 30                # seconds


# ── Oracle background daemon detection ────────────────────────────────────────
def is_oracle_background(name: str) -> bool:
    """
    Return True if the process name is an Oracle background daemon.
    Background daemons have names like: ora_pmon_SID, ora_lgwr_SID, etc.
    """
    return (name or "").lower().strip().startswith("ora_")


# ── SID matching ──────────────────────────────────────────────────────────────
def filter_for_sid(processes: list, sid: str) -> list:
    """
    Return only Oracle USER/SERVER processes belonging to the given SID.

    Includes:
      - oracle@<hostname>-<SID> (the canonical Oracle server process format)
      - oracle or oracleORCL where SID appears in args
    Excludes:
      - All background daemons (ora_* prefix)
      - Any process that doesn't reference the SID
    """
    if not sid:
        # No SID filter — just exclude background daemons
        return [p for p in processes if not is_oracle_background(p.get("name", ""))]

    sid_lower = sid.lower()
    result = []
    for p in processes:
        name = (p.get("name") or "").lower().strip()
        args = (p.get("args") or "").lower().strip()

        # Always exclude background daemons
        if is_oracle_background(name):
            continue

        # Match Oracle user/server processes for this SID
        # Patterns:
        #   oracle@kasadara-ORCL (W005)  → name contains sid
        #   oracleORCL                   → name ends with sid
        #   /u01/app/oracle/... ORCL     → args contain sid
        if sid_lower in name or sid_lower in args:
            result.append(p)

    return result


# ── ps output parser ──────────────────────────────────────────────────────────
def _parse_ps_output(raw: str) -> list:
    """
    Parse: ps -eo pid,user,pcpu,pmem,vsz,comm,args --no-headers
    Columns: PID USER %CPU %MEM VSZ COMM ARGS...

    Returns list of dicts per process.
    """
    processes = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Split into at most 7 parts (last field is full args)
        parts = line.split(None, 6)
        if len(parts) < 6:
            continue
        try:
            pid_str  = parts[0]
            user_str = parts[1]
            cpu_pct  = round(float(parts[2]), 2)
            mem_pct  = round(float(parts[3]), 2)
            vsz_kb   = parts[4]
            comm_str = parts[5]
            args_str = parts[6] if len(parts) > 6 else comm_str

            vsz_mb = round(int(vsz_kb) / 1024, 1) if vsz_kb.isdigit() else 0.0

            processes.append({
                "pid":      pid_str,
                "username": user_str,
                "name":     comm_str,
                "args":     args_str,
                "cpu":      cpu_pct,
                "mem":      mem_pct,
                "vsz_mb":   vsz_mb,
            })
        except (ValueError, IndexError):
            continue

    return processes


# ── SSH fetch with cache ───────────────────────────────────────────────────────
def _fetch_all_processes(
    host: str,
    username: str,
    password: str,
    port: int = 22,
    timeout: int = 3,
) -> tuple:
    """
    SSH into host and return (procs_list, error_str).
    Results cached per host for CACHE_TTL seconds.
    """
    now = time.monotonic()

    with _cache_lock:
        cached = _proc_cache.get(host)
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["procs"], None

    try:
        import paramiko
    except ImportError:
        return [], "paramiko not installed — run: pip install paramiko"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=3,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=3,
            auth_timeout=3,
        )

    except paramiko.AuthenticationException:
        return [], f"SSH authentication failed for {username}@{host}"
    except (paramiko.SSHException, socket.timeout, TimeoutError, OSError) as exc:
        return [], f"SSH connection failed: {exc}"
    except Exception as exc:
        return [], f"SSH unexpected error: {exc}"

    try:
        # Fetch top 200 processes sorted by CPU, with full arg list
        cmd = (
            "ps -eo pid,user,pcpu,pmem,vsz,comm,args --no-headers --sort=-%cpu "
            "2>/dev/null | head -200"
        )
        _, stdout, _ = client.exec_command(cmd, timeout=15)
        raw = stdout.read().decode(errors="replace")

        procs = _parse_ps_output(raw)

        with _cache_lock:
            _proc_cache[host] = {"ts": time.monotonic(), "procs": procs}

        print(f"[ssh_proc] OK {host}: {len(procs)} processes fetched")
        return procs, None

    except Exception as exc:
        return [], f"Error running ps on {host}: {exc}"
    finally:
        client.close()


# ── Public API ─────────────────────────────────────────────────────────────────

def _fetch_processes_via_db(sid: str, limit: int = 10) -> tuple:
    """
    Fallback method: Query Oracle DB directly for process info.
    Runs TWO separate queries:
      - top_cpu : sorted by CPU seconds used (v$sesstat + 'CPU used by this session')
                  EXCLUDES SYS/SYSTEM — shows oracle application user sessions only
      - top_mem : sorted by PGA memory used (v$process.pga_used_mem)
                  EXCLUDES SYS/SYSTEM — shows oracle application user sessions only
    """
    try:
        from db_connection import get_api_connection
        conn, err = get_api_connection(sid)
        if not conn:
            return [], [], f"DB connection error: {err}"

        # ── CPU Query: oracle application user sessions only (no SYS/SYSTEM) ──
        sql_cpu = """
            SELECT p.spid        AS pid,
                   s.sid,
                   s.username,
                   ROUND(NVL(ss.value, 0) / 100, 2)  AS cpu_sec,
                   ROUND(p.pga_used_mem / 1024 / 1024, 1) AS mem_mb,
                   s.status
            FROM v$process  p
            JOIN v$session  s  ON s.paddr = p.addr
            LEFT JOIN v$sesstat  ss ON ss.sid   = s.sid
            LEFT JOIN v$statname sn ON sn.statistic# = ss.statistic# AND sn.name = 'CPU used by this session'
            WHERE s.type      = 'USER'
              AND p.spid     IS NOT NULL
              AND s.username IS NOT NULL
              AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
            ORDER BY NVL(ss.value, 0) DESC
        """

        # ── MEM Query: oracle application user sessions only (no SYS/SYSTEM) ──
        sql_mem = """
            SELECT p.spid        AS pid,
                   s.sid,
                   s.username,
                   ROUND(p.pga_used_mem / 1024 / 1024, 1) AS mem_mb,
                   s.status
            FROM v$process p
            JOIN v$session s ON s.paddr = p.addr
            WHERE s.type      = 'USER'
              AND p.spid     IS NOT NULL
              AND s.username IS NOT NULL
              AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
            ORDER BY p.pga_used_mem DESC
        """

        cursor = conn.cursor()

        # Fetch CPU data
        cursor.execute(sql_cpu)
        cpu_rows = cursor.fetchmany(limit)
        cpu_cols = [col[0] for col in cursor.description]

        # Fetch MEM data
        cursor.execute(sql_mem)
        mem_rows = cursor.fetchmany(limit)
        mem_cols = [col[0] for col in cursor.description]

        cursor.close()
        conn.close()

        # Build top_cpu list
        top_cpu = []
        for r in cpu_rows:
            rd = dict(zip(cpu_cols, r))
            sid_num  = rd.get("SID", "")
            status   = str(rd.get("STATUS") or "").capitalize()
            raw_user = str(rd.get("USERNAME") or "oracle").lower()
            cpu_sec  = float(rd.get("CPU_SEC") or 0.0)
            mem_mb   = float(rd.get("MEM_MB") or 0.0)
            top_cpu.append({
                "pid":        str(rd.get("PID") or "N/A"),
                "sid":        sid_num,
                "username":   raw_user,              # plain oracle username
                "user_label": f"{raw_user} ({sid})", # "oracle (kasorcl)"
                "status":     status,
                "cpu_sec":    cpu_sec,
                "cpu":        cpu_sec,
                "mem":        mem_mb,
                "vsz_mb":     mem_mb,
            })

        # Build top_mem list
        top_mem = []
        for r in mem_rows:
            rd = dict(zip(mem_cols, r))
            sid_num  = rd.get("SID", "")
            status   = str(rd.get("STATUS") or "").capitalize()
            raw_user = str(rd.get("USERNAME") or "oracle").lower()
            mem_mb   = float(rd.get("MEM_MB") or 0.0)
            top_mem.append({
                "pid":        str(rd.get("PID") or "N/A"),
                "sid":        sid_num,
                "username":   raw_user,
                "user_label": f"{raw_user} ({sid})",
                "status":     status,
                "cpu_sec":    0.0,
                "cpu":        0.0,
                "mem":        mem_mb,
                "vsz_mb":     mem_mb,
            })

        return top_cpu, top_mem, None

    except Exception as exc:
        return [], [], f"DB query error: {exc}"





def get_top_processes_for_db(
    host: str,
    username: str,
    password: str,
    sid: str,
    limit: int = 10,
    port: int = 22,
) -> dict:
    """
    Fetch top CPU & Memory consuming Oracle USER processes for a specific SID.

    Tier 1: SSH (Paramiko)
    Tier 2: Direct Oracle DB v$process + v$session query (if SSH fails or yields 0 user processes)

    Returns:
        {
            "top_cpu": [{"pid","sid","name","username","cpu","mem","vsz_mb","args"}, ...],
            "top_mem": [same structure, sorted by mem desc],
            "error": None | "description of error",
            "source": "ssh" | "v$session" | "none",
        }
    """
    result = {"top_cpu": [], "top_mem": [], "error": None, "source": "none"}

    # 1. Try SSH first
    if host and username:
        all_procs, err = _fetch_all_processes(host, username, password, port)
        if not err and all_procs:
            sid_procs = filter_for_sid(all_procs, sid)
            if sid_procs:
                result["top_cpu"] = sorted(sid_procs, key=lambda x: x.get("cpu", 0.0), reverse=True)[:limit]
                result["top_mem"] = sorted(sid_procs, key=lambda x: x.get("mem", 0.0), reverse=True)[:limit]
                result["source"]  = "ssh"
                return result

    # 2. Fallback to Oracle DB query (v$process + v$session)
    top_cpu, top_mem, db_err = _fetch_processes_via_db(sid, limit)
    if top_cpu or top_mem:
        result["top_cpu"] = top_cpu
        result["top_mem"] = top_mem
        result["source"]  = "v$session"
        return result

    result["error"] = f"No user processes found ({db_err if db_err else '0 active sessions'})"
    return result



def get_top_processes_for_host(
    host: str,
    username: str,
    password: str,
    limit: int = 10,
    port: int = 22,
) -> dict:
    """
    Fetch top processes for the entire host (no SID filter).
    Excludes Oracle background daemons.

    Returns same structure as get_top_processes_for_db.
    """
    result = {"top_cpu": [], "top_mem": [], "error": None, "source": "none"}

    if not host or not username:
        result["error"] = "SSH credentials not configured"
        return result

    all_procs, err = _fetch_all_processes(host, username, password, port)

    if err:
        result["error"] = f"SSH unavailable - {err}"
        return result

    # Exclude Oracle background daemons only
    filtered = [p for p in all_procs if not is_oracle_background(p.get("name", ""))]

    result["top_cpu"]  = sorted(filtered, key=lambda x: x.get("cpu", 0.0), reverse=True)[:limit]
    result["top_mem"]  = sorted(filtered, key=lambda x: x.get("mem", 0.0), reverse=True)[:limit]
    result["source"]   = "ssh"
    return result


def invalidate_cache(host: str = None):
    """Force-clear the process cache for a specific host (or all hosts)."""
    with _cache_lock:
        if host:
            _proc_cache.pop(host, None)
        else:
            _proc_cache.clear()


def get_top_processes_for_server_via_db(db_list: list, limit: int = 10) -> dict:
    """
    Query ALL databases on the same server via Oracle DB views (v$session, v$process,
    v$sesstat) to get the top CPU and Memory consuming sessions.

    No SSH required — works purely through the Oracle DB connection.
    Results from all DBs on the same host are combined and sorted server-wide.

    Parameters
    ----------
    db_list : list of DB name strings (all belonging to the same host/server)
    limit   : max rows to return per table (default 10)

    Returns
    -------
    {
        "top_cpu": [...],   sorted by CPU time descending
        "top_mem": [...],   sorted by PGA memory descending
        "error":   None | "error message",
        "source":  "v$session"
    }
    """
    result = {"top_cpu": [], "top_mem": [], "error": None, "source": "v$session"}

    all_cpu = []
    all_mem = []
    errors  = []

    for db_name in db_list:
        top_cpu, top_mem, err = _fetch_processes_via_db(db_name, limit=limit * 2)
        if err:
            errors.append(f"{db_name}: {err}")
            continue
        all_cpu.extend(top_cpu)
        all_mem.extend(top_mem)

    if not all_cpu and not all_mem:
        result["error"] = "; ".join(errors) if errors else "No active oracle user sessions (0 active sessions)"
        return result

    # Sort combined results server-wide and take top N
    result["top_cpu"] = sorted(all_cpu, key=lambda x: x.get("cpu_sec", x.get("cpu", 0.0)), reverse=True)[:limit]
    result["top_mem"] = sorted(all_mem, key=lambda x: x.get("mem", 0.0), reverse=True)[:limit]
    result["source"]  = "v$session"
    return result
