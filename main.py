"""
FastAPI Backend for DB Health Dashboard
========================================
Runs on port 8000 alongside Streamlit (port 8501).
Uses API-safe db_connection functions — NO Streamlit session_state dependency.
Works on both Windows and Linux servers.

Start:  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
Docs:   http://<host>:8000/docs
"""

import os
import sys
import socket
import datetime

# Ensure project root is in path (works on Windows and Linux)
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from db_connection import (
    get_txt_path_api,
    load_db_names_api,
    get_config_for_db,
    get_api_connection,
    read_db_file_to_df,
)

# ─────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="DB Health Dashboard API",
    description=(
        "REST API for Oracle Database Health Monitoring.\n\n"
        "All endpoints query the Oracle server directly using credentials "
        "from the configured registry file. Works on Windows & Linux."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _tcp_check(host: str, port: int, timeout: float = 2.0) -> dict:
    """Quick TCP socket probe — does not need Oracle drivers."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return {"reachable": True, "error": None}
    except socket.timeout:
        return {"reachable": False, "error": f"Timed out ({host}:{port}) — host unreachable or firewall blocking port {port}"}
    except ConnectionRefusedError:
        return {"reachable": False, "error": f"Connection refused ({host}:{port}) — Oracle Listener may not be running"}
    except OSError as e:
        return {"reachable": False, "error": f"Network error ({host}:{port}): {e}"}


def _now() -> str:
    return datetime.datetime.now().isoformat()


def _require_db(db_name: str):
    """Raise 404 if db_name is not in registry."""
    names = load_db_names_api()
    if db_name.strip().lower() not in [n.strip().lower() for n in names]:
        raise HTTPException(status_code=404, detail=f"Database '{db_name}' not found in registry.")


def _query(conn, sql: str, params=None) -> list:
    """Run a SQL query and return list of dicts."""
    cur = conn.cursor()
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# General endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/", tags=["General"])
def root():
    """API root — returns service info."""
    registry = get_txt_path_api()
    db_count = len(load_db_names_api())
    return {
        "service": "DB Health Dashboard API",
        "version": "2.0.0",
        "status": "running",
        "timestamp": _now(),
        "registry_configured": bool(registry),
        "registry_path": registry or "not configured",
        "database_count": db_count,
        "docs": "/docs",
    }


@app.get("/health", tags=["General"])
def health():
    """API server health check."""
    return {
        "status": "ok",
        "timestamp": _now(),
        "registry": get_txt_path_api() or "not configured",
    }


# ─────────────────────────────────────────────────────────────
# Registry endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/api/registry", tags=["Registry"])
def registry_info():
    """
    Returns the active registry file path and all database entries.
    Passwords are masked for security.
    """
    path = get_txt_path_api()
    if not path:
        return {"configured": False, "path": None, "entry_count": 0, "entries": []}

    try:
        df = read_db_file_to_df(path)
        rows = df.to_dict(orient="records")
        for r in rows:
            if "password" in r:
                r["password"] = "***"
        return {
            "configured": True,
            "path": path,
            "entry_count": len(rows),
            "entries": rows,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading registry: {e}")


@app.get("/api/databases", tags=["Databases"])
def list_databases():
    """Lists all databases configured in the registry file."""
    names = load_db_names_api()
    return {
        "count": len(names),
        "databases": names,
        "registry": get_txt_path_api() or "not configured",
    }


# ─────────────────────────────────────────────────────────────
# Connectivity endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/api/databases/connectivity", tags=["Connectivity"])
def check_all_connectivity():
    """
    TCP reachability test for ALL databases in the registry.
    Does NOT need an Oracle connection — just tests host:port.
    """
    path = get_txt_path_api()
    if not path:
        return {"overall_status": "no_registry", "message": "Registry not configured.", "results": []}

    try:
        df = read_db_file_to_df(path)
        rows = df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading registry: {e}")

    results = []
    ok = 0
    for row in rows:
        db_name = str(row.get("db_name", "")).strip()
        host    = str(row.get("host", "")).strip()
        port_s  = str(row.get("port", "1521")).strip().rstrip(".0") or "1521"
        try:
            port = int(float(port_s))
        except Exception:
            port = 1521

        chk = _tcp_check(host, port)
        if chk["reachable"]:
            ok += 1
        results.append({
            "db_name": db_name,
            "host": host,
            "port": port,
            "reachable": chk["reachable"],
            "error": chk["error"],
        })

    total = len(results)
    failed = total - ok
    if ok == 0:
        overall = "all_unreachable"
    elif failed > 0:
        overall = "partial"
    else:
        overall = "all_reachable"

    return {
        "overall_status": overall,
        "total": total,
        "reachable": ok,
        "unreachable": failed,
        "checked_at": _now(),
        "results": results,
    }


@app.get("/api/databases/{db_name}/connectivity", tags=["Connectivity"])
def check_db_connectivity(db_name: str):
    """TCP + Oracle login test for a single database."""
    _require_db(db_name)
    cfg = get_config_for_db(db_name)
    host = cfg["host"]
    port = int(cfg["port"]) if str(cfg["port"]).isdigit() else 1521

    tcp = _tcp_check(host, port)
    oracle_ok = False
    oracle_error = None

    if tcp["reachable"]:
        conn, err = get_api_connection(db_name)
        if conn:
            oracle_ok = True
            try:
                conn.close()
            except Exception:
                pass
        else:
            oracle_error = err

    return {
        "db_name": db_name,
        "host": host,
        "port": port,
        "service_name": cfg["service_name"],
        "tcp_reachable": tcp["reachable"],
        "tcp_error": tcp["error"],
        "oracle_login_ok": oracle_ok,
        "oracle_error": oracle_error,
        "checked_at": _now(),
    }


# ─────────────────────────────────────────────────────────────
# Live Oracle DB data endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/api/databases/{db_name}/status", tags=["Database Details"])
def db_status(db_name: str):
    """
    Fetches live Oracle database status:
    DB open mode, instance name, version, platform, uptime.
    """
    _require_db(db_name)
    conn, err = get_api_connection(db_name)
    if not conn:
        raise HTTPException(status_code=503, detail=err)
    try:
        rows = _query(conn, """
            SELECT
                i.instance_name,
                i.host_name,
                i.version,
                i.status        AS instance_status,
                d.open_mode,
                d.db_unique_name,
                d.platform_name,
                ROUND((SYSDATE - i.startup_time) * 24, 2) AS uptime_hours
            FROM v$instance i, v$database d
        """)
        return {
            "db_name": db_name,
            "connected": True,
            "data": rows[0] if rows else {},
            "fetched_at": _now(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/databases/{db_name}/sessions", tags=["Database Details"])
def db_sessions(db_name: str):
    """
    Returns active, inactive, and total session counts
    plus current license HWM and active query details.
    """
    _require_db(db_name)
    conn, err = get_api_connection(db_name)
    if not conn:
        raise HTTPException(status_code=503, detail=err)
    try:
        summary = _query(conn, """
            SELECT
                COUNT(*) AS total_sessions,
                SUM(CASE WHEN status='ACTIVE'   THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status='INACTIVE' THEN 1 ELSE 0 END) AS inactive
            FROM v$session
            WHERE type = 'USER'
        """)
        hwm = _query(conn, """
            SELECT sessions_highwater AS hwm
            FROM v$license
        """)
        active_queries = _query(conn, """
            SELECT
                s.sid,
                s.serial#,
                s.username,
                s.status,
                s.machine,
                s.program,
                ROUND(sq.elapsed_time/1e6, 2) AS elapsed_sec,
                SUBSTR(sq.sql_text, 1, 120)   AS sql_text
            FROM v$session s
            JOIN v$sql sq ON s.sql_id = sq.sql_id
            WHERE s.type = 'USER' AND s.status = 'ACTIVE'
            AND ROWNUM <= 20
        """)
        return {
            "db_name": db_name,
            "summary": summary[0] if summary else {},
            "hwm": hwm[0]["HWM"] if hwm else None,
            "active_queries": active_queries,
            "fetched_at": _now(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/databases/{db_name}/tablespaces", tags=["Database Details"])
def db_tablespaces(db_name: str):
    """
    Returns tablespace utilization — name, total MB, used MB, free MB, usage %.
    """
    _require_db(db_name)
    conn, err = get_api_connection(db_name)
    if not conn:
        raise HTTPException(status_code=503, detail=err)
    try:
        rows = _query(conn, """
            SELECT
                df.tablespace_name,
                ROUND(df.total_bytes / 1048576, 2)                          AS total_mb,
                ROUND((df.total_bytes - NVL(fs.free_bytes,0)) / 1048576, 2) AS used_mb,
                ROUND(NVL(fs.free_bytes,0) / 1048576, 2)                    AS free_mb,
                ROUND((df.total_bytes - NVL(fs.free_bytes,0))
                    / df.total_bytes * 100, 2)                               AS used_pct
            FROM (
                SELECT tablespace_name, SUM(bytes) AS total_bytes
                FROM dba_data_files GROUP BY tablespace_name
            ) df
            LEFT JOIN (
                SELECT tablespace_name, SUM(bytes) AS free_bytes
                FROM dba_free_space GROUP BY tablespace_name
            ) fs ON df.tablespace_name = fs.tablespace_name
            ORDER BY used_pct DESC
        """)
        return {
            "db_name": db_name,
            "count": len(rows),
            "tablespaces": rows,
            "fetched_at": _now(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/databases/{db_name}/backup", tags=["Database Details"])
def db_backup(db_name: str):
    """
    Returns last 10 RMAN backup jobs — type, status, start time, duration (minutes).
    """
    _require_db(db_name)
    conn, err = get_api_connection(db_name)
    if not conn:
        raise HTTPException(status_code=503, detail=err)
    try:
        rows = _query(conn, """
            SELECT
                session_key,
                input_type          AS backup_type,
                status,
                TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
                TO_CHAR(end_time,   'YYYY-MM-DD HH24:MI:SS') AS end_time,
                ROUND((end_time - start_time) * 24 * 60, 2)  AS duration_minutes
            FROM v$rman_backup_job_details
            ORDER BY start_time DESC
            FETCH FIRST 10 ROWS ONLY
        """)
        last_status = rows[0]["STATUS"] if rows else "NO BACKUP FOUND"
        return {
            "db_name": db_name,
            "last_backup_status": last_status,
            "history": rows,
            "fetched_at": _now(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/databases/{db_name}/summary", tags=["Database Details"])
def db_summary(db_name: str):
    """
    One-shot summary: status + sessions + last backup.
    Useful for a quick health overview of a single database.
    """
    _require_db(db_name)
    conn, err = get_api_connection(db_name)
    if not conn:
        return {
            "db_name": db_name,
            "connected": False,
            "error": err,
            "fetched_at": _now(),
        }
    try:
        status_rows = _query(conn, """
            SELECT i.instance_name, i.status AS instance_status, d.open_mode,
                   ROUND((SYSDATE - i.startup_time)*24, 2) AS uptime_hours
            FROM v$instance i, v$database d
        """)
        sess_rows = _query(conn, """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='ACTIVE'   THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN status='INACTIVE' THEN 1 ELSE 0 END) AS inactive
            FROM v$session WHERE type='USER'
        """)
        backup_rows = _query(conn, """
            SELECT status, TO_CHAR(start_time,'YYYY-MM-DD HH24:MI:SS') AS start_time
            FROM v$rman_backup_job_details
            ORDER BY start_time DESC FETCH FIRST 1 ROWS ONLY
        """)
        return {
            "db_name": db_name,
            "connected": True,
            "status": status_rows[0] if status_rows else {},
            "sessions": sess_rows[0] if sess_rows else {},
            "last_backup": backup_rows[0] if backup_rows else {"status": "NO BACKUP"},
            "fetched_at": _now(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass
