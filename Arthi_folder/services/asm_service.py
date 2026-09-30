import base64
import logging
import os
import re
import sys
import threading
import time

from services.config_service import get_all_db_configs
from services.ssh_service import get_grid_ssh_connection

# Dedicated Grid/ASM log file, in the same "logs" folder app.py already uses
# (sibling to the exe when frozen, else the repo root) - every step of the
# SSH -> discovery -> sqlplus / as sysasm -> v$asm_diskgroup pipeline is
# logged here in full, in addition to the main dashboard_monitor.log/console
# (propagate=True), so ASM/Grid troubleshooting never has to be grepped out
# of the general application log. Never logs passwords, private keys, or
# other secrets - only hostnames, OS usernames/groups, paths, SQL*Plus
# output (which itself never contains a password in this OS-authenticated
# flow), and counts.
def _asm_log_path():
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "asm_grid.log")


_asm_logger = logging.getLogger("asm_grid")
_asm_logger.setLevel(logging.DEBUG)
# Every ASM sweep (every 60s cache cycle) logs dozens of [ASM] lines (PID,
# SID, ORACLE_HOME, preflight values, per-diskgroup rows, etc.) - full
# detail always lands in the dedicated logs/asm_grid.log file above.
# propagate=False keeps that same full detail out of the shared root
# logger's console/dashboard_monitor.log handlers, so it no longer repeats
# there on every cycle; nothing is lost, it just isn't duplicated to
# console anymore.
_asm_logger.propagate = False
if not any(getattr(h, "_is_asm_grid_handler", False) for h in _asm_logger.handlers):
    try:
        _asm_file_handler = logging.FileHandler(_asm_log_path(), encoding="utf-8")
        _asm_file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s'))
        _asm_file_handler.setLevel(logging.DEBUG)
        _asm_file_handler._is_asm_grid_handler = True
        _asm_logger.addHandler(_asm_file_handler)
    except Exception as e:
        logging.error(f"[ASM] Failed to initialize dedicated logs/asm_grid.log file handler: {e}")

# ASM disk groups are discovered directly on the Production DB server(s)
# already present in the uploaded DB configuration (databases.csv) - there is
# no separate Grid/ASM configuration file or upload. See
# _get_production_targets() below for how the host + Grid OS user are derived.
#
# Grid/ASM required flow (deliberately different from every other widget):
#   Windows EXE -> SSH as OS user "opc" (ssh_service.get_grid_ssh_connection(),
#   its own isolated connection pool) -> "sudo su - <grid_user>" on that
#   session (per remote command, see _build_grid_remote_command() below) ->
#   grid OS user -> "sqlplus -L / as sysasm" -> v$asm_diskgroup.
# grid_user here is ONLY the OS user we switch into after the opc SSH login -
# it is never used to SSH in directly, and os_user/oracle (the "oracle" SSH
# pools used by Mount Points/Oracle Processes/etc via
# ssh_service.get_ssh_connection()/get_ssh_connection_via_key()) is never
# read or used as a fallback here. The one and only fallback when the DB
# configuration's optional grid_user column is blank is _GRID_DEFAULT_OS_USER
# below. This module is otherwise entirely separate from
# db_service.py/dr_service.py's Oracle Database connections: a failure here
# can never mark a database DOWN, close an Oracle pool, or touch those SSH
# pools, and no DB/Oracle credentials of any kind are used for ASM.
_GRID_DEFAULT_OS_USER = "grid"

# OS usernames are a closed, simple charset - anything else in the DB
# configuration's grid_user column is a config error, not a real username,
# and is rejected up front rather than risking shell injection into the
# `sudo su - <grid_user> -c "..."` remote command below.
_VALID_OS_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")

_ASM_LOCK = threading.Lock()
_single_host_asm_cache = {}          # host -> {"timestamp": ..., "data": {...}}
_all_asm_cache = {"timestamp": 0, "data": None}

_CACHE_TTL_SECONDS = 60


def clear_asm_cache():
    with _ASM_LOCK:
        _single_host_asm_cache.clear()
        _all_asm_cache["timestamp"] = 0
        _all_asm_cache["data"] = None
        _asm_logger.info("[ASM Cache] Cleared all ASM disk group caches.")


def _get_production_targets():
    """
    Grid/ASM has no configuration of its own: it is discovered directly on
    the Production DB server(s) from the uploaded DB configuration, using
    that row's grid_user (falling back to _GRID_DEFAULT_OS_USER, never to
    os_user/oracle). Returns one {"host", "grid_user", "label"} dict per
    unique Production host (multiple DB rows can share a host; grid_user
    from the first row seen for that host wins). ASM is server-level infra,
    so the label is always the host/IP itself - never a DB service_name -
    since databases.csv has no dedicated server-name field.
    """
    targets = {}
    for cfg in get_all_db_configs():
        host = str(cfg.get("host") or "").strip()
        if not host or host in targets:
            continue
        targets[host] = {
            "host": host,
            "grid_user": str(cfg.get("grid_user") or "").strip() or _GRID_DEFAULT_OS_USER,
            "label": host,
        }
    return list(targets.values())


def _unavailable_result(host, message):
    return {
        "status": "error",
        "host": host,
        "message": message or "Not Available",
        "diskgroups": []
    }


