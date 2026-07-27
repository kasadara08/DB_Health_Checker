from db_connection import get_api_connection, get_config_for_db
from utils.storage_provider import get_storage_provider

cfg = get_config_for_db("kasorcl") or {}
print("Registry config for kasorcl:", cfg)

conn, err = get_api_connection("kasorcl")
print("API connection err:", err)
print("conn.dsn:", getattr(conn, "dsn", None))

sp = get_storage_provider(conn)
vols = sp.get_storage_info()
print("Storage provider returned volumes count:", len(vols))
for v in vols:
    print(v)
