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
    """
    if not sid:
        # No SID filter — just exclude background daemons and system tools
        result = []
        for p in processes:
            name = (p.get("name") or "").lower().strip()
            args = (p.get("args") or "").lower().strip()
            if is_oracle_background(name):
                continue
            if "oracle" in name or "oracle" in args:
                if not any(x in name or x in args for x in ["top", "grep", "ps -eo", "tnslsnr", "systemd"]):
                    result.append(p)
        return result

    sid_lower = sid.lower()
    result = []
    for p in processes:
        name = (p.get("name") or "").lower().strip()
        args = (p.get("args") or "").lower().strip()
        p_sid = (p.get("sid") or "").lower().strip()

        # Always exclude background daemons
        if is_oracle_background(name):
            continue

        # Exclude listener, top, and common OS tools
        if any(x in name or x in args for x in ["top", "grep", "ps -eo", "tnslsnr", "systemd"]):
            continue

        # Match by ORACLE_SID environment mapping, or string matches in name/args
        if p_sid == sid_lower or sid_lower in name or sid_lower in args:
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

        # Get ORACLE_SID for all processes to map PID -> SID
        pid_to_sid = {}
        try:
            _, stdout_env, _ = client.exec_command("grep -ao 'ORACLE_SID=[a-zA-Z0-9_-]*' /proc/[0-9]*/environ 2>/dev/null", timeout=5)
            env_raw = stdout_env.read().decode(errors="replace")
            for line in env_raw.splitlines():
                if "/proc/" in line and "ORACLE_SID=" in line:
                    parts = line.split("/")
                    if len(parts) >= 3:
                        pid = parts[2]
                        sid_part = line.split("ORACLE_SID=")
                        if len(sid_part) >= 2:
                            pid_to_sid[pid] = sid_part[1].strip()

            # Map ORACLE_SID to each parsed process
            for p in procs:
                pid = p.get("pid")
                if pid in pid_to_sid:
                    p["sid"] = pid_to_sid[pid]
        except Exception as ee:
            print(f"[ssh_proc] Failed to fetch ORACLE_SID mapping: {ee}")

        with _cache_lock:
            _proc_cache[host] = {"ts": time.monotonic(), "procs": procs}

        print(f"[ssh_proc] OK {host}: {len(procs)} processes fetched")
        return procs, None

    except Exception as exc:
        return [], f"Error running ps on {host}: {exc}"
    finally:
        client.close()


# ── Public API ─────────────────────────────────────────────────────────────────

def _fetch_processes_via_db(sid: str, limit: int = 10, sample_interval: float = 1.0) -> tuple:
    """
    Fallback method (no SSH): query Oracle DB directly for process info.

    Returns a genuine LIVE %CPU (the same 0-100+ convention OS `ps`/`top`
    use), not a cumulative total. v$sesstat's "CPU used by this session" is
    a lifetime counter (centiseconds accumulated since the session started),
    so a single read of it is a total, not a percentage. This samples it
    TWICE, ~1 second apart, and divides the delta by the elapsed wall time —
    same technique as the SSH-based live-CPU sampler in this module, just
    over a DB connection instead of `ps`.

    top_mem is sorted by PGA memory (v$process.pga_used_mem, already an
    instantaneous gauge, not a counter — no need to sample it twice), but
    carries the SAME live %CPU per PID so both tables agree.
    """
    conn = None
    try:
        from db_connection import get_api_connection
        conn, err = get_api_connection(sid)
        if not conn:
            return [], [], f"DB connection error: {err}"

        sql_snapshot = """
            SELECT p.spid        AS pid,
                   s.sid,
                   NVL(s.username, 'oracle') AS username,
                   NVL(ss.value, 0) AS cpu_centisec,
                   ROUND(p.pga_used_mem / 1024 / 1024, 1) AS mem_mb,
                   s.status
            FROM v$process  p
            JOIN v$session  s  ON s.paddr = p.addr
            LEFT JOIN (
                SELECT st.sid, st.value
                FROM v$sesstat st
                JOIN v$statname nm ON nm.statistic# = st.statistic#
                WHERE nm.name = 'CPU used by this session'
            ) ss ON ss.sid = s.sid
            WHERE p.spid IS NOT NULL
              AND NVL(s.username, 'oracle') NOT IN ('DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC')
        """

        cursor = conn.cursor()

        t0 = time.monotonic()
        cursor.execute(sql_snapshot)
        cols = [c[0] for c in cursor.description]
        snap1 = {str(dict(zip(cols, r)).get("SID")): dict(zip(cols, r)) for r in cursor.fetchall()}

        time.sleep(sample_interval)

        cursor.execute(sql_snapshot)
        snap2 = {str(dict(zip(cols, r)).get("SID")): dict(zip(cols, r)) for r in cursor.fetchall()}
        elapsed = max(time.monotonic() - t0, 0.001)

        cursor.close()

        merged = []
        for sid_key, row2 in snap2.items():
            row1 = snap1.get(sid_key)
            centisec2 = float(row2.get("CPU_CENTISEC") or 0.0)
            centisec1 = float(row1.get("CPU_CENTISEC")) if row1 else centisec2
            delta_centisec = max(centisec2 - centisec1, 0.0)
            # centiseconds of CPU used per second elapsed == %CPU (100
            # centisec used in 1.0s elapsed == 1.0s of CPU time == 100%).
            live_cpu_pct = round(delta_centisec / elapsed, 1)
            raw_user = str(row2.get("USERNAME") or "oracle").lower()
            merged.append({
                "pid":        str(row2.get("PID") or "N/A"),
                "sid":        sid_key,
                "username":   raw_user,
                "user_label": raw_user,
                "status":     str(row2.get("STATUS") or "").capitalize(),
                "cpu":        live_cpu_pct,
                "mem":        float(row2.get("MEM_MB") or 0.0),
                "vsz_mb":     float(row2.get("MEM_MB") or 0.0),
            })

        top_cpu = sorted(merged, key=lambda x: x["cpu"], reverse=True)[:limit]
        top_mem = sorted(merged, key=lambda x: x["mem"], reverse=True)[:limit]

        return top_cpu, top_mem, None

    except Exception as exc:
        return [], [], f"DB query error: {exc}"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_top_processes_via_db_only(db_name: str, limit: int = 10) -> dict:
    """
    Public wrapper around _fetch_processes_via_db, returning the same
    {"top_cpu", "top_mem", "error", "source"} shape as
    get_top_processes_for_host/get_top_processes_for_db. Lets a caller that
    has no SSH access to a host (e.g. no key configured) still show a
    genuine live %CPU by querying one representative DB on that host
    directly, instead of showing nothing.
    """
    result = {"top_cpu": [], "top_mem": [], "error": None, "source": "none"}
    top_cpu, top_mem, err = _fetch_processes_via_db(db_name, limit)
    if top_cpu or top_mem:
        result["top_cpu"] = top_cpu
        result["top_mem"] = top_mem
        result["source"]  = "v$session"
        return result
    result["error"] = err or "No active oracle user sessions (0 active sessions)"
    return result


def extract_sid_from_command(args_str: str) -> str:
    """
    Extract Oracle SID from command line arguments.
    Example: 
      - 'ora_vktm_testsql2orac' -> 'testsql2orac'
      - 'oraclekasorcl (LOCAL=NO)' -> 'kasorcl'
      - 'ora_dbw0_testsql2orac' -> 'testsql2orac'
    """
    import re
    if not args_str:
        return "N/A"
    
    # 1. Match background process patterns: ora_<proc_name>_<sid>
    bg_match = re.search(r'\bora_[a-z0-9]+_([a-zA-Z0-9_-]+)\b', args_str)
    if bg_match:
        return bg_match.group(1)
        
    # 2. Match oracle foreground/shadow processes: oracle<sid>
    fg_match = re.search(r'\boracle([a-zA-Z0-9_-]+)\b', args_str)
    if fg_match:
        # Avoid matching 'oracle' itself
        candidate = fg_match.group(1)
        if candidate and candidate.lower() not in ["d", "xe", "client"]:
            return candidate
        
    return "N/A"


def _parse_custom_ps_output(raw: str) -> list:
    processes = []
    lines = raw.strip().splitlines()
    for line in lines:
        s_ln = line.strip()
        if not s_ln:
            continue
        if s_ln.startswith("USER") or "PID" in s_ln or "COMMAND" in s_ln:
            continue
        parts = s_ln.split(None, 7)
        if len(parts) < 8:
            continue
        try:
            user_str = parts[0]
            pid_str  = parts[1]
            ppid_str = parts[2]
            cpu_pct  = round(float(parts[3]), 2)
            mem_pct  = round(float(parts[4]), 2)
            rss_kb   = parts[5]
            etime    = parts[6]
            args_str = parts[7]
            
            rss_mb = round(int(rss_kb) / 1024, 1) if rss_kb.isdigit() else 0.0
            
            processes.append({
                "pid":      pid_str,
                "ppid":     ppid_str,
                "username": user_str,
                "cpu":      cpu_pct,
                "mem":      rss_mb if rss_mb > 0 else mem_pct,
                "mem_pct":  mem_pct,
                "rss_mb":   rss_mb,
                "time":     etime,
                "name":     args_str,
                "args":     args_str,
                "sid":      extract_sid_from_command(args_str),
            })
        except Exception:
            continue
    return processes


def _fetch_live_cpu_delta_via_ssh(
    host: str,
    username: str,
    password: str = "",
    port: int = 22,
    timeout: int = 3,
    key_filename: str = None,
    sample_interval: int = 1,
) -> tuple:
    """
    Sample every process's cumulative CPU time TWICE, `sample_interval`
    seconds apart, and return ({pid: live_cpu_pct}, error).

    `ps`'s own %CPU column is a *lifetime* average — total CPU time used
    divided by total time since the process started. For a long-running
    Oracle background/shadow process (up for hours or days), that average
    decays toward ~0% even while it's actively busy right now. This
    mirrors what `top` does internally: two close-together samples of
    cumulative CPU time, diffed over the elapsed interval, give a genuine
    "live" reading instead of a diluted lifetime average.

    Both samples and the `sleep` happen in a single remote shell pipeline
    (one SSH round trip), so the interval isn't skewed by SSH latency.
    """
    import paramiko
    import os
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
    except paramiko.AuthenticationException:
        return {}, f"SSH authentication failed for {username}@{host}"
    except Exception as exc:
        return {}, f"SSH connection failed: {exc}"

    try:
        cmd = (
            "{ ps -eo pid,cputimes --no-headers 2>/dev/null; "
            "echo '---SPLIT---'; "
            f"sleep {sample_interval}; "
            "ps -eo pid,cputimes --no-headers 2>/dev/null; } | "
            "awk '/---SPLIT---/{m=2;next} m!=2{a[$1]=$2;next} ($1 in a){print $1, $2-a[$1]}'"
        )
        _, stdout, _ = client.exec_command(cmd, timeout=timeout + sample_interval + 10)
        raw = stdout.read().decode(errors="replace")

        cpu_map = {}
        for line in raw.strip().splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            pid_s, delta_s = parts
            try:
                delta_secs = float(delta_s)
            except ValueError:
                continue
            cpu_map[pid_s] = round(max(delta_secs, 0.0) * 100.0 / sample_interval, 1)

        return cpu_map, None
    except Exception as exc:
        return {}, f"SSH live-CPU sampling failed: {exc}"
    finally:
        client.close()


def _cached_fetch_live_cpu_delta(
    host: str,
    username: str,
    password: str = "",
    port: int = 22,
    timeout: int = 3,
    key_filename: str = None,
) -> tuple:
    """
    Cached wrapper around _fetch_live_cpu_delta_via_ssh, throttled to one
    SSH round trip per (host, username) every CACHE_TTL (30s) seconds and
    shared between the CPU-sorted and MEM-sorted process fetches below —
    both should show the exact same live %CPU for a given PID.
    """
    cache_key = (host, username, "cpu_delta")
    now = time.monotonic()

    with _cache_lock:
        cached = _proc_cache.get(cache_key)
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["procs"], cached["err"]

    cpu_map, err = _fetch_live_cpu_delta_via_ssh(
        host, username, password, port=port, timeout=timeout, key_filename=key_filename
    )

    with _cache_lock:
        _proc_cache[cache_key] = {"ts": now, "procs": cpu_map, "err": err}

    return cpu_map, err


def _cached_fetch_custom_os_processes_via_ssh(
    host: str,
    username: str,
    password: str = "",
    sort_by_cpu: bool = True,
    port: int = 22,
    timeout: int = 3,
    key_filename: str = None
) -> tuple:
    """
    Same as _fetch_custom_os_processes_via_ssh, but throttled to one SSH call
    per (host, username, sort_by_cpu) every CACHE_TTL (30s) seconds. The cache
    is process-wide (not per Streamlit session), so when the UI polls every
    30 seconds — from one or many browser sessions, and from both the
    per-host and per-DB callers below — it shares one SSH round trip instead
    of hammering the server.
    """
    cache_key = (host, username, sort_by_cpu)
    now = time.monotonic()

    with _cache_lock:
        cached = _proc_cache.get(cache_key)
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["procs"], cached["err"]

    procs, err = _fetch_custom_os_processes_via_ssh(
        host, username, password, sort_by_cpu, port, timeout, key_filename
    )

    # ps's own %CPU is a lifetime-decayed average (see
    # _fetch_live_cpu_delta_via_ssh above) — overlay a genuine live %CPU
    # for each PID so long-running-but-currently-busy Oracle processes
    # don't show a misleading 0.0%. Shared/cached across both sort orders.
    if procs:
        cpu_map, _cpu_err = _cached_fetch_live_cpu_delta(
            host, username, password, port=port, timeout=timeout, key_filename=key_filename
        )
        if cpu_map:
            for p in procs:
                live_val = cpu_map.get(str(p.get("pid", "")).strip())
                if live_val is not None:
                    p["cpu"] = live_val

    with _cache_lock:
        _proc_cache[cache_key] = {"ts": now, "procs": procs, "err": err}

    return procs, err


def _fetch_custom_os_processes_via_ssh(
    host: str,
    username: str,
    password: str = "",
    sort_by_cpu: bool = True,
    port: int = 22,
    timeout: int = 3,
    key_filename: str = None
) -> tuple:
    import paramiko
    import os
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
    except paramiko.AuthenticationException:
        return [], f"SSH authentication failed for {username}@{host}"
    except Exception as exc:
        return [], f"SSH connection failed: {exc}"

    try:
        sort_flag = "-pcpu" if sort_by_cpu else "-pmem"
        cmd = f"ps -eo user,pid,ppid,pcpu,pmem,rss,etime,args --sort={sort_flag} 2>/dev/null | awk 'NR == 1 || ($1 == \"oracle\" && ++count <= 200)'"
        _, stdout, _ = client.exec_command(cmd, timeout=10)
        raw = stdout.read().decode(errors="replace")
        procs = _parse_custom_ps_output(raw)
        return procs, None
    except Exception as e:
        return [], f"SSH command execution failed: {e}"
    finally:
        client.close()

def get_top_processes_for_db(
    host: str,
    username: str,
    password: str,
    sid: str,
    limit: int = 10,
    port: int = 22,
    key_filename: str = None,
) -> dict:
    """
    Fetch top CPU & Memory oracle processes for THIS specific DB only.

    Strategy:
      1. SSH  → Get all 'oracle' user processes from the OS (ps output)
      2. DB   → Query v$process for THIS DB to get its exact OS process PIDs (spid)
               v$process on a DB connection only returns PIDs for THAT instance —
               so filtering by this set correctly isolates this DB's processes.
      3. Filter SSH processes to only those PIDs in v$process for this DB.
         Enrich matched rows with SID + username from v$session.
      4. Fallback A: If DB connection fails, filter by SID name appearing in process cmd.
      5. Fallback B: If SSH fails entirely, query v$process + v$session directly (DB-only).

    `key_filename` should be the same SSH key used by the host-level fetch
    (get_top_processes_for_host) so both pages report identical %CPU/%MEM —
    both read from the same `ps` snapshot on the host (see CACHE_TTL above).
    """
    result = {"top_cpu": [], "top_mem": [], "error": None, "source": "none"}
    sid_lower = (sid or "").lower()

    # ── Step 1: SSH — get all oracle OS processes sorted by CPU and MEM ──────────
    if host and username:
        cpu_procs, err_cpu = _cached_fetch_custom_os_processes_via_ssh(host, username, password, sort_by_cpu=True,  port=port, key_filename=key_filename)
        mem_procs, err_mem = _cached_fetch_custom_os_processes_via_ssh(host, username, password, sort_by_cpu=False, port=port, key_filename=key_filename)

        if not (err_cpu and err_mem):
            # SSH succeeded — now filter to THIS DB's processes only

            # ── Step 2: Get this DB's process PIDs from v$process ─────────────
            db_spids     = set()   # PIDs owned by this DB instance
            pid_to_sess  = {}      # PID → {sid, username}
            db_conn_ok   = False

            conn = None
            try:
                from db_connection import get_api_connection
                conn, _db_err = get_api_connection(sid)
                if conn:
                    db_conn_ok = True
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT p.spid,
                               s.sid,
                               NVL(s.username, 'oracle') AS username
                        FROM   v$process p
                        LEFT JOIN v$session s ON s.paddr = p.addr
                        WHERE  p.spid IS NOT NULL
                    """)
                    for r in cur.fetchall():
                        spid = str(r[0]).strip()
                        db_spids.add(spid)
                        pid_to_sess[spid] = {
                            "sid":      str(r[1]).strip() if r[1] else "N/A",
                            "username": str(r[2] or "oracle").strip(),
                        }
                    cur.close()
            except Exception:
                db_conn_ok = False
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

            # ── Step 3: Filter and enrich ─────────────────────────────────────
            def _filter_and_enrich(procs):
                result_list = []
                for p in procs:
                    pid_k = str(p.get("pid", "")).strip()
                    args  = (p.get("args", "") or "").lower()
                    name  = (p.get("name", "") or "").lower()

                    if db_conn_ok and db_spids:
                        # Strict: only include PIDs that v$process says belong to this DB
                        if pid_k not in db_spids:
                            continue
                        # Copy before enriching — `procs` may be the exact same
                        # cached list/dicts returned to other callers (the home
                        # page, or another DB on this host) within the 30s SSH
                        # cache window; mutating in place would leak this DB's
                        # sid/username onto their view of the same processes.
                        p = dict(p)
                        sess = pid_to_sess.get(pid_k, {})
                        p["sid"]        = sess.get("sid", "N/A")
                        p["username"]   = sess.get("username", "oracle")
                        p["user_label"] = sess.get("username", "oracle")
                    else:
                        # Fallback: filter by SID name appearing in process command
                        if sid_lower not in args and sid_lower not in name:
                            continue

                    result_list.append(p)
                return result_list

            filtered_cpu = _filter_and_enrich(list(cpu_procs))
            filtered_mem = _filter_and_enrich(list(mem_procs))

            # If filtering produced nothing (e.g. no active sessions right now),
            # fall back to name-based matching so the table is never blank
            if not filtered_cpu and not filtered_mem and cpu_procs:
                def _name_filter(procs):
                    return [p for p in procs
                            if sid_lower in (p.get("args","") or "").lower()
                            or sid_lower in (p.get("name","") or "").lower()]
                filtered_cpu = _name_filter(cpu_procs)
                filtered_mem = _name_filter(mem_procs)

            result["top_cpu"] = sorted(filtered_cpu, key=lambda x: x.get("cpu", 0.0), reverse=True)[:limit]
            result["top_mem"] = sorted(filtered_mem, key=lambda x: x.get("mem", 0.0), reverse=True)[:limit]
            result["source"]  = "ssh"
            return result

    # ── Fallback B: SSH unavailable — query Oracle DB directly ───────────────────
    top_cpu, top_mem, db_err = _fetch_processes_via_db(sid, limit)
    if top_cpu or top_mem:
        result["top_cpu"] = top_cpu
        result["top_mem"] = top_mem
        result["source"]  = "v$session"
        return result

    result["error"] = f"No processes found ({db_err if db_err else '0 active sessions'})"
    return result


