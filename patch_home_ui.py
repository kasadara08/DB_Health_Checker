with open('dashboard/home.py', 'r', encoding='utf-8') as f:
    home_content = f.read()

# 1. Update load_db_status_summary tablespace check
old_ts_check = '''            # Tablespace check - important ones (SYSTEM, USERS, SYSAUX)
            from queries.queries import get_tablespace_utilization, get_arc_log_info, get_blocking_sessions
            try:
                df_ts = get_tablespace_utilization()
                full_ts = []
                critical_ts_names = ["SYSTEM", "USERS", "SYSAUX", "UNDOTBS1"]
                if not df_ts.empty:
                    for _, ts in df_ts.iterrows():
                        ts_name = ts["TABLESPACE_NAME"]
                        ts_pct = float(ts["USED_PCT"])
                        # Flag if a critical tablespace is >= 95% full
                        if ts_name in critical_ts_names and ts_pct >= 95:
                            full_ts.append(f"{ts_name} ({ts_pct:.0f}% full)")
                        elif ts_pct >= 100:
                            full_ts.append(f"{ts_name} (100% full)")
                status_data["full_ts"] = full_ts
            except Exception:
                status_data["full_ts"] = []'''

new_ts_check = '''            # Tablespace check - filter out TEMP/UNDO, find highest used
            from queries.queries import get_tablespace_utilization, get_arc_log_info, get_blocking_sessions
            try:
                df_ts = get_tablespace_utilization()
                balance_ts = []
                full_ts = []
                critical_ts_names = ["SYSTEM", "USERS", "SYSAUX", "UNDOTBS1"]
                if not df_ts.empty:
                    for _, ts in df_ts.iterrows():
                        ts_name = ts["TABLESPACE_NAME"]
                        ts_pct = float(ts["USED_PCT"])
                        # Flag if a critical tablespace is >= 95% full
                        if ts_name in critical_ts_names and ts_pct >= 95:
                            full_ts.append(f"{ts_name} ({ts_pct:.0f}% full)")
                        elif ts_pct >= 100:
                            full_ts.append(f"{ts_name} (100% full)")
                            
                        # Balance tablespace filtering
                        if "TEMP" in ts_name.upper() or "UNDO" in ts_name.upper():
                            continue
                        total_mb = float(ts["TOTAL_MB"])
                        used_mb = float(ts["USED_MB"])
                        free_mb = max(0.0, total_mb - used_mb)
                        balance_ts.append({
                            "name": ts_name,
                            "pct": ts_pct,
                            "free_mb": free_mb
                        })
                balance_ts.sort(key=lambda x: x["pct"], reverse=True)
                status_data["balance_ts"] = balance_ts
                status_data["full_ts"] = full_ts
            except Exception:
                status_data["balance_ts"] = []
                status_data["full_ts"] = []'''

home_content = home_content.replace(old_ts_check, new_ts_check)

# 2. Update card UI rendering logic for tablespace
old_card_ts = '''                full_ts = stats.get("full_ts", [])
                ts_dot = "dot-red" if full_ts else "dot-green"
                ts_val_class = "val-red" if full_ts else "val-green"
                # Strip the " (X% full)" part for the UI column, show only the name
                ts_text = full_ts[0].split(" (")[0] if full_ts else "None"'''

