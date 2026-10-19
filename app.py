"""Dashboard de telemetria Fórmula SAE — rode com: streamlit run app.py"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from telemetry.analysis import (
    CHANNEL_LABELS,
    DEFAULT_RULES,
    AlertRule,
    check_alerts,
    delta_time,
    detect_laps,
    lap_table,
    lap_trace,
    load_log,
)

SAMPLE = Path(__file__).parent / "data" / "sample_endurance.csv"
RED, WHITE, GREY = "#FF2A1F", "#F4F4F5", "#71717A"
LAP_COLORS = ["#FF2A1F", "#38BDF8", "#FACC15", "#A3E635", "#C084FC", "#FB923C"]

st.set_page_config(page_title="FSAE Telemetry", page_icon="🏁", layout="wide")

PLOT_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=70, b=10),
    font=dict(family="Inter, sans-serif"),
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
)


@st.cache_data(show_spinner="Processando log…")
def process(file_bytes: bytes | None, radius: float) -> pd.DataFrame:
    import io

    src = io.BytesIO(file_bytes) if file_bytes is not None else SAMPLE
    return detect_laps(load_log(src), radius_m=radius)


def fmt_lap(sec: float) -> str:
    if pd.isna(sec):
        return "—"
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


# ───────────────────────── Sidebar ─────────────────────────
with st.sidebar:
    st.markdown("## 🏁 FSAE Telemetry")
    up = st.file_uploader("Log da sessão (CSV)", type=["csv", "txt"])
    st.caption("Sem arquivo? É usado um enduro simulado de 12 voltas.")
    radius = st.slider("Raio da linha de chegada (m)", 5, 40, 15)

    with st.expander("Limites de alerta"):
        rules: list[AlertRule] = []
        for r in DEFAULT_RULES:
            c1, c2 = st.columns(2)
            w = c1.number_input(f"{r.label} – atenção", value=float(r.warn), key=f"w_{r.channel}")
            c = c2.number_input("crítico", value=float(r.crit), key=f"c_{r.channel}")
            rules.append(AlertRule(r.channel, r.label, w, c, r.above))

try:
    df = process(up.getvalue() if up else None, radius)
except ValueError as e:
    st.error(f"Não consegui ler o arquivo: {e}")
    st.stop()

laps = lap_table(df)
complete = laps[laps["completa"]]
if complete.empty:
    st.warning("Nenhuma volta completa detectada. Ajuste o raio da linha de chegada.")
    st.stop()

best_lap = int(complete["tempo_volta_s"].idxmin())
channels = [c for c in CHANNEL_LABELS if c in df.columns]
alerts = check_alerts(df[df["lap"] > 0], rules)

# ───────────────────────── Header + KPIs ─────────────────────────
st.markdown(
    f"<h1 style='margin-bottom:0'>Telemetria <span style='color:{RED}'>Fórmula SAE</span></h1>",
    unsafe_allow_html=True,
)
st.caption(
    f"{'Arquivo: ' + up.name if up else 'Sessão de exemplo (simulada)'} · "
    f"{len(df):,} amostras · {df['time_s'].iloc[-1] - df['time_s'].iloc[0]:.0f} s de dados"
)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric(f"Melhor volta · #{best_lap}", fmt_lap(complete["tempo_volta_s"].min()))
k2.metric(f"Média · σ {complete['tempo_volta_s'].std():.2f} s", fmt_lap(complete["tempo_volta_s"].mean()))
k3.metric("Vel. máxima", f"{df['speed_kmh'].max():.1f} km/h")
k4.metric("Voltas completas", len(complete))
n_crit = int((alerts["nivel"] == "CRÍTICO").sum()) if not alerts.empty else 0
k5.metric(f"Alertas · {n_crit} crítico(s)" if n_crit else "Alertas", len(alerts))

if not alerts.empty:
    worst = alerts.sort_values("nivel", key=lambda s: s.map({"CRÍTICO": 0, "ATENÇÃO": 1}), kind="stable").iloc[0]
    msg = (f"**{worst['canal']}** passou de {worst['limite']:g} na volta {worst['primeira_volta']} "
           f"(pior valor {worst['pior_valor']:g}). Voltas afetadas: {worst['voltas']}.")
    (st.error if worst["nivel"] == "CRÍTICO" else st.warning)(msg, icon="🚨")

tab_over, tab_map, tab_cmp, tab_ch, tab_gg, tab_th = st.tabs(
    ["📊 Visão geral", "🗺️ Pista", "⏱️ Comparar voltas", "📈 Canais", "🎯 Diagrama G-G", "🌡️ Térmica"]
)

# ───────────────────────── Visão geral ─────────────────────────
with tab_over:
    c1, c2 = st.columns([3, 2])
    with c1:
        colors = [RED if i == best_lap else GREY for i in complete.index]
        fig = go.Figure(go.Bar(
            x=complete.index, y=complete["tempo_volta_s"], marker_color=colors,
            text=[fmt_lap(v) for v in complete["tempo_volta_s"]], textposition="outside",
            hovertemplate="Volta %{x}<br>%{text}<extra></extra>",
        ))
        lo, hi = complete["tempo_volta_s"].min(), complete["tempo_volta_s"].max()
        fig.update_layout(**PLOT_LAYOUT, title="Tempo por volta", height=380,
                          xaxis_title="Volta", yaxis_title="s",
                          yaxis_range=[lo - (hi - lo) * 1.5 - 0.5, hi + (hi - lo) * 0.6 + 0.3])
        fig.update_xaxes(dtick=1)
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("##### Alertas")
        if alerts.empty:
            st.success("Nenhum limite ultrapassado.")
        else:
            st.dataframe(
                alerts.rename(columns={
                    "nivel": "Nível", "canal": "Canal", "primeira_volta": "1ª volta",
                    "primeiro_tempo_s": "t (s)", "pior_valor": "Pior", "limite": "Limite",
                    "voltas": "Voltas"}),
                hide_index=True, width="stretch",
            )

    st.markdown("##### Resumo por volta")
    show = laps.copy()
    show["tempo_volta"] = show["tempo_volta_s"].map(fmt_lap)
    cols = ["tempo_volta", "gap_melhor_s", "vel_max_kmh", "vel_media_kmh"] + [
        c for c in ["rpm_max", "freio_max_bar", "agua_max_c", "oleo_max_c", "bateria_min_v"] if c in show
    ]
    st.dataframe(
        show[cols].rename(columns={
            "tempo_volta": "Tempo", "gap_melhor_s": "Gap (s)", "vel_max_kmh": "V máx",
            "vel_media_kmh": "V média", "rpm_max": "RPM máx", "freio_max_bar": "Freio máx",
            "agua_max_c": "Água máx", "oleo_max_c": "Óleo máx", "bateria_min_v": "Bat. mín"}),
        width="stretch",
    )

# ───────────────────────── Mapa da pista ─────────────────────────
with tab_map:
    if not {"pos_x_m", "pos_y_m"} <= set(df.columns):
        st.info("O log não tem posição (pos_x_m/pos_y_m), então não dá pra desenhar a pista.")
    else:
        c1, c2 = st.columns([1, 3])
        lap_sel = c1.selectbox("Volta", complete.index, index=list(complete.index).index(best_lap))
        ch = c1.selectbox("Colorir por", channels, format_func=CHANNEL_LABELS.get)
        d = df[df["lap"] == lap_sel]
        scale = "Turbo" if ch not in ("brake_bar",) else "Reds"
        fig = go.Figure(go.Scattergl(
            x=d["pos_x_m"], y=d["pos_y_m"], mode="markers",
            marker=dict(color=d[ch], colorscale=scale, size=6,
                        colorbar=dict(title=CHANNEL_LABELS[ch])),
            hovertemplate=f"{CHANNEL_LABELS[ch]}: %{{marker.color:.1f}}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(x=[d["pos_x_m"].iloc[0]], y=[d["pos_y_m"].iloc[0]], mode="markers+text",
                                 marker=dict(symbol="square", size=14, color=WHITE),
                                 text=["Largada"], textposition="top center", showlegend=False))
        fig.update_layout(**PLOT_LAYOUT, height=560, showlegend=False,
                          title=f"Volta {lap_sel} — {CHANNEL_LABELS[ch]}")
        fig.update_yaxes(scaleanchor="x", scaleratio=1, visible=False)
        fig.update_xaxes(visible=False)
        c2.plotly_chart(fig, width="stretch")

# ───────────────────────── Comparar voltas ─────────────────────────
with tab_cmp:
    c1, c2, c3 = st.columns(3)
    ref = c1.selectbox("Volta de referência", complete.index,
                       index=list(complete.index).index(best_lap), key="ref")
    others = [i for i in complete.index if i != ref]
    cmp_laps = c2.multiselect("Comparar com", others, default=others[-1:])
    ch = c3.selectbox("Canal", channels, format_func=CHANNEL_LABELS.get, key="cmp_ch")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                        vertical_spacing=0.04)
    for i, lap in enumerate([ref] + cmp_laps):
        tr = lap_trace(df, lap, ch)
        name = f"Volta {lap} ({fmt_lap(laps.loc[lap, 'tempo_volta_s'])})"
        fig.add_trace(go.Scatter(x=tr["lap_dist_m"], y=tr[ch], name=name,
                                 line=dict(color=LAP_COLORS[i % len(LAP_COLORS)], width=2 if i == 0 else 1.5)),
                      row=1, col=1)
        if lap != ref:
            dt = delta_time(df, ref, lap)
            fig.add_trace(go.Scatter(x=dt["lap_dist_m"], y=dt["delta_s"], showlegend=False,
                                     line=dict(color=LAP_COLORS[i % len(LAP_COLORS)], width=1.5),
                                     hovertemplate="Δ %{y:+.3f} s<extra></extra>"), row=2, col=1)
    fig.add_hline(y=0, line=dict(color=GREY, dash="dot"), row=2, col=1)
    fig.update_layout(**PLOT_LAYOUT, height=620, title=f"{CHANNEL_LABELS[ch]} ao longo da volta")
    fig.update_yaxes(title_text=CHANNEL_LABELS[ch], row=1, col=1)
    fig.update_yaxes(title_text="Δ tempo (s)", row=2, col=1)
    fig.update_xaxes(title_text="Distância na volta (m)", row=2, col=1)
    st.plotly_chart(fig, width="stretch")
    st.caption("Δ tempo positivo = a volta comparada está perdendo tempo para a referência naquele ponto.")

# ───────────────────────── Canais ─────────────────────────
with tab_ch:
    c1, c2 = st.columns([1, 3])
    sel = c1.multiselect("Canais", channels,
                         default=[c for c in ["speed_kmh", "throttle_pct", "brake_bar", "rpm", "gear"] if c in channels],
                         format_func=CHANNEL_LABELS.get)
    lap_ch = c1.selectbox("Volta", ["Sessão inteira"] + list(complete.index), index=1 + list(complete.index).index(best_lap))
    d = df if lap_ch == "Sessão inteira" else df[df["lap"] == lap_ch]
    xcol = "time_s" if lap_ch == "Sessão inteira" else "lap_dist_m"
    if sel:
        fig = make_subplots(rows=len(sel), cols=1, shared_xaxes=True, vertical_spacing=0.02)
        for i, c in enumerate(sel, start=1):
            fig.add_trace(go.Scattergl(x=d[xcol], y=d[c], name=CHANNEL_LABELS[c],
                                       line=dict(width=1.3, color=LAP_COLORS[(i - 1) % len(LAP_COLORS)])),
                          row=i, col=1)
            fig.update_yaxes(title_text=CHANNEL_LABELS[c].split(" (")[0], row=i, col=1, title_font_size=11)
        fig.update_layout(**PLOT_LAYOUT, height=150 * len(sel) + 80, showlegend=False)
        fig.update_xaxes(title_text="Tempo (s)" if xcol == "time_s" else "Distância na volta (m)",
                         row=len(sel), col=1)
        c2.plotly_chart(fig, width="stretch")

# ───────────────────────── G-G ─────────────────────────
with tab_gg:
    if {"g_lat", "g_long"} <= set(df.columns):
        c1, c2 = st.columns([3, 2])
        d = df[df["lap"].isin(complete.index)]
        heat = [[0, "rgba(0,0,0,0)"], [0.15, "rgba(120,10,5,0.5)"], [0.5, "#FF2A1F"], [1, "#FFE08A"]]
        fig = go.Figure(go.Histogram2dContour(x=d["g_lat"], y=d["g_long"], colorscale=heat,
                                              showscale=False, ncontours=14,
                                              contours=dict(coloring="fill", showlines=False)))
        fig.add_trace(go.Scattergl(x=d["g_lat"], y=d["g_long"], mode="markers",
                                   marker=dict(size=2, color=WHITE, opacity=0.25), showlegend=False))
        theta = np.linspace(0, 2 * np.pi, 200)
        for g in (0.5, 1.0, 1.5):
            fig.add_trace(go.Scatter(x=g * np.cos(theta), y=g * np.sin(theta), mode="lines",
                                     line=dict(color=GREY, dash="dot", width=1), showlegend=False,
                                     hoverinfo="skip"))
        fig.update_layout(**PLOT_LAYOUT, height=560, title="Envelope de aderência (todas as voltas)",
                          xaxis_title="G lateral", yaxis_title="G longitudinal")
        fig.update_yaxes(scaleanchor="x", scaleratio=1, range=[-1.8, 1.8])
        fig.update_xaxes(range=[-1.8, 1.8])
        c1.plotly_chart(fig, width="stretch")
        comb = np.hypot(d["g_lat"], d["g_long"])
        c2.metric("G lateral máx", f"{d['g_lat'].abs().max():.2f} g")
        c2.metric("G frenagem máx", f"{-d['g_long'].min():.2f} g")
        c2.metric("G aceleração máx", f"{d['g_long'].max():.2f} g")
        c2.metric("Tempo acima de 1 g combinado", f"{(comb > 1).mean() * 100:.0f}%")
        c2.caption("Quanto mais 'cheio' o círculo, mais o piloto usa a aderência disponível "
                   "combinando frenagem/aceleração com curva.")
    else:
        st.info("O log não tem acelerômetro (g_lat / g_long).")

# ───────────────────────── Térmica ─────────────────────────
with tab_th:
    th = [c for c in ["coolant_c", "oil_c"] if c in df.columns]
    if th:
        fig = go.Figure()
        for c, color in zip(th, [RED, "#FACC15"]):
            fig.add_trace(go.Scattergl(x=df["time_s"], y=df[c], name=CHANNEL_LABELS[c], line=dict(color=color)))
            rule = next((r for r in rules if r.channel == c), None)
            if rule:
                fig.add_hline(y=rule.crit, line=dict(color=color, dash="dash", width=1),
                              annotation_text=f"crítico {rule.label.lower()} {rule.crit:g}°C",
                              annotation_position="top left", annotation_font_color=color)
        for lap, row in complete.iterrows():
            fig.add_vline(x=row["inicio_s"], line=dict(color="rgba(255,255,255,0.08)", width=1))
        fig.update_layout(**PLOT_LAYOUT, height=480, title="Temperaturas ao longo da sessão",
                          xaxis_title="Tempo (s)", yaxis_title="°C")
        st.plotly_chart(fig, width="stretch")

        trend = laps.loc[complete.index, [c for c in ["agua_max_c", "oleo_max_c"] if c in laps]]
        slope = np.polyfit(trend.index[-4:], trend.iloc[-4:, 0], 1)[0] if len(trend) >= 4 else 0
        if slope > 0.8:
            st.warning(f"A temperatura máxima da água está subindo ~{slope:.1f} °C por volta nas últimas voltas. "
                       "Vale checar radiador, ventoinha e nível de água antes do próximo stint.", icon="🌡️")
    else:
        st.info("O log não tem canais de temperatura.")

with st.expander("Formato esperado do CSV"):
    st.markdown(
        "Colunas obrigatórias: `time_s`, `speed_kmh`. Opcionais: "
        + ", ".join(f"`{c}`" for c in CHANNEL_LABELS if c not in ("speed_kmh",))
        + ", `pos_x_m`, `pos_y_m`.\n\nNomes comuns de dataloggers (ex.: `RPM`, `TPS`, `ECT`, "
        "`Vehicle Speed`) são reconhecidos automaticamente."
    )
