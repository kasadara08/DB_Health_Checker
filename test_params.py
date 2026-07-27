from db_connection import get_api_connection

conn, err = get_api_connection("kasorcl")
if conn:
    cursor = conn.cursor()
    cursor.execute("SELECT name, value FROM v$parameter WHERE name LIKE '%dest%' OR name LIKE '%file%' OR name LIKE '%dump%'")
    print("--- PARAMETERS ---")
    for r in cursor.fetchall():
        if r[1]:
            print(f"{r[0]} = {r[1]}")
