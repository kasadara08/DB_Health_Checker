# Oracle DB Monitoring Dashboard (GreenWorld)

A Flask-based web dashboard for monitoring Oracle 19c database estates in real time: Production, Standby/DR, and Reporting databases, each with their own host OS, ASM, and mount-point metrics gathered over SSH. Includes RMAN backup tracking, session/process monitoring, archive log cleanup, and email (Microsoft 365 / Graph API) alerting. Ships as a source app or a packaged Windows executable (PyInstaller).

---

## Project Structure

```
project/
├── app.py                        # Flask app: routes, logging, auto-shutdown watchdog
├── deploy_pkg.py                 # Standalone script to (re)deploy the PL/SQL monitoring package
├── pkg_spec.sql / pkg_body.sql   # GREENWORLD_MONITOR_PKG source (also auto-deployed at runtime)
├── database_procedures.sql       # Reference copy of the monitoring procedures
├── requirements.txt              # Python dependencies
├── *.spec                        # PyInstaller build specs (Windows .exe packaging)
├── frontend/
│   └── index.html                # Main dashboard UI (served at /dashboard)
├── templates/
│   ├── index.html                # Home page template
│   └── backup_graph.html         # Backup graph page template
├── services/
│   ├── db_service.py             # Oracle queries: sessions, backups, memory, growth, processes, thick-client init, graceful_shutdown()
│   ├── dr_service.py             # Standby/DR + Reporting DB status, archive gap checks (SSH-tunneled thick-mode subprocess)
│   ├── ssh_service.py            # Pooled SSH connections (password, key-based, and Grid/ASM user)
│   ├── remote_process_service.py # Remote OS process listing per DB server (Oracle vs. all processes)
│   ├── mountpoint_service.py     # Remote (df -hT) and local mount-point/disk usage
│   ├── asm_service.py            # ASM disk group discovery via Grid Infrastructure over SSH
│   ├── archive_log_service.py    # Archive log usage + retention-based cleanup
│   ├── listener_service.py       # Oracle listener status (lsnrctl)
│   ├── os_service.py             # Local/remote OS info (CPU, memory, disks, top processes)
│   ├── email_service.py          # Microsoft 365 OAuth (MSAL/Graph) email alerts, history, settings
│   ├── config_service.py         # Multi-database CSV configuration loader/writer
│   ├── history_service.py        # Upload history for databases.csv (restore/delete past uploads)
│   └── package_definition.py     # PL/SQL source for GREENWORLD_MONITOR_PKG, used for auto-deploy
├── static/                       # CSS, JS, image assets
├── data/
│   ├── databases.csv             # Active DB connection details (one row per Production database)
│   └── *history/settings json    # Upload history, email settings/history (generated at runtime)
├── oracle_client/                # Bundled Oracle Instant Client (thick mode, for DR/standby access)
├── prerequisites/                # VC++ redistributable required by the bundled client
├── tests/                        # Test scripts
└── .venv-1/                      # Python virtual environment (not committed)
```

---

## Key Features

- **Multi-database, multi-role monitoring** — each `databases.csv` row can define a Production DB plus its paired Standby/DR, Reporting, and Standalone databases; the dashboard reports role-aware status for all of them.
- **Remote host visibility over SSH** — OS processes, mount points, and ASM disk groups are pulled from each database's actual host (and its Grid Infrastructure user), not just the local machine.
- **RMAN backup tracking** — latest backup, 7-day history, daily stats, and session logs.
- **Session & process monitoring** — active/idle/blocking sessions, top CPU/memory consumers, session kill, orphaned OS process detection.
- **Archive log management** — usage reporting and retention-based cleanup with an audit trail.
- **Email alerting** — Microsoft 365 OAuth2 (MSAL + Graph) or SMTP, with connection testing and send history.
- **Config upload history** — every `databases.csv` upload is retained and can be restored or deleted.
- **Auto-shutdown watchdog** — the server exits automatically once the dashboard tab is closed (heartbeat + explicit close signal), or via the manual Disconnect action; both paths run `graceful_shutdown()` to close Oracle/SSH pools first.
- **Self-deploying PL/SQL package** — `GREENWORLD_MONITOR_PKG` is checked and (re)deployed automatically on connect; `deploy_pkg.py` is available for manual deployment.
- **Windows packaging** — PyInstaller `.spec` files build a standalone `.exe` with the Oracle Instant Client and VC++ redistributable bundled.

---

## Prerequisites

- Python 3.8+
- Oracle Instant Client (Thick mode) — a copy is bundled under `oracle_client/`; `db_service.init_thick_client()` locates/loads it automatically
- Oracle 19c database(s) running and network-reachable
- `lsnrctl` available on the server PATH (for listener checks)
- SSH access (password or key-based) to each database host for OS/ASM/mount-point metrics
- A Microsoft Entra ID (Azure AD) app registration with Mail.Send permission, if using Microsoft 365 email alerts