def _not_mounted_result(host, instance_name, instance_status):
    """
    ASM is genuinely up (the instance state check itself succeeded) but no
    disk group is mounted yet - a normal, valid ASM state, not a failure.
    Distinct "status" value so the UI never renders this as "Not Available"/
    an SSH or connection failure; the frontend must branch on this before
    its generic status != 'success' error path.
    """
    return {
        "status": "not_mounted",
        "host": host,
        "message": "ASM Instance: AVAILABLE - ASM Disk Groups: NOT MOUNTED",
        "instance_name": instance_name or None,
        "instance_status": instance_status or None,
        "diskgroups": []
    }


# Performance tuning for the combined SQL*Plus session below - configurable
# via env vars rather than hard-coded, per the optimization requirement.
# _ASM_SQLPLUS_TIMEOUT_SECONDS is the `timeout` budget for ONE sqlplus
# invocation (it now covers both V$INSTANCE and v$asm_diskgroup in a single
# process, so it's a bit larger than the old single-query budget to still
# comfortably cover a legitimate slow Production query). _KILL_GRACE is the
# `-k` grace period given to a SIGTERM'd sqlplus before SIGKILL.
_ASM_SQLPLUS_TIMEOUT_SECONDS = int(os.environ.get("ASM_SQLPLUS_TIMEOUT_SECONDS", "45"))
_ASM_SQLPLUS_KILL_GRACE_SECONDS = int(os.environ.get("ASM_SQLPLUS_KILL_GRACE_SECONDS", "5"))
# Worst realistic case is two sequential sqlplus invocations (the SID-case
# retry) each allowed up to TIMEOUT+KILL_GRACE seconds - the SSH channel's
# own read timeout must comfortably exceed that so it never cuts off a
# legitimate slow-but-completing remote script before the retry can finish.
_ASM_SSH_EXEC_TIMEOUT_SECONDS = int(os.environ.get(
    "ASM_SSH_EXEC_TIMEOUT_SECONDS",
    str(2 * (_ASM_SQLPLUS_TIMEOUT_SECONDS + _ASM_SQLPLUS_KILL_GRACE_SECONDS) + 15)
))

# SQL is the exact query requested, pipe-delimited into one line per disk
# group so it survives a plain SSH exec_command/sqlplus round trip without
# needing any column-width parsing. Text is UNCHANGED from before - only how
# it gets executed (combined into one sqlplus session with the instance
# check below) has changed, per the performance optimization.
_ASM_DISKGROUP_SQL_FRAGMENT = """PROMPT ===ASM_DISKGROUPS_START===
SELECT name || '|' || total_mb || '|' || free_mb || '|' ||
       ROUND((total_mb-free_mb)/1024,2) || '|' ||
       ROUND(free_mb/1024,2) || '|' ||
       ROUND((total_mb-free_mb)*100/total_mb,2)
FROM v$asm_diskgroup
ORDER BY name;
PROMPT ===ASM_DISKGROUPS_END===
"""

# ASM instance state check - also unchanged text, now run as the FIRST
# statement of the same combined session instead of its own separate
# sqlplus process. An ASM instance can be up (STARTED/NOMOUNT) without any
# disk group mounted - that is a normal, valid ASM state, not a failure.
_ASM_INSTANCE_SQL_FRAGMENT = """PROMPT ===ASM_INSTANCE_ROW_START===
SELECT INSTANCE_NAME || '|' || STATUS FROM V$INSTANCE;
PROMPT ===ASM_INSTANCE_ROW_END===
"""

# One shared header (SET/WHENEVER) + both fragments + a single EXIT: this is
# the entire combined session's SQL text. WHENEVER SQLERROR EXIT SQL.SQLCODE
# means that if the V$INSTANCE statement itself errors (e.g. ORA-01034), the
# session exits immediately and the diskgroup SELECT below it never runs in
# that same invocation - so a genuinely failed connect still never touches
# v$asm_diskgroup, exactly as before, just without a second sqlplus process
# for the normal (successful) case.
_ASM_COMBINED_SQL_BODY = (
    """SET PAGESIZE 0 LINESIZE 32767 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON TAB OFF TERMOUT ON
SET COLSEP '|'
WHENEVER SQLERROR EXIT SQL.SQLCODE
WHENEVER OSERROR EXIT FAILURE
"""
    + _ASM_INSTANCE_SQL_FRAGMENT
    + _ASM_DISKGROUP_SQL_FRAGMENT
    + "EXIT\n"
)

