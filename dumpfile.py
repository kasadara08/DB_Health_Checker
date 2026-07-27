# import json, sys
# import os
# from pathlib import Path

# BASE_DIR = Path(__file__).resolve().parent
# config_file = BASE_DIR / "config" / "email_config.json"
# test_cfg = {
#                     "smtp_server": cfg_server,
#                     "smtp_port": int(cfg_port),
#                     "smtp_username": cfg_username,
#                     "smtp_password": cfg_password,
#                     "from_email": cfg_from,
#                     "to_emails": [e.strip() for e in cfg_to.split(",") if e.strip()],
#                     "enabled": True
#                 }

# if not config_file.exists():
#     print(f"Config file not found: {config_file}")
# else:

#     with open(config_file, "w") as f:
#         json.dump(test_cfg, f, indent=4)
#         print(test_cfg)

#     with open(config_file, "r") as f:   
#         mail_cfg = json.load(f)
#         print(mail_cfg)


import json
import os
import sys
 
if getattr(sys, 'frozen', False):
    _base = os.path.dirname(sys.executable)
else:
    _base = os.path.dirname(os.path.abspath(__file__))
 
config_dir = os.path.join(_base, "config")
config_file = os.path.join(config_dir, "kas.json")
 
# Load JSON into mail_cfg
with open(config_file, "r") as f:
    mail_cfg = json.load(f)
 
# Print the whole configuration
print(mail_cfg)
 
# Access individual values
print(mail_cfg["smtp_server"])
print(mail_cfg["smtp_port"])
print(mail_cfg["smtp_username"])
print(mail_cfg["to_emails"])