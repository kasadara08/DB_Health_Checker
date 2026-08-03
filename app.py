import os
import time
import streamlit as st
import db_connection as db
from db_connection import clear_active_registry, discard_pending_path

import monitor_thread

# ================= BACKGROUND MONITORING THREAD =================
# Start once per process — guarded internally, safe on every Streamlit rerun
try:
    monitor_thread.start_monitor_thread()
except Exception as _mt_err:
    print(f"[app] Monitor thread start error: {_mt_err}")

# ================= STARTUP REGISTRY CHECK =================
# Runs ONCE per Streamlit server-session (new WebSocket connection).
# On F5 refresh OR new tab → new session → session_config_done = False → show config.
# On back navigation from monitoring → SAME session → session_config_done stays True → show DB boxes.


_STARTUP_CHECKED_KEY = "_startup_registry_checked"  # defining the flag key name(defining the string )
if _STARTUP_CHECKED_KEY not in st.session_state: #checking if the flag is not present in the session state
    st.session_state[_STARTUP_CHECKED_KEY] = True  #set flag to True to indicate that the startup check has been performed
    st.session_state.session_config_done   = False   # Always show config on every new session
    st.session_state.monitoring_started    = False 
    try:
        from db_connection import startup_registry_check
        startup_registry_check()
    except Exception as _src_err:
        print(f"[app] startup_registry_check error: {_src_err}")


