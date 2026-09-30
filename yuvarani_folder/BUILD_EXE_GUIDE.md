# Guide: How to Package your Streamlit Dashboard into a Standalone Executable (.exe)

This guide outlines how to bundle this Streamlit application into a single executable or distributable desktop directory using **PyInstaller**. This allows your clients to launch the portal without installing Python, Streamlit, or other dependencies manually.

---

## 🛠️ Step-by-Step Packaging Instructions

### Step 1: Install PyInstaller
In your development command prompt/terminal, activate your virtual environment and install PyInstaller:
```bash
# Activate virtual environment
.\venv\Scripts\activate

# Install PyInstaller
pip install pyinstaller
```

### Step 2: Create the Launcher Entrypoint Script
Create a new file in your project root named **`launcher.py`** to start Streamlit programmatically inside PyInstaller's environment:
```python
import os
import sys
import streamlit.web.cli as stcli

if __name__ == '__main__':
    # Locate app.py relative to the executable
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(current_dir, "app.py")
    
    # Configure Streamlit arguments
    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=true"
    ]
    sys.exit(stcli.main())
```

### Step 3: Run PyInstaller to Bundle the App
To bundle the Streamlit app, run PyInstaller. Since Streamlit uses dynamic asset paths and HTML/CSS files, we compile it as a directory to make it load instantly (recommended) or as a single `.exe` file:

```bash
# Option A: Multi-file Directory (Recommended - Faster startup & fully functional)
pyinstaller --onedir --clean --name="DB_Health_Portal" --collect-all streamlit launcher.py

# Option B: Single File Executable (Creates one .exe file)
pyinstaller --onefile --clean --name="DB_Health_Portal" --collect-all streamlit launcher.py
```

### Step 4: Copy Static Web Assets (Important)
Once PyInstaller finishes compiling, it creates a `dist/DB_Health_Portal` folder. Streamlit requires the project directories (`assets`, `dashboard`, `queries`, `utils`, `db_connection.py`, `app.py`) to be in the folder:

Copy the following files/folders from your project root into the output directory (**`dist/DB_Health_Portal/`** or next to your generated `.exe` file):
* `app.py`
* `db_connection.py`
* `assets/` (custom styles)
* `dashboard/` (UI components)
* `queries/` (SQL queries)
* `utils/` (Storage engine providers)

### Step 5: Distribute to Client
Zip the final output folder (`dist/DB_Health_Portal/`) and send it to your client. 
* To start the dashboard, the client just double-clicks **`DB_Health_Portal.exe`**.
* The application will boot up and automatically open the portal in their default browser (Chrome, Edge, Safari, etc.) at `http://localhost:8501`.

---

## 💻 Cross-Platform Compatibility (Windows, macOS, Linux)

PyInstaller packages are **OS-specific**. 
* To create a `.exe` for **Windows**, compile it on a Windows computer.
* To create an app bundle for **macOS**, run the compile commands on a macOS machine.
* To create an executable for **Linux**, run them on a Linux machine.

The code itself is fully cross-platform and handles drive space allocation and paths automatically.

---

## ⚙️ Dynamic Registry File Upload / Path Config
When the client boots up the application on their desktop:
1. If the default notepad file `db_name_own.txt` is missing on their machine, the portal will immediately show a **Configuration Interface**.
2. They can **upload the `db_name_own.txt` file directly** in the web browser, OR **type/paste their local absolute path**.
3. Once they save it, the app registers the path locally and displays their databases. 
4. They can change or reset this path at any time using the **⚙️ Reset/Change Path** button in the sidebar.
