import oracledb
from db_connection import get_api_connection

conn, err = get_api_connection("kasorcl")
if conn:
    cursor = conn.cursor()

    # Read the /tmp/df_output.txt that the scheduler wrote
    result_var = cursor.var(oracledb.DB_TYPE_CLOB)
    try:
        cursor.execute("""
            DECLARE
              l_file  UTL_FILE.file_type;
              l_line  VARCHAR2(4000);
              l_out   VARCHAR2(32767) := '';
            BEGIN
              l_file := UTL_FILE.FOPEN('DASH_TMP_DIR', 'df_output.txt', 'r', 4000);
              LOOP
                BEGIN
                  UTL_FILE.GET_LINE(l_file, l_line);
                  l_out := l_out || l_line || CHR(10);
                EXCEPTION
                  WHEN NO_DATA_FOUND THEN EXIT;
                END;
              END LOOP;
              UTL_FILE.FCLOSE(l_file);
              :result := l_out;
            END;
        """, [result_var])
        result = result_var.getvalue()
        print("=== df output ===")
        print(result)
    except Exception as e:
        print("Read file error:", e)
