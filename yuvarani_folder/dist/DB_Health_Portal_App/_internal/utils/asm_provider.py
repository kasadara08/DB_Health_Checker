"""
utils/asm_provider.py
──────────────────────
Fetches ASM diskgroup usage (v$asm_diskgroup) from a database host's local
+ASM instance, via SSH + SQL*Plus.

Connection is a two-step hop over a single REAL interactive shell
(paramiko invoke_shell — not one-shot exec_command calls), matching how
these cloud hosts are actually locked down: direct SSH login as "grid" is
disabled, so:
  1. SSH in as "opc" (the cloud host's admin login) using the same
     host password already configured in the registry file.
  2. Open one live shell and send `sudo su - grid` into it. If a
     password prompt appears, it's opc's OWN password being asked for
     (su - grid itself needs none once sudo has elevated to root), so
     the same host password is sent again automatically.
  3. Confirm the switch actually happened (`whoami`) before proceeding —
     never assumed just because no error text appeared.
  4. Send the ASM discovery + SQL*Plus commands into that SAME shell,
     now running as grid — needed both for SYSASM OS authentication and
     because reading the ASM process's own /proc/<pid>/environ (see
     below) requires being that same OS user.

SID / ORACLE_HOME discovery is DYNAMIC, not a static guess:
  1. Find the actually-running ASM instance via its PMON process
     (`ps -eo pid,comm | grep '[a]sm_pmon_'`) — this is exactly how the
     process names itself (asm_pmon_+ASM, asm_pmon_+ASM1, asm_pmon_+ASM2,
     ...), so it correctly covers both standalone and RAC ASM naming.
  2. Read that PMON process's OWN environment from /proc/<pid>/environ to
     get its real ORACLE_HOME — authoritative, since it's what the
     instance actually started with, not a guess from a config file.
  3. Only if no ASM PMON process is currently running at all does this
     fall back to a /etc/oratab "+ASM*" lookup, since GI-managed
     instances aren't always listed there. No hard-coded SID/path.
"""

import time
import threading

# ── Cache ──────────────────────────────────────────────────────────────────
# Process-wide (not per Streamlit session), same convention as
# utils/ssh_process_provider.py — throttles repeated fetches for the same
# host to one SSH round trip per CACHE_TTL seconds.
_asm_cache: dict = {}
_asm_cache_lock = threading.Lock()
CACHE_TTL = 60  # seconds — diskgroup usage changes slowly, no need for a tight TTL

SSH_LOGIN_USER = "opc"   # the account we actually SSH in as
GRID_OS_USER = "grid"    # the account we `sudo su -` into once connected

_ASM_QUERY = """SELECT
    name,
    total_mb,
    free_mb,
    ROUND((total_mb-free_mb)/1024,2) AS used_gb,
    ROUND(free_mb/1024,2) AS free_gb,
    ROUND((total_mb-free_mb)*100/total_mb,2) AS used_pct
FROM v$asm_diskgroup
ORDER BY name;"""

