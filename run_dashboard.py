import os
import sys
import socket
import webbrowser
import time
import streamlit.web.cli as stcli

# === Explicit Imports for PyInstaller ===
# By placing these here, PyInstaller statically analyzes and bundles them.
import monitor_thread
import db_connection
import dashboard.home
import dashboard.monitoring
import queries.queries
import utils.alerts
import utils.storage_provider
import utils.ssh_mount_provider

PORT = 8501

def is_port_in_use(port: int) -> bool:
    """Check if a given port is already listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0

def open_browser(port: int):
    """Open Firefox (or default browser) to the dashboard URL."""
    url = f"http://127.0.0.1:{port}"
    ff_paths = [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
    ]
    import subprocess
    for path in ff_paths:
        if os.path.exists(path):
            subprocess.Popen([path, url])
            return
    # Fallback to system default browser
    webbrowser.open(url)

if __name__ == "__main__":
    # ── If dashboard is already running, just open Firefox ──────────────
    if is_port_in_use(PORT):
        print(f"Dashboard already running on port {PORT}. Opening browser...")
        open_browser(PORT)
        sys.exit(0)

    # ── Determine base directory ─────────────────────────────────────────
    if getattr(sys, "frozen", False):
        current_dir = sys._MEIPASS
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))

    app_path = os.path.join(current_dir, "app.py")

    # Allow external app.py override when frozen
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        external = os.path.join(exe_dir, "app.py")
        if os.path.exists(external):
            app_path = external

    # ── Write force_refresh flag so the monitor thread runs immediately ──
    try:
        config_dir = os.path.join(current_dir, "config")
        if getattr(sys, "frozen", False):
            config_dir = os.path.join(os.path.dirname(sys.executable), "config")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "force_refresh.flag"), "w") as frf:
            frf.write("1")
    except Exception:
        pass

    # ── Configure Streamlit ──────────────────────────────────────────────
    sys.argv = [
        "streamlit", "run", app_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        f"--server.port={PORT}",
        "--server.address=127.0.0.1"
    ]

    # Open browser after short delay
    import threading
    def _open():
        time.sleep(3)
        open_browser(PORT)
    threading.Thread(target=_open, daemon=True).start()

    # ── Start Streamlit (blocking) ───────────────────────────────────────
    sys.exit(stcli.main())