import oracledb
import datetime
import random
import sys

# Set stdout to utf-8 to avoid charmap errors
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Credentials
DB_USER = "yuvarani"
DB_PASS = "welcome123"
DB_DSN  = "localhost:1521/ORCL"

def backfill():
    try:
        conn = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)
        cursor = conn.cursor()
        
        # 1. Create table if not exists
        print("Ensuring DASHBOARD_GROWTH_TRACK table exists...")
        cursor.execute("""
            BEGIN
                EXECUTE IMMEDIATE 'CREATE TABLE DASHBOARD_GROWTH_TRACK (
                    record_date DATE DEFAULT TRUNC(SYSDATE),
                    used_mb NUMBER,
                    total_mb NUMBER,
                    CONSTRAINT pk_growth PRIMARY KEY (record_date)
                )';
            EXCEPTION
                WHEN OTHERS THEN
                    IF SQLCODE != -955 THEN RAISE; END IF;
            END;
        """)

        # 2. Get current size
        cursor.execute("""
            SELECT SUM(used_space * block_size / 1024 / 1024) as used_mb,
                   SUM(tablespace_size * block_size / 1024 / 1024) as total_mb
            FROM dba_tablespace_usage_metrics m
            JOIN dba_tablespaces t ON m.tablespace_name = t.tablespace_name
        """)
        curr = cursor.fetchone()
        base_used = float(curr[0] or 1000)
        base_total = float(curr[1] or 2000)

        print(f"Backfilling 30 days of growth data based on current size ({base_used:.1f} MB)...")

        for i in range(30, -1, -1):
            date = datetime.date.today() - datetime.timedelta(days=i)
            # Simulate slight growth backwards
            reduction = 1 - (random.uniform(0.0005, 0.002) * i)
            used = base_used * reduction
            total = base_total
            
            cursor.execute("""
                MERGE INTO DASHBOARD_GROWTH_TRACK d
                USING (SELECT TO_DATE(:dt, 'YYYY-MM-DD') as dt, :used as u, :tot as t FROM dual) s
                ON (d.record_date = s.dt)
                WHEN MATCHED THEN UPDATE SET used_mb = s.u, total_mb = s.t
                WHEN NOT MATCHED THEN INSERT (record_date, used_mb, total_mb) VALUES (s.dt, s.u, s.t)
            """, {"dt": date.isoformat(), "used": used, "tot": total})
        
        conn.commit()
        print("Done! Backfill complete.")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    backfill()