# Discovers the Grid environment live on the server rather than assuming a
# fixed Grid Home path: finds the running ASM instance's own pmon process,
# reads its ORACLE_HOME from /proc/<pid>/environ, and falls back to /etc/oratab
# (the "+ASM" entry) if that process isn't found/readable. Mirrors the
# discovery pattern already used for the Standby-via-Production sqlplus path
# in dr_service.py (env/pmon/oratab fallback chain), so ASM never needs a
# hard-coded Grid Home for any particular server.
_ASM_DISCOVERY_AND_QUERY_CMD = """
ORACLE_HOME=""
ASM_SID=""
ASM_SID_FROM_ENV=""

echo "===GRID_SU_OK==="
echo "===CONNECTED_AS=$(id -un 2>/dev/null)==="
echo "===GRID_WHOAMI=$(whoami 2>/dev/null)==="
echo "===GROUPS=$(id -Gn 2>/dev/null)==="

ASM_PS_LINE=$(ps -eo pid,comm | grep '[a]sm_pmon_' | head -1)
if [ -n "$ASM_PS_LINE" ]; then
    ASM_PID=$(echo "$ASM_PS_LINE" | awk '{print $1}')
    ASM_PROC_NAME=$(echo "$ASM_PS_LINE" | awk '{print $2}')
    # This is a DIAGNOSTIC guess only (ps process names are always
    # lowercase, e.g. "asm_pmon_+asm1", even though the real SID is
    # "+ASM1") - it is NEVER used as the final ORACLE_SID. The authoritative
    # value is read from that same process's own /proc/<pid>/environ below,
    # exactly like ORACLE_HOME already is.
    ASM_SID=$(echo "$ASM_PROC_NAME" | sed 's/^asm_pmon_//')
    echo "===ASM_PID=$ASM_PID==="
    echo "===ASM_SID_FROM_PROCESS_NAME=$ASM_SID==="
    ASM_ENVIRON=$(cat /proc/$ASM_PID/environ 2>/dev/null | tr '\\0' '\\n')
    CAND_HOME=$(echo "$ASM_ENVIRON" | grep -a '^ORACLE_HOME=' | cut -d= -f2)
    CAND_SID=$(echo "$ASM_ENVIRON" | grep -a '^ORACLE_SID=' | cut -d= -f2)
    if [ -n "$CAND_SID" ]; then
        ASM_SID_FROM_ENV="$CAND_SID"
        echo "===ASM_SID_SRC=asm_pmon process environ (pid=$ASM_PID)==="
    fi
    if [ -n "$CAND_HOME" ] && [ -x "$CAND_HOME/bin/sqlplus" ]; then
        ORACLE_HOME="$CAND_HOME"
        echo "===ORACLE_HOME_SRC=asm_pmon process (pid=$ASM_PID)==="
    fi
else
    echo "===ASM_PMON_NOT_FOUND==="
fi

if [ -z "$ORACLE_HOME" ] && [ -f /etc/oratab ]; then
    OT_LINE=$(grep -a -i '^\\+ASM' /etc/oratab | head -1)
    if [ -n "$OT_LINE" ]; then
        CAND=$(echo "$OT_LINE" | cut -d: -f2)
        if [ -z "$ASM_SID_FROM_ENV" ] && [ -z "$ASM_SID" ]; then
            ASM_SID=$(echo "$OT_LINE" | cut -d: -f1)
        fi
        if [ -n "$CAND" ] && [ -x "$CAND/bin/sqlplus" ]; then
            ORACLE_HOME="$CAND"
            echo "===ORACLE_HOME_SRC=/etc/oratab==="
        fi
    fi
fi

# Authoritative SID: the exact value the running ASM instance was actually
# started with, from its own /proc/<pid>/environ (correct case guaranteed).
# Only fall back to the ps-parsed process-name guess or the /etc/oratab SID
# field when the process environment wasn't readable/available at all.
if [ -n "$ASM_SID_FROM_ENV" ]; then
    RESOLVED_SID="$ASM_SID_FROM_ENV"
elif [ -n "$ASM_SID" ]; then
    RESOLVED_SID="$ASM_SID"
else
    RESOLVED_SID=""
fi

if [ -n "$ORACLE_HOME" ]; then
    export ORACLE_HOME
    export ORACLE_SID="$RESOLVED_SID"
    export PATH="$ORACLE_HOME/bin:$PATH"
    export LD_LIBRARY_PATH="$ORACLE_HOME/lib:$LD_LIBRARY_PATH"
    echo "===ORACLE_HOME_RESOLVED=$ORACLE_HOME==="
    echo "===ASM_SID_RESOLVED=$RESOLVED_SID==="
else
    echo "===ORACLE_HOME_NOT_FOUND==="
fi

if [ -n "$ORACLE_HOME" ] && [ -x "$ORACLE_HOME/bin/sqlplus" ]; then
    echo "===SQLPLUS_AVAILABLE==="
    echo "===SQLPLUS_PATH=$ORACLE_HOME/bin/sqlplus==="

    # Re-assert the exact same three variables one more time, immediately
    # before sqlplus runs, in THIS shell/process - not relying on the
    # earlier export still being in scope, and not relying on anything
    # profile/rc-file-provided. Then print them back (plus whoami and
    # `command -v sqlplus`) so the logged values are what this specific
    # sqlplus invocation actually saw, not just what discovery computed.
    export ORACLE_SID="$ORACLE_SID"
    export ORACLE_HOME="$ORACLE_HOME"
    export PATH="$ORACLE_HOME/bin:$PATH"
    echo "===PREFLIGHT_WHOAMI=$(whoami 2>/dev/null)==="
    echo "===PREFLIGHT_ORACLE_SID=$ORACLE_SID==="
    echo "===PREFLIGHT_ORACLE_HOME=$ORACLE_HOME==="
    echo "===PREFLIGHT_SQLPLUS_WHICH=$(command -v sqlplus 2>/dev/null)==="

    # Performance optimization: V$INSTANCE and v$asm_diskgroup now run in
    # ONE combined sqlplus session/process (one bequeath connect) instead of
    # two separate sqlplus invocations. WHENEVER SQLERROR EXIT SQL.SQLCODE
    # means that if V$INSTANCE itself errors, the whole session exits right
    # there and the diskgroup SELECT never runs in that invocation - so a
    # genuinely failed connect still never touches v$asm_diskgroup, exactly
    # like the previous two-process design, just without the second process
    # in the normal (successful) case.
    echo "===ASM_COMBINED_QUERY_BEGIN==="
    ASM_COMBINED_OUT=$(timeout -k __ASM_KILL_GRACE__ __ASM_TIMEOUT__ sqlplus -s -L / as sysasm <<'SQLEOF_COMBINED'
""" + _ASM_COMBINED_SQL_BODY + """SQLEOF_COMBINED
)
    ASM_COMBINED_EXIT=$?

    # Safety fallback ONLY for this one narrow case: the resolved SID is
    # lowercase and the connect failed with ORA-01034. Retry exactly ONCE
    # with the uppercase version of that same SID (never repeatedly, and
    # never for any other error) - this is the ONLY scenario allowed to
    # start a second sqlplus process; the normal healthy path uses exactly
    # one. If the resolved SID was already uppercase (the normal
    # /proc/<pid>/environ case), this block is a no-op.
    if [ "$ASM_COMBINED_EXIT" != "0" ] && echo "$ASM_COMBINED_OUT" | grep -q 'ORA-01034'; then
        UPPER_SID=$(echo "$ORACLE_SID" | tr '[:lower:]' '[:upper:]')
        if [ -n "$UPPER_SID" ] && [ "$UPPER_SID" != "$ORACLE_SID" ]; then
            echo "===ASM_SID_RETRY_TRIGGER=$ORACLE_SID==="
            export ORACLE_SID="$UPPER_SID"
            echo "===ASM_SID_RETRY_NEW_SID=$ORACLE_SID==="
            ASM_COMBINED_OUT=$(timeout -k __ASM_KILL_GRACE__ __ASM_TIMEOUT__ sqlplus -s -L / as sysasm <<'SQLEOF_COMBINED_RETRY'
""" + _ASM_COMBINED_SQL_BODY + """SQLEOF_COMBINED_RETRY
)
            ASM_COMBINED_EXIT=$?
        fi
    fi

    echo "$ASM_COMBINED_OUT"
    echo "===ASM_COMBINED_EXIT_CODE=$ASM_COMBINED_EXIT==="
    echo "===ASM_SID_FINAL=$ORACLE_SID==="

    # Only parse a real instance name/status out of successful output - a
    # failed connect never gets to a real result row, and the echoed SQL
    # text (or ORA- error block) must never be mistaken for one. Exit 0 on
    # the combined session guarantees BOTH statements ran without a SQL
    # error (WHENEVER SQLERROR EXIT would have fired otherwise), so the
    # diskgroup markers are safe to trust whenever this block ran too.
    if [ "$ASM_COMBINED_EXIT" = "0" ]; then
        ASM_INSTANCE_ROW=$(echo "$ASM_COMBINED_OUT" | sed -n '/===ASM_INSTANCE_ROW_START===/,/===ASM_INSTANCE_ROW_END===/p' | grep -v '===ASM_INSTANCE_ROW_' | grep -v '^[[:space:]]*$' | head -1)
        ASM_INSTANCE_NAME=$(echo "$ASM_INSTANCE_ROW" | cut -d'|' -f1 | tr -d '[:space:]')
        ASM_INSTANCE_STATUS=$(echo "$ASM_INSTANCE_ROW" | cut -d'|' -f2 | tr -d '[:space:]')
        echo "===ASM_INSTANCE_NAME=$ASM_INSTANCE_NAME==="
        echo "===ASM_INSTANCE_STATUS=$ASM_INSTANCE_STATUS==="
    fi
else
    echo "===SQLPLUS_NOT_AVAILABLE==="
fi
"""

