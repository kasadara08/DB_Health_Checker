import streamlit as st
import streamlit.components.v1 as components
import requests
import time

# ================= CONFIGURATION =================
st.set_page_config(
    page_title="DB Health Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

BASE_URL = "http://127.0.0.1:8000"

# ================= CUSTOM CSS (LIGHT THEME & INLINE LAYOUT) =================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Card Styling (Light Theme) */
    .dashboard-card {
        background-color: #ffffff;
        border-radius: 6px;
        padding: 5px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
        border: 1px solid #e5e7eb;
        text-align: center;
        transition: all 0.3s ease;
        
        /* Tiny Responsive Square Layout */
        width: 100%;
        max-width: 100px;
        aspect-ratio: 1 / 1;
        margin: 0 auto 10px auto;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    
    .dashboard-card:hover {
        box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
        border-color: #3b82f6;
    }

    .card-title {
        color: #000;
        font-size: 0.6rem;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 2px;
    }

    .card-value {
        font-size: 1.4rem;
        font-weight: 800;
        margin-bottom: 0;
    }

    /* Status Colors */
    .status-up { color: #10b981; }
    .status-down { color: #ef4444; }
    .status-warning { color: #f59e0b; }

    /* Animations */
    @keyframes pulse-green {
        0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.5); }
        70% { box-shadow: 0 0 0 15px rgba(16, 185, 129, 0); }
        100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
    }

    @keyframes pulse-red {
        0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.5); }
        70% { box-shadow: 0 0 0 15px rgba(239, 68, 68, 0); }
        100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
    }

    @keyframes pulse-yellow {
        0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.5); }
        70% { box-shadow: 0 0 0 15px rgba(245, 158, 11, 0); }
        100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
    }

    .box-anim-up {
        border: 2px solid #10b981;
        animation: pulse-green 2s infinite;
    }

    .box-anim-warning {
        border: 2px solid #f59e0b;
        animation: pulse-yellow 2s infinite;
    }

    @keyframes float {
        0% { transform: translateY(0px); }
        50% { transform: translateY(-3px); }
        100% { transform: translateY(0px); }
    }

    .dynamic-icon {
        animation: float 2s ease-in-out infinite;
        display: flex;
        justify-content: center;
        align-items: center;
    }

    .box-anim-warning {
        border: 2px solid #f59e0b;
        animation: pulse-yellow 2s infinite;
    }

    /* Tablespace Section */
    .ts-container {
        background-color: #f9fafb;
        border-radius: 12px;
        padding: 24px;
        margin-top: 20px;
        border: 1px solid #e5e7eb;
    }

    /* Inline layout for Name and Bar */
    .ts-row {
        display: flex;
        align-items: center;
        margin-bottom: 12px;
        gap: 20px;
    }

    .ts-name {
        width: 200px; /* Fixed width for names to align bars */
        font-weight: 600;
        color: #374151;
        font-size: 0.95rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .ts-bar-bg {
        width: 15%; /* User requested 15% width */
        height: 28px;
        background-color: #e5e7eb;
        border-radius: 6px;
        overflow: hidden;
        display: flex;
        position: relative;
        border: 1px solid #d1d5db;
    }

    .ts-bar-used {
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        color: white;
        font-size: 0.75rem;
        transition: width 0.6s ease;
    }

    .ts-bar-free {
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 600;
        color: #6b7280;
        font-size: 0.75rem;
    }

    .ts-meta {
        font-size: 0.85rem;
        color: #9ca3af;
        margin-left: 10px;
    }

    /* Refresh Button Styling */
    .stButton>button {
        background-color: #3b82f6;
        color: white !important;
        border-radius: 8px;
        padding: 6px 20px;
        border: none;
        font-weight: 600;
        box-shadow: 0 2px 4px rgba(59, 130, 246, 0.3);
    }
    
    .stButton>button:hover {
        background-color: #2563eb;
        box-shadow: 0 4px 8px rgba(37, 99, 235, 0.4);
    }

    /* Hide default Streamlit headers */
    header {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ================= STATE MANAGEMENT =================
if 'page' not in st.session_state:
    st.session_state.page = 'selection'
if 'selected_db' not in st.session_state:
    st.session_state.selected_db = None

def navigate_to(page, db=None):
    st.session_state.page = page
    st.session_state.selected_db = db
    st.rerun()

# ================= DATA FETCHING =================
def fetch_status(endpoint):
    params = {}
    if st.session_state.selected_db:
        params["db"] = st.session_state.selected_db
    
    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception:
        return {"error": "API Down"}
    return {"error": "Unknown error"}

def fetch_databases():
    try:
        response = requests.get(f"{BASE_URL}/databases", timeout=5)
        if response.status_code == 200:
            return response.json().get("databases", [])
    except Exception:
        return []
    return []

# ================= UI COMPONENTS =================
def render_status_card(title, state):
    # state: "up", "down", "warning"
    if state == "up":
        status_class = "status-up"
        box_anim = "box-anim-up"
        icon_svg = '<svg width="35" height="35" viewBox="0 0 24 24" fill="currentColor"><path d="M12 4l-9 9h6v7h6v-7h6z"/></svg>'
    elif state == "warning":
        status_class = "status-warning"
        box_anim = "box-anim-warning"
        icon_svg = '<svg width="35" height="35" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>'
    else:
        status_class = "status-down"
        box_anim = "box-anim-down"
        icon_svg = '<svg width="35" height="35" viewBox="0 0 24 24" fill="currentColor"><path d="M12 20l9-9h-6v-7h-6v7h-6z"/></svg>'

    st.markdown(f"""
        <div class="dashboard-card {box_anim}">
            <div class="card-title">{title}</div>
            <div class="card-value {status_class} dynamic-icon" style="margin-top: 5px;">{icon_svg}</div>
        </div>
    """, unsafe_allow_html=True)

def render_session_card(title, active, inactive, total):
    sess_html = f"<div class='dashboard-card' style='border: 2px solid #9d50bb; background: #fff; height: 100px; max-height: 100px; padding: 2px; overflow: hidden;'><div class='card-title' style='margin-bottom: 0px; color: #000; font-size: 0.6rem;'>{title}</div><div style='display: flex; flex-direction: column; align-items: center; line-height: 0.9; margin-top: -3px;'><div style='display: flex; align-items: baseline; gap: 4px; margin-bottom: 1px;'><span style='color: #00d2ff; font-weight: 800; font-size: 1.8rem;'>{total}</span><span style='color: #888; font-size: 0.55rem; text-transform: uppercase; font-weight: 800;'>Total</span></div><div style='margin: 0px 0 2px 0; height: 1px; width: 85%; background: rgba(0,0,0,0.1);'></div><div style='display: flex; justify-content: space-between; width: 92%; gap: 10px;'><div style='text-align: center;'><div style='color: #10b981; font-weight: 800; font-size: 1.5rem; margin-bottom: 0px;'>{active}</div><div style='color: #10b981; font-size: 0.65rem; text-transform: uppercase; font-weight: 900;'>Active</div></div><div style='text-align: center;'><div style='color: #f59e0b; font-weight: 800; font-size: 1.5rem; margin-bottom: 0px;'>{inactive}</div><div style='color: #f59e0b; font-size: 0.65rem; text-transform: uppercase; font-weight: 900;'>Inactive</div></div></div></div></div>"
    st.markdown(sess_html, unsafe_allow_html=True)

def render_tablespace_bar(name, used_pct, free_pct):
    # Dynamic color based on usage
    if used_pct > 80:
        bar_color = "#ef4444"  # Red
        free_bg = "#fee2e2"    # Very light red
    elif used_pct > 60:
        bar_color = "#f59e0b"  # Amber
        free_bg = "#f3f4f6"    # Default light grey
    else:
        bar_color = "#10b981"  # Emerald Green
        free_bg = "#f3f4f6"    # Default light grey

    st.markdown(f"""
        <div class="ts-row">
            <div class="ts-name" title="{name}">{name}</div>
            <div class="ts-bar-bg">
                <div class="ts-bar-used" style="width: {used_pct}%; background-color: {bar_color};">
                    {used_pct if used_pct > 20 else ''}%
                </div>
                <div class="ts-bar-free" style="width: {free_pct}%; background-color: {free_bg};">
                    {free_pct if free_pct > 20 else ''}%
                </div>
            </div>
            <div class="ts-meta">Used: {used_pct}% | Free: {free_pct}%</div>
        </div>
    """, unsafe_allow_html=True)

# ================= DBWR ANIMATION COMPONENT =================
def render_dbwr_animation():
    dbwr_html = """
    <div style="background:#f9fafb;border-radius:12px;padding:20px 24px;border:1px solid #e5e7eb;margin-top:20px;font-family:'Inter',sans-serif;">

      <!-- Header -->
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
        <span style="font-size:1rem;font-weight:700;color:#374151;">⚡ DBWR Write Activity</span>
        <span id="st-badge" style="padding:3px 12px;border-radius:9999px;font-size:0.72rem;font-weight:700;background:#d1fae5;color:#065f46;transition:all 0.4s;">● Connecting…</span>
      </div>

      <!-- Flow diagram -->
      <div style="display:flex;align-items:center;justify-content:center;gap:10px;">

        <!-- Buffer Cache box -->
        <div id="buf-box" style="width:130px;height:90px;border-radius:10px;border:2.5px solid #10b981;background:#ecfdf5;display:flex;flex-direction:column;align-items:center;justify-content:center;transition:all 0.5s ease;">
          <div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:.06em;">Buffer Cache</div>
          <div id="dirty-val" style="font-size:1.7rem;font-weight:900;color:#10b981;line-height:1.1;transition:color 0.4s;">0</div>
          <div style="font-size:0.6rem;color:#6b7280;">dirty blocks</div>
        </div>

        <!-- Arrow 1 -->
        <div id="arr1" style="position:relative;width:64px;height:22px;opacity:0.2;transition:opacity 0.5s;">
          <div style="position:absolute;top:50%;left:0;right:10px;height:2.5px;background:#3b82f6;transform:translateY(-50%);"></div>
          <div style="position:absolute;right:0;top:50%;transform:translateY(-50%);width:0;height:0;border-top:7px solid transparent;border-bottom:7px solid transparent;border-left:11px solid #3b82f6;"></div>
          <div id="dot1" style="position:absolute;top:50%;left:0;width:9px;height:9px;border-radius:50%;background:#3b82f6;transform:translate(0,-50%);transition:none;"></div>
        </div>

        <!-- DBWR box -->
        <div id="dbwr-box" style="width:130px;height:90px;border-radius:10px;border:2.5px solid #3b82f6;background:#eff6ff;display:flex;flex-direction:column;align-items:center;justify-content:center;transition:all 0.5s ease;">
          <div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:.06em;">DBWR</div>
          <div id="dbwr-val" style="font-size:1.7rem;font-weight:900;color:#3b82f6;line-height:1.1;">—</div>
          <div style="font-size:0.6rem;color:#6b7280;">events</div>
        </div>

        <!-- Arrow 2 -->
        <div id="arr2" style="position:relative;width:64px;height:22px;opacity:0.2;transition:opacity 0.5s;">
          <div style="position:absolute;top:50%;left:0;right:10px;height:2.5px;background:#3b82f6;transform:translateY(-50%);"></div>
          <div style="position:absolute;right:0;top:50%;transform:translateY(-50%);width:0;height:0;border-top:7px solid transparent;border-bottom:7px solid transparent;border-left:11px solid #3b82f6;"></div>
          <div id="dot2" style="position:absolute;top:50%;left:0;width:9px;height:9px;border-radius:50%;background:#3b82f6;transform:translate(0,-50%);transition:none;"></div>
        </div>

        <!-- Datafile box -->
        <div id="df-box" style="width:130px;height:90px;border-radius:10px;border:2.5px solid #e5e7eb;background:#f9fafb;display:flex;flex-direction:column;align-items:center;justify-content:center;transition:all 0.5s ease;">
          <div style="font-size:0.62rem;font-weight:800;color:#374151;text-transform:uppercase;letter-spacing:.06em;">Datafile</div>
          <div style="font-size:1.6rem;line-height:1.1;">💾</div>
          <div style="font-size:0.6rem;color:#6b7280;">Disk</div>
        </div>

      </div><!-- end flow -->

      <div id="st-text" style="text-align:center;font-size:0.8rem;color:#6b7280;margin-top:10px;">Connecting to Oracle DB…</div>
      <div id="st-time" style="text-align:center;font-size:0.68rem;color:#9ca3af;margin-top:3px;"></div>
    </div>

    <style>
      @keyframes pulse-orange {
        0%   { box-shadow: 0 0 0 0 rgba(245,158,11,0.55); }
        70%  { box-shadow: 0 0 0 12px rgba(245,158,11,0); }
        100% { box-shadow: 0 0 0 0 rgba(245,158,11,0); }
      }
      @keyframes pulse-red {
        0%   { box-shadow: 0 0 0 0 rgba(239,68,68,0.55); }
        70%  { box-shadow: 0 0 0 12px rgba(239,68,68,0); }
        100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
      }
      @keyframes slideRight {
        0%   { left: 0px;   opacity: 1; }
        80%  { left: 44px;  opacity: 1; }
        100% { left: 44px;  opacity: 0; }
      }
    </style>

    <script>
      var prevDbwr = -1;
      var dotAnim1 = null, dotAnim2 = null;

      function setDot(id, active) {
        var d = document.getElementById(id);
        if (!d) return;
        d.style.animation = active ? 'slideRight 1.1s linear infinite' : 'none';
        d.style.left = active ? '0px' : '0px';
      }

      function update(data) {
        var dirty   = (data.dirty_blocks           || 0);
        var events  = (data.dbwr_event_total        || 0);
        var active  = (events > prevDbwr && prevDbwr >= 0 && dirty > 0);

        document.getElementById('dirty-val').textContent = dirty;
        document.getElementById('dbwr-val').textContent  = events;

        var buf    = document.getElementById('buf-box');
        var arr1   = document.getElementById('arr1');
        var arr2   = document.getElementById('arr2');
        var dfBox  = document.getElementById('df-box');
        var badge  = document.getElementById('st-badge');
        var txt    = document.getElementById('st-text');
        var ts     = document.getElementById('st-time');

        if (dirty === 0) {
          // ---- IDLE / CLEAN ----
          buf.style.borderColor    = '#10b981';
          buf.style.background     = '#ecfdf5';
          buf.style.animation      = 'none';
          document.getElementById('dirty-val').style.color = '#10b981';
          arr1.style.opacity = '0.2';
          arr2.style.opacity = '0.2';
          dfBox.style.borderColor  = '#e5e7eb';
          dfBox.style.background   = '#f9fafb';
          badge.style.background   = '#d1fae5'; badge.style.color='#065f46';
          badge.textContent        = '● Idle';
          txt.textContent          = (prevDbwr > 0 && prevDbwr !== events)
                                       ? 'Write completed ✓'
                                       : 'No write activity — Buffer Cache is clean';
          setDot('dot1', false); setDot('dot2', false);

        } else if (active) {
          // ---- WRITING ----
          buf.style.borderColor    = '#f59e0b';
          buf.style.background     = '#fffbeb';
          buf.style.animation      = 'pulse-orange 1.2s infinite';
          document.getElementById('dirty-val').style.color = '#d97706';
          arr1.style.opacity = '1';
          arr2.style.opacity = '1';
          dfBox.style.borderColor  = '#3b82f6';
          dfBox.style.background   = '#eff6ff';
          badge.style.background   = '#fef3c7'; badge.style.color='#92400e';
          badge.textContent        = '● Writing';
          txt.textContent          = '⚡ DBWR writing to disk — ' + dirty + ' dirty block(s)';
          setDot('dot1', true); setDot('dot2', true);

        } else {
          // ---- DIRTY, WAITING ----
          buf.style.borderColor    = '#ef4444';
          buf.style.background     = '#fef2f2';
          buf.style.animation      = 'pulse-red 1.2s infinite';
          document.getElementById('dirty-val').style.color = '#dc2626';
          arr1.style.opacity = '0.35';
          arr2.style.opacity = '0.2';
          dfBox.style.borderColor  = '#e5e7eb';
          dfBox.style.background   = '#f9fafb';
          badge.style.background   = '#fee2e2'; badge.style.color='#991b1b';
          badge.textContent        = '● Dirty';
          txt.textContent          = '🟠 Dirty blocks detected — DBWR pending';
          setDot('dot1', false); setDot('dot2', false);
        }

        prevDbwr = events;
        ts.textContent = 'Last polled: ' + new Date().toLocaleTimeString();
      }

      function poll() {
        fetch('http://127.0.0.1:8000/dbwr-stats')
          .then(function(r){ return r.json(); })
          .then(function(d){ update(d); })
          .catch(function(){
            var b = document.getElementById('st-badge');
            var t = document.getElementById('st-text');
            if(b){ b.style.background='#fee2e2'; b.style.color='#991b1b'; b.textContent='● DB Unavailable'; }
            if(t) t.textContent = '⚠️ Cannot reach Oracle DB — retrying…';
          });
      }

      poll();
      setInterval(poll, 4000);
    </script>
    """
    components.html(dbwr_html, height=210, scrolling=False)


# ================= SELECTION PAGE =================
def render_selection_page():
    # Injecting selection-specific CSS for cards
    st.markdown("""
    <style>
        .db-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 20px 0;
        }
        .db-card {
            background: white;
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            border: 1px solid #e5e7eb;
            transition: all 0.3s ease;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .db-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
            border-color: #3b82f6;
        }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("<h1 style='text-align: center; color: #111827; margin-bottom: 10px;'>🗄️ Database Portal</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #6b7280; margin-bottom: 40px;'>Select an instance to monitor health and performance</p>", unsafe_allow_html=True)
    
    dbs = fetch_databases()
    
    if not dbs:
        st.error("⚠️ Backend API unreachable or no databases found.")
        if st.button("Retry Connection"):
            st.rerun()
        return

    cols = st.columns(len(dbs))
    for i, db in enumerate(dbs):
        with cols[i]:
            st.markdown(f"""
                <div class="db-card">
                    <div style="font-size: 3rem; margin-bottom: 1rem;">💾</div>
                    <div style="font-size: 1.25rem; font-weight: 700; color: #1f2937;">{db}</div>
                    <div style="font-size: 0.875rem; color: #6b7280; margin-top: 4px;">Oracle Database</div>
                </div>
            """, unsafe_allow_html=True)
            if st.button(f"Open Dashboard", key=f"btn_{db}", use_container_width=True):
                navigate_to('dashboard', db)

# ================= MAIN DASHBOARD =================
def main():
    if st.session_state.page == 'selection':
        render_selection_page()
        return

    db_name = st.session_state.selected_db
    
    # Header with Back Button
    h_col1, h_col2, h_col3 = st.columns([1, 4, 1])
    with h_col1:
        if st.button("← Back"):
            navigate_to('selection')
    with h_col2:
        st.markdown(f"<h1 style='color: #111827; text-align: center; margin: 0;'>📊 Health: {db_name}</h1>", unsafe_allow_html=True)
    with h_col3:
        if st.button("🔄 Refresh"):
            st.rerun()
    
    st.markdown("<br>", unsafe_allow_html=True)

    # Data Loading
    with st.spinner("Synchronizing..."):
        db_data = fetch_status("db-status")
        lsnr_data = fetch_status("listener-status")
        ts_data = fetch_status("tablespace")
        bkp_data = fetch_status("backup-status")
        sess_data = fetch_status("session-stats")

    # Adjusted ratio to make status cards very compact
    left_col, right_col = st.columns([1.2, 3])
    
    with left_col:
        # Row 1: DB and Listener
        row1_col1, row1_col2 = st.columns(2)
        
        with row1_col1:
            status = db_data.get("db_status", "UNKNOWN")
            db_state = "up" if status == "OPEN" else "down"
            render_status_card("DB Status", db_state)
            
        with row1_col2:
            status = lsnr_data.get("listener_status", "UNKNOWN")
            lsnr_state = "up" if status == "UP" else "down"
            render_status_card("Listener", lsnr_state)

        # Row 2: Backup and Sessions
        row2_col1, row2_col2 = st.columns(2)
        with row2_col1:
            status = bkp_data.get("backup_status", "UNKNOWN")
            if status == "COMPLETED":
                bkp_state = "up"
            elif "FAILED" in status or "ERROR" in status:
                bkp_state = "down"
            else:
                bkp_state = "warning"
            render_status_card("Backup", bkp_state)
            
        with row2_col2:
            active = sess_data.get("ACTIVE", 0)
            inactive = sess_data.get("INACTIVE", 0)
            total = sess_data.get("TOTAL", 0)
            render_session_card("Session Count", active, inactive, total)

    with right_col:
        # Tablespace Section
        st.markdown('<div class="ts-container" style="margin-top: 0;">', unsafe_allow_html=True)
        st.markdown("<h3 style='color: #374151; margin-top: 0;'>💾 Tablespace Utilization</h3>", unsafe_allow_html=True)
        
        if "tablespaces" in ts_data:
            for ts in ts_data["tablespaces"]:
                render_tablespace_bar(ts["tablespace"], ts["used_pct"], ts["free_pct"])
        elif "error" in ts_data:
            st.error(f"⚠️ {ts_data['error']}")
        else:
            st.info("No active tablespace metrics found.")
        
        st.markdown('</div>', unsafe_allow_html=True)

    # ── DBWR Write Activity (appended below tablespace) ──
    st.markdown("<br>", unsafe_allow_html=True)
    render_dbwr_animation()

    # Footer
    st.markdown(f"""
        <div style='margin-top: 40px; padding: 20px; text-align: center; color: #9ca3af; font-size: 0.8rem; border-top: 1px solid #f3f4f6;'>
            System clock: {time.strftime('%Y-%m-%d %H:%M:%S')} | Auto-refresh: 60s
        </div>
    """, unsafe_allow_html=True)

    time.sleep(60)
    st.rerun()

if __name__ == "__main__":
    main()