# One remote shell script: dynamically discover the running ASM instance's
# SID + ORACLE_HOME from its live PMON process, falling back to /etc/oratab
# only if no ASM PMON process is found at all, then run SQL*Plus. Markers
# (##LOG##/##STAGE_ERROR##/##DATA_START##/##DATA_END##) follow the same
# convention already used by the standby-DB SSH flow in db_connection.py.
_ASM_DISCOVERY_SCRIPT = r"""
PMON_LINE=$(ps -eo pid,comm 2>/dev/null | grep '[a]sm_pmon_' | head -n1)
if [ -n "$PMON_LINE" ]; then
    PMON_PID=$(echo "$PMON_LINE" | awk '{print $1}')
    PMON_COMM=$(echo "$PMON_LINE" | awk '{print $2}')
    # Oracle always lowercases the SID in its own background-process names
    # (asm_pmon_+asm1 even when the real ORACLE_SID is +ASM1), so this is
    # only a best-effort fallback label, NOT what gets connected with.
    NAME_SID=$(echo "$PMON_COMM" | sed 's/^asm_pmon_//')
    echo "##LOG##[ASM] Found running ASM instance: pid=$PMON_PID (process name suggests sid=$NAME_SID)"
    if [ -r "/proc/$PMON_PID/environ" ]; then
        ORACLE_HOME=$(tr '\0' '\n' < "/proc/$PMON_PID/environ" 2>/dev/null | grep '^ORACLE_HOME=' | cut -d= -f2-)
        # The real, case-correct ORACLE_SID the instance is actually running
        # under lives in its own environment, same as ORACLE_HOME - read it
        # from there instead of trusting the (always-lowercased) process name.
        ENV_SID=$(tr '\0' '\n' < "/proc/$PMON_PID/environ" 2>/dev/null | grep '^ORACLE_SID=' | cut -d= -f2-)
        if [ -n "$ENV_SID" ]; then
            ASM_SID="$ENV_SID"
            echo "##LOG##[ASM] ORACLE_SID read from PMON process environment (authoritative, case-correct): $ASM_SID"
        else
            ASM_SID="$NAME_SID"
            echo "##LOG##[ASM] WARNING: ORACLE_SID not present in PMON process environment - falling back to process name (may be wrong case): $ASM_SID"
        fi
        if [ -n "$ORACLE_HOME" ]; then
            echo "##LOG##[ASM] ORACLE_HOME discovered from running PMON process: $ORACLE_HOME"
        else
            echo "##LOG##[ASM] WARNING: ORACLE_HOME not present in PMON process environment"
        fi
    else
        ASM_SID="$NAME_SID"
        echo "##LOG##[ASM] WARNING: /proc/$PMON_PID/environ is not readable (permission or process gone) - using process name for sid (may be wrong case): $ASM_SID"
    fi
else
    echo "##LOG##[ASM] WARNING: no running asm_pmon_* process found"
fi

if [ -z "$ASM_SID" ] || [ -z "$ORACLE_HOME" ]; then
    echo "##LOG##[ASM] Falling back to /etc/oratab (+ASM*) lookup"
    if [ -f /etc/oratab ]; then
        OTAB_LINE=$(grep -i '^+ASM' /etc/oratab 2>/dev/null | head -n1)
        if [ -n "$OTAB_LINE" ]; then
            [ -z "$ASM_SID" ] && ASM_SID=$(echo "$OTAB_LINE" | cut -d: -f1)
            [ -z "$ORACLE_HOME" ] && ORACLE_HOME=$(echo "$OTAB_LINE" | cut -d: -f2)
            echo "##LOG##[ASM] oratab fallback resolved: sid=$ASM_SID home=$ORACLE_HOME"
        else
            echo "##LOG##[ASM] WARNING: no +ASM* entry found in /etc/oratab either"
        fi
    else
        echo "##LOG##[ASM] WARNING: /etc/oratab does not exist"
    fi
fi

if [ -z "$ASM_SID" ]; then
    echo "##STAGE_ERROR##ASM_SID_NOT_FOUND##No running ASM instance (asm_pmon_* process) was found, and no +ASM entry exists in /etc/oratab."
    exit 90
fi
if [ -z "$ORACLE_HOME" ]; then
    echo "##STAGE_ERROR##ORACLE_HOME_NOT_FOUND##Could not determine ORACLE_HOME for ASM instance $ASM_SID."
    exit 91
fi

export ORACLE_SID="$ASM_SID"
export ORACLE_HOME
export PATH="$ORACLE_HOME/bin:$PATH"

if ! command -v sqlplus >/dev/null 2>&1; then
    echo "##STAGE_ERROR##SQLPLUS_NOT_FOUND##sqlplus executable not found under ORACLE_HOME=$ORACLE_HOME"
    exit 92
fi

echo "##LOG##[ASM] Connecting: sqlplus / as sysasm (ORACLE_SID=$ORACLE_SID)"
SQLPLUS_OUT=$(timeout -k 5 30 sqlplus -L -S / as sysasm <<'SQLEOF'
set markup csv on delimiter | quote off
set feedback off verify off heading on pagesize 50000 linesize 32767
prompt ##DATA_START##
__QUERY__
prompt ##DATA_END##
exit;
SQLEOF
)
SQLPLUS_RC=$?
echo "$SQLPLUS_OUT"

if [ "$SQLPLUS_RC" -eq 124 ] || [ "$SQLPLUS_RC" -eq 137 ]; then
    echo "##STAGE_ERROR##SQLPLUS_TIMEOUT##SQL*Plus did not complete within the timeout window."
elif echo "$SQLPLUS_OUT" | grep -q "ORA-01034"; then
    # ORA-01034 with an instance that's genuinely running is a classic
    # symptom of a SID case mismatch (Oracle background process names are
    # always lowercase even when the real ORACLE_SID is uppercase). We
    # already try to read the case-correct SID from /proc/<pid>/environ
    # above, but as a defensive fallback - in case that ever comes back
    # lowercase too - retry once with the opposite (uppercased) case before
    # giving up.
    ALT_SID=$(echo "$ASM_SID" | tr '[:lower:]' '[:upper:]')
    if [ "$ALT_SID" != "$ASM_SID" ]; then
        echo "##LOG##[ASM] ORA-01034 with ORACLE_SID=$ASM_SID - retrying once with uppercase ORACLE_SID=$ALT_SID"
        export ORACLE_SID="$ALT_SID"
        timeout -k 5 30 sqlplus -L -S / as sysasm <<'SQLEOF2'
set markup csv on delimiter | quote off
set feedback off verify off heading on pagesize 50000 linesize 32767
prompt ##DATA_START##
__QUERY__
prompt ##DATA_END##
exit;
SQLEOF2
        SQLPLUS_RC=$?
        if [ "$SQLPLUS_RC" -eq 124 ] || [ "$SQLPLUS_RC" -eq 137 ]; then
            echo "##STAGE_ERROR##SQLPLUS_TIMEOUT##SQL*Plus did not complete within the timeout window (retry)."
        fi
    fi
fi
"""