_ASM_DISCOVERY_AND_QUERY_CMD = (
    _ASM_DISCOVERY_AND_QUERY_CMD
    .replace("__ASM_KILL_GRACE__", str(_ASM_SQLPLUS_KILL_GRACE_SECONDS))
    .replace("__ASM_TIMEOUT__", str(_ASM_SQLPLUS_TIMEOUT_SECONDS))
)


def _extract_ora_error(text):
    """Pulls the first ORA-XXXXX: ... line out of sqlplus output, if any, for surfacing to the UI."""
    match = re.search(r"ORA-\d{4,5}:[^\r\n]*", text or "")
    return match.group(0).strip() if match else None


def _parse_diskgroups(out):
    try:
        block = out.split("===ASM_DISKGROUPS_START===", 1)[1].split("===ASM_DISKGROUPS_END===", 1)[0]
    except IndexError:
        return []

    diskgroups = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        name, total_mb, free_mb, used_gb, free_gb, used_pct = parts[:6]

        def _num(v, cast):
            v = (v or "").strip()
            try:
                return cast(float(v)) if cast is int else cast(v)
            except ValueError:
                return None

        diskgroups.append({
            "name": name.strip(),
            "total_mb": _num(total_mb, int),
            "free_mb": _num(free_mb, int),
            "used_gb": _num(used_gb, float),
            "free_gb": _num(free_gb, float),
            "used_pct": _num(used_pct, float),
        })
    return diskgroups


def _build_grid_su_command(script, grid_user):
    """
    Wraps `script` (the ASM discovery+query bash script) so it actually runs
    as the Grid OS user (default "grid") once the opc SSH session from
    get_grid_ssh_connection() is open, via:

        sudo -n su - <grid_user> -c "echo <base64> | base64 -d | bash"

    `su - <grid_user>` starts a real login shell for that user (so its own
    profile-set ORACLE_HOME/PATH/etc are picked up, per the login-shell
    environment requirement) before running the piped-in script. `sudo -n`
    (non-interactive) makes a missing/failed sudo grant fail immediately with
    a clear error instead of hanging on a password prompt over the
    non-interactive SSH exec channel used here. The script is base64-encoded
    so its heredoc/quoting never has to survive being embedded inside the
    su -c "..." string.
    """
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return f'sudo -n su - {grid_user} -c "echo {b64} | base64 -d | bash"'