def get_top_processes_for_host(
    host: str,
    username: str,
    password: str = "",
    limit: int = 10,
    port: int = 22,
    key_filename: str = None
) -> dict:
    """
    Fetch top CPU and Memory processes for the entire host (no database SID filter).
    Runs the custom ps + awk command to query oracle user processes.
    """
    result = {"top_cpu": [], "top_mem": [], "error": None, "source": "none"}

    if not host or not username:
        result["error"] = "SSH credentials not configured"
        return result

    cpu_procs, err_cpu = _cached_fetch_custom_os_processes_via_ssh(host, username, password, sort_by_cpu=True, port=port, key_filename=key_filename)
    mem_procs, err_mem = _cached_fetch_custom_os_processes_via_ssh(host, username, password, sort_by_cpu=False, port=port, key_filename=key_filename)

    if err_cpu and err_mem:
        result["error"] = f"SSH unavailable - CPU: {err_cpu}, MEM: {err_mem}"
        return result

    result["top_cpu"] = sorted(cpu_procs, key=lambda x: x.get("cpu", 0.0), reverse=True)[:limit]
    result["top_mem"] = sorted(mem_procs, key=lambda x: x.get("mem", 0.0), reverse=True)[:limit]
    result["source"]  = "ssh"
    return result


def invalidate_cache(host: str = None):
    """Force-clear the process cache for a specific host (or all hosts)."""
    with _cache_lock:
        if host:
            for key in [k for k in _proc_cache if (isinstance(k, tuple) and k[0] == host) or k == host]:
                _proc_cache.pop(key, None)
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
    result["top_cpu"] = sorted(all_cpu, key=lambda x: x.get("cpu", 0.0), reverse=True)[:limit]
    result["top_mem"] = sorted(all_mem, key=lambda x: x.get("mem", 0.0), reverse=True)[:limit]
    result["source"]  = "v$session"
    return result
