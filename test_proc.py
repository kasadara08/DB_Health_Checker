from db_connection import get_api_connection, get_config_for_db
from utils.ssh_process_provider import get_top_processes_for_db

conn, err = get_api_connection('kasorcl')
print("Connection err:", err)
if conn:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.spid, s.sid, s.username, s.type, s.status, p.program
        FROM v$process p
        JOIN v$session s ON s.paddr = p.addr
    """)
    rows = cursor.fetchall()
    print(f"\n--- ALL SESSIONS IN DB ({len(rows)}) ---")
    for r in rows:
        print(r)

print("\n--- TESTING get_top_processes_for_db('kasorcl') ---")
cfg = get_config_for_db('kasorcl') or {}
res = get_top_processes_for_db(
    host=cfg.get("host", ""),
    username=cfg.get("user", ""),
    password=cfg.get("password", ""),
    sid='kasorcl',
    limit=10
)
print("Result source:", res.get("source"))
print("Result error:", res.get("error"))
print("Top CPU count:", len(res.get("top_cpu", [])))
print("Top CPU:", res.get("top_cpu"))
print("Top MEM count:", len(res.get("top_mem", [])))
print("Top MEM:", res.get("top_mem"))
