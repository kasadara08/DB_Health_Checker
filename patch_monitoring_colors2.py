with open('dashboard/monitoring.py', 'r', encoding='utf-8') as f:
    mon_content = f.read()

# Insert plotly_text_color after theme_mode is declared in render_dashboard specifically
mon_content = mon_content.replace(
    '    theme_mode = st.session_state.get("theme", "Light")\n    chart_template = "plotly_dark" if theme_mode == "Dark" else "plotly_white"',
    '    theme_mode = st.session_state.get("theme", "Light")\n    plotly_text_color = "#e2e8f0" if theme_mode == "Dark" else "#0f172a"\n    chart_template = "plotly_dark" if theme_mode == "Dark" else "plotly_white"'
)

# 1. Update Backup Durations Plotly settings
old_rman_layout = '''                fig_rman.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color='var(--text-primary)', size=8),
                    margin=dict(l=5, r=5, t=20, b=5),
                    xaxis=dict(type='category', showgrid=False, showticklabels=True, tickangle=-45, title=None, tickfont=dict(size=8)),
                    yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)', title=None),
                    showlegend=False, bargap=0.3
                )'''

new_rman_layout = '''                fig_rman.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color=plotly_text_color, size=8),
                    margin=dict(l=5, r=5, t=20, b=5),
                    xaxis=dict(type='category', showgrid=False, showticklabels=True, tickangle=-45, title=None, tickfont=dict(size=8, color=plotly_text_color)),
                    yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)', title=None, tickfont=dict(size=8, color=plotly_text_color)),
                    showlegend=False, bargap=0.3
                )'''

mon_content = mon_content.replace(old_rman_layout, new_rman_layout)

old_rman_traces = '''                fig_rman.update_traces(
                    texttemplate='%{text:.1f}m', textposition='outside', textfont_size=8,
                    hovertemplate="<b>%{x}</b><br>Duration: %{customdata[1]:.2f} min<extra></extra>",
                    customdata=df_rman_all[["INPUT_TYPE", "DURATION_MIN"]].values
                )'''

new_rman_traces = '''                fig_rman.update_traces(
                    texttemplate='%{text:.1f}m', textposition='outside', textfont_size=8, textfont_color=plotly_text_color,
                    hovertemplate="<b>%{x}</b><br>Duration: %{customdata[1]:.2f} min<extra></extra>",
                    customdata=df_rman_all[["INPUT_TYPE", "DURATION_MIN"]].values
                )'''

mon_content = mon_content.replace(old_rman_traces, new_rman_traces)

# 2. Update DB Growth Trends Plotly settings
old_growth_layout = '''            fig_growth.update_layout(
                template=chart_template, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color='var(--text-primary)', size=9), height=230,
                margin=dict(l=5, r=5, t=40, b=5),
                uniformtext_minsize=9, uniformtext_mode='show',
                xaxis=dict(showgrid=False, tickfont=dict(size=9)),
                yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)', range=[0, y_max])
            )
            fig_growth.update_traces(textfont_size=9, textfont_color='var(--text-primary)')'''

new_growth_layout = '''            fig_growth.update_layout(
                template=chart_template, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color=plotly_text_color, size=9), height=230,
                margin=dict(l=5, r=5, t=40, b=5),
                uniformtext_minsize=9, uniformtext_mode='show',
                xaxis=dict(showgrid=False, tickfont=dict(size=9, color=plotly_text_color)),
                yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.06)' if theme_mode == "Dark" else 'rgba(0,0,0,0.04)', range=[0, y_max], tickfont=dict(size=9, color=plotly_text_color))
            )
            fig_growth.update_traces(textfont_size=9, textfont_color=plotly_text_color)'''

mon_content = mon_content.replace(old_growth_layout, new_growth_layout)

with open('dashboard/monitoring.py', 'w', encoding='utf-8') as f:
    f.write(mon_content)

print("monitoring.py colors updated successfully.")
