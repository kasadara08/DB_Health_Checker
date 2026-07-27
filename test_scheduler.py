from db_connection import get_api_connection

conn, err = get_api_connection("kasorcl")
if conn:
    cursor = conn.cursor()

    # Try to get mount points via DBMS_SCHEDULER running df -h
    # Write to a temp file, then read it back
    try:
        # Create a directory object pointing to /tmp
        try:
            cursor.execute("CREATE OR REPLACE DIRECTORY DASH_TMP_DIR AS '/tmp'")
            print("Directory created OK")
        except Exception as e:
            print("Dir create:", e)

        # Run df -h via scheduler job that writes to file
        job_sql = """
        BEGIN
          DBMS_SCHEDULER.CREATE_JOB(
            job_name        => 'DASH_DF_JOB',
            job_type        => 'EXECUTABLE',
            job_action      => '/bin/bash',
            number_of_arguments => 2,
            enabled         => FALSE,
            auto_drop       => TRUE
          );
          DBMS_SCHEDULER.SET_JOB_ARGUMENT_VALUE('DASH_DF_JOB', 1, '-c');
          DBMS_SCHEDULER.SET_JOB_ARGUMENT_VALUE('DASH_DF_JOB', 2, 'df -Pk > /tmp/df_output.txt 2>&1');
          DBMS_SCHEDULER.ENABLE('DASH_DF_JOB');
          DBMS_SCHEDULER.RUN_JOB('DASH_DF_JOB', use_current_session => FALSE);
        END;
        """
        cursor.execute(job_sql)
        conn.commit()
        import time; time.sleep(3)
        print("Job submitted OK")

        # Read the output file back
        cursor.execute("""
            DECLARE
              l_file  UTL_FILE.file_type;
              l_line  VARCHAR2(4000);
              l_out   CLOB := '';
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
        """, result=cursor.var(__import__('oracledb').CLOB))
        # This is complex, let me try simpler
    except Exception as e:
        print("Scheduler approach:", e)

    # Check if UTL_FILE directory exists
    try:
        cursor.execute("SELECT directory_name, directory_path FROM dba_directories")
        dirs = cursor.fetchall()
        print("\n--- Oracle Directories ---")
        for d in dirs:
            print(d)
    except Exception as e:
        print("dba_directories error:", e)
