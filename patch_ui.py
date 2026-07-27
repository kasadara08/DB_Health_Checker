import re

with open('dashboard/home.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the indicators
content = content.replace('cpu_indicator = "??" if cpu_used > 85 else "??"', 'cpu_indicator = "&#128308;" if cpu_used > 85 else "&#128994;"')
content = content.replace('ram_indicator = "??" if ram_pct > 85 else "??"', 'ram_indicator = "&#128308;" if ram_pct > 85 else "&#128994;"')

# Increase height of iframe to prevent overlap, and add the second box.
# We will just replace the entire os_html assignment block.
# Let's find the start and end of os_html string.

start_marker = 'os_html = f\"\"\"'
end_marker = '\"\"\"\n                        _comp.html(os_html, height='

start_idx = content.find(start_marker)
end_idx = content.find(end_marker, start_idx)
if start_idx != -1 and end_idx != -1:
    end_statement_idx = content.find(')', end_idx) + 1
    
    # We will build a new os_html block with two boxes
    new_os_html = '''os_html = f\"\"\"
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
                        \"\"\"
                        _comp.html(os_html, height=270, scrolling=False)'''
    
    content = content[:start_idx] + new_os_html + content[end_statement_idx:]

with open('dashboard/home.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("UI Fixed.")
