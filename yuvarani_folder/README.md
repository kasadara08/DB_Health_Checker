# DB Health Checker

A Streamlit-based monitoring portal for Oracle databases. It connects to one or more Oracle instances (production, reporting, and standby), reports health status, tablespace/session/backup metrics, and host-level OS resources (CPU, memory, disk, mounts, processes) over SSH — with background monitoring and email/Microsoft 365 alerting. It is packaged into a standalone Windows executable for client distribution.

## Tools Used

| Tool | Purpose |
|---|---|
| **Python 3** | Application language for the dashboard, monitoring daemon, and providers. |
| **Streamlit** | Web UI framework — renders the dashboard, forms, and pages (`app.py`, `dashboard/`). |
| **Oracle Instant Client (Thick mode)** | Bundled DLLs in `oracle_client/` so `oracledb` can connect using Oracle's native OCI driver (`oraociei19.dll`, `oci.dll`, etc.) without a separate Oracle install on the client machine. |
| **PyInstaller** | Bundles the Python app + dependencies + Oracle Client DLLs into a single `.exe`/directory (see `dash_manually.spec`, `BUILD_EXE_GUIDE.md`). |
| **Paramiko / SSH** | Remote command execution against DB hosts to pull OS-level metrics (mounts, CPU/RAM, top processes, ASM diskgroups) without installing an agent on the target servers. |
| **Git LFS** | Tracks large binary files (Oracle client DLLs, build output) — see `.gitattributes`. |

## Libraries & Packages (`requirements.txt`)

### Oracle connectivity
| Package | Used for |
|---|---|
| `oracledb` | Official Oracle Python driver. Used in Thick mode (via bundled Instant Client) in `db_connection.py`, `monitor_daemon.py`, `generate_db_status_log.py` to run SQL against production/reporting/standby databases. |

### Web dashboard (Streamlit + its runtime dependencies)
| Package | Used for |
|---|---|
| `streamlit` | Renders the entire UI (`app.py`, `dashboard/home.py`, `dashboard/monitoring.py`, `dashboard/mail_config.py`). |
| `watchdog`, `watchfiles` | File-system watchers Streamlit uses for hot-reload during development. |
| `tornado` | Async web server Streamlit runs on. |
| `click` | CLI parsing used by Streamlit's own command-line entry point (`streamlit.web.cli`, invoked from `run_dashboard.py`). |
| `blinker` | Signal/event dispatch used internally by Streamlit. |
| `cachetools` | Backing cache for Streamlit's `@st.cache_*` decorators. |
| `colorama` | Colored terminal output on Windows (Streamlit CLI logs). |
| `toml` | Parses `.streamlit/config.toml`. |
| `packaging` | Version parsing used by Streamlit internals. |
| `pillow` | Image handling for Streamlit's asset/image rendering. |
| `protobuf` | Wire format Streamlit uses for frontend/backend messages. |
| `pydeck`, `pyarrow`, `narwhals`, `altair` | Chart/dataframe backends Streamlit ships for its built-in chart elements. |

### Data processing
| Package | Used for |
|---|---|
| `pandas` | Core data structure for query results (`db_connection.py`, `queries/queries.py`) — every SQL result is returned as a DataFrame and rendered as tables/charts. |
| `python-dateutil`, `pytz`, `tzdata`, `six` | Date/timezone handling required by `pandas`. |
| `numpy` | Transitive dependency of `pandas`/`streamlit` (not imported directly in project code). |

### Charting
| Package | Used for |
|---|---|
| `plotly` | Interactive gauges/bar/line charts on the monitoring pages (`plotly.express`, `plotly.graph_objects` in `dashboard/home.py`, `dashboard/monitoring.py`). |
| `matplotlib`, `contourpy`, `cycler`, `fonttools`, `kiwisolver`, `pyparsing` | Pulled in as a Streamlit/plotly plotting dependency; not called directly in the project's own code. |

### SSH remote access
| Package | Used for |
|---|---|
| `paramiko` | Opens SSH sessions to Oracle DB hosts to run shell/SQL*Plus commands for OS metrics and ASM info (`utils/ssh_mount_provider.py`, `utils/ssh_process_provider.py`, `utils/ssh_resource_provider.py`, `utils/asm_provider.py`). |
| `bcrypt`, `cryptography`, `PyNaCl`, `cffi`, `pycparser` | Cryptographic primitives required by Paramiko for SSH key/cipher handling. |

### OS/process monitoring
| Package | Used for |
|---|---|
| `psutil` | Local process/resource inspection support alongside the SSH-based remote metrics. |

### Email / Microsoft 365 alerts
| Package | Used for |
|---|---|
| `requests`, `charset-normalizer`, `idna`, `certifi`, `urllib3` | HTTP support for outbound calls (transitively required by `msgraph-core`/`httpx`). |
| `msgraph-core` + `microsoft-kiota-*` | Microsoft Graph SDK — sends alert emails via Microsoft 365/Exchange Online as an alternative to SMTP (`utils/alerts.py`, guarded by a try/except so the app still runs if these aren't installed). |
| `azure-core`, `azure-identity`, `msal`, `msal-extensions`, `PyJWT` | Azure AD authentication the Graph SDK uses to obtain an access token. |
| `httpx`, `httpcore`, `h2`, `hpack`, `hyperframe`, `anyio`, `sniffio` | HTTP/2 client stack used internally by `msgraph-core`. |
| `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-semantic-conventions` | Tracing hooks required by the Graph SDK's instrumentation. |
| `std-uritemplate` | URL template expansion used by the Kiota-generated Graph client. |
| *(stdlib)* `smtplib`, `email.message` | Fallback plain-SMTP email sending path in `utils/alerts.py`. |

### Async/HTTP support (transitive)
| Package | Used for |
|---|---|
| `aiohttp`, `aiohappyeyeballs`, `aiosignal`, `async-timeout`, `frozenlist`, `multidict`, `propcache`, `yarl`, `h11` | Async HTTP internals required by `httpx`/`msgraph-core`. |

### Type system / misc
| Package | Used for |
|---|---|
| `typing_extensions`, `annotated-types`, `attrs`, `exceptiongroup` | Type-hinting/validation support required by the Microsoft Graph SDK's Pydantic models. |
| `greenlet`, `MarkupSafe`, `Jinja2`, `Werkzeug` | Transitive dependencies pulled in by Streamlit's toolchain. |

### Environment / config
| Package | Used for |
|---|---|
| `python-dotenv` | Loads `ORACLE_USER` / `ORACLE_PASS` / `ORACLE_DSN` and other secrets from `.env` (`db_connection.py`, `utils/alerts.py`). |

## Configuration Files

- **`.env`** — Oracle connection credentials (`ORACLE_USER`, `ORACLE_PASS`, `ORACLE_DSN`). Not committed with real values.
- **Registry `.txt` file** (per `NC_CONFIG_GUIDE.txt`) — one line per database, 20 whitespace-separated columns covering production, reporting, and standby connection details. Uploaded/configured from the dashboard on first run.
- **`config/*.json`** — persisted app state: alert history, email preferences, per-DB monitor cache, daemon status.
- **`.streamlit/config.toml`** — disables usage stats, sets headless server mode.
- **`keys/oci_key.pem`** — key used for host authentication where password auth isn't available.

## Running the App

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run_dashboard.py
```

The dashboard opens at `http://localhost:8501`.

## Building the Windows Executable

See `BUILD_EXE_GUIDE.md` for the full PyInstaller packaging steps (`dash_manually.spec` already bundles the Oracle Instant Client DLLs and Streamlit assets needed to run without a separate Python install on the target machine).
