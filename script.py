import sys

def rewrite_layout(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find boundaries
    start_idx = -1
    end_idx = -1
    for i, l in enumerate(lines):
        if "ROW 2: RESOURCE SPACE" in l:
            start_idx = i
        if "Connected Storage Devices (Moved to bottom" in l:
            end_idx = i
            break
            
    if start_idx == -1 or end_idx == -1:
        print("Could not find boundaries")
        return
        
    # We will just generate the new layout code and replace everything between start_idx and end_idx
    
    new_layout = '''    # ================= ROW 2: TABLESPACES & CPU =================
    row2_col1, row2_col2 = st.columns([2.0, 2.0])
    
    with row2_col1:
        render_tablespace_bars(df_ts, cols_count=3)
        
    with row2_col2:
        st.markdown("<h4 style='color: var(--text-primary); margin-top: 0; font-family: Space Grotesk; font-size: 1.05rem; font-weight: 700;'>⚡ Top 10 CPU Sessions</h4>", unsafe_allow_html=True)
        if not df_cpu.empty:
            df_cpu["SESSION_LABEL"] = df_cpu.apply(
                lambda r: f"SID {r['SID']} ({r['USERNAME']})", axis=1
            )

            def format_sql(sql):
                if pd.isna(sql) or not sql:
                    return "No Active SQL text"
                import textwrap
                sql_str = str(sql).strip()
                raw_lines = sql_str.split('\\n')
                wrapped_lines = []
                for line in raw_lines:
                    if len(line) > 80:
                        wrapped_lines.extend(textwrap.wrap(line, width=80, break_long_words=False, break_on_hyphens=False))
                    else:
                        wrapped_lines.append(line)
                return "<br>".join(wrapped_lines)

            df_cpu["SQL_TOOLTIP"] = df_cpu["SQL_TEXT"].apply(format_sql)
            df_cpu_top = df_cpu.sort_values(by="CPU_TIME_VAL", ascending=False).head(10).copy()
            
            blocking_sids = set()
            if not df_blocking.empty:
                for col in ["BLOCKING_SID", "BLOCKED_SID"]:
                    if col in df_blocking.columns:
                        for val in df_blocking[col].dropna().unique():
                            try:
                                blocking_sids.add(str(int(float(val))))
                            except (ValueError, TypeError):
                                blocking_sids.add(str(val).strip())

            def get_bar_color(row):
                try:
                    sid_str = str(int(float(row["SID"])))
                except (ValueError, TypeError):
                    sid_str = str(row["SID"]).strip()
                if sid_str in blocking_sids:
                    return "Involved in Blocking"
                    
                # CPU Running Time Logic
                try:
                    hours = float(row.get("LOGON_HOURS", 0.0) or 0.0)
                except:
                    hours = 0.0
                
                if hours >= 4.0:
                    return "Running >= 4 Hrs"
                elif hours >= 2.0:
                    return "Running >= 2 Hrs"
                return "Running < 2 Hrs"

            df_cpu_top["Status"] = df_cpu_top.apply(get_bar_color, axis=1)
            df_cpu_top["HOVER_HOURS"] = df_cpu_top["LOGON_HOURS"].apply(lambda h: f"{h:.2f}")

            fig_cpu = px.bar(
                df_cpu_top,
                x="CPU_TIME_VAL",
                y="SESSION_LABEL",
                orientation='h',
                labels={"CPU_TIME_VAL": "CPU Time", "SESSION_LABEL": "Session"},
                color="Status",
                color_discrete_map={
                    "Involved in Blocking": "#9333ea",
                    "Running >= 4 Hrs": "#064e3b",
                    "Running >= 2 Hrs": "#059669",
                    "Running < 2 Hrs": "#34d399"
                },
                template=chart_template,
                height=180
            )
            
            fig_cpu.update_traces(
                hovertemplate="<b>%{y}</b><br>CPU Time: %{x}<br>Active Duration: %{customdata[1]} hrs<br><br><b>Running Query:</b><br>%{customdata[0]}<extra></extra>",
                customdata=df_cpu_top[["SQL_TOOLTIP", "HOVER_HOURS"]].values
            )
            
            fig_cpu.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color='var(--text-primary)', size=8),
                margin=dict(l=5, r=5, t=5, b=5),
                xaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.08)' if theme_mode == "Dark" else 'rgba(0,0,0,0.05)'),
                yaxis=dict(showgrid=False, categoryorder="total ascending"),
                showlegend=False,
                hoverlabel=dict(
                    font_size=14,
                    font_family="monospace",
                    align="left"
                )
            )
            st.plotly_chart(fig_cpu, use_container_width=True, config={'displayModeBar': False})
        else:
            st.info("No CPU session details")

    st.markdown("<hr style='margin: 15px 0; border-color: var(--border-color);'>", unsafe_allow_html=True)
    
    # ================= ROW 3: SESSION HWM, BLOCKING & DEADLOCKS, BACKUP =================
    row3_col1, row3_col2, row3_col3 = st.columns([1.2, 1.2, 1.6])
    
    with row3_col1:
        st.markdown("<h4 style='color: var(--text-primary); margin-top: 0; font-family: Space Grotesk; font-size: 1.05rem; font-weight: 700;'>📈 Session Peak</h4>", unsafe_allow_html=True)
        df_sessions_hist = update_session_history(license_stats["sessions_current"], license_stats["sessions_highwater"])
        
        fig_sess = go.Figure()
        fig_sess.add_trace(go.Scatter(
            x=df_sessions_hist["TIMESTAMP"],
            y=df_sessions_hist["HWM"],
            mode='lines',
            name='Peak',
            line=dict(color='#ef4444', width=1.5, dash='dash')
        ))
        fig_sess.add_trace(go.Scatter(
            x=df_sessions_hist["TIMESTAMP"],
            y=df_sessions_hist["SESSIONS"],
            mode='lines',
            name='Current',
            line=dict(color='#3b82f6', width=1.5)
        ))
        
        fig_sess.update_layout(
            title="Session Peak & HWM",
            template=chart_template,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='var(--text-primary)', size=8),
            height=130,
            margin=dict(l=5, r=5, t=25, b=5),
            showlegend=False,
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)')
        )
        st.plotly_chart(fig_sess, use_container_width=True, config={'displayModeBar': False})

    with row3_col2:
        st.markdown("<h4 style='font-size: 1.05rem; font-weight: 700; color: var(--text-primary); margin-top: 0; margin-bottom: 6px; font-family: Space Grotesk;'>🛡️ Blocking &amp; Deadlocks</h4>", unsafe_allow_html=True)
        
        blocking_events = []
        if not df_blocking.empty:
            for _, b in df_blocking.iterrows():
                wait_s = int(b.get("SECONDS_IN_WAIT", 0) or 0)
                sql_snippet = str(b.get("BLOCKED_SQL_TEXT") or "").strip()[:100]
                blocking_events.append({
                    "type": "BLOCKING",
                    "label": "🚨 BLOCKING",
                    "color": "#ef4444",
                    "bg":    "rgba(239,68,68,0.08)",
                    "border":"#ef4444",
                    "detail": (
                        f"SID <b>{b.get('BLOCKING_SID','?')}</b> "
                        f"blocks <b>{b.get('BLOCKED_SID','?')}</b> "
                        f"| {wait_s}s wait"
                    )
                })

        blocking_blocked_sids = set()
        if not df_blocking.empty:
            for col in ["BLOCKING_SID", "BLOCKED_SID"]:
                if col in df_blocking.columns:
                    blocking_blocked_sids.update(
                        str(int(float(v))) for v in df_blocking[col].dropna()
                        if str(v).strip().lstrip('-').replace('.','',1).isdigit()
                    )

        lock_events = []
        if not df_locks.empty:
            for _, lk in df_locks.iterrows():
                sid_str = str(int(float(lk["SID"]))) if str(lk["SID"]).strip().lstrip('-').replace('.','',1).isdigit() else str(lk["SID"])
                if sid_str in blocking_blocked_sids:
                    continue
                wait_s    = int(lk.get("SECONDS_IN_WAIT", 0) or 0)
                blk_by    = lk.get("BLOCKING_SESSION")
                
                if blk_by and str(blk_by).strip() not in ("", "None", "nan"):
                    lock_events.append({
                        "type":   "DEADLOCK WAIT",
                        "label":  "☠️ DEADLOCK",
                        "color":  "#f59e0b",
                        "bg":     "rgba(245,158,11,0.08)",
                        "border": "#f59e0b",
                        "detail": f"SID <b>{sid_str}</b> blocked by <b>{blk_by}</b> | {wait_s}s"
                    })
                else:
                    lock_events.append({
                        "type":   "LOCK CONTENTION",
                        "label":  "⚠️ LOCK",
                        "color":  "#8b5cf6",
                        "bg":     "rgba(139,92,246,0.08)",
                        "border": "#8b5cf6",
                        "detail": f"SID <b>{sid_str}</b> waiting | {wait_s}s"
                    })

        all_events = blocking_events + lock_events

        if all_events:
            rows_html = ""
            for ev in all_events:
                rows_html += (
                    f'<div style="background:{ev["bg"]}; border:1px solid {ev["border"]}; border-radius:6px; '
                    f'padding:7px; font-size:0.7rem; margin-bottom:5px;">'
                    f'<span style="color:{ev["color"]}; font-weight:700; margin-right:5px;">{ev["label"]}</span>'
                    f'{ev["detail"]}'
                    f'</div>'
                )

            st.markdown(
                f'<div style="border:1px solid var(--border-color); border-radius:12px; padding:10px; '
                f'background:var(--bg-secondary); box-shadow:0 1px 3px rgba(0,0,0,0.04); overflow-y:auto; height:130px;">'
                f'{rows_html}'
                f'</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                '<div style="border:1px solid var(--border-color); border-radius:12px; height:130px; '
                'display:flex; align-items:center; justify-content:center; background:var(--bg-secondary); '
                'box-shadow:0 1px 3px rgba(0,0,0,0.02); text-align:center; gap:8px;">'
                '<span style="color:var(--emerald); font-weight:700; font-size:0.75rem;">No Blocking or Deadlocks</span>'
                '</div>',
                unsafe_allow_html=True
            )

    with row3_col3:
        with st.container(border=True):
            st.markdown("<h4 style='font-size: 1.05rem; font-weight: 700; color: var(--text-primary); margin-top: 0; margin-bottom: 6px; font-family: Space Grotesk;'>📊 Backup Durations</h4>", unsafe_allow_html=True)
            if not df_rman.empty:
                df_rman_all = df_rman.copy()
                df_rman_all["DISPLAY_DURATION"] = df_rman_all["DURATION_MIN"].apply(lambda x: max(x, 1))

                color_map = {
                    "COMPLETED": "#10b981",
                    "SUCCESS": "#10b981",
                    "COMPLETED WITH WARNINGS": "#f59e0b",
                    "COMPLETED WITH ERRORS": "#ef4444",
                    "FAILED": "#ef4444",
                    "RUNNING": "#3b82f6"
                }
                for status in df_rman_all["STATUS"].unique():
                    if status not in color_map:
                        color_map[status] = "#ef4444"

                fig_rman = px.bar(
                    df_rman_all,
                    x="START_TIME_STR",
                    y="DISPLAY_DURATION",
                    color="STATUS",
                    color_discrete_map=color_map,
                    text="DURATION_MIN",
                    title="Backup Durations (min)",
                    template=chart_template,
                    height=130
                )
                fig_rman.update_traces(
                    texttemplate='%{text:.2f}m',
                    textposition='inside',
                    textfont_size=7,
                    hovertemplate="<b>Date/Time</b>: %{x}<br><b>Type</b>: %{customdata[0]}<br><b>Duration</b>: %{customdata[1]:.2f} min<extra></extra>",
                    customdata=df_rman_all[["INPUT_TYPE", "DURATION_MIN"]].values
                )
                fig_rman.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color='var(--text-primary)', size=8),
                    margin=dict(l=5, r=5, t=30, b=10),
                    xaxis=dict(
                        type='category', 
                        showgrid=False, 
                        showticklabels=False,  
                        title=None             
                    ),
                    yaxis=dict(
                        showgrid=True, 
                        gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)',
                        title=None             
                    ),
                    showlegend=False,
                    bargap=0.3
                )
                st.plotly_chart(fig_rman, use_container_width=True, config={'displayModeBar': False})
            else:
                st.info("No backup data available")

    '''
    
    new_lines = lines[:start_idx] + [new_layout] + lines[end_idx:]
    with open(filename, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print("Rewritten successfully")

rewrite_layout(r'c:\Users\KTS\Desktop\dash_manually\dashboard\monitoring.py')