new_card_ts = '''                balance_ts = stats.get("balance_ts", [])
                if balance_ts:
                    highest_ts = balance_ts[0]
                    ts_name = highest_ts["name"]
                    ts_pct = highest_ts["pct"]
                    free_mb = highest_ts["free_mb"]
                    
                    if ts_pct >= 100:
                        ts_dot, ts_val_class = "dot-red", "val-red"
                    elif ts_pct >= 95:
                        ts_dot, ts_val_class = "dot-amber", "val-amber"
                    else:
                        ts_dot, ts_val_class = "dot-green", "val-green"
                    
                    if ts_pct >= 100 and free_mb > 0:
                        ts_html_val = f'<span class="{ts_val_class}" style="text-align:right; display:flex; flex-direction:column; align-items:flex-end; font-size:0.8rem; font-weight:800; line-height:1.1;"><span>{ts_name} ({ts_pct:.0f}%)</span><span style="font-size:0.58rem; color:var(--text-secondary); font-weight:normal; margin-top:2px;">Free: {free_mb:.1f} MB</span></span>'
                    else:
                        ts_html_val = f'<span class="{ts_val_class}" style="text-align:right; display:flex; flex-direction:column; align-items:flex-end; font-size:0.8rem; font-weight:800; line-height:1.1;"><span>{ts_name} ({ts_pct:.0f}%)</span></span>'
                else:
                    ts_dot, ts_val_class = "dot-green", "val-green"
                    ts_html_val = f'<span class="{ts_val_class}" style="font-size:0.8rem; font-weight:800;">None</span>\''''

home_content = home_content.replace(old_card_ts, new_card_ts)

# 3. Update the db-status-row for tablespace in card_html
old_status_row = '''                    f'<div class="db-status-row">'
                    f'<span class="db-status-label"><span class="{ts_dot}"></span> Tablespace</span>'
                    f'<span class="{ts_val_class}">{ts_text}</span></div>\''''

new_status_row = '''                    f'<div class="db-status-row" style="align-items: flex-start;">'
                    f'<span class="db-status-label"><span class="{ts_dot}"></span> Tablespace</span>'
                    f'{ts_html_val}</div>\''''

home_content = home_content.replace(old_status_row, new_status_row)

# 4. Update the Host OS Resource HTML boxes width and hover tooltips
old_os_html_block = '''                        os_html = f"""
                        <style>
                        .os-wrapper {{ display: flex; flex-direction: column; gap: 8px; font-family: 'Inter', sans-serif; }}
                        .os-box {{ display: flex; flex-direction: column; gap: 3px; padding: 6px 8px;
                                   border: 1px solid {os_border}; border-radius: 8px; background: {os_bg}; }}
                        .os-title {{ font-size: 0.70rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 2px; }}
                        .os-label {{ font-size: 0.65rem; font-weight: 700; color: {os_text}; display:flex; justify-content:space-between; margin-bottom:2px; }}
                        .os-sub {{ font-size: 0.55rem; color: {os_sub_text}; display:flex; justify-content:space-between; margin-bottom:2px; }}
                        .os-bar-track {{ height:6px; border-radius:3px; background:rgba(128,128,128,0.18); position:relative; overflow:hidden; margin-bottom:2px; }}
                        .os-seg {{ position:absolute; top:0; height:100%; }}
                        b {{ color: {os_text}; }}
                        </style>
                        <div class="os-wrapper">
                            <!-- Box 1: Host OS Resource Utilized by Oracle -->
                            <div class="os-box">
                                <div class="os-title">Host OS Resource Utilized by Oracle</div>
                                <div>
                                    <div class="os-label">
                                        <span>{cpu_indicator} Host CPU</span>
                                        <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Free: {cpu_free:.1f} MB">
                                        <div class="os-seg" style="left:0; width:{cpu_oracle_capped}%; background:#f59e0b;" title="Oracle: {cpu_oracle:.1f} MB"></div>
                                        <div class="os-seg" style="left:{cpu_oracle_capped}%; width:{cpu_other_pct}%; background:{cpu_bar_color};" title="Other: {cpu_other_pct:.1f} MB"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{cpu_oracle:.1f}</b></span>
                                        <span><span style="color:{cpu_bar_color};">&#9632;</span> Other <b>{cpu_other_pct:.1f}</b></span>
                                        <span>Free <b>{cpu_free:.1f}</b></span>
                                    </div>
                                </div>
                                <div style="margin-top: 2px;">
                                    <div class="os-label">
                                        <span>{ram_indicator} Host RAM</span>
                                        <span style="color:#38bdf8;">{ram_used_mb:,.0f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Free: {ram_free_mb:,.0f} MB">
                                        <div class="os-seg" style="left:0; width:{ram_oracle_pct}%; background:#f59e0b;" title="Oracle: {ram_oracle_mb:,.0f} MB"></div>
                                        <div class="os-seg" style="left:{ram_oracle_pct}%; width:{ram_other_pct}%; background:{ram_bar_color};" title="Other: {(ram_used_mb - ram_oracle_mb):,.0f} MB"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{ram_oracle_mb:,.0f} MB</b></span>
                                        <span><span style="color:{ram_bar_color};">&#9632;</span> Other <b>{(ram_used_mb - ram_oracle_mb):,.0f} MB</b></span>
                                        <span>Free <b>{ram_free_mb:,.0f} MB</b></span>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Box 2: Overall Host OS Resource -->
                            <div class="os-box">
                                <div class="os-title">Host OS Resource</div>
                                <div>
                                    <div class="os-label">
                                        <span>{cpu_indicator} Host CPU</span>
                                        <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Free: {cpu_free:.1f} MB">
                                        <div class="os-seg" style="left:0; width:{cpu_used}%; background:{cpu_bar_color};"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:{cpu_bar_color};">&#9632;</span> Used <b>{cpu_used:.1f}</b></span>
                                        <span>Free <b>{cpu_free:.1f}</b></span>
                                    </div>
                                </div>
                                <div style="margin-top: 2px;">
                                    <div class="os-label">
                                        <span>{ram_indicator} Host RAM</span>
                                        <span style="color:#38bdf8;">{ram_used_mb:,.0f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Free: {ram_free_mb:,.0f} MB">
                                        <div class="os-seg" style="left:0; width:{ram_pct}%; background:{ram_bar_color};"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:{ram_bar_color};">&#9632;</span> Used <b>{ram_used_mb:,.0f} MB</b></span>
                                        <span>Free <b>{ram_free_mb:,.0f} MB</b></span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        """
                        _comp.html(os_html, height=270, scrolling=False)'''