def _classify_grid_su_failure(err_out, exit_code):
    """
    Best-effort classification of a `sudo -n su - <grid_user>` failure (the
    ===GRID_SU_OK=== marker never appeared in stdout) into one of the
    distinct failure reasons this module must surface: sudo denied/misconfigured
    vs. the grid OS user/switch itself failing. Falls back to a generic but
    still specific message (exit code + raw stderr) when the text doesn't
    match a known pattern, so nothing is ever hidden behind "Not Available".
    """
    text = (err_out or "").lower()
    stderr_snippet = (err_out or "").strip() or "(no error output)"

    if any(p in text for p in ("password is required", "no tty present", "not in the sudoers", "sudo:")) and "su:" not in text:
        return f"sudo to grid failed: {stderr_snippet}"
    if any(p in text for p in ("su:", "no such user", "does not exist", "unknown user")):
        return f"grid user switch failed: {stderr_snippet}"
    return f"Failed to switch to grid OS user (exit_code={exit_code}): {stderr_snippet}"


def get_asm_diskgroups_raw(host, grid_user=None):
    """
    Runs the full opc SSH -> sudo su - <grid_user> -> ASM environment
    discovery -> `sqlplus / as sysasm` -> V$ASM_DISKGROUP pipeline for one
    server (identified by host). The host comes from that server's
    Production DB entry in the uploaded DB configuration; `grid_user` is
    that same row's grid_user (or the single "grid" default) - the OS user
    switched into via sudo after the opc SSH login, deliberately NOT
    os_user/oracle, so ASM commands always run as a different OS user than
    the existing Oracle SSH pools. `grid_user` can be passed directly (the
    all-servers fan-out already knows it); if omitted, it's looked
    up/defaulted from the current DB configuration.

    Returns a dict with one of two statuses:
      - "error": no Production config for this host, or Production is
        configured but SSH, environment discovery, or the ASM query failed.
        The frontend shows the specific "message" for this - it never
        implies the Oracle *database* on this host is down.
      - "success": disk groups retrieved.

    Never touches db_service's Oracle connection pools or ssh_service's
    "oracle" SSH pools (get_ssh_connection()/get_ssh_connection_via_key()) -
    failures here are fully isolated, and get_grid_ssh_connection() keeps its
    own separate connection pool so this can never reuse or invalidate an
    "oracle" SSH session.

    Every failure branch below returns the actual, specific reason as
    "message" (never a generic "Not Available" string) so the Home Page card
    can surface what really went wrong - e.g. the Grid OS user's SSH error,
    or an ORA- error - instead of hiding it. Messages are built only from
    SSH/SQL*Plus error text (host/user/ORA- codes), never from key paths,
    passphrases, or credentials, so nothing sensitive leaks to the frontend.
    """
    start_time = time.time()
    host = str(host or "").strip()

    _asm_logger.info(f"[ASM] ===== BEGIN ASM check: server={host or '(blank)'} =====")

    if not host:
        _asm_logger.error("[ASM] Server host is missing/blank - aborting.")
        return _unavailable_result(host, "Server host is missing.")

    if grid_user is None:
        db_cfg = next(
            (c for c in get_all_db_configs() if str(c.get("host") or "").strip() == host),
            None
        )
        if not db_cfg:
            _asm_logger.error(f"[ASM] No Production database configuration found for server={host}.")
            return _unavailable_result(host, "No Production database is configured for this server.")
        grid_user = str(db_cfg.get("grid_user") or "").strip() or _GRID_DEFAULT_OS_USER

    if not _VALID_OS_USERNAME_RE.match(grid_user):
        _asm_logger.error(f"[ASM] Configured grid_user '{grid_user}' for server={host} is not a valid OS username - aborting.")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, f"Configured Grid OS user '{grid_user}' is not a valid OS username.")

    _asm_logger.info(f"[ASM] Server={host}")
    _asm_logger.info("[ASM] SSH user=opc")

    ssh_client, ssh_err = get_grid_ssh_connection(host, timeout=10, banner_timeout=10)
    if not ssh_client:
        _asm_logger.error(f"[ASM] SSH connection to {host} as opc failed: {ssh_err}")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, f"SSH as opc failed: {ssh_err or 'unknown error'}")

    _asm_logger.info(f"[ASM] Connected to Production server (server={host}, user=opc)")
    _asm_logger.info(f"[ASM] Switching to grid using sudo -n (target user={grid_user})")

    remote_cmd = _build_grid_su_command(_ASM_DISCOVERY_AND_QUERY_CMD, grid_user)
    _asm_logger.debug(f"[ASM] Remote su+discovery+query command built for {host} (OS user '{grid_user}') - see raw STDOUT/STDERR below for what actually ran.")

    try:
        _, stdout, stderr = ssh_client.exec_command(remote_cmd, timeout=_ASM_SSH_EXEC_TIMEOUT_SECONDS)
        out = stdout.read().decode("utf-8", errors="ignore")
        err_out = stderr.read().decode("utf-8", errors="ignore")
        exit_status = stdout.channel.recv_exit_status()
    except Exception as e:
        _asm_logger.error(f"[ASM] SSH command execution to {host} failed: {e}")
        try:
            ssh_client.close()
        except Exception:
            pass
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, f"SSH command execution failed: {e}")

    try:
        ssh_client.close()
    except Exception:
        pass

    _asm_logger.debug(f"[ASM] Raw remote STDOUT (server={host}):\n{out}")
    _asm_logger.debug(f"[ASM] Raw remote STDERR (server={host}):\n{err_out}")

    if "===GRID_SU_OK===" not in out:
        detail = _classify_grid_su_failure(err_out, exit_status)
        _asm_logger.error(f"[ASM] opc -> sudo -n su - {grid_user} failed on {host} (exit_code={exit_status}): {detail}")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, f"Grid privilege/sudo failure: {detail}")

    # Explicitly verify the switch actually landed on the expected Grid OS
    # user via `whoami` (not just trusting that GRID_SU_OK printing means
    # the switch is genuine) - a mismatch is treated the same as a sudo/su
    # failure, never silently accepted.
    grid_whoami_match = re.search(r"===GRID_WHOAMI=(.*?)===", out)
    grid_whoami = grid_whoami_match.group(1).strip() if grid_whoami_match else ""
    if grid_whoami != grid_user:
        detail = f"Grid privilege/sudo failure: expected to be running as '{grid_user}' after sudo -n su - {grid_user}, but whoami reports '{grid_whoami or 'unknown'}'."
        _asm_logger.error(f"[ASM] {detail} (server={host})")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, detail)

    _asm_logger.info(f"[ASM] Connected as {grid_user} (server={host})")

    connected_as_match = re.search(r"===CONNECTED_AS=(.*?)===", out)
    groups_match = re.search(r"===GROUPS=(.*?)===", out)
    _asm_logger.info(
        f"[ASM] Connected as OS user={connected_as_match.group(1).strip() if connected_as_match else 'unknown'} "
        f"groups=[{groups_match.group(1).strip() if groups_match else 'unknown'}] (server={host})"
    )

    if "===ASM_PMON_NOT_FOUND===" in out and "===ORACLE_HOME_NOT_FOUND===" in out:
        _asm_logger.error(f"[ASM] No running ASM instance found on {host} (no asm_pmon process, no /etc/oratab '+ASM' entry).")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(
            host,
            "No running ASM instance found (no asm_pmon_* process, and no '+ASM' entry in /etc/oratab)."
        )

    if "===ORACLE_HOME_NOT_FOUND===" in out:
        _asm_logger.error(f"[ASM] Could not locate a usable Grid Home on {host}.")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(
            host,
            "ASM instance is running but ORACLE_HOME could not be determined "
            "(checked /proc/<pid>/environ and /etc/oratab)."
        )

    if "===SQLPLUS_NOT_AVAILABLE===" in out:
        _asm_logger.error(f"[ASM] sqlplus not found under discovered ORACLE_HOME on {host}.")
        home_match = re.search(r"===ORACLE_HOME_RESOLVED=(.*?)===", out)
        home_hint = f" (ORACLE_HOME={home_match.group(1).strip()})" if home_match else ""
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, f"sqlplus executable not found under ORACLE_HOME/bin{home_hint}.")

    home_match = re.search(r"===ORACLE_HOME_RESOLVED=(.*?)===", out)
    sid_match = re.search(r"===ASM_SID_RESOLVED=(.*?)===", out)
    sqlplus_path_match = re.search(r"===SQLPLUS_PATH=(.*?)===", out)
    pid_match = re.search(r"===ASM_PID=(.*?)===", out)
    home_src_match = re.search(r"===ORACLE_HOME_SRC=(.*?)===", out)
    # ASM_SID_SRC only appears when the SID came from the process's own
    # /proc/<pid>/environ (the authoritative source, correct case
    # guaranteed) - absent means it fell back to the ps process-name guess
    # or /etc/oratab, which are logged as-is via ASM_SID_RESOLVED above.
    sid_src_match = re.search(r"===ASM_SID_SRC=(.*?)===", out)
    _asm_logger.info(f"[ASM] ASM PID={pid_match.group(1).strip() if pid_match else 'unknown'}")
    _asm_logger.info(f"[ASM] ASM SID={sid_match.group(1).strip() if sid_match else 'unknown'}")
    _asm_logger.info(f"[ASM] ORACLE_HOME={home_match.group(1).strip() if home_match else 'unknown'} (source={home_src_match.group(1).strip() if home_src_match else 'unknown'})")
    _asm_logger.info(
        f"[ASM] ASM SID from process environment={sid_match.group(1).strip() if sid_match else 'unknown'} "
        f"(source={sid_src_match.group(1).strip() if sid_src_match else 'ps process name / oratab fallback'})"
    )
    _asm_logger.info(f"[ASM] SQLPLUS_PATH={sqlplus_path_match.group(1).strip() if sqlplus_path_match else 'unknown'}")

    # Preflight values are read back from the SAME shell/process that is
    # about to exec sqlplus (echoed immediately beforehand, in the remote
    # script) - this is what that process actually saw, not just what our
    # Python-side discovery computed earlier, so it catches any discrepancy
    # introduced between discovery and the sqlplus invocation itself.
    preflight_whoami_match = re.search(r"===PREFLIGHT_WHOAMI=(.*?)===", out)
    preflight_sid_match = re.search(r"===PREFLIGHT_ORACLE_SID=(.*?)===", out)
    preflight_home_match = re.search(r"===PREFLIGHT_ORACLE_HOME=(.*?)===", out)
    preflight_which_match = re.search(r"===PREFLIGHT_SQLPLUS_WHICH=(.*?)===", out)
    _asm_logger.info(f"[ASM] Running as {preflight_whoami_match.group(1).strip() if preflight_whoami_match else 'unknown'} (server={host})")
    _asm_logger.info(f"[ASM] ORACLE_SID={preflight_sid_match.group(1).strip() if preflight_sid_match else 'unknown'} (server={host})")
    _asm_logger.info(f"[ASM] ORACLE_HOME={preflight_home_match.group(1).strip() if preflight_home_match else 'unknown'} (server={host})")
    _asm_logger.info(f"[ASM] SQLPLUS_PATH={preflight_which_match.group(1).strip() if preflight_which_match else 'unknown'} (server={host})")
    _asm_logger.info(f"[ASM] Starting combined SQL*Plus session (server={host})")
    _asm_logger.info(f"[ASM] Executing V$INSTANCE validation (server={host})")

    # ------------------------------------------------------------------
    # Performance optimization: V$INSTANCE and v$asm_diskgroup are executed
    # in ONE combined sqlplus session (see _ASM_COMBINED_SQL_BODY) instead
    # of two separate sqlplus processes - one bequeath connect covers both
    # queries for the normal (successful) case. WHENEVER SQLERROR EXIT
    # SQL.SQLCODE in that script means: if V$INSTANCE itself fails, the
    # WHOLE session exits immediately with a nonzero code and the diskgroup
    # SELECT never ran in that invocation - so a failed connect still never
    # touches v$asm_diskgroup, same guarantee as before, just in one process.
    #
    # The remote script already retried once, internally, with an
    # uppercased SID if the first attempt hit ORA-01034 (see
    # ASM_SID_RETRY_TRIGGER/ASM_SID_RETRY_NEW_SID below) - that retry is a
    # second full combined session, and is the ONLY scenario where a normal
    # healthy check ever spawns more than one sqlplus process. What we
    # parse here (exit code, output, ASM_SID_FINAL) is always that retry's
    # result when a retry happened, never the original failed attempt.
    # ------------------------------------------------------------------
    retry_trigger_match = re.search(r"===ASM_SID_RETRY_TRIGGER=(.*?)===", out)
    retry_new_sid_match = re.search(r"===ASM_SID_RETRY_NEW_SID=(.*?)===", out)
    if retry_trigger_match:
        _asm_logger.info(f"[ASM] ORA-01034 detected with lowercase SID={retry_trigger_match.group(1).strip()} (server={host})")
    if retry_new_sid_match:
        _asm_logger.info(f"[ASM] Retrying once with uppercase SID={retry_new_sid_match.group(1).strip()} (server={host})")

    final_sid_match = re.search(r"===ASM_SID_FINAL=(.*?)===", out)
    combined_exit_match = re.search(r"===ASM_COMBINED_EXIT_CODE=(-?\d+)===", out)
    combined_exit_code = int(combined_exit_match.group(1)) if combined_exit_match else None
    _asm_logger.info(f"[ASM] Combined SQLPlus exit code={combined_exit_code} (server={host})")

    # Everything up to the combined session's own exit-code marker - the
    # ORA- scope for this whole (single) sqlplus invocation.
    combined_section = out.split("===ASM_COMBINED_EXIT_CODE=", 1)[0]

    if combined_exit_code != 0 or "ORA-" in combined_section:
        # A failed connect never produces a real V$INSTANCE row, and
        # WHENEVER SQLERROR EXIT means the diskgroup SELECT never ran
        # either - the remote script emits neither ASM_INSTANCE_NAME/
        # ASM_INSTANCE_STATUS nor ASM_DISKGROUPS_START/END in this case, so
        # there is nothing here to (mis)parse as a name or diskgroup row.
        _asm_logger.info(f"[ASM] ASM instance state check result=failure (server={host})")
        _asm_logger.error(
            f"[ASM] Combined V\\$INSTANCE/v\\$asm_diskgroup session on {host} failed (exit_code={combined_exit_code}, "
            f"SID={final_sid_match.group(1).strip() if final_sid_match else 'unknown'}). "
            f"Raw output:\n{out}\nstderr:\n{err_out}"
        )
        ora_detail = _extract_ora_error(combined_section) or _extract_ora_error(err_out)
        if ora_detail:
            detail = ora_detail
        elif combined_exit_code is not None:
            detail = f"SQL*Plus session exited with code {combined_exit_code}."
        else:
            detail = "ASM instance state could not be determined (SQL*Plus session did not complete)."
        _asm_logger.error(f"[ASM] Final error reported to UI (server={host}): {detail}")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, detail)

    # Combined session succeeded end-to-end - exit 0 guarantees every
    # statement in it (both V$INSTANCE and v$asm_diskgroup) ran without a
    # SQL error, since WHENEVER SQLERROR EXIT would otherwise have fired.
    # Only now is it safe to parse a real instance name/status.
    instance_name_match = re.search(r"===ASM_INSTANCE_NAME=(.*?)===", out)
    instance_status_match = re.search(r"===ASM_INSTANCE_STATUS=(.*?)===", out)
    instance_name = instance_name_match.group(1).strip() if instance_name_match else ""
    instance_status = instance_status_match.group(1).strip().upper() if instance_status_match else ""
    _asm_logger.info(f"[ASM] ===ASM_INSTANCE_NAME==={instance_name or 'unknown'} (server={host})")
    _asm_logger.info(f"[ASM] ===ASM_INSTANCE_STATUS==={instance_status or 'unknown'} (server={host})")

    if not instance_status:
        _asm_logger.error(f"[ASM] SQL*Plus succeeded on {host} but no V$INSTANCE row could be parsed.")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, "SELECT INSTANCE_NAME, STATUS FROM V$INSTANCE returned no result.")

    _asm_logger.info(f"[ASM] ASM instance state check result=success (server={host}), STATUS={instance_status}")
    _asm_logger.info(f"[ASM] Executing V$ASM_DISKGROUP query (server={host})")

    if "===ASM_DISKGROUPS_START===" not in out or "===ASM_DISKGROUPS_END===" not in out:
        # Defensive only - should be unreachable given combined_exit_code==0
        # already guarantees every statement (including this SELECT) ran
        # without a SQL error, per WHENEVER SQLERROR EXIT.
        _asm_logger.error(f"[ASM] Combined session succeeded but no diskgroup markers found on {host}. Raw output:\n{out}")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=error elapsed={time.time() - start_time:.2f}s =====")
        return _unavailable_result(host, "ASM query produced no result (diskgroup markers missing).")

    _asm_logger.info(f"[ASM] SYSASM authentication result=success (server={host})")

    # The diskgroup SELECT ran unconditionally in this same session once
    # V$INSTANCE itself succeeded, so it is parsed unconditionally too -
    # whether any rows actually came back (not the literal STATUS text) is
    # what decides "not mounted" vs "success". ASM instances commonly
    # report V$INSTANCE.STATUS as e.g. STARTED even while disk groups are
    # fully mounted and queryable (diskgroup mount state lives in
    # V$ASM_DISKGROUP, not V$INSTANCE) - gating on STATUS == 'MOUNTED' was
    # discarding real, already-fetched diskgroup data. Real rows always
    # win: they are only ever absent when nothing is genuinely mounted yet.
    diskgroups = _parse_diskgroups(out)
    _asm_logger.info(f"[ASM] ===ASM_DISKGROUP_COUNT==={len(diskgroups)} (server={host}) - disk groups fetched")
    for dg in diskgroups:
        _asm_logger.info(
            f"[ASM]   diskgroup name={dg.get('name')} total_mb={dg.get('total_mb')} free_mb={dg.get('free_mb')} "
            f"used_gb={dg.get('used_gb')} free_gb={dg.get('free_gb')} used_pct={dg.get('used_pct')} (server={host})"
        )

    if not diskgroups and instance_status != "MOUNTED":
        _asm_logger.info(f"[ASM] ASM Instance: AVAILABLE - ASM Disk Groups: NOT MOUNTED (server={host})")
        _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=not_mounted elapsed={time.time() - start_time:.2f}s =====")
        return _not_mounted_result(host, instance_name, instance_status)

    _asm_logger.info(f"[ASM] ===== END ASM check: server={host} status=success elapsed={time.time() - start_time:.2f}s =====")

    return {
        "status": "success",
        "host": host,
        "message": None,
        "diskgroups": diskgroups
    }