_ASM_ORA_MESSAGES = [
    ("ORA-01017", "SYSASM authentication failed for the grid OS user — check that grid is a member of the correct OSASM group (typically asmadmin)."),
    ("ORA-01034", "ASM instance is not available (ORA-01034)."),
    ("ORA-12547", "Lost contact with the ASM instance (ORA-12547) — the instance may have just been restarted."),
]


def _extract_ora_line(text):
    import re
    m = re.search(r'((?:ORA|SP2)-\d{4,5}[^\n\r]*)', text)
    return m.group(1).strip() if m else ""


def _log_both(msg):
    """
    Every ASM connection/detail message goes to BOTH the console (so it's
    visible live while the app is running, same as every other feature's
    diagnostics) AND the persistent log file (db_connection._log ->
    oracle_thick_mode_debug.log) — so it survives after the console
    closes and sits alongside the standby-DB connection diagnostics that
    already use that same file.
    """
    from db_connection import _log
    print(msg)
    _log(msg)


def _read_until(channel, markers, timeout=25, poll_interval=0.2):
    """
    Read from a live (invoke_shell) channel until any of `markers`
    (substrings) shows up in the accumulated output, or `timeout` seconds
    pass. Returns (accumulated_text, found: bool). Used instead of
    exec_command() because an interactive shell has no natural "this
    command is done" signal — a distinctive marker echoed by the remote
    side after each step is what tells us it's safe to read the result
    and move on to the next one.
    """
    buf = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if channel.recv_ready():
            try:
                chunk = channel.recv(65536).decode(errors="replace")
            except Exception:
                break
            buf += chunk
            if any(m in buf for m in markers):
                return buf, True
        else:
            time.sleep(poll_interval)
    return buf, False


