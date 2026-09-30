# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [
    ('app.py', '.'),
    ('db_connection.py', '.'),
    ('monitor_thread.py', '.'),
    ('monitor_daemon.py', '.'),
    ('dashboard', 'dashboard'),
    ('queries', 'queries'),
    ('utils', 'utils'),
    ('config', 'config'),
    ('assets', 'assets'),
    ('keys', 'keys')
]
# Bundle Oracle Client DLLs so Thick mode (NNE) works on any machine without Oracle installed
import glob as _glob
import os as _os
_oci_dlls = _glob.glob('oracle_client/*.dll')
binaries = [(dll, 'oracle_client') for dll in _oci_dlls]
# Also bundle Microsoft Visual C++ Redistributable DLLs required by Oracle client
_msvc_dlls = ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll']
for _dll in _msvc_dlls:
    _dll_path = _os.path.join(r'C:\Windows\System32', _dll)
    if _os.path.exists(_dll_path):
        binaries += [(_dll_path, 'oracle_client')]
hiddenimports = ['streamlit.web.cli', 'db_connection', 'monitor_thread', 'monitor_daemon']
datas += copy_metadata('streamlit')
hiddenimports += collect_submodules('dashboard')
hiddenimports += collect_submodules('utils')
hiddenimports += collect_submodules('queries')
tmp_ret = collect_all('streamlit')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('oracledb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run_dashboard.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'scipy', 'sklearn', 'matplotlib', 'pytest', 'unittest', 'sphinx', 
        'IPython', 'jupyter', 'pandas.tests', 'numpy.tests', 'pyarrow.tests',
        'setuptools', 'pip', 'distutils', 'playwright', 'Faker', 'flask',
        'flask_cors', 'flask_login', 'flask_sqlalchemy', 'reportlab', 'openpyxl',
        'azure', 'msal', 'msgraph', 'kiota', 'pyarrow',
        # Streamlit-optional features this app never calls: st.connection("sql", ...)
        # and the native altair-backed charts (st.line_chart/bar_chart/area_chart/
        # altair_chart/vega_lite_chart) — this app only uses st.plotly_chart.
        'sqlalchemy', 'altair'
    ],
    noarchive=False,
    optimize=0,
)

# Filter out system Oracle client DLLs from the root _internal directory
# We only want MSVC dlls and oracle_client/ DLLs that we explicitly defined in binaries!
filtered_binaries = []
for dest_name, src_path, typecode in a.binaries:
    base_name = _os.path.basename(dest_name).lower()
    if base_name.startswith("ora") and not dest_name.lower().replace("\\", "/").startswith("oracle_client/"):
        continue
    filtered_binaries.append((dest_name, src_path, typecode))
a.binaries = filtered_binaries

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DB_Health_Portal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DB_Health_Portal_App',
)