def get_asm_diskgroups(host):
    """TTL-cached wrapper around get_asm_diskgroups_raw(), per host."""
    host = str(host or "").strip()
    now = time.time()

    with _ASM_LOCK:
        cached = _single_host_asm_cache.get(host)
        if cached and (now - cached["timestamp"] < _CACHE_TTL_SECONDS):
            return cached["data"]

    try:
        data = get_asm_diskgroups_raw(host)
    except Exception as e:
        _asm_logger.error(f"[ASM] Unexpected error fetching ASM disk groups for {host}: {e}")
        data = _unavailable_result(host, "Not Available")

    with _ASM_LOCK:
        _single_host_asm_cache[host] = {"timestamp": time.time(), "data": data}
    return data


def get_all_servers_asm_diskgroups_raw():
    """
    One ASM collection per unique Production DB server host - grouped the
    same way mount points/OS processes are. Grid/ASM has no configuration of
    its own: the host list and OS user come straight from the uploaded DB
    configuration's Production entries (see _get_production_targets()).
    """
    start_time = time.time()
    targets = _get_production_targets()
    _asm_logger.info(f"[ASM] ##### BEGIN all-servers ASM sweep: {len(targets)} server(s) - {[t['host'] for t in targets]} #####")

    if not targets:
        _asm_logger.info("[ASM] No Production servers configured - nothing to check.")
        _asm_logger.info(f"[ASM] ##### END all-servers ASM sweep: 0 server(s), elapsed={time.time() - start_time:.2f}s #####")
        return {"status": "success", "servers": {}}

    servers = {}
    servers_lock = threading.Lock()

    def process_target(target):
        result = get_asm_diskgroups_raw(target["host"], grid_user=target["grid_user"])
        with servers_lock:
            servers[target["host"]] = {
                **result,
                "server": target["label"]
            }

    threads = [threading.Thread(target=process_target, args=(t,)) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = {host: r.get("status") for host, r in servers.items()}
    _asm_logger.info(f"[ASM] ##### END all-servers ASM sweep: results={summary}, elapsed={time.time() - start_time:.2f}s #####")

    return {"status": "success", "servers": servers}


def get_all_servers_asm_diskgroups():
    """TTL-cached wrapper around get_all_servers_asm_diskgroups_raw() - the Home Page card's data source."""
    now = time.time()
    with _ASM_LOCK:
        cached = _all_asm_cache["data"]
        if cached and (now - _all_asm_cache["timestamp"] < _CACHE_TTL_SECONDS):
            _asm_logger.debug("[ASM] Serving all-servers ASM result from cache (within TTL).")
            return cached

    try:
        data = get_all_servers_asm_diskgroups_raw()
    except Exception as e:
        _asm_logger.error(f"[ASM] Unexpected error fetching all-server ASM disk groups: {e}")
        return {"status": "error", "message": str(e), "servers": {}}

    with _ASM_LOCK:
        _all_asm_cache["timestamp"] = time.time()
        _all_asm_cache["data"] = data
    return data