new_os_html_block = '''                        os_html = f"""
                        <style>
                        .os-wrapper {{ display: flex; flex-direction: column; gap: 8px; font-family: 'Inter', sans-serif; width: 100%; box-sizing: border-box; }}
                        .os-box {{ display: flex; flex-direction: column; gap: 3px; padding: 6px 8px;
                                   border: 1px solid {os_border}; border-radius: 8px; background: {os_bg}; width: 100%; box-sizing: border-box; }}
                        .os-title {{ font-size: 0.70rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 2px; }}
                        .os-label {{ font-size: 0.65rem; font-weight: 700; color: {os_text}; display:flex; justify-content:space-between; margin-bottom:2px; }}
                        .os-sub {{ font-size: 0.55rem; color: {os_sub_text}; display:flex; justify-content:space-between; margin-bottom:2px; }}
                        .os-bar-track {{ height:6px; border-radius:3px; background:rgba(128,128,128,0.18); position:relative; overflow:hidden; margin-bottom:2px; width: 100%; }}
                        .os-seg {{ position:absolute; top:0; height:100%; }}
                        b {{ color: {os_text}; }}
                        </style>
                        <div class="os-wrapper">
                            <!-- Box 1: Host OS Resource Utilized by Oracle -->
                            <div class="os-box" title="Oracle CPU: {cpu_oracle:.1f}% | Other CPU: {cpu_other_pct:.1f}% | Free CPU: {cpu_free:.1f}%&#13;Oracle RAM: {ram_oracle_mb:,.0f} MB | Other RAM: {(ram_used_mb - ram_oracle_mb):,.0f} MB | Free RAM: {ram_free_mb:,.0f} MB">
                                <div class="os-title">Host OS Resource Utilized by Oracle</div>
                                <div>
                                    <div class="os-label">
                                        <span>{cpu_indicator} Host CPU</span>
                                        <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Oracle CPU: {cpu_oracle:.1f}% | Other CPU: {cpu_other_pct:.1f}% | Free CPU: {cpu_free:.1f}%">
                                        <div class="os-seg" style="left:0; width:{cpu_oracle_capped}%; background:#f59e0b;" title="Oracle CPU: {cpu_oracle:.1f}%"></div>
                                        <div class="os-seg" style="left:{cpu_oracle_capped}%; width:{cpu_other_pct}%; background:{cpu_bar_color};" title="Other CPU: {cpu_other_pct:.1f}%"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{cpu_oracle:.1f}</b></span>
                                        <span><span style="color:{cpu_bar_color};">&#9632;</span> Other <b>{cpu_other_pct:.1f}</b></span>
                                        <span>Free <b>{cpu_free:.1f}</b></span>
                                    </div>
                                </div>
                                <div style="margin-top: 2px;">
                                    <div class="os-label">
                                        <span>{ram_indicator} Host RAM</span>
                                        <span style="color:#38bdf8;">{ram_used_mb:,.0f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Oracle RAM: {ram_oracle_mb:,.0f} MB | Other RAM: {(ram_used_mb - ram_oracle_mb):,.0f} MB | Free RAM: {ram_free_mb:,.0f} MB">
                                        <div class="os-seg" style="left:0; width:{ram_oracle_pct}%; background:#f59e0b;" title="Oracle RAM: {ram_oracle_mb:,.0f} MB"></div>
                                        <div class="os-seg" style="left:{ram_oracle_pct}%; width:{ram_other_pct}%; background:{ram_bar_color};" title="Other RAM: {(ram_used_mb - ram_oracle_mb):,.0f} MB"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{ram_oracle_mb:,.0f} MB</b></span>
                                        <span><span style="color:{ram_bar_color};">&#9632;</span> Other <b>{(ram_used_mb - ram_oracle_mb):,.0f} MB</b></span>
                                        <span>Free <b>{ram_free_mb:,.0f} MB</b></span>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Box 2: Overall Host OS Resource -->
                            <div class="os-box" title="Total CPU: 100% | Free CPU: {cpu_free:.1f}%&#13;Total RAM: {ram_total_mb:,.0f} MB | Free RAM: {ram_free_mb:,.0f} MB">
                                <div class="os-title">Host OS Resource</div>
                                <div>
                                    <div class="os-label">
                                        <span>{cpu_indicator} Host CPU</span>
                                        <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Total: 100% | Free: {cpu_free:.1f}%">
                                        <div class="os-seg" style="left:0; width:{cpu_used}%; background:{cpu_bar_color};"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:{cpu_bar_color};">&#9632;</span> Used <b>{cpu_used:.1f}</b></span>
                                        <span>Free <b>{cpu_free:.1f}</b></span>
                                    </div>
                                </div>
                                <div style="margin-top: 2px;">
                                    <div class="os-label">
                                        <span>{ram_indicator} Host RAM</span>
                                        <span style="color:#38bdf8;">{ram_used_mb:,.0f} MB used</span>
                                    </div>
                                    <div class="os-bar-track" title="Total: {ram_total_mb:,.0f} MB | Free: {ram_free_mb:,.0f} MB">
                                        <div class="os-seg" style="left:0; width:{ram_pct}%; background:{ram_bar_color};"></div>
                                    </div>
                                    <div class="os-sub">
                                        <span><span style="color:{ram_bar_color};">&#9632;</span> Used <b>{ram_used_mb:,.0f} MB</b></span>
                                        <span>Free <b>{ram_free_mb:,.0f} MB</b></span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        """
                        _comp.html(os_html, height=270, scrolling=False)'''

home_content = home_content.replace(old_os_html_block, new_os_html_block)

with open('dashboard/home.py', 'w', encoding='utf-8') as f:
    f.write(home_content)

print("home.py updated successfully.")
