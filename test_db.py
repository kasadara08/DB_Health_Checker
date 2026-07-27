import os
import oracledb
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any

# ------------------- Configuration -------------------
DB_USER = os.getenv("ORACLE_USER", "yuvarani")
DB_PASS = os.getenv("ORACLE_PASS", "welcome123")
DB_DSN  = os.getenv("ORACLE_DSN", "localhost:1521/ORCL")
POOL_MIN = int(os.getenv("ORACLE_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("ORACLE_POOL_MAX", "5"))
POOL_INC = int(os.getenv("ORACLE_POOL_INC", "1"))

# ------------------- Connection pool -------------------
pool: oracledb.ConnectionPool | None = None

def init_pool() -> None:
    """Initialise a global Oracle connection pool.
    The `encoding` argument is omitted for compatibility with the thin driver.
    """
    global pool
    if pool is None:
        pool = oracledb.create_pool(
            user=DB_USER,
            password=DB_PASS,
            dsn=DB_DSN,
            min=POOL_MIN,
            max=POOL_MAX,
            increment=POOL_INC,
        )

# ------------------- Helper to call package -------------------
def exec_pkg_proc(proc_name: str) -> List[Dict[str, Any]]: 
     #proc_name: str => it a function it accep the single parameter and list[Dict][str, Any] => it calls the list of dictionary
     #Calls DASHBOARD_PKG.<proc_name>(OUT SYS_REFCURSOR) and returns rows as dicts."""
    conn = None
    cur = None
    try:
        conn = pool.acquire()  #acquire a connection from the pool it defining the pool
        cur = conn.cursor()  #defining the cursor
        out_cursor = cur.var(oracledb.CURSOR)  #creating a variable to hold the output cursor from the stored procedure
        cur.callproc(f"DASHBOARD_PKG.{proc_name}", (out_cursor,)) #calling the stored procedure with the output cursor as an argument
        rc = out_cursor.getvalue()  #getting the result set from the output cursor
        cols = [d[0] for d in rc.description]
        rows = [dict(zip(cols, r)) for r in rc]
        return rows
    except oracledb.DatabaseError as exc:
        raise HTTPException(status_code=500, detail=f"{proc_name} failed: {exc}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ------------------- FastAPI app -------------------
app = FastAPI(title="Oracle DB Health Dashboard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ------------------- Endpoints (unchanged URLs) -------------------
@app.get("/db-status")
def db_status(db: str = Query("")):
    rows = exec_pkg_proc("GET_DATABASE_STATUS")
    return rows[0] if rows else {"error": "No data"}

@app.get("/tablespace")
def tablespace():
    rows = exec_pkg_proc("GET_TABLESPACE")
    return {"tablespaces": rows}

@app.get("/session-stats")
def session_stats():
    rows = exec_pkg_proc("GET_SESSION_STATS")
    return {"session_stats": {r["STATUS"]: r["CNT"] for r in rows}}

@app.get("/backup-status")
def backup_status():
    rows = exec_pkg_proc("GET_BACKUP_STATUS")
    return {"backup_status": rows[0]["STATUS"] if rows else "UNKNOWN"}

@app.get("/listener-status")
def listener_status():
    import subprocess
    try:
        result = subprocess.run(["lsnrctl", "status"], capture_output=True, text=True, timeout=5)
        status = "UP" if "READY" in result.stdout.upper() else "DOWN"
        return {"listener_status": status}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/blocking-sessions")
def blocking_sessions():
    rows = exec_pkg_proc("GET_BLOCKING_SESSIONS")
    return {"blocking_sessions": rows}

@app.get("/top-cpu-sessions")
def top_cpu_sessions():
    rows = exec_pkg_proc("GET_TOP_CPU_SESSIONS")
    return {"top_cpu_sessions": rows}

# ------------------- Startup event -------------------
@app.on_event("startup")
def on_startup():
    init_pool()
