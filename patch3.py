with open('dashboard/home.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the HTML block with the new layout
old_html_block = '''                        import streamlit.components.v1 as _comp
                        os_html = f\"\"\"
                        <style>
                        .os-box {{ font-family: 'Inter', sans-serif; display: flex; flex-direction: column; gap: 4px; padding: 8px 10px;
                                   border: 1px solid {os_border}; border-radius: 8px; background: {os_bg}; margin-top: 4px; }}
                        .os-title {{ font-size: 0.72rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 4px; display:flex; align-items:center; gap:4px; }}
                        .os-label {{ font-size: 0.65rem; font-weight: 700; color: {os_text}; display:flex; justify-content:space-between; margin-bottom:2px; }}
                        .os-sub {{ font-size: 0.55rem; color: {os_sub_text}; display:flex; justify-content:space-between; margin-bottom:3px; }}
                        .os-bar-track {{ height:6px; border-radius:3px; background:rgba(128,128,128,0.18); position:relative; overflow:hidden; margin-bottom:3px; cursor:pointer; }}
                        .os-seg {{ position:absolute; top:0; height:100%; }}
                        b {{ color: {os_text}; }}
                        </style>
                        <div class="os-box">
                            <div class="os-title">??? Host OS Resource Utilized by Oracle</div>
                            <div>
                                <div class="os-label">
                                    <span>{cpu_indicator} Host CPU</span>
                                    <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                </div>
                                <div class="os-bar-track" onclick="alert('Free CPU: {cpu_free:.1f} MB')" title="Free: {cpu_free:.1f} MB">
                                    <div class="os-seg" style="left:0; width:{cpu_oracle_capped}%; background:#f59e0b;" onclick="event.stopPropagation(); alert('Oracle CPU: {cpu_oracle:.1f} MB');" title="Oracle: {cpu_oracle:.1f} MB"></div>
                                    <div class="os-seg" style="left:{cpu_oracle_capped}%; width:{cpu_other_pct}%; background:{cpu_bar_color};" onclick="event.stopPropagation(); alert('Other CPU: {cpu_other_pct:.1f} MB');" title="Other: {cpu_other_pct:.1f} MB"></div>
                                </div>
                                <div class="os-sub">
                                    <span><span style="color:#f59e0b;">&#9632;</span> Oracle: <b>{cpu_oracle:.1f} MB</b></span>
                                    <span><span style="color:{cpu_bar_color};">&#9632;</span> Other: <b>{cpu_other_pct:.1f} MB</b></span>
                                    <span>Free: <b>{cpu_free:.1f} MB</b></span>
                                </div>
                            </div>
                            <div style="margin-top: 4px;">
                                <div class="os-label">
                                    <span>{ram_indicator} Host RAM</span>
                                    <span style="color:#38bdf8;">{ram_used_mb:,.0f} MB used</span>
                                </div>
                                <div class="os-bar-track" onclick="alert('Free RAM: {ram_free_mb:,.0f} MB')" title="Free: {ram_free_mb:,.0f} MB">
                                    <div class="os-seg" style="left:0; width:{ram_oracle_pct}%; background:#f59e0b;" onclick="event.stopPropagation(); alert('Oracle RAM: {ram_oracle_mb:,.0f} MB');" title="Oracle: {ram_oracle_mb:,.0f} MB"></div>
                                    <div class="os-seg" style="left:{ram_oracle_pct}%; width:{ram_other_pct}%; background:{ram_bar_color};" onclick="event.stopPropagation(); alert('Other RAM: {(ram_used_mb - ram_oracle_mb):,.0f} MB');" title="Other: {(ram_used_mb - ram_oracle_mb):,.0f} MB"></div>
                                </div>
                                <div class="os-sub">
                                    <span><span style="color:#f59e0b;">&#9632;</span> Oracle: <b>{ram_oracle_mb:,.0f} MB</b></span>
                                    <span><span style="color:{ram_bar_color};">&#9632;</span> Other: <b>{(ram_used_mb - ram_oracle_mb):,.0f} MB</b></span>
                                    <span>Free: <b>{ram_free_mb:,.0f} MB</b></span>
                                </div>
                            </div>
                        </div>
                        \"\"\"
                        _comp.html(os_html, height=155, scrolling=False)'''

new_html_block = '''                        import streamlit.components.v1 as _comp
                        os_html = f\"\"\"
                        <style>
                        .os-box {{ font-family: 'Inter', sans-serif; display: flex; flex-direction: column; gap: 2px; padding: 6px 8px;
                                   border: 1px solid {os_border}; border-radius: 8px; background: {os_bg}; margin-top: 4px; }}
                        .os-title {{ font-size: 0.72rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 2px; display:flex; align-items:center; gap:4px; }}
                        .os-label {{ font-size: 0.65rem; font-weight: 700; color: {os_text}; display:flex; justify-content:space-between; margin-bottom:2px; }}
                        .os-sub {{ font-size: 0.53rem; color: {os_sub_text}; display:flex; justify-content:space-between; margin-bottom:2px; flex-wrap:wrap; }}
                        .os-bar-track {{ height:6px; border-radius:3px; background:rgba(128,128,128,0.18); position:relative; overflow:hidden; margin-bottom:2px; cursor:pointer; }}
                        .os-seg {{ position:absolute; top:0; height:100%; }}
                        b {{ color: {os_text}; }}
                        </style>
                        
                        <!-- Box 1: Host OS Resource Utilized by Oracle -->
                        <div class="os-box">
                            <div class="os-title">Host OS Resource Utilized by Oracle</div>
                            <div>
                                <div class="os-label">
                                    <span>{cpu_indicator} Host CPU</span>
                                    <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                </div>
                                <div class="os-bar-track" onclick="alert('Free CPU: {cpu_free:.1f} MB')" title="Free: {cpu_free:.1f} MB">
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
                                <div class="os-bar-track" onclick="alert('Free RAM: {ram_free_mb:,.0f} MB')" title="Free: {ram_free_mb:,.0f} MB">
                                    <div class="os-seg" style="left:0; width:{ram_oracle_pct}%; background:#f59e0b;" title="Oracle: {ram_oracle_mb:,.0f} MB"></div>
                                    <div class="os-seg" style="left:{ram_oracle_pct}%; width:{ram_other_pct}%; background:{ram_bar_color};" title="Other: {(ram_used_mb - ram_oracle_mb):,.0f} MB"></div>
                                </div>
                                <div class="os-sub">
                                    <span><span style="color:#f59e0b;">&#9632;</span> Oracle <b>{ram_oracle_mb:,.0f}</b></span>
                                    <span><span style="color:{ram_bar_color};">&#9632;</span> Other <b>{(ram_used_mb - ram_oracle_mb):,.0f}</b></span>
                                    <span>Free <b>{ram_free_mb:,.0f}</b></span>
                                </div>
                            </div>
                        </div>

                        <!-- Box 2: Host OS Resource (Total only) -->
                        <div class="os-box" style="margin-top: 6px;">
                            <div class="os-title">Host OS Resource</div>
                            <div>
                                <div class="os-label">
                                    <span>{cpu_indicator} Host CPU</span>
                                    <span style="color:#38bdf8;">{cpu_used:.1f} MB used</span>
                                </div>
                                <div class="os-bar-track" onclick="alert('Free CPU: {cpu_free:.1f} MB')" title="Free: {cpu_free:.1f} MB">
                                    <div class="os-seg" style="left:0; width:{cpu_used}%; background:{cpu_bar_color};"></div>
                                </div>
                                <div class="os-sub">
                                    <span>Used <b>{cpu_used:.1f} MB</b></span>
                                    <span>Free <b>{cpu_free:.1f} MB</b></span>
                                </div>
                            </div>
                            <div style="margin-top: 2px;">
                                <div class="os-label">
                                    <span>{ram_indicator} Host RAM</span>
                                    <span style="color:#38bdf8;">{ram_used_mb:,.0f} MB used</span>
                                </div>
                                <div class="os-bar-track" onclick="alert('Free RAM: {ram_free_mb:,.0f} MB')" title="Free: {ram_free_mb:,.0f} MB">
                                    <div class="os-seg" style="left:0; width:{ram_pct}%; background:{ram_bar_color};"></div>
                                </div>
                                <div class="os-sub">
                                    <span>Used <b>{ram_used_mb:,.0f} MB</b></span>
                                    <span>Free <b>{ram_free_mb:,.0f} MB</b></span>
                                </div>
                            </div>
                        </div>
                        \"\"\"
                        _comp.html(os_html, height=260, scrolling=False)'''

# Using a heuristic regex or find to replace
if "import streamlit.components.v1 as _comp" in content:
    idx1 = content.find("import streamlit.components.v1 as _comp")
    idx2 = content.find("_comp.html(os_html, height=155, scrolling=False)") + len("_comp.html(os_html, height=155, scrolling=False)")
    if idx1 != -1 and idx2 != -1:
        content = content[:idx1] + new_html_block.strip() + content[idx2:]
        with open('dashboard/home.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Success")
    else:
        print("Could not find the exact bounds.")
else:
    print("Could not find import comp")

