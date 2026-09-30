import os
import sys
import oracledb
import socket

# Add services folder to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.config_service import get_all_db_configs

def is_host_reachable(host, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        res = s.connect_ex((host, int(port)))
        s.close()
        return res == 0
    except:
        return False

def deploy():
    # Read spec and body
    spec_path = os.path.join(os.path.dirname(__file__), "pkg_spec.sql")
    body_path = os.path.join(os.path.dirname(__file__), "pkg_body.sql")
    
    if not os.path.exists(spec_path) or not os.path.exists(body_path):
        print("Error: pkg_spec.sql or pkg_body.sql not found in workspace.")
        return
        
    with open(spec_path, "r", encoding="utf-8") as f:
        spec_sql = f.read()
    with open(body_path, "r", encoding="utf-8") as f:
        body_sql = f.read()
        
    # Ensure CREATE OR REPLACE is present
    if not spec_sql.strip().upper().startswith("CREATE"):
        spec_sql = "CREATE OR REPLACE " + spec_sql
    if not body_sql.strip().upper().startswith("CREATE"):
        body_sql = "CREATE OR REPLACE " + body_sql
        
    configs = get_all_db_configs()
    print(f"Loaded {len(configs)} database configuration(s) for package deployment.")
    
    for cfg in configs:
        db_id = cfg['db_id']
        host = cfg['host']
        port = cfg.get('port', 1521)
        user = cfg['username']
        password = cfg['password']
        service = cfg['service_name']
        
        print(f"\n=========================================")
        print(f"Deploying to DB: {db_id} ({host}:{port}/{service})")
        print(f"=========================================")
        
        if not is_host_reachable(host, port):
            print(f"Skipping {db_id}: Host {host}:{port} is unreachable.")
            continue
            
        dsn = f"{host}:{port}/{service}"
        conn_kwargs = {"user": user, "password": password, "dsn": dsn}
        if user.lower() == 'sys':
            conn_kwargs["mode"] = oracledb.SYSDBA
            
        try:
            conn = oracledb.connect(**conn_kwargs)
            cursor = conn.cursor()
            
            print("Compiling Package Specification...")
            cursor.execute(spec_sql)
            print("Package Spec compiled.")
            
            print("Compiling Package Body...")
            cursor.execute(body_sql)
            print("Package Body compiled.")
            
            # Check compilation errors
            cursor.execute("""
                SELECT line, position, text FROM user_errors 
                WHERE name = 'GREENWORLD_MONITOR_PKG'
                ORDER BY sequence
            """)
            errors = cursor.fetchall()
            if errors:
                print("WARNING: Compilation Errors found:")
                for err in errors:
                    print(f"  Line {err[0]}, Pos {err[1]}: {err[2]}")
            else:
                print("SUCCESS: Package compiled with NO errors!")
                
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"ERROR: Deployment failed for {db_id}: {str(e)}")

if __name__ == '__main__':
    deploy()