---

## Installation

### 1. Clone the repository

### 2. Create and activate a virtual environment

```bash
python -m venv .venv-1

# Windows
.venv-1\Scripts\activate

# Linux / macOS
source .venv-1/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

**Core runtime dependencies:**
- `Flask` / `flask-cors` — web framework and CORS
- `oracledb` — Oracle database driver (python-oracledb, thick mode)
- `paramiko` (with `cryptography`, `bcrypt`, `PyNaCl`) — SSH to remote DB/Grid hosts
- `psutil` — local OS metrics
- `msal`, `msgraph-core`, `azure-identity` — Microsoft 365 OAuth email alerting
- `pyinstaller` (+ hooks, `pefile`) — only needed to build the Windows `.exe`

> The `csv` and `json` modules used for config and history storage are part of Python's standard library — no extra install needed.

---

## Database Configuration

Connections are stored in `data/databases.csv`, read via `csv.DictReader` (`services/config_service.py`). Each row is one Production database, optionally paired with its Standby/DR, Reporting, and Standalone counterparts.

### Columns

| Column group | Columns | Purpose |
|---|---|---|
| Production DB | `db_id`, `host`, `port`, `service_name`, `username`, `password` | Primary Oracle connection |
| Production OS | `os_user`, `os_password` | SSH login to the Production host (mount points, processes) |
| Reporting DB | `rep_db_id`, `rep_host`, `rep_port`, `rep_service_name`, `rep_username`, `rep_password`, `rep_os_user`, `rep_os_password` | Reporting database + its host |
| Standby/DR DB | `stby_db_id`, `stby_host`, `stby_port`, `stby_service_name`, `stby_username`, `stby_password`, `stby_os_user`, `stby_os_password` | Standby database + its host (accessed via an SSH tunnel through the Production host) |
| Standalone DB | `standalone_db_id`, `standalone_host`, `standalone_port`, `standalone_service_name`, `standalone_username`, `standalone_password`, `standalone_os_user`, `standalone_os_password` | A database with no DR pairing |
| Grid/ASM | `grid_user` | OS user used to query ASM disk groups on the Production host |

Any column can be left blank if that role doesn't apply to a given `db_id`.

### Example

```csv
db_id,host,port,service_name,username,password,os_user,os_password,rep_db_id,rep_host,rep_port,rep_service_name,rep_username,rep_password,rep_os_user,rep_os_password,stby_db_id,stby_host,stby_port,stby_service_name,stby_username,stby_password,stby_os_user,stby_os_password,standalone_db_id,standalone_host,standalone_port,standalone_service_name,standalone_username,standalone_password,standalone_os_user,standalone_os_password,grid_user
kasorcl,10.10.10.11,1521,kasorcl,sys,Welcome123,oracle,oracle123,orcl,192.168.14.109,1521,orcl,arthi,Arthi123,,,orcl,192.168.14.109,1521,orcl,arthi,Arthi123,,,,,,,,,,,
```

> **Security note:** All credentials (DB and OS) are stored in **plaintext** in `databases.csv`, and email OAuth secrets are only **base64-obfuscated** (`ENC:` prefix in `email_service.py`), not encrypted. Do not commit real credentials to version control — add `data/*.csv` and `data/email_settings.json` to `.gitignore`, and restrict filesystem access to this directory.

---

## Running the Application

```bash
python app.py
```

The app starts at `http://127.0.0.1:5000` by default and opens the dashboard in a browser tab.

| Page | URL |
|---|---|
| Home | `http://localhost:5000/` |
| Dashboard | `http://localhost:5000/dashboard` |
| Backup Graph | `http://localhost:5000/backup-graph` |

The server shuts itself down automatically when the dashboard tab is closed (or after 15 minutes of total silence as a fallback), and can also be stopped on demand via the Disconnect action (`/api/shutdown`). Both paths call `graceful_shutdown()` to close Oracle connection pools and SSH sessions before exiting.

---

## API Reference

### Config & Session

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/databases` | List configured databases |
| POST | `/api/set-database` | Set the active database (`{ "db_id": "..." }`) |
| POST | `/api/clear-database` | Clear active database selection |
| POST | `/api/delete-database` | Delete a database entry from the config |
| POST | `/api/upload-databases` | Upload a new `databases.csv` |
| GET | `/api/upload-history` | List past config uploads |
| POST | `/api/upload-history/restore` | Restore a previous upload |
| DELETE | `/api/upload-history/<upload_id>` | Delete a past upload record |
| GET | `/api/config-status` | Current config status (Uploaded / Refreshed) |

### Database & Backup

| Method | Endpoint | Description |
|---|---|---|
| GET | `/db-status` | Database connection status (UP/DOWN) |
| GET | `/tablespace-used` | Tablespace usage details |
| GET | `/api/db-growth` | Database size growth over time |
| GET | `/api/db-summary` | Full status summary, including Standby/DR and Reporting roles |
| GET | `/api/servers-metrics` | Combined metrics across all configured servers |
| GET | `/listener-status` | Oracle listener status |
| GET | `/api/rman/latest` | Latest backup details |
| GET | `/api/rman/history` | Backup history (last 7 days) |
| GET | `/api/rman/daily-stats` | Daily backup stats (for graphs) |
| GET | `/api/rman/sessions-chart` | Stacked bar + cumulative line chart data |
| GET | `/api/rman/session-log` | Detailed RMAN session log |

### Sessions & Memory

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/sessions/count` | Total active session count |
| GET | `/api/sessions/old-count` | Inactive/idle session count |
| GET | `/api/sessions/cpu-usage` | Top 10 active sessions by CPU usage |
| GET | `/api/sessions/cpu-usage-24h` | Top CPU sessions, 24h view |
| GET | `/api/sessions/memory-usage` | Top sessions by memory usage |
| GET | `/api/sessions/blocking` | Currently blocking sessions |
| POST | `/api/sessions/kill` | Kill a session (`sid`, `serial`) |
| GET | `/api/memory-info` | SGA and PGA memory usage |

### Archive Logs

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/archive-log-info` | Archive log usage |
| GET | `/api/archive-cleanup/info` | Retention/cleanup status |
| POST | `/api/archive-cleanup/run` | Run archive log cleanup |

### Processes, Mount Points & ASM (host-level, over SSH)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/oracle-processes` | Oracle OS processes for the active DB host |
| GET | `/api/server/all-oracle-processes` | Oracle OS processes across all configured hosts |
| GET | `/api/top-processes` | Top local CPU/memory processes |
| GET | `/api/server-processes` | All OS processes for a given DB host |
| GET | `/api/server/mount-points` | Mount points for the active DB host |
| GET | `/api/server/all-mount-points` | Mount points across all configured hosts |
| GET | `/api/server/all-asm-diskgroups` | ASM disk group usage across all hosts |
| GET | `/api/os-info` | Local/remote OS-level metrics (CPU, memory) |

### Email Alerting

| Method | Endpoint | Description |
|---|---|---|
| GET / POST | `/api/email/config`, `/api/email-settings` | Get/save email settings |
| POST | `/api/email/connect` | Authenticate via Microsoft OAuth |
| POST | `/api/email/disconnect` | Clear stored OAuth session |
| POST | `/api/email/test`, `/api/test-email` | Send a test email |
| POST | `/api/test-smtp` | Test SMTP connectivity |
| GET | `/api/email-history` | Sent email history |
| POST | `/api/clear-email-history` | Clear email history |

---

## What Each Service Does

| Service | Purpose |
|---|---|
| `db_service.py` | Core Oracle queries (sessions, tablespace, backup, memory, growth, processes); thick-client init; connection pooling; `graceful_shutdown()` |
| `dr_service.py` | Standby/DR and Reporting database status, archive gap checks, run via an SSH-tunneled thick-mode subprocess |
| `ssh_service.py` | Pooled SSH connections — password auth, private-key auth, and Grid/ASM user auth |
| `remote_process_service.py` | Lists remote OS processes per DB host, flags orphaned Oracle processes |
| `mountpoint_service.py` | Remote (`df -hT`) and local disk/mount-point usage |
| `asm_service.py` | Discovers ASM disk groups via the Grid Infrastructure user over SSH |
| `archive_log_service.py` | Archive log usage and retention-based cleanup with history |
| `listener_service.py` | Runs `lsnrctl status` and parses the result |
| `os_service.py` | Local/remote OS metrics — CPU, memory, disks, top processes |
| `email_service.py` | Microsoft 365 OAuth (MSAL/Graph) and SMTP email alerts, settings, and send history |
| `config_service.py` | Reads/writes `data/databases.csv`, derives Reporting-environment (ASTON/FIAT) grouping |
| `history_service.py` | Tracks and restores past `databases.csv` uploads |
| `package_definition.py` | PL/SQL source for `GREENWORLD_MONITOR_PKG`, auto-deployed on connect |

---

## Packaging as a Windows Executable

The repo includes PyInstaller `.spec` files (e.g. `DB16.52_Monitor_exa.spec`) that bundle the app, `oracle_client/` (Instant Client), and the VC++ redistributable into a standalone `.exe`. Build with:

```bash
pyinstaller DB16.52_Monitor_exa.spec
```

Output is written to `dist/`. At runtime, a frozen build places `data/`, `logs/`, and log files next to the `.exe` instead of next to `app.py`.
