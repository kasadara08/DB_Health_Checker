import os
import time
import streamlit as st

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
if 'selected_db' not in st.session_state:
    st.session_state.selected_db = None

# ================= LOAD ASSETS (CSS) =================
def load_css():
    """Load custom CSS styling and inject it into the Streamlit app supporting Light/Dark modes."""
    try:
        # Check local styles.css first, fallback to parent assets/style.css
        css_path = os.path.join(os.path.dirname(__file__), "styles.css")
        if not os.path.exists(css_path):
            css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "style.css")
            
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                css_data = f.read()
                
            if st.session_state.theme == "Dark":
                dark_override = """
                :root {
                    --bg-primary: #0a0f1d !important;
                    --bg-secondary: #161e2f !important;
                    --border-color: #2a354f !important;
                    --text-primary: #f1f5f9 !important;
                    --text-secondary: #94a3b8 !important;
                }
                .stApp, section[data-testid="stSidebar"] {
                    background-color: var(--bg-primary) !important;
                    color: var(--text-primary) !important;
                }
                div[data-testid="stMetricValue"] > div {
                    color: var(--text-primary) !important;
                }
                h1, h2, h3, h4, h5, h6, p, span, label, li {
                    color: var(--text-primary) !important;
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
                div[data-testid="stMarkdownContainer"] {
                    color: var(--text-primary) !important;
                }
                """
                css_data += dark_override
                
            st.markdown(f"<style>{css_data}</style>", unsafe_allow_html=True)
    except Exception as e:
        print(f"Error loading CSS file: {e}")

# Inject styles
load_css()

# Local imports
from home import render_home_page, load_db_names
from monitoring import render_dashboard, get_drive_details
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from queries.queries import get_db_status, get_listener_status, get_backup_status
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
    if st.sidebar.button("🏠 Portal Home", key="btn_nav_home", use_container_width=True):
        st.session_state.page = "home"
        st.session_state.selected_db = None
        st.session_state.force_config_screen = False
        st.session_state.session_config_done = True
        st.session_state.monitoring_started = True
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
    session_done = st.session_state.get("session_config_done", False)
    force_config = st.session_state.get("force_config_screen", False)

    if session_done and not force_config:
        st.sidebar.markdown("<br><p style='color: #9ca3af; font-size: 0.75rem; font-weight: 700; text-transform: uppercase;'>Database Connections</p>", unsafe_allow_html=True)
        
        # 3. Dynamic DB Selection list
        db_names = load_db_names()
    from dashboard.home import load_db_status_summary_cached, determine_health_category
    for db in db_names:
        # Highlight active database if selected
        is_active = (st.session_state.page == "monitoring" and st.session_state.selected_db == db)
        stats = load_db_status_summary_cached(db)
        cat = determine_health_category(stats)
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
            st.rerun()

# Render the sidebar navigation
render_sidebar()

# ================= PAGE ROUTING =================
def main():
    if st.session_state.page == "home":
        render_home_page()
    elif st.session_state.page == "monitoring":
        render_dashboard(st.session_state.selected_db)
        
        # Auto-refresh loop (sleep for 30s, then trigger rerun)
        time.sleep(30)
        st.rerun()

if __name__ == "__main__":
    main()
