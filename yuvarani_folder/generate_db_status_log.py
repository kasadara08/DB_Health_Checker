import os
import sys
import time
import datetime
import socket
import traceback
import oracledb

# Add workspace directory to python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

import db_connection

# Initialize thick mode using project helper
db_connection._ensure_thick_mode()

LOG_FILE_PATH = os.path.join(current_dir, "db_status_report.log")

def tcp_ping(host, port):
    try:
        s = socket.create_connection((host, int(port)), timeout=2)
        s.close()
        return True
    except Exception:
        return False

def check_db(cfg, label):
    if not cfg:
        return "NOT_CONFIGURED", ""
    
    host = cfg.get("host", "")
    port = cfg.get("port", "1521")
    user = cfg.get("user", "")
    password = cfg.get("password", "")
    dsn = cfg.get("dsn", "")
    
    if not host or not port or not user:
        return "DOWN", f"[{label}] Missing connection parameters (host={host}, port={port}, user={user})"
        
    # Check TCP port first (log if failed, but proceed anyway)
    if not tcp_ping(host, port):
        print(f"[{label}] Warning: Listener port {port} on host {host} is unreachable via TCP ping. Attempting connection anyway...")
        
    # Try connecting
    try:
        mode = db_connection.get_oracle_mode(user)
        conn = oracledb.connect(
            user=user,
            password=password,
            dsn=dsn,
            mode=mode,
            tcp_connect_timeout=2
        )
        conn.close()
        return "UP", ""
    except Exception as e:
        tb = traceback.format_exc()
        err_msg = str(e)
        if isinstance(e, oracledb.DatabaseError):
            try:
                error_obj = e.args[0]
                if hasattr(error_obj, "code"):
                    ora_code = f"ORA-{error_obj.code:05d}"
                    detail = getattr(error_obj, "message", str(e)).strip()
                    err_msg = f"{ora_code}: {detail}"
            except Exception:
                pass
                
        ssh_user = cfg.get("host_username", "").strip() or cfg.get("user", "").strip() or "opc"
        ssh_pass = cfg.get("host_password", "").strip()
        key_file = (
            cfg.get("key_filename") or cfg.get("ssh_key_path") or cfg.get("oci_key_path") or
            cfg.get("ssh_key") or cfg.get("oci_key")
        )
        if not key_file:
            import os
            for default_key in [
                os.path.expanduser("~/.oci/oci_api_key.pem"),
                os.path.expanduser("~/.oci/id_rsa"),
                os.path.expanduser("~/.ssh/id_rsa"),
                os.path.expanduser("~/.ssh/id_ed25519"),
                os.path.expanduser("~/.ssh/oci_id_rsa"),
                os.path.expanduser("~/.ssh/id_ecdsa"),
            ]:
                if os.path.isfile(default_key):
                    key_file = default_key
                    break

        connect_kwargs = {
            "hostname": host,
            "port": 22,
            "username": ssh_user,
            "timeout": 3,
        }
        if ssh_pass:
            connect_kwargs["password"] = ssh_pass
        if key_file and os.path.isfile(key_file):
            connect_kwargs["key_filename"] = key_file
            connect_kwargs["look_for_keys"] = True
            connect_kwargs["allow_agent"] = True
        elif not ssh_pass:
            connect_kwargs["look_for_keys"] = True
            connect_kwargs["allow_agent"] = True

        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(**connect_kwargs)
            client.close()
            return "UP", f"[{label}] Direct connection failed ({err_msg}), but host is UP and accessible via SSH."
        except Exception as ssh_err:
            return "DOWN", f"[{label}] Direct connection failed ({err_msg}) and SSH connection failed ({ssh_err})\nTraceback:\n{tb}"

