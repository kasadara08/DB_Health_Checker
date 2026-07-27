import pandas as pd
import oracledb
import datetime

DB_USER = "yuvarani"
DB_PASS = "welcome123"
DB_DSN  = "localhost:1521/ORCL"

def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)

def test():
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT record_date, used_mb, total_mb FROM DASHBOARD_GROWTH_TRACK ORDER BY record_date", conn)
        conn.close()
        
        print("DF Head:")
        print(df.head())
        print("DF Dtypes:")
        print(df.dtypes)
        
        df["record_date"] = pd.to_datetime(df["record_date"])
        
        freq = "D"
        df_res = df.resample(freq, on="record_date").mean().reset_index()
        print("Resampled Head:")
        print(df_res.head())
        
        df_res["size_gb"] = df_res["used_mb"] / 1024
        df_res["prev_size"] = df_res["size_gb"].shift(1)
        df_res["growth_pct"] = ((df_res["size_gb"] - df_res["prev_size"]) / df_res["prev_size"] * 100).fillna(0)
        
        result = []
        for _, row in df_res.iterrows():
            result.append({
                "date": row["record_date"].strftime("%Y-%m-%d"),
                "size_gb": round(float(row["size_gb"]), 3),
                "growth_pct": round(float(row["growth_pct"]), 2)
            })
        print("First result item:")
        print(result[0])
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