# ================= CONFIGURATION =================
st.set_page_config(
    page_title="DB Health Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ================= STATE MANAGEMENT =================
if 'theme' not in st.session_state:
    st.session_state.theme = "Light"
if 'page' not in st.session_state:
    st.session_state.page = "home"
if 'selected_db' not in st.session_state:  #it Stores the currently selected database name in selected_db
    st.session_state.selected_db = None

# ================= QUERY PARAMETER REDIRECTS =================
try:
    q_params = st.query_params  # st.query_params is the URL query string parameters from the browser
    
    # 1. Fresh Tab Load / Start session redirect handler
    if "fresh_session" in q_params:
        from db_connection import clear_active_registry, startup_registry_check
        try:
            startup_registry_check()
            clear_active_registry()
        except Exception as _e:
            print(f"[app] Error handling fresh session startup: {_e}")
        st.session_state.session_config_done = False
        st.session_state.monitoring_started  = False

        # Clear SMTP form fields so the sidebar shows blank on fresh tab open
        for _k in ["alert_enabled_cb","alert_server_txt","alert_port_num",
                   "alert_user_txt","alert_pass_txt","alert_from_txt","alert_to_txt"]:
            if _k in st.session_state:
                del st.session_state[_k]
        st.query_params.clear()
        st.rerun()

    # 2. Main Dashboard direct deep link handler
    if "selected_db" in q_params:  #it Stores the currently selected database name in selected_db and q_params URL query string parameters from the browser
        db = q_params["selected_db"]
        st.query_params.clear()
        st.session_state.page = "monitoring"
        st.session_state.selected_db = db
        # The user already completed configuration (they clicked a DB box on Portal Home),
        # so preserve these flags so Back navigation shows DB boxes, not config.
        st.session_state.session_config_done = True
        st.session_state.monitoring_started  = True
        st.session_state.selected_chk_db = None
        st.session_state.dialog_open = False
        if "_refresh_cycle_start" in st.session_state:
            del st.session_state["_refresh_cycle_start"]
        st.rerun()
except AttributeError:
    try:
        q_params = st.experimental_get_query_params()
        if "fresh_session" in q_params:
            from db_connection import clear_active_registry, startup_registry_check
            try:
                startup_registry_check()
                clear_active_registry()
            except Exception as _e:
                print(f"[app] Error handling fresh session startup: {_e}")
            st.session_state.session_config_done = False
            st.experimental_set_query_params()  # Clear query params
            st.rerun()

        if "selected_db" in q_params:
            db = q_params["selected_db"][0]
            st.experimental_set_query_params()  # Clear query params
            st.session_state.page = "monitoring"
            st.session_state.selected_db = db
            st.session_state.session_config_done = True
            st.session_state.monitoring_started  = True
            st.session_state.selected_chk_db = None
            st.session_state.dialog_open = False
            if "_refresh_cycle_start" in st.session_state:
                del st.session_state["_refresh_cycle_start"]
            st.rerun()
    except Exception:
        pass

# ================= LOAD ASSETS (CSS) =================
CSS_PATH = os.path.join(os.path.dirname(__file__), "assets", "style.css")

def load_css(): #Load custom CSS styling and inject it into the Streamlit app supporting Light/Dark modes.
    try:
        if os.path.exists(CSS_PATH):
            with open(CSS_PATH, "r") as f:
                css_data = f.read()
                
            if st.session_state.theme == "Dark":
                dark_override = """
                :root {
                    --bg-primary: #0a0f1d !important;
                    --bg-secondary: #161e2f !important;
                    --border-color: #2a354f !important;
                    --text-primary: #ffffff !important;
                    --text-secondary: #cbd5e1 !important;
                }
                .stApp, section[data-testid="stSidebar"] {
                    background-color: var(--bg-primary) !important;
                    color: var(--text-primary) !important;
                }
                div[data-testid="stMetricValue"] > div {
                    color: var(--text-primary) !important;
                }
                
                /* Universal text white override for dark mode */
                h1, h2, h3, h4, h5, h6, p, span, label, li, div, caption, small, th, td {
                    color: #ffffff !important;
                }
                
                /* Keep status/health colors intact */
                .val-green, .val-green *, .ts-pct-good, .state-green, .state-green * {
                    color: #10b981 !important;
                }
                .val-amber, .val-amber *, .val-warning, .val-warning *, .ts-pct-warning, .state-orange, .state-orange * {
                    color: #f59e0b !important;
                }
                .val-red, .val-red *, .ts-pct-critical, .state-red, .state-red * {
                    color: #ef4444 !important;
                }
                
                /* Sidebar specific text overrides */
                section[data-testid="stSidebar"] p, 
                section[data-testid="stSidebar"] span, 
                section[data-testid="stSidebar"] label, 
                section[data-testid="stSidebar"] h2, 
                section[data-testid="stSidebar"] h3 {
                    color: #ffffff !important;
                }
                
                /* Sidebar and Main Buttons styling for dark mode */
                button, .stButton > button, button[kind="secondary"] {
                    background-color: var(--bg-secondary) !important;
                    color: #ffffff !important;
                    border: 1px solid var(--border-color) !important;
                }
                button p, .stButton > button p {
                    color: #ffffff !important;
                }
                button:hover, .stButton > button:hover, button[kind="secondary"]:hover {
                    background-color: var(--bg-primary) !important;
                    border-color: var(--neon-blue) !important;
                    color: #ffffff !important;
                }
                button:hover p, .stButton > button:hover p {
                    color: #ffffff !important;
                }
                
                .tablespace-container, .status-card {
                    background-color: var(--bg-secondary) !important;
                    border-color: var(--border-color) !important;
                }
                div[data-testid="stForm"] {
                    background-color: var(--bg-secondary) !important;
                    border: 1px solid var(--border-color) !important;
                }
                hr {
                    border-top-color: var(--border-color) !important;
                }
                /* Streamlit default tabs theme overrides */
                button[data-baseweb="tab"] {
                    color: var(--text-secondary) !important;
                }
                button[data-baseweb="tab"][aria-selected="true"] {
                    color: var(--neon-blue) !important;
                    border-bottom-color: var(--neon-blue) !important;
                }
                .ts-bar-outer {
                    background: #2a354f !important;
                }
                /* Additional dark-theme specific overrides */
                div[data-testid="stMarkdownContainer"] {
                    color: var(--text-primary) !important;
                }
                /* Force all text-secondary (grey) to bright enough for dark bg */
                .db-status-row, .db-status-label, .val-grey {
                    color: #ffffff !important;
                }
                .db-card {
                    background: #161e2f !important;
                    border-color: #2a354f !important;
                }
                .db-card-name { color: #ffffff !important; }
                 
                 /* Selectboxes: keep text dark so readable on light widget background */
                 div[data-baseweb="select"] * {
                     color: #1e293b !important;
                 }
                 div[role="listbox"] * {
                     color: #1e293b !important;
                 }
                 /* BaseWeb dropdown popover — force white bg + dark text on ALL list items */
                 div[data-baseweb="popover"] {
                     background: #ffffff !important;
                 }
                 div[data-baseweb="popover"] * {
                     color: #1e293b !important;
                     background-color: transparent !important;
                 }
                 div[data-baseweb="menu"] {
                     background: #ffffff !important;
                 }
                 div[data-baseweb="menu"] li,
                 div[data-baseweb="menu"] [role="option"],
                 div[data-baseweb="menu"] span {
                     color: #1e293b !important;
                 }
                 div[data-baseweb="menu"] li:hover,
                 div[data-baseweb="menu"] [role="option"]:hover {
                     background-color: #e2e8f0 !important;
                     color: #0f172a !important;
                 }
                 /* Selected option text inside the closed dropdown control */
                 div[data-baseweb="select"] [data-testid="stSelectbox"] span,
                 div[data-baseweb="select"] div[class*="ValueContainer"] span,
                 div[data-baseweb="select"] div[class*="SingleValue"] {
                     color: #1e293b !important;
                 }
                 /* Multiselect tags */
                 div[data-baseweb="tag"] span {
                     color: #1e293b !important;
                 }
                 
                 /* DB Error msg container styling for dark mode */
                 .db-error-msg {
                     color: #ff8b8b !important;
                     background: rgba(239, 68, 68, 0.15) !important;
                     border: 1px solid rgba(239, 68, 68, 0.3) !important;
                 }

                 /* ── Dark Mode: Search / Text Input placeholder & value ── */
                 input[type="text"]::placeholder, input[type="search"]::placeholder {
                     color: #94a3b8 !important;
                     opacity: 1 !important;
                 }
                 div[data-testid="stTextInput"] input {
                     color: #ffffff !important;
                     background-color: #1e2d45 !important;
                     border-color: #2a354f !important;
                 }

                 /* ── Dark Mode: Checkbox labels ── */
                 div[data-testid="stCheckbox"] label p,
                 div[data-testid="stCheckbox"] span {
                     color: #e2e8f0 !important;
                 }

                 /* ── Dark Mode: Dataframe / Table ── */
                 div[data-testid="stDataFrame"] th,
                 div[data-testid="stDataFrame"] td,
                 div[data-testid="stDataFrame"] {
                     color: #ffffff !important;
                     border-color: #2a354f !important;
                 }
                 div[data-testid="stDataFrame"] thead th {
                     background-color: #1e2d45 !important;
                     color: #38bdf8 !important;
                     font-weight: 700 !important;
                 }
                 div[data-testid="stDataFrame"] tbody tr:nth-child(even) td {
                     background-color: rgba(255,255,255,0.03) !important;
                 }

                 /* ── Dark Mode: Caption / small info text ── */
                 div[data-testid="stCaptionContainer"] p,
                 .stCaption p {
                     color: #94a3b8 !important;
                 }

                 /* ── Dark Mode: Number Input ── */
                 div[data-testid="stNumberInput"] input {
                     color: #ffffff !important;
                     background-color: #1e2d45 !important;
                 }

                 /* ── Dark Mode: Metric labels ── */
                 div[data-testid="stMetricLabel"] p,
                 div[data-testid="stMetricLabel"] span {
                     color: #94a3b8 !important;
                 }
                 
                 /* ── Dark Mode: Info / Warning / Success boxes ── */
                 div[data-testid="stAlert"] {
                     background-color: #1e2d45 !important;
                     border-color: #2a354f !important;
                 }
                 div[data-testid="stAlert"] p,
                 div[data-testid="stAlert"] span {
                     color: #e2e8f0 !important;
                 }
                """
                css_data += dark_override
                
                # Add pulse-dot animation globally regardless of theme
            st.markdown(f"""<style>{css_data}
            @keyframes pulse-dot {{
                0%   {{ box-shadow: 0 0 0 0 rgba(239,68,68,0.5); }}
                70%  {{ box-shadow: 0 0 0 6px rgba(239,68,68,0); }}
                100% {{ box-shadow: 0 0 0 0 rgba(239,68,68,0); }}
            }}
            </style>""", unsafe_allow_html=True)
    except Exception as e:
        print(f"Error loading CSS file: {e}")

# Inject styles
load_css()

# Imports inside routing to avoid premature connection calls

from dashboard.home import render_home_page, load_db_names
from dashboard.monitoring import render_dashboard, get_drive_details
from queries.queries import get_db_status, get_listener_status, get_backup_status
from db_connection import get_txt_path
from utils.alerts import load_config as load_alert_config
import plotly.graph_objects as go

# ================= SIDEBAR MENU =================
def render_sidebar():
    """Renders the sidebar database menu with Theme toggler."""
    st.sidebar.markdown("""
        <div style="text-align: center; margin-bottom: 15px;">
            <h2 style="font-family: 'Space Grotesk', sans-serif; color: #00d2ff; margin-bottom: 0;">🧭 Portal</h2>
        </div>
        <hr style="margin: 10px 0; border-color: rgba(255,255,255,0.08);">
    """, unsafe_allow_html=True)
    
    # 1. Theme Toggle Radio Button
    st.sidebar.markdown("<p style='color: #9ca3af; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; margin-bottom: 2px;'>🌗 Display Mode</p>", unsafe_allow_html=True)
    theme_choice = st.sidebar.radio(
        "Theme selector",
        ["Light", "Dark"],
        index=0 if st.session_state.theme == "Light" else 1,
        label_visibility="collapsed"
    )
    if theme_choice != st.session_state.theme:
        st.session_state.theme = theme_choice
        st.rerun()
        
    st.sidebar.markdown("<hr style='margin: 10px 0; border-color: rgba(255,255,255,0.08);'>", unsafe_allow_html=True)
    
    # 2. Back to Home / Portal selection

    # 2. Main Sidebar Menu Routing Options
    if st.sidebar.button("🏠 Portal Home", key="btn_nav_home", use_container_width=True):
        if "_refresh_cycle_start" in st.session_state:
            del st.session_state["_refresh_cycle_start"]
        st.session_state.page = "home"
        st.session_state.selected_db = None
        st.session_state.force_config_screen = False
        st.session_state.session_config_done = True
        st.session_state.monitoring_started = True
        st.rerun()

    # Mail History button — always visible
    if st.sidebar.button("📬 Mail History", key="btn_nav_mail", use_container_width=True):
        st.session_state.page = "mail_history"
        st.session_state.selected_db = None
        st.rerun()


    st.sidebar.markdown("""
        <style>
        @keyframes btnZoomPulse {
            0% { transform: scale(1); }
            50% { transform: scale(1.03); }
            100% { transform: scale(1); }
        }

        /* 3-Inch Width (~270px) & 1-Inch Height (~48px) for All Sidebar Buttons */
        div[data-testid="stSidebar"] button,
        section[data-testid="stSidebar"] button {
            width: 270px !important;
            max-width: 270px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            display: block !important;
            font-size: 0.85rem !important;
            padding: 6px 12px !important;
            min-height: 48px !important;
            height: 48px !important;
            border-radius: 8px !important;
        }

        /* Catch-All Uniform Style for All Non-DB Sidebar Buttons (Portal Home, Mail History, Reset Active Path, Email Config) */
        .st-key-btn_nav_home button,
        .st-key-btn_nav_mail button,
        .st-key-btn_reset_path button,
        .st-key-btn_conf button,
        div[data-testid="stSidebar"] button:not([class*="3d"]) {
            width: 270px !important;
            max-width: 270px !important;
            min-height: 48px !important;
            height: 48px !important;
            border: 1px solid #cbd5e1 !important;
            border-color: #cbd5e1 !important;
            color: #0f172a !important;
            background: #ffffff !important;
            background-color: #ffffff !important;
            border-radius: 8px !important;
            font-size: 0.85rem !important;
            font-weight: 700 !important;
            outline: none !important;
            box-shadow: none !important;
        }

        .st-key-btn_nav_home button *,
        .st-key-btn_nav_mail button *,
        .st-key-btn_reset_path button *,
        .st-key-btn_conf button *,
        div[data-testid="stSidebar"] button:not([class*="3d"]) * {
            color: #0f172a !important;
            font-weight: 700 !important;
        }

        /* ALL STATES (hover, focus, active, focus-visible) — NO RED BORDER, BLACK TEXT ALWAYS */
        .st-key-btn_nav_home button:hover, .st-key-btn_nav_home button:focus, .st-key-btn_nav_home button:active, .st-key-btn_nav_home button:focus-visible,
        .st-key-btn_nav_mail button:hover, .st-key-btn_nav_mail button:focus, .st-key-btn_nav_mail button:active, .st-key-btn_nav_mail button:focus-visible,
        .st-key-btn_reset_path button:hover, .st-key-btn_reset_path button:focus, .st-key-btn_reset_path button:active, .st-key-btn_reset_path button:focus-visible,
        .st-key-btn_conf button:hover, .st-key-btn_conf button:focus, .st-key-btn_conf button:active, .st-key-btn_conf button:focus-visible,
        div[data-testid="stSidebar"] button:not([class*="3d"]):hover,
        div[data-testid="stSidebar"] button:not([class*="3d"]):focus,
        div[data-testid="stSidebar"] button:not([class*="3d"]):active,
        div[data-testid="stSidebar"] button:not([class*="3d"]):focus-visible {
            border: 1px solid #cbd5e1 !important;
            border-color: #cbd5e1 !important;
            color: #0f172a !important;
            background: #f1f5f9 !important;
            background-color: #f1f5f9 !important;
            outline: none !important;
            outline-width: 0 !important;
            box-shadow: none !important;
        }

        .st-key-btn_nav_home button:hover *, .st-key-btn_nav_home button:focus *, .st-key-btn_nav_home button:active *,
        .st-key-btn_nav_mail button:hover *, .st-key-btn_nav_mail button:focus *, .st-key-btn_nav_mail button:active *,
        .st-key-btn_reset_path button:hover *, .st-key-btn_reset_path button:focus *, .st-key-btn_reset_path button:active *,
        .st-key-btn_conf button:hover *, .st-key-btn_conf button:focus *, .st-key-btn_conf button:active *,
        div[data-testid="stSidebar"] button:not([class*="3d"]):hover * {
            color: #0f172a !important;
        }

        /* Minimize Previous Registries Dropdown Expander Box */
        div[data-testid="stSidebar"] div[data-testid="stExpander"] {
            border: 1px solid #cbd5e1 !important;
            border-radius: 6px !important;
            margin-top: 4px !important;
            margin-bottom: 4px !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stExpander"] summary {
            padding: 4px 8px !important;
            font-size: 0.75rem !important;
            min-height: 32px !important;
            height: 32px !important;
        }

        /* 3D Zoom In / Zoom Out Solid Filled Buttons for Database Connections */
        div[data-testid="stSidebar"] button[aria-label*="🟢"] {
            background: #10b981 !important;
            background-image: linear-gradient(180deg, #10b981 0%, #059669 100%) !important;
            color: #ffffff !important;
            border: 1px solid #047857 !important;
            box-shadow: 0 4px 10px rgba(16, 185, 129, 0.4), 0 3px 0 #047857 !important;
            font-weight: 800 !important;
            margin-bottom: 8px !important;
            animation: btnZoomPulse 3s ease-in-out infinite !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }
        div[data-testid="stSidebar"] button[aria-label*="🟢"]:hover {
            transform: scale(1.05) !important;
            box-shadow: 0 6px 14px rgba(16, 185, 129, 0.5), 0 4px 0 #047857 !important;
        }
        div[data-testid="stSidebar"] button[aria-label*="🟢"]:active {
            transform: translateY(2px) scale(0.98) !important;
            box-shadow: 0 1px 0 #047857, 0 2px 4px rgba(0,0,0,0.2) !important;
        }

        div[data-testid="stSidebar"] button[aria-label*="🟡"] {
            background: #f59e0b !important;
            background-image: linear-gradient(180deg, #f59e0b 0%, #d97706 100%) !important;
            color: #ffffff !important;
            border: 1px solid #b45309 !important;
            box-shadow: 0 4px 10px rgba(245, 158, 11, 0.4), 0 3px 0 #b45309 !important;
            font-weight: 800 !important;
            margin-bottom: 8px !important;
            animation: btnZoomPulse 2.5s ease-in-out infinite !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }
        div[data-testid="stSidebar"] button[aria-label*="🟡"]:hover {
            transform: scale(1.05) !important;
            box-shadow: 0 6px 14px rgba(245, 158, 11, 0.5), 0 4px 0 #b45309 !important;
        }
        div[data-testid="stSidebar"] button[aria-label*="🟡"]:active {
            transform: translateY(2px) scale(0.98) !important;
            box-shadow: 0 1px 0 #b45309, 0 2px 4px rgba(0,0,0,0.2) !important;
        }

        div[data-testid="stSidebar"] button[aria-label*="🔴"] {
            background: #ef4444 !important;
            background-image: linear-gradient(180deg, #ef4444 0%, #dc2626 100%) !important;
            color: #ffffff !important;
            border: 1px solid #b91c1c !important;
            box-shadow: 0 4px 10px rgba(239, 68, 68, 0.4), 0 3px 0 #b91c1c !important;
            font-weight: 800 !important;
            margin-bottom: 8px !important;
            animation: btnZoomPulse 2s ease-in-out infinite !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }
        div[data-testid="stSidebar"] button[aria-label*="🔴"]:hover {
            transform: scale(1.05) !important;
            box-shadow: 0 6px 14px rgba(239, 68, 68, 0.5), 0 4px 0 #b91c1c !important;
        }
        div[data-testid="stSidebar"] button[aria-label*="🔴"]:active {
            transform: translateY(2px) scale(0.98) !important;
            box-shadow: 0 1px 0 #b91c1c, 0 2px 4px rgba(0,0,0,0.2) !important;
        }
        </style>
    """, unsafe_allow_html=True)

    current_path = get_txt_path()
    session_done = st.session_state.get("session_config_done", False)
    force_config = st.session_state.get("force_config_screen", False)

    # Only show sidebar database connections when configuration setup is complete
    if current_path and session_done and not force_config:

        st.sidebar.markdown("<br><p style='color: #9ca3af; font-size: 0.75rem; font-weight: 700; text-transform: uppercase;'>Database Connections</p>", unsafe_allow_html=True)
        db_names = load_db_names()
        from dashboard.home import load_db_status_summary_cached, determine_health_category
        
        # Pre-evaluate categories and sort databases
        db_list = []
        for db in db_names:
            stats = load_db_status_summary_cached(db)
            cat = determine_health_category(stats)
            db_list.append({
                "name": db,
                "stats": stats,
                "cat": cat
            })
            
        def cat_sort_key(item):
            c = item["cat"]
            if c == "Critical":
                return 0
            elif c == "Warning":
                return 1
            else:
                return 2
                
        db_list.sort(key=cat_sort_key)
        
        for item in db_list:
            db = item["name"]
            stats = item["stats"]
            cat = item["cat"]
            is_active = (st.session_state.page == "monitoring" and st.session_state.selected_db == db)
            key_id = f"sidebar_nav_{db}"
            
            if cat == "Healthy":
                bg_color = "#10b981"
                gradient = "linear-gradient(180deg, #10b981 0%, #059669 100%)"
                border   = "#047857"
                shadow   = "0 4px 10px rgba(16, 185, 129, 0.45), 0 3px 0 #047857"
                icon     = "🟢"
            elif cat == "Warning":
                bg_color = "#f59e0b"
                gradient = "linear-gradient(180deg, #f59e0b 0%, #d97706 100%)"
                border   = "#b45309"
                shadow   = "0 4px 10px rgba(245, 158, 11, 0.45), 0 3px 0 #b45309"
                icon     = "🟡"
            else:
                bg_color = "#ef4444"
                gradient = "linear-gradient(180deg, #ef4444 0%, #dc2626 100%)"
                border   = "#b91c1c"
                shadow   = "0 4px 10px rgba(239, 68, 68, 0.45), 0 3px 0 #b91c1c"
                icon     = "🔴"

            st.sidebar.markdown(f"""
                <style>
                div[data-testid="stSidebar"] div.stButton:has(.st-key-{key_id}) > button,
                div[data-testid="stSidebar"] div[data-testid="stElementContainer"]:has(.st-key-{key_id}) button,
                div[data-testid="stSidebar"] .st-key-{key_id} button,
                .st-key-{key_id} button {{
                    background-color: {bg_color} !important;
                    background: {bg_color} !important;
                    background-image: {gradient} !important;
                    color: #ffffff !important;
                    border: 1px solid {border} !important;
                    box-shadow: {shadow} !important;
                    font-weight: 800 !important;
                    min-height: 48px !important;
                    height: 48px !important;
                    animation: btnZoomPulse 2.5s ease-in-out infinite !important;
                    transition: transform 0.2s ease, box-shadow 0.2s ease !important;
                }}
                div[data-testid="stSidebar"] .st-key-{key_id} button *,
                div[data-testid="stSidebar"] .st-key-{key_id} button p,
                div[data-testid="stSidebar"] .st-key-{key_id} button span {{
                    color: #ffffff !important;
                    font-weight: 800 !important;
                    font-size: 0.88rem !important;
                }}
                div[data-testid="stSidebar"] .st-key-{key_id} button:hover {{
                    transform: scale(1.05) !important;
                    box-shadow: 0 6px 14px {bg_color}88, 0 4px 0 {border} !important;
                }}
                div[data-testid="stSidebar"] .st-key-{key_id} button:active {{
                    transform: translateY(2px) scale(0.98) !important;
                    box-shadow: 0 1px 0 {border} !important;
                }}
                </style>
            """, unsafe_allow_html=True)

            btn_label = f"{icon} {db} (Active)" if is_active else f"{icon} {db}"
            
            if st.sidebar.button(btn_label, key=key_id, use_container_width=True):
                st.session_state.page = "monitoring"
                st.session_state.selected_db = db
                st.session_state.session_config_done = True
                st.session_state.monitoring_started  = True
                st.rerun()
                
        # 4. Previous registry configuration history
        st.sidebar.markdown("<hr style='margin: 10px 0; border-color: rgba(255,255,255,0.08);'>", unsafe_allow_html=True)
        from db_connection import get_registry_history, save_txt_path
        import hashlib
        history = get_registry_history()
        if history:
            with st.sidebar.expander("📁 Previous Registries", expanded=False):
                for hist_path in history:
                    filename = os.path.basename(hist_path)
                    is_active = (current_path and os.path.normpath(current_path) == os.path.normpath(hist_path))
                    btn_label = f"✅ {filename}" if is_active else f"📄 {filename}"
                    path_hash = hashlib.md5(hist_path.encode()).hexdigest()
                    if st.button(btn_label, key=f"hist_reg_{path_hash}", use_container_width=True):
                        save_txt_path(hist_path)
                        st.session_state.custom_txt_path = hist_path
                        if "status_cache" in st.session_state:
                            del st.session_state.status_cache
                        if "diag_result" in st.session_state:
                            del st.session_state.diag_result
                        st.session_state.force_config_screen = False
                        st.session_state.session_config_done = True
                        st.session_state.monitoring_started  = True
                        st.rerun()
            st.sidebar.markdown("<hr style='margin: 5px 0; border-color: rgba(255,255,255,0.08);'>", unsafe_allow_html=True)


#------------------------------------------------------ deleting parts----------------------------------------------------------
        st.sidebar.caption(f"Active Registry: `{os.path.basename(current_path)}`")
        if st.sidebar.button("⚠️ Reset Active Path", key="btn_reset_path", use_container_width=True):
  
            clear_active_registry()
            discard_pending_path()  
            if "custom_txt_path" in st.session_state:
                del st.session_state.custom_txt_path  #clearing any pending path that was set in registory.
            if "status_cache" in st.session_state:
                del st.session_state.status_cache # clearing any cached status data for the previous registry
            if "diag_result" in st.session_state:
                del st.session_state.diag_result  #clearing any cached diagnostic results for the previous registry

            # Clear all email widget keys so sidebar form resets to blank
            for _k in ["alert_enabled_cb", "alert_server_txt", "alert_port_num",
                       "alert_user_txt", "alert_pass_txt", "alert_from_txt", "alert_to_txt"]:
                if _k in st.session_state:
                    del st.session_state[_k]
            st.session_state.monitoring_started   = False
            st.session_state.session_config_done  = False   # ← force config screen
            st.session_state.force_config_screen  = True
            st.session_state.page = "home"
            st.rerun()

    # 5. Email Alerts configuration expander
    st.sidebar.markdown("<hr style='margin: 10px 0; border-color: rgba(255,255,255,0.08);'>", unsafe_allow_html=True)
    st.sidebar.markdown("<p style='color: #9ca3af; font-size: 0.75rem; font-weight: 700; text-transform: uppercase;'>🔔 Alert Notifications</p>", unsafe_allow_html=True)

    # ── Show unseen email notifications as toast popups ──
    try:
        import json as _json
        _notif_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "email_notifications.json"
        )
        if os.path.exists(_notif_file):
            with open(_notif_file, "r") as _nf:
                _notifs = _json.load(_nf)
            unseen = [n for n in _notifs if not n.get("seen")]
            if unseen:
                for n in unseen:
                    if n["type"] == "error":
                        st.toast(
                            f"❌ **Email Failed**\n\n**{n['subject']}**\n\n{n['message']}",
                            icon="🚨"
                        )
                    else:
                        st.toast(
                            f"✅ **Email Sent**\n\n**{n['subject']}**\n\n{n['message']}",
                            icon="📧"
                        )
                    n["seen"] = True
                with open(_notif_file, "w") as _nf:
                    _json.dump(_notifs, _nf, indent=2)
    except Exception:
        pass

    if st.sidebar.button("⚙️ Email Configuration", key="btn_conf", use_container_width=True):
        st.session_state.page = "mail_config"
        st.rerun()

