import re

# ============================================================
# PART 1: monitoring.py — CPU time centiseconds ? seconds
#         + fix xaxis/yaxis tickfont colors explicitly
# ============================================================
with open('dashboard/monitoring.py', 'r', encoding='utf-8') as f:
    mon = f.read()

# 1a. Section header
mon = mon.replace(
    '? Top 10 CPU Sessions & SQL',
    '? Top 10 CPU Sessions & SQL  (CPU Time in seconds)'
)

# 1b. Convert cpu_val from centiseconds to seconds when reading
old_cpu_read = """                    cpu_val = float(row.get('CPU_TIME_VAL', 0))"""
new_cpu_read = """                    cpu_val = float(row.get('CPU_TIME_VAL', 0)) / 100.0  # centiseconds ? seconds"""
mon = mon.replace(old_cpu_read, new_cpu_read)

# 1c. Fix bar label: show seconds properly
mon = mon.replace(
    "f'<div style=\"color:var(--text-primary) !important; font-weight:700; z-index:2;\">{cpu_val:.0f} CPU</div>'",
    "f'<div style=\"color:var(--text-primary) !important; font-weight:700; z-index:2;\">{cpu_val:.1f}s CPU</div>'"
)

# 1d. Fix SQL box header to show seconds
mon = mon.replace(
    "f'if(box){{ box.textContent=\\'-- SID \\' + sid + \\' (\\' + usr + \\') | CPU Time: \\' + cpu + \\'\\\\n\\\\n\\' + sql; box.style.display=\\'block\\'; }} '",
    "f'if(box){{ box.textContent=\\'-- SID \\' + sid + \\' (\\' + usr + \\') | CPU Time: \\' + (parseFloat(cpu)/100).toFixed(1) + \\' seconds\\\\n\\\\n\\' + sql; box.style.display=\\'block\\'; }} '"
)

# 1e. Fix inline sql-content hover to show seconds  
mon = mon.replace(
    "f'<div class=\"sql-content\">-- SID {sid} ({user}) | CPU Time: {cpu_val:.0f}<br><br>{sql_safe.replace(chr(10), \"<br>\")}</div>'",
    "f'<div class=\"sql-content\">-- SID {sid} ({user}) | CPU Time: {cpu_val:.1f} seconds<br><br>{sql_safe.replace(chr(10), \"<br>\")}</div>'"
)

# Also fix data-cpu attribute to store raw centiseconds for JS conversion
mon = mon.replace(
    'f\'<div class="cpu-row" style="{bg_style}" data-sql="{sql_safe}" data-sid="{sid}" data-user="{user}" data-cpu="{cpu_val:.0f}" \'',
    'f\'<div class="cpu-row" style="{bg_style}" data-sql="{sql_safe}" data-sid="{sid}" data-user="{user}" data-cpu="{cpu_val:.1f}" \''
)

# 1f. Fix threshold colors (they compare centiseconds, fix for seconds now)
mon = mon.replace(
    '                    elif cpu_time > 3600:  return "#14532d"   # dark green >1 hour',
    '                    elif cpu_time > 36.0:  return "#14532d"   # dark green >1 hour (>3600 centisec = 36s)'
)
mon = mon.replace(
    '                    elif cpu_time > 1800:  return "#16a34a"   # medium green >30 min',
    '                    elif cpu_time > 18.0:  return "#16a34a"   # medium green >30 sec (>1800 centisec)'
)

# ============================================================
# PART 2: Fix Backup chart — explicit tickfont colors
# ============================================================
old_xaxis_bkp = "xaxis=dict(type='category', showgrid=False, showticklabels=True, tickangle=-45, title=None, tickfont=dict(size=8)),"
new_xaxis_bkp = "xaxis=dict(type='category', showgrid=False, showticklabels=True, tickangle=-45, title=None, tickfont=dict(size=8, color='#1e293b' if theme_mode != 'Dark' else '#f8fafc')),"
mon = mon.replace(old_xaxis_bkp, new_xaxis_bkp)

old_yaxis_bkp = "yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == \"Dark\" else 'rgba(0,0,0,0.04)', title=None),"
new_yaxis_bkp = "yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == \"Dark\" else 'rgba(0,0,0,0.04)', title=None, tickfont=dict(color='#1e293b' if theme_mode != 'Dark' else '#f8fafc', size=8)),"
mon = mon.replace(old_yaxis_bkp, new_yaxis_bkp)

# ============================================================
# PART 3: Fix DB Growth chart — explicit tickfont colors
# ============================================================
old_xaxis_gr = "xaxis=dict(showgrid=False, tickfont=dict(size=9)),"
new_xaxis_gr = "xaxis=dict(showgrid=False, tickfont=dict(size=9, color='#1e293b' if theme_mode != 'Dark' else '#f8fafc')),"
mon = mon.replace(old_xaxis_gr, new_xaxis_gr)

old_yaxis_gr = "yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == \"Dark\" else 'rgba(0,0,0,0.04)', range=[0, y_max])"
new_yaxis_gr = "yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == \"Dark\" else 'rgba(0,0,0,0.04)', range=[0, y_max], tickfont=dict(color='#1e293b' if theme_mode != 'Dark' else '#f8fafc', size=9))"
mon = mon.replace(old_yaxis_gr, new_yaxis_gr)

with open('dashboard/monitoring.py', 'w', encoding='utf-8') as f:
    f.write(mon)
