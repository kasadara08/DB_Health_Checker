import oracledb
import pandas as pd
import matplotlib.pyplot as plt
from db_connection import get_config_for_db, load_db_names_api

# 1. Load default database configuration
db_names = load_db_names_api()
if not db_names:
    print("No databases found in registry.")
    exit(1)

db_choice = db_names[0]
print(f"Connecting to database: {db_choice}")

cfg = get_config_for_db(db_choice)
if not cfg:
    print(f"Failed to load config for {db_choice}")
    exit(1)

# 2. Establish connection to remote database
try:
    from db_connection import get_oracle_mode
    mode = get_oracle_mode(cfg["user"])
    conn = oracledb.connect(
        user=cfg["user"],
        password=cfg["password"],
        dsn=cfg["dsn"],
        mode=mode,
        tcp_connect_timeout=5
    )
    print("Connected successfully!")
except Exception as e:
    print(f"Failed to connect: {e}")
    exit(1)

# 3. Retrieve df -h equivalent results from remote server via Java SP bridge
try:
    cursor = conn.cursor()
    
    # Deploy Java bridge if not already present
    from utils.storage_provider import StorageProvider
    provider = StorageProvider(conn)
    provider.deploy_java_bridge()
    
    # Query storage info string
    cursor.execute("SELECT get_host_storage_info() FROM dual")
    raw_res = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    
    if not raw_res or raw_res.startswith("ERR"):
        print(f"Failed to retrieve storage info from server: {raw_res}")
        exit(1)
        
except Exception as e:
    print(f"Database query failed: {e}")
    try:
        conn.close()
    except Exception:
        pass
    exit(1)

# 4. Parse custom Java response (mount|device|fstype|total|used|free|uuid|stype;)
rows = []
for item in raw_res.split(";"):
    item = item.strip()
    if not item:
        continue
    parts = item.split("|")
    if len(parts) < 6:
        continue
    
    mount_point = parts[0]
    device = parts[1]
    fs_type = parts[2]
    total_bytes = int(parts[3])
    used_bytes = int(parts[4])
    free_bytes = int(parts[5])
    
    # Convert to GB
    total_gb = total_bytes / (1024**3)
    used_gb = used_bytes / (1024**3)
    free_gb = free_bytes / (1024**3)
    pct = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0
    
    rows.append({
        "Mounted": mount_point,
        "Device": device,
        "Type": fs_type,
        "Total_GB": total_gb,
        "Used_GB": used_gb,
        "Free_GB": free_gb,
        "Use%": pct
    })

# 5. Create DataFrame
df = pd.DataFrame(rows)

# Print mount point information to console
print("\nMount Point Information (Retrieved from Server):")
print(df[["Mounted", "Device", "Type", "Total_GB", "Used_GB", "Free_GB", "Use%"]].to_string(index=False))

# 6. Create Pie Chart
plt.figure(figsize=(8, 8))
if df["Used_GB"].sum() > 0:
    plt.pie(df["Used_GB"], labels=df["Mounted"], autopct="%1.1f%%", startangle=140)
    plt.title(f"Used Space Distribution on {db_choice} Host Server")
    plt.tight_layout()
    plt.show()
else:
    print("\nNo used space recorded on volumes. Skipping pie chart display.")