# Render the sidebar navigation
render_sidebar()

# ================= MAIL HISTORY PAGE =================
def render_mail_history():
    """Renders the full email alert history page."""
    import json as _json

    # ── Mail History Credentials Form ──
    if not st.session_state.get("mail_history_authenticated", False):
        cfg = load_alert_config()
        configured_user = cfg.get("from_email", "").strip()
        configured_pass = cfg.get("smtp_password", "").strip()

        st.markdown("""
            <div style='background: linear-gradient(135deg, #1e293b, #0f172a); border: 1px solid #334155; border-radius: 12px; padding: 24px; max-width: 480px; margin: 40px auto;'>
                <h3 style='margin-top:0; color:#38bdf8; text-align:center;'>🔑 Access Mail History</h3>
                <p style='font-size:0.85rem; color:#94a3b8; text-align:center; margin-bottom:20px;'>
                    Please log in with the configured Sender Email and SMTP Password.
                </p>
            </div>
        """, unsafe_allow_html=True)

        if not configured_user or not configured_pass:
            st.warning("⚠️ **SMTP Configuration Missing:** Please set the Sender Email and SMTP Password in the sidebar's **Configure Email Alerts** panel first to set your access credentials.")
            return

        col_left, col_mid, col_right = st.columns([1, 2, 1])
        with col_mid:
            u_input = st.text_input("Sender Email", key="mh_login_user", placeholder="e.g. alerts@company.com")
            p_input = st.text_input("SMTP Password", type="password", key="mh_login_pass", placeholder="Enter SMTP password")
            if st.button("🔑 Access History", type="primary", use_container_width=True):
                if u_input.strip() == configured_user and p_input.strip() == configured_pass:
                    st.session_state.mail_history_authenticated = True
                    st.success("Access Granted!")
                    st.rerun()
                else:
                    st.error("Invalid Email or SMTP Password. Please try again.")
        return

    # Add Logout option at top right
    col_t1, col_t2 = st.columns([5, 1])
    with col_t2:
        if st.button("🔓 Log Out", key="mh_logout_btn", use_container_width=True):
            st.session_state.mail_history_authenticated = False
            st.rerun()

    st.markdown("""
        <style>
        .mh-header{background:linear-gradient(135deg,#1e3a5f,#1e293b);border-radius:12px;
            padding:1.2rem 1.8rem;margin-bottom:1.2rem;}
        .mh-header h2{color:#38bdf8;margin:0;font-size:1.5rem;font-family:'Space Grotesk',sans-serif;}
        .mh-header p{color:#94a3b8;margin:4px 0 0;font-size:0.82rem;}
        .mh-badge-sent{display:inline-block;padding:2px 10px;border-radius:999px;
            background:#d1fae5;color:#065f46;font-size:0.7rem;font-weight:700;}
        .mh-badge-failed{display:inline-block;padding:2px 10px;border-radius:999px;
            background:#fee2e2;color:#991b1b;font-size:0.7rem;font-weight:700;}
        .mh-badge-disabled{display:inline-block;padding:2px 10px;border-radius:999px;
            background:#f3f4f6;color:#6b7280;font-size:0.7rem;font-weight:700;}
        .mh-card{border:1px solid #e5e7eb;border-radius:10px;padding:10px 14px;
            margin-bottom:8px;background:#ffffff;transition:box-shadow 0.2s;}
        .mh-card:hover{box-shadow:0 4px 12px rgba(0,0,0,0.08);}
        .mh-card-err{border-left:3px solid #ef4444;}
        .mh-card-ok{border-left:3px solid #10b981;}
        .mh-card-dis{border-left:3px solid #9ca3af;}
        .mh-ts{font-size:0.68rem;color:#9ca3af;}
        .mh-subject{font-size:0.88rem;font-weight:700;color:#1e293b;}
        .mh-meta{font-size:0.7rem;color:#64748b;margin-top:2px;}
        .mh-error{font-size:0.68rem;color:#991b1b;background:#fef2f2;border-radius:4px;
            padding:4px 8px;margin-top:4px;word-break:break-all;}
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="mh-header">
            <h2>📬 Email Alert History</h2>
            <p>Complete log of all alert emails — sent, failed, and suppressed</p>
        </div>
    """, unsafe_allow_html=True)

    # Load history from JSON file
    _base = os.path.dirname(os.path.abspath(__file__))
    history_file = os.path.join(_base, "config", "email_history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as hf:
                history = _json.load(hf)
        except Exception:
            history = []

    # Also parse historical records from alerts_log.txt (for records before JSON tracking)
    log_file = os.path.join(_base, "alerts_log.txt")
    legacy_records = []
    if os.path.exists(log_file) and not history:
        try:
            with open(log_file, "r") as lf:
                content = lf.read()
            blocks = content.split("-----------------------")
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                lines = block.splitlines()
                rec = {"timestamp": "", "subject": "", "db_name": "",
                       "server": "", "to": [], "status": "sent", "error": "",
                       "alert_type": "General", "body_preview": ""}
                body_lines = []
                for ln in lines:
                    ln = ln.strip()
                    if ln.startswith("Date:"):
                        raw_ts = ln.replace("Date:", "").strip()[:19]
                        rec["timestamp"] = raw_ts
                    elif ln.startswith("Subject:"):
                        rec["subject"] = ln.replace("Subject:", "").strip()
                    elif ln.lower().startswith("database name"):
                        rec["db_name"] = ln.split(":", 1)[-1].strip()
                    elif ln.lower().startswith("server name"):
                        rec["server"] = ln.split(":", 1)[-1].strip()
                    elif ln.startswith("SMTP") or ln.startswith("Auth"):
                        rec["status"] = "failed"
                        rec["error"] = ln
                    else:
                        body_lines.append(ln)
                rec["body_preview"] = " | ".join(body_lines)[:200]
                for kw in ("Database Down", "Listener Down", "Tablespace", "Archive Log",
                           "Mount Point", "ORA Error", "Deadlock", "Blocking Session", "Test Alert"):
                    if kw.lower() in rec["subject"].lower():
                        rec["alert_type"] = kw
                        break
                if rec["timestamp"]:
                    legacy_records.append(rec)
            history = list(reversed(legacy_records))
        except Exception:
            pass

    if not history:
        st.info("📭 No email history found yet. Alerts will appear here once monitoring starts sending emails.")
        return

    # Reverse so newest first
    history_rev = list(reversed(history))
    total = len(history_rev)
    sent_count    = sum(1 for r in history_rev if r.get("status") == "sent")
    failed_count  = sum(1 for r in history_rev if r.get("status") == "failed")
    disabled_count = sum(1 for r in history_rev if r.get("status") == "disabled")

    # KPI row
    kc1, kc2, kc3, kc4 = st.columns(4)
    kc1.metric("Total Alerts", total)
    kc2.metric("✅ Sent", sent_count)
    kc3.metric("❌ Failed", failed_count)
    kc4.metric("⏸ Disabled", disabled_count)

    st.markdown("<hr style='margin:10px 0;border-color:#f0f0f0;'>", unsafe_allow_html=True)

    # Filters row
    fc1, fc2, fc3 = st.columns([1.2, 1.2, 1.6])
    with fc1:
        filter_status = st.selectbox("Filter by Status",
            ["All", "✅ Sent", "❌ Failed", "⏸ Disabled"],
            label_visibility="collapsed")
    with fc2:
        filter_type = st.selectbox("Filter by Alert Type",
            ["All Types", "Database Down", "Listener Down", "Tablespace",
             "Archive Log", "Mount Point", "ORA Error", "Deadlock",
             "Blocking Session", "Test Alert", "General"],
            label_visibility="collapsed")
    with fc3:
        search_db = st.text_input("🔍 Search by DB / Server name...",
                                  placeholder="Type to filter...", label_visibility="collapsed")

    # Apply filters
    filtered = history_rev
    if filter_status == "✅ Sent":
        filtered = [r for r in filtered if r.get("status") == "sent"]
    elif filter_status == "❌ Failed":
        filtered = [r for r in filtered if r.get("status") == "failed"]
    elif filter_status == "⏸ Disabled":
        filtered = [r for r in filtered if r.get("status") == "disabled"]

    if filter_type != "All Types":
        filtered = [r for r in filtered if filter_type.lower() in r.get("alert_type", "").lower()
                    or filter_type.lower() in r.get("subject", "").lower()]

    if search_db:
        q = search_db.lower()
        filtered = [r for r in filtered
                    if q in r.get("db_name", "").lower() or q in r.get("server", "").lower()
                    or q in r.get("subject", "").lower()]

    st.caption(f"Showing {len(filtered)} of {total} records")

    if not filtered:
        st.info("No records match the current filter.")
        return

    # Render records as styled cards
    for i, rec in enumerate(filtered[:100]):  # cap at 100 for performance
        status  = rec.get("status", "sent")
        subject = rec.get("subject", "(no subject)")
        ts      = rec.get("timestamp", "")
        db_name = rec.get("db_name", "—")
        server  = rec.get("server", "—")
        to_list = rec.get("to", [])
        to_str  = ", ".join(to_list) if to_list else "—"
        error   = rec.get("error", "")
        preview = rec.get("body_preview", "")
        atype   = rec.get("alert_type", "General")

        if status == "sent":
            badge  = '<span class="mh-badge-sent">✅ SENT</span>'
            card_c = "mh-card mh-card-ok"
            icon   = "✅"
        elif status == "failed":
            badge  = '<span class="mh-badge-failed">❌ FAILED</span>'
            card_c = "mh-card mh-card-err"
            icon   = "❌"
        else:
            badge  = '<span class="mh-badge-disabled">⏸ DISABLED</span>'
            card_c = "mh-card mh-card-dis"
            icon   = "⏸"

        # Type icon
        type_icons = {
            "Database Down": "🔴", "Listener Down": "🔴", "Tablespace": "🟡",
            "Archive Log": "🟡", "Mount Point": "🟡", "ORA Error": "🔴",
            "Deadlock": "🔴", "Blocking Session": "🟡", "Test Alert": "🧪"
        }
        type_icon = type_icons.get(atype, "📧")

        card_html = f"""
        <div class="{card_c}">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                    <div class="mh-subject">{type_icon} {subject}</div>
                    <div class="mh-meta">
                        🖥️ Server: <b>{server}</b> &nbsp;|
                        🗄️ DB: <b>{db_name}</b> &nbsp;|
                        📨 To: {to_str}
                    </div>
                </div>
                <div style="text-align:right;flex-shrink:0;margin-left:12px;">
                    {badge}<br>
                    <span class="mh-ts">{ts}</span>
                </div>
            </div>
            {f'<div class="mh-error">⚠ {error}</div>' if error else ''}
        </div>"""

        st.markdown(card_html, unsafe_allow_html=True)

        # Optional: detail expand for body preview
        if preview:
            with st.expander(f"📄 View Details — {subject[:60]}", expanded=False):
                st.code(preview.replace(" | ", "\n"), language=None)

    if len(filtered) > 100:
        st.caption(f"⚠ Showing first 100 of {len(filtered)} records. Use filters to narrow down.")

    # Clear History button
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🗑️ Clear All History", key="clear_mail_hist", type="secondary"):
        try:
            with open(history_file, "w") as hf:
                _json.dump([], hf)
            st.success("History cleared.")
            st.rerun()
        except Exception as ex:
            st.error(f"Could not clear: {ex}")


# ================= PAGE ROUTING =================
def main():
    # Session state handles all navigation logic:
    #   - New session (F5 / new tab / server restart) → _STARTUP_CHECKED_KEY missing
    #     → startup block sets session_config_done = False → config screen shows
    #   - Back nav from monitoring (same session) → session_config_done stays True
    #     → DB boxes show instantly from cache

    if st.session_state.page == "home":
        # Reset the refresh cycle timer whenever user returns to home
        if "_refresh_cycle_start" in st.session_state:
            del st.session_state["_refresh_cycle_start"]
        render_home_page()
    elif st.session_state.page == "monitoring":
        # If navigating to monitoring fresh (no cycle running), start a new cycle
        if "_refresh_cycle_start" not in st.session_state:
            st.session_state._refresh_cycle_start = time.time()
        render_dashboard(st.session_state.selected_db)
    elif st.session_state.page == "mail_history":
        render_mail_history()
    elif st.session_state.page == "mail_config":
        from dashboard.mail_config import render_mail_config_page
        render_mail_config_page()

if __name__ == "__main__":
    main()