def _fetch_asm_diskgroups_via_ssh(host, password="", key_filename=None, port=22, timeout=45):
    """
    SSH into `host` as the opc OS user, switch to grid via `sudo su -`,
    dynamically discover the running ASM instance (see module docstring),
    and run SQL*Plus against it. Returns (diskgroups, error) — diskgroups
    is a list of dicts with keys: name, total_mb, free_mb, used_gb,
    free_gb, used_pct.
    """
    import os as _os
    import paramiko

    if not host:
        _log_both("[ASM] FAILED: no host configured for this server.")
        return [], "No host configured for this server."
    if not password and not key_filename:
        _log_both(f"[ASM:{host}] FAILED: no SSH credentials configured for the {SSH_LOGIN_USER} login.")
        return [], f"No SSH credentials configured for the {SSH_LOGIN_USER} login on this host."

    _log_both(f"[ASM:{host}] Connecting via SSH as {SSH_LOGIN_USER}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    base_kwargs = {
        "hostname": host,
        "port": port,
        "username": SSH_LOGIN_USER,
        "timeout": 15,
        "banner_timeout": 15,
        "auth_timeout": 15,
    }
    have_key = bool(key_filename and _os.path.isfile(key_filename))
    key_error = None
    connected = False

    # Try the key FIRST if one's available (this is the SAME account/key
    # Mount Points and top processes already connect with successfully —
    # opc commonly only allows key-based login at all, so this is the more
    # likely-to-work path), but always fall back to the password rather
    # than giving up if the key turns out not to be authorized for opc
    # specifically.
    if have_key:
        try:
            client.connect(key_filename=key_filename, look_for_keys=True, allow_agent=True, **base_kwargs)
            connected = True
            _log_both(f"[ASM:{host}] Authenticated as {SSH_LOGIN_USER} using the SSH key.")
        except paramiko.AuthenticationException as exc:
            key_error = exc
            _log_both(f"[ASM:{host}] SSH key was not accepted for {SSH_LOGIN_USER} — trying the password instead.")
        except Exception as exc:
            _log_both(f"[ASM:{host}] FAILED: SSH connection error (key attempt): {exc}")
            return [], f"SSH connection to {host} failed: {exc}"

    if not connected:
        if not password:
            reason = f" ({key_error})" if key_error else ""
            _log_both(f"[ASM:{host}] FAILED: SSH key rejected for {SSH_LOGIN_USER} and no password configured{reason}")
            return [], f"SSH authentication failed for {SSH_LOGIN_USER}@{host}"
        try:
            client.connect(password=password, look_for_keys=False, allow_agent=False, **base_kwargs)
            connected = True
            _log_both(f"[ASM:{host}] Authenticated as {SSH_LOGIN_USER} using the host password.")
        except paramiko.AuthenticationException:
            _log_both(f"[ASM:{host}] FAILED: SSH authentication failed for {SSH_LOGIN_USER}@{host} "
                      f"(tried {'key and ' if have_key else ''}password)")
            return [], f"SSH authentication failed for {SSH_LOGIN_USER}@{host}"
        except Exception as exc:
            _log_both(f"[ASM:{host}] FAILED: SSH connection error: {exc}")
            return [], f"SSH connection to {host} failed: {exc}"

    # ---- Real interactive shell from here on: opc -> grid -> discovery+sqlplus ----
    # Requested explicitly instead of the single combined `sudo su -c`
    # command: open one live shell, send `sudo su - grid` into it, CONFIRM
    # the switch actually took effect (via `whoami`), and only then send
    # the discovery/SQL*Plus commands into that same now-grid shell.
    _log_both(f"[ASM:{host}] SSH connected as {SSH_LOGIN_USER} — opening an interactive shell...")
    try:
        channel = client.get_transport().open_session()
        channel.get_pty(width=220, height=50)
        channel.invoke_shell()
    except Exception as exc:
        _log_both(f"[ASM:{host}] FAILED: could not open interactive shell: {exc}")
        try:
            client.close()
        except Exception:
            pass
        return [], f"Could not open an interactive shell on {host}: {exc}"

    try:
        # Drain the login banner/MOTD, then ask the tty to stop echoing our
        # own keystrokes back — keeps the output we have to parse below
        # limited to actual command results instead of a mix of typed
        # input and output.
        _read_until(channel, ["$", "#", ">"], timeout=5)
        _log_both(f"[ASM:{host}] Login banner drained — disabling terminal echo...")
        channel.send("stty -echo\n")
        _read_until(channel, ["\n"], timeout=3)
        _log_both(f"[ASM:{host}] Terminal echo disabled — shell ready.")

        # Up to 2 attempts: if whoami doesn't come back as grid after the
        # first try (a missed/mistimed password prompt, a slow shell,
        # etc.), explicitly retry the switch with the host password once
        # more before giving up — rather than failing on a single attempt.
        actual_user = ""
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            _log_both(f"[ASM:{host}] Sending 'sudo su - {GRID_OS_USER}' (attempt {attempt}/{max_attempts})...")
            channel.send(f"sudo -S su - {GRID_OS_USER}\n")
            # It's opc authenticating to sudo here (not grid) — reusing the
            # same host password already used for opc's own SSH login is
            # correct. If passwordless sudo is already configured for opc,
            # no password prompt appears and this branch is simply skipped.
            buf, _ = _read_until(channel, ["assword", "$", "#", "Sorry", "not allowed", "not in the sudoers"], timeout=10)
            # On a retry (attempt > 1), send the password UNCONDITIONALLY,
            # not just when a prompt was detected — the whole reason a
            # retry exists is that the first attempt's detection of that
            # prompt could itself have been missed or mistimed, so relying
            # on the same detection again would just repeat the failure.
            if "assword" in buf or attempt > 1:
                reason = "password prompt detected" if "assword" in buf else "retry — sending password unconditionally"
                _log_both(f"[ASM:{host}] Attempt {attempt}: {reason} — sending host password to sudo...")
                channel.send(password + "\n")
                more, _ = _read_until(channel, ["$", "#", "Sorry", "not allowed"], timeout=10)
                buf += more
            else:
                _log_both(f"[ASM:{host}] Attempt {attempt}: no password prompt detected (passwordless sudo?) — proceeding without sending one.")

            low = buf.lower()
            if "is not allowed to run" in low or "not in the sudoers file" in low:
                # A permissions problem, not a timing/password one — retrying
                # won't change the outcome, so stop immediately.
                _log_both(f"[ASM:{host}] FAILED: {SSH_LOGIN_USER} is not permitted to 'sudo su - {GRID_OS_USER}' (sudoers configuration).")
                return [], f"{SSH_LOGIN_USER} is not permitted to run 'sudo su - {GRID_OS_USER}' on {host} (check sudoers)."

            # Don't just assume the switch worked because no error text
            # appeared — confirm it directly.
            channel.send("echo WHOAMI_START; whoami; echo WHOAMI_END\n")
            whoami_buf, got_whoami = _read_until(channel, ["WHOAMI_END"], timeout=10)
            actual_user = ""
            if "WHOAMI_START" in whoami_buf and "WHOAMI_END" in whoami_buf:
                block = whoami_buf.split("WHOAMI_START", 1)[1].split("WHOAMI_END", 1)[0]
                for line in block.splitlines():
                    line = line.strip()
                    if line:
                        actual_user = line
                        break

            _log_both(f"[ASM:{host}] Attempt {attempt}: whoami reports '{actual_user or 'unknown'}'"
                      f"{' (no response received)' if not got_whoami else ''}.")

            if actual_user == GRID_OS_USER:
                _log_both(f"[ASM:{host}] Attempt {attempt}: switch to {GRID_OS_USER} confirmed.")
                break

            wrong_pwd = "sorry, try again" in low or "incorrect password" in low
            if attempt < max_attempts:
                _log_both(f"[ASM:{host}] Attempt {attempt}: still running as '{actual_user or 'unknown'}'"
                          f"{' (sudo reported a wrong password)' if wrong_pwd else ''} — retrying with the host password...")
            else:
                _log_both(f"[ASM:{host}] FAILED: still running as '{actual_user or 'unknown'}' after "
                          f"{max_attempts} attempt(s) of 'sudo su - {GRID_OS_USER}'"
                          f"{' — sudo rejected the password' if wrong_pwd else ''}.")
                if wrong_pwd:
                    return [], f"Wrong password for {SSH_LOGIN_USER} when running 'sudo su - {GRID_OS_USER}' on {host}."
                return [], f"Could not switch to {GRID_OS_USER} on {host} (still '{actual_user or 'unknown'}')."

        _log_both(f"[ASM:{host}] Confirmed running as {GRID_OS_USER} — running discovery + SQL*Plus...")

        # Send the whole discovery + sqlplus script as one block into the
        # now-grid shell, followed by a unique marker so we know exactly
        # when it's finished (bash executes multi-line input, including
        # the sqlplus heredoc inside it, the same way whether it's typed,
        # piped, or read from a script file).
        inner_script = _ASM_DISCOVERY_SCRIPT.replace("__QUERY__", _ASM_QUERY)
        _log_both(f"[ASM:{host}] Sending SID/ORACLE_HOME discovery + SQL*Plus script to the {GRID_OS_USER} shell...")
        channel.send(inner_script + "\necho __ASM_SCRIPT_DONE__\n")
        out_content, got_done = _read_until(channel, ["__ASM_SCRIPT_DONE__"], timeout=timeout)
        err_content = ""
        if not got_done:
            _log_both(f"[ASM:{host}] FAILED: discovery/SQL*Plus did not complete within the timeout window.")
            return [], f"ASM query on {host} timed out."
        _log_both(f"[ASM:{host}] Script finished — parsing output...")
    except Exception as exc:
        _log_both(f"[ASM:{host}] FAILED: interactive shell error: {exc}")
        return [], f"ASM query on {host} failed: {exc}"
    finally:
        try:
            client.close()
        except Exception:
            pass

    # -- Walk ##LOG##/##STAGE_ERROR## markers ---------------------------------
    stage_error = None
    for raw_line in out_content.splitlines():
        line = raw_line.strip()
        # Search for the marker anywhere in the line, not just at position 0:
        # a multi-line script typed into a real interactive bash shell (not a
        # script file) can get bash's own "> " continuation prompt prepended
        # to some lines while it's still reading the compound if/then/fi
        # block, which would silently defeat a plain startswith() check and
        # drop that line's ##LOG##/##STAGE_ERROR## entirely.
        if "##STAGE_ERROR##" in line:
            parts = line[line.index("##STAGE_ERROR##"):].split("##")
            stage_error = parts[3] if len(parts) > 3 else "ASM connection failed."
        elif "##LOG##" in line:
            msg = line[line.index("##LOG##") + len("##LOG##"):]
            _log_both(f"[ASM:{host}] {msg}")

    if stage_error:
        _log_both(f"[ASM:{host}] FAILED: {stage_error}")
        return [], f"ASM connection failed on {host}: {stage_error}"

    combined = out_content + "\n" + err_content

    # Check for a valid data block BEFORE treating any ORA-code text as
    # fatal: the discovery script can retry once internally with an
    # uppercased ORACLE_SID after an ORA-01034 (see _ASM_DISCOVERY_SCRIPT),
    # so the FIRST (failed) attempt's ORA-01034 text can still be sitting
    # in `combined` even though the retry right after it actually
    # succeeded. Only fall back to the ORA-code / "no result" diagnosis
    # when there's genuinely no data block to parse.
    have_data = "##DATA_START##" in out_content and "##DATA_END##" in out_content

    if not have_data:
        for ora_code, message in _ASM_ORA_MESSAGES:
            if ora_code in combined:
                _log_both(f"[ASM:{host}] FAILED: {message}")
                return [], f"ASM connection failed on {host}: {message}"

        ora_line = _extract_ora_line(combined)
        err_msg = ora_line or "ASM query did not return a result."
        _log_both(f"[ASM:{host}] FAILED: {err_msg}")
        if ora_line:
            return [], f"ASM query failed on {host}: {ora_line}"
        return [], f"ASM query did not return a result on {host}."

    # Use the LAST ##DATA_START##/##DATA_END## pair, not the first: an
    # interactive shell can echo back the script's own source text before
    # it actually executes (even with `stty -echo` attempted, e.g. if `su
    # -` resets tty settings), and that echoed "prompt ##DATA_START##"
    # source line would also contain the marker substring. The real,
    # already-executed output is always the pair that appears last.
    start_idx = out_content.rfind("##DATA_START##")
    end_idx = out_content.rfind("##DATA_END##")
    data_block = out_content[start_idx + len("##DATA_START##"):end_idx] if end_idx > start_idx else ""
    lines = [ln.strip() for ln in data_block.splitlines() if ln.strip()]
    _log_both(f"[ASM:{host}] Extracted {len(lines)} CSV line(s) between ##DATA_START##/##DATA_END##.")

    # Reuse the app's existing CSV-block parser rather than re-implementing
    # SQL*Plus output parsing (db_connection._parse_csv_block already
    # handles the "set markup csv on delimiter |" convention used
    # everywhere else in this app).
    from db_connection import _parse_csv_block
    records, cols = _parse_csv_block(lines)
    _log_both(f"[ASM:{host}] Parsed {len(records)} data row(s), columns: {', '.join(cols) or 'none'}.")

    if not records:
        ora_line = _extract_ora_line(combined)
        err_msg = ora_line or "No ASM diskgroups returned."
        _log_both(f"[ASM:{host}] FAILED: {err_msg}")
        if ora_line:
            return [], f"ASM query failed on {host}: {ora_line}"
        return [], f"No ASM diskgroups returned on {host}."

    def _col(row_dict, *names):
        for n in names:
            if n in row_dict:
                return row_dict[n]
        return ""

    diskgroups = []
    for r in records:
        row_dict = dict(zip(cols, r))
        try:
            diskgroups.append({
                "name":      _col(row_dict, "NAME"),
                "total_mb":  float(_col(row_dict, "TOTAL_MB") or 0),
                "free_mb":   float(_col(row_dict, "FREE_MB") or 0),
                "used_gb":   float(_col(row_dict, "USED_GB") or 0),
                "free_gb":   float(_col(row_dict, "FREE_GB") or 0),
                "used_pct":  float(_col(row_dict, "USED_PCT") or 0),
            })
        except (ValueError, TypeError) as exc:
            _log_both(f"[ASM:{host}] Skipped an unparsable diskgroup row {row_dict}: {exc}")
            continue

    _log_both(f"[ASM:{host}] SUCCESS: {len(diskgroups)} diskgroup(s) retrieved "
              f"({', '.join(g['name'] for g in diskgroups) or 'none'})")
    return diskgroups, None


def get_asm_diskgroup_info(host, password="", key_filename=None, port=22) -> dict:
    """
    Cached (process-wide, CACHE_TTL seconds) wrapper — returns
    {"diskgroups": [...], "error": None | "message"}.
    """
    cache_key = (host, GRID_OS_USER)
    now = time.monotonic()

    with _asm_cache_lock:
        cached = _asm_cache.get(cache_key)
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return {"diskgroups": cached["diskgroups"], "error": cached["error"]}

    diskgroups, error = _fetch_asm_diskgroups_via_ssh(host, password=password, key_filename=key_filename, port=port)

    with _asm_cache_lock:
        _asm_cache[cache_key] = {"ts": now, "diskgroups": diskgroups, "error": error}

    return {"diskgroups": diskgroups, "error": error}