def get_standby_sync_status(primary_cfg, standby_cfg):
    """
    Step 1: Connect to Primary DB using registry credentials.
    Step 2: SSH into the Primary host using its OCI key, source the primary
            db's own .env file, resolve/verify TNS_ADMIN, then run sqlplus
            from there into the Standby (using the registry-configured
            Standby username/password/TNS alias, as SYSDBA) to read its
            last received/applied log sequence, and compare against the
            Primary's own last generated sequence to compute the gap.
    """
    # --- Step 1: Connect to Primary DB using registry credentials ---
    p_host = primary_cfg.get("host", "") if primary_cfg else ""
    p_port = primary_cfg.get("port", "1521") if primary_cfg else ""
    if not primary_cfg or not p_host:
        return "DOWN", "Primary DB configuration or host missing"
        
    if not tcp_ping(p_host, p_port):
        print(f"[primary-check] Warning: Primary host {p_host}:{p_port} is unreachable via TCP ping. Attempting connection anyway...")

    p_conn = None
    try:
        p_mode = db_connection.get_oracle_mode(primary_cfg["user"])
        p_kwargs = {
            "user": primary_cfg["user"],
            "password": primary_cfg["password"],
            "dsn": primary_cfg["dsn"],
            "mode": p_mode,
            "tcp_connect_timeout": 2
        }
        if os.path.isdir(db_connection._ORACLE_CONFIG_DIR):
            p_kwargs["config_dir"] = db_connection._ORACLE_CONFIG_DIR
        p_conn = oracledb.connect(**p_kwargs)
    except Exception as e:
        tb = traceback.format_exc()
        if primary_cfg.get("host_username") and (primary_cfg.get("host_password") or primary_cfg.get("key_filename") or primary_cfg.get("ssh_key_path")):
            print(f"[primary-check] Direct primary connection failed ({e}). Falling back to SSH connection.")
            p_conn = db_connection.SSHConnection(primary_cfg)
        else:
            return "DOWN", f"Primary DB connection failed: {str(e)}\nTraceback:\n{tb}"

    # --- Step 2: Query Standby via OCI-key SSH into the Primary Server ---
    try:
        from queries.queries import get_log_gap_info

        db_name = primary_cfg.get("db_name", "unknown") if primary_cfg else "unknown"

        print(f"[standby-check] Checking standby via SSH to Primary Server using OCI Key for {db_name}...")
        s_ssh_conn = db_connection.SSHConnection(primary_cfg, is_standby=True, standby_cfg=standby_cfg)
        dg_res = get_log_gap_info(primary_conn=p_conn, standby_conn=s_ssh_conn)

        if dg_res and dg_res.get("configured"):
            max_gap = dg_res.get("max_gap", 0)
            if max_gap >= 2:
                status_str = f"UP (NOT SYNCHRONIZED - Gap: {max_gap} sequences)"
            else:
                status_str = "UP (SYNCHRONIZED)"

            dest_errs = []
            for d in dg_res.get("standby_dests", []):
                if d.get("error"):
                    dest_errs.append(f"Dest #{d.get('dest_id')} status: {d.get('status')}, error: {d.get('error')}")
            err_str = "\n".join(dest_errs) if dest_errs else ""

            # NOTE: intentionally NOT calling db_connection._log_standby_db()
            # here - this function runs automatically every ~2 minutes as
            # part of monitor_thread.py's own cycle (which already checks
            # every DB, standby included, via its ThreadPoolExecutor pass),
            # so logging here as well just wrote the same standby DB entry
            # to the console/normal_db_debug.log twice per cycle. The
            # status_str/err_str below still drive db_status_report.log
            # normally - only the duplicate console/log side effect is removed.
            return status_str, err_str
        else:
            err_str = (dg_res or {}).get("error", "") or "Standby log gap query failed"
            status_str = "DOWN (Standby Unreachable via OCI Key SSH)"
            return status_str, err_str
    except Exception as e:
        tb = traceback.format_exc()
        err_msg = f"Gap query failed: {str(e)}\nTraceback:\n{tb}"
        return "UP (Query Error)", err_msg
    finally:
        if p_conn:
            try: p_conn.close()
            except Exception: pass

def generate_report():
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_lines = []
    report_lines.append(f"======================================================================")
    report_lines.append(f"DATABASE HEALTH CHECK REPORT - Checked at {timestamp}")
    report_lines.append(f"======================================================================\n")
    
    try:
        db_names = db_connection.load_db_names_api()
        if not db_names:
            report_lines.append("No databases found in the active registry configuration.\n")
        else:
            for db in db_names:
                report_lines.append(f"DATABASE: {db}")
                report_lines.append(f"----------------------------------------------------------------------")
                
                # 1. Normal DB Check
                primary_cfg = db_connection.get_config_for_db(db)
                status_norm, err_norm = check_db(primary_cfg, "Primary DB")
                if status_norm == "UP":
                    report_lines.append(f"  * Normal DB   : UP")
                else:
                    report_lines.append(f"  * Normal DB   : {status_norm} - Error details:\n{err_norm}")
                    
                # 2. Reporting DB Check
                rpt_cfg = db_connection.get_reporting_db_config(db)
                if rpt_cfg:
                    status_rpt, err_rpt = check_db(rpt_cfg, "Reporting DB")
                    if status_rpt == "UP":
                        report_lines.append(f"  * Reporting DB: UP")
                    else:
                        report_lines.append(f"  * Reporting DB: {status_rpt} - Error details:\n{err_rpt}")
                else:
                    report_lines.append(f"  * Reporting DB: NOT CONFIGURED")
                    
                # 3. Standby DB Check
                stby_cfg = db_connection.get_standby_db_config(db)
                if stby_cfg:
                    stby_server = stby_cfg.get("host", "unknown")
                    stby_port   = stby_cfg.get("port", "1521")
                    stby_db     = stby_cfg.get("db_name", db)
                    # Show clearly which server the DR DB is on
                    different_server = stby_server != (primary_cfg.get("host","") if primary_cfg else "")
                    server_tag = f" [Server: {stby_server}:{stby_port}" + (" ← different server]" if different_server else "]")
                    status_stby, err_stby = get_standby_sync_status(primary_cfg, stby_cfg)
                    if status_stby.startswith("UP"):
                        report_lines.append(f"  * Standby DB  : {status_stby}{server_tag}")
                        if err_stby: # query warnings
                            report_lines.append(f"    Warning details:\n{err_stby}")
                    else:
                        report_lines.append(f"  * Standby DB  : {status_stby}{server_tag}")
                        if err_stby:
                            report_lines.append(f"    Error details:\n{err_stby}")
                else:
                    report_lines.append(f"  * Standby DB  : NOT CONFIGURED")

                    
                report_lines.append("") # Empty separator line
    except Exception as e:
        report_lines.append(f"Critical error during report generation: {e}\n{traceback.format_exc()}")
        
    report_lines.append(f"======================================================================\n")
    
    report_content = "\n".join(report_lines)
    
    # Write to single log file
    try:
        with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(report_content)
        print(f"Report written successfully to {LOG_FILE_PATH}")
    except Exception as e:
        print(f"Failed to write report to file: {e}")

if __name__ == "__main__":
    generate_report()