print("monitoring.py updated")

# ============================================================
# PART 4: home.py — increase OS card height + fix tablespace
# ============================================================
with open('dashboard/home.py', 'r', encoding='utf-8') as f:
    home = f.read()

# 4a. Increase OS card iframe height from 290 to 380
home = home.replace('_comp.html(os_html, height=290, scrolling=False)', '_comp.html(os_html, height=380, scrolling=False)')

# 4b. Increase OS card padding and font sizes in CSS
home = home.replace(
    '.os-card{{border:1px solid {os_bdr};border-radius:8px;padding:7px 9px;background:transparent;}}',
    '.os-card{{border:1px solid {os_bdr};border-radius:8px;padding:10px 12px;background:transparent;margin-bottom:2px;}}'
)
home = home.replace(
    '.os-card-title{{font-size:0.62rem;font-weight:800;color:{os_sub};margin-bottom:4px;letter-spacing:0.03em;text-transform:uppercase;}}',
    '.os-card-title{{font-size:0.65rem;font-weight:800;color:{os_sub};margin-bottom:6px;letter-spacing:0.03em;text-transform:uppercase;}}'
)
home = home.replace(
    '.os-row{{display:flex;justify-content:space-between;align-items:center;font-size:0.64rem;font-weight:700;color:{os_text};margin-bottom:2px;}}',
    '.os-row{{display:flex;justify-content:space-between;align-items:center;font-size:0.68rem;font-weight:700;color:{os_text};margin-bottom:3px;}}'
)
home = home.replace(
    '.os-sub-row{{font-size:0.54rem;color:{os_sub};display:flex;justify-content:space-between;margin-bottom:2px;}}',
    '.os-sub-row{{font-size:0.58rem;color:{os_sub};display:flex;justify-content:space-between;margin-bottom:4px;}}'
)
home = home.replace(
    '.os-bar{{height:6px;border-radius:3px;background:rgba(128,128,128,0.2);position:relative;overflow:hidden;margin-bottom:3px;}}',
    '.os-bar{{height:7px;border-radius:3px;background:rgba(128,128,128,0.2);position:relative;overflow:hidden;margin-bottom:4px;}}'
)

# 4c. Fix tablespace card display: name on first row, MB on second row
old_ts_display = '''                balance_ts = stats.get("balance_ts", [])
                full_ts = stats.get("full_ts", [])
                if balance_ts:
                    top_ts = balance_ts[0]  # least free space first
                    ts_name = top_ts["name"]
                    ts_pct = top_ts["pct"]
                    ts_free_mb = top_ts["free_mb"]
                    ts_used_mb = top_ts["used_mb"]
                    if ts_pct >= 95:
                        ts_dot, ts_val_class = "dot-red", "val-red"
                    elif ts_pct >= 70:
                        ts_dot, ts_val_class = "dot-amber", "val-amber"
                    else:
                        ts_dot, ts_val_class = "dot-green", "val-green"
                    if ts_free_mb < 100:
                        ts_text = f"{ts_name} ({ts_free_mb:.0f}MB free)"
                    else:
                        ts_text = f"{ts_name} ({ts_pct:.0f}%)"
                else:
                    ts_dot, ts_val_class = "dot-green", "val-green"
                    ts_text = "None"'''

new_ts_display = '''                balance_ts = stats.get("balance_ts", [])
                full_ts = stats.get("full_ts", [])
                # Find most critical permanent tablespace (least free, excl TEMP/UNDO)
                critical_ts = next((t for t in balance_ts if t["pct"] >= 70), None)
                if not critical_ts and balance_ts:
                    critical_ts = balance_ts[0]  # pick least free even if below 70%
                if critical_ts:
                    ts_name = critical_ts["name"]
                    ts_pct = critical_ts["pct"]
                    ts_free_mb = critical_ts["free_mb"]
                    ts_used_mb = critical_ts["used_mb"]
                    if ts_pct >= 95:
                        ts_dot, ts_val_class = "dot-red", "val-red"
                    elif ts_pct >= 70:
                        ts_dot, ts_val_class = "dot-amber", "val-amber"
                    else:
                        ts_dot, ts_val_class = "dot-green", "val-green"
                    # Name on first line, MB info on second line if available
                    if ts_free_mb > 0:
                        ts_text = f'<span style="display:flex;flex-direction:column;align-items:flex-end;gap:1px;"><span>{ts_name}</span><span style="font-size:0.6rem;color:var(--text-secondary);">{ts_free_mb:.0f}MB free</span></span>'
                    else:
                        ts_text = ts_name
                else:
                    ts_dot, ts_val_class = "dot-green", "val-green"
                    ts_text = "None"'''

if old_ts_display in home:
    home = home.replace(old_ts_display, new_ts_display)
    print("Fixed tablespace display")
else:
    print("WARNING: old ts display not found")

# 4d. Fix DB card tablespace HTML line to use unsafe_allow_html style rendering
# The card_html uses f-string and injects ts_text — it must allow HTML in the span
old_ts_card_html = "f'<span class=\"{ts_val_class}\">{ts_text}</span></div>'"
new_ts_card_html = "f'<span class=\"{ts_val_class}\">{ts_text}</span></div>'"
# This is fine as-is since the card renders via st.markdown with unsafe_allow_html=True

with open('dashboard/home.py', 'w', encoding='utf-8') as f:
    f.write(home)
print("home.py updated")
