from db_connection import get_api_connection

conn, err = get_api_connection("kasorcl")
if conn:
    cursor = conn.cursor()
    cursor.execute("SELECT file_name FROM dba_data_files")
    print("--- dba_data_files ---")
    for r in cursor.fetchall():
        print(r[0])
        
    try:
        cursor.execute("SELECT file_name FROM dba_temp_files")
        print("--- dba_temp_files ---")
        for r in cursor.fetchall():
            print(r[0])
    except Exception as e:
        print("temp files err:", e)
