import socket
from flask import session
from services.config_service import get_db_config, get_all_db_configs

def check_listener():
    try:
        active_db_id = session.get('active_db_id')
        if not active_db_id:
            all_dbs = get_all_db_configs()
            if all_dbs:
                active_db_id = all_dbs[0].get('db_id')
        
        # If the database itself is currently connected, the listener must be running
        from services.db_service import check_db_connection
        if check_db_connection() == "Connected":
            return "Running"

        db_cfg = get_db_config(active_db_id)
        if not db_cfg:
            return "Error"

        host = db_cfg.get('host', 'localhost')
        port = int(db_cfg.get('port', 1521))

        # Perform a TCP ping to check if the port is open with a 2.5s timeout
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.5)
        result = s.connect_ex((host, port))
        s.close()
        
        if result == 0:
            return "Running"
        else:
            return "Stopped"
            
    except Exception as e:
        print("Listener Error:", e)
        return "Error"
