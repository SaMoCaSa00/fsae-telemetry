"""Carregamento, detecção de voltas e análises de telemetria."""
from __future__ import annotations

from dataclasses import dataclass
from typing import IO

import numpy as np
import pandas as pd

# Nome padrão -> apelidos comuns em exportações de ECU/datalogger
CHANNEL_ALIASES: dict[str, list[str]] = {
    "time_s": ["time", "tempo", "t", "timestamp", "time [s]"],
    "pos_x_m": ["x", "pos_x", "gps_x"],
    "pos_y_m": ["y", "pos_y", "gps_y"],
    "speed_kmh": ["speed", "velocidade", "vehicle speed", "gps speed", "vel"],
    "rpm": ["engine speed", "rotacao", "rotação", "engine rpm"],
    "gear": ["marcha", "gear position"],
    "throttle_pct": ["throttle", "tps", "acelerador", "throttle position"],
    "brake_bar": ["brake", "brake pressure", "freio", "pressao freio"],
    "g_long": ["accel x", "long g", "ax", "glong"],
    "g_lat": ["accel y", "lat g", "ay", "glat"],
    "coolant_c": ["coolant", "ect", "water temp", "temp agua", "temperatura agua"],
    "oil_c": ["oil temp", "temp oleo", "temperatura oleo"],
    "battery_v": ["battery", "bateria", "vbat", "battery voltage"],
}

CHANNEL_LABELS: dict[str, str] = {
    "speed_kmh": "Velocidade (km/h)",
    "rpm": "RPM",
    "gear": "Marcha",
    "throttle_pct": "Acelerador (%)",
    "brake_bar": "Pressão de freio (bar)",
    "g_long": "G longitudinal",
    "g_lat": "G lateral",
    "coolant_c": "Temp. água (°C)",
    "oil_c": "Temp. óleo (°C)",
    "battery_v": "Bateria (V)",
}

REQUIRED = ["time_s", "speed_kmh"]


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def standardize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Renomeia colunas conhecidas para o padrão do projeto.

    Retorna o DataFrame renomeado e o mapeamento aplicado (original -> padrão).
    """
    mapping: dict[str, str] = {}
    lookup = {}
    for std, aliases in CHANNEL_ALIASES.items():
        lookup[_norm(std)] = std
        for a in aliases:
            lookup[_norm(a)] = std
    for col in df.columns:
        std = lookup.get(_norm(col))
        if std and std not in mapping.values() and col != std:
            mapping[col] = std
    return df.rename(columns=mapping), mapping


def load_log(source: str | IO) -> pd.DataFrame:
    """Lê um CSV de telemetria, padroniza colunas e valida o mínimo necessário."""
    df = pd.read_csv(source, sep=None, engine="python")
    df, _ = standardize_columns(df)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"Colunas obrigatórias ausentes: {missing}. "
            f"Encontradas: {list(df.columns)}"
        )
    df = df.sort_values("time_s").reset_index(drop=True)
    df = df.apply(pd.to_numeric, errors="coerce")
    return add_distance(df)


def add_distance(df: pd.DataFrame) -> pd.DataFrame:
    """Distância acumulada (m) integrando a velocidade no tempo (regra do trapézio)."""
    v = df["speed_kmh"].to_numpy() / 3.6
    t = df["time_s"].to_numpy()
    seg = np.r_[0.0, np.diff(t) * (v[1:] + v[:-1]) / 2]
    out = df.copy()
    out["dist_m"] = np.cumsum(seg)
    return out


def detect_laps(
    df: pd.DataFrame,
    start_xy: tuple[float, float] | None = None,
    radius_m: float = 15.0,
    min_lap_s: float = 15.0,
) -> pd.DataFrame:
    """Marca cada amostra com o número da volta.

    A linha de chegada é um ponto (por padrão a posição inicial do log). Cada vez
    que o carro passa a menos de `radius_m` desse ponto, depois de pelo menos
    `min_lap_s` desde a última passagem, uma nova volta começa (no instante de
    menor distância ao ponto).

    Sem GPS, cai para o modo por distância não é possível — nesse caso tudo vira
    volta 1.
    """
    out = df.copy()
    if not {"pos_x_m", "pos_y_m"} <= set(out.columns):
        out["lap"] = 1
        out["lap_dist_m"] = out["dist_m"]
        out["lap_time_s"] = out["time_s"] - out["time_s"].iloc[0]
        return out

    x, y, t = out["pos_x_m"].to_numpy(), out["pos_y_m"].to_numpy(), out["time_s"].to_numpy()
    sx, sy = start_xy if start_xy is not None else (x[0], y[0])
    d = np.hypot(x - sx, y - sy)

    crossings: list[int] = [0] if d[0] < radius_m else []
    i, n = 0, len(d)
    while i < n:
        if d[i] < radius_m and (not crossings or t[i] - t[crossings[-1]] >= min_lap_s):
            # histerese: só considera que saiu da zona quando passar de 2x o raio
            # (evita que ruído de GPS na borda quebre a passagem em duas)
            j = i
            while j < n and d[j] < 2 * radius_m:
                j += 1
            k = i + int(np.argmin(d[i:j]))
            if not crossings or k != crossings[-1]:
                crossings.append(k)
            i = j
        else:
            i += 1

    # descarta um "pedaço" final com menos de 1 s (log terminou em cima da linha)
    if len(crossings) > 1 and t[-1] - t[crossings[-1]] < 1.0:
        crossings.pop()

    lap = np.zeros(n, dtype=int)
    for num, idx in enumerate(crossings, start=1):
        lap[idx:] = num
    out["lap"] = lap  # 0 = antes da primeira passagem (out lap)

    first = out.groupby("lap")[["dist_m", "time_s"]].transform("first")
    out["lap_dist_m"] = out["dist_m"] - first["dist_m"]
    out["lap_time_s"] = out["time_s"] - first["time_s"]
    return out


def lap_table(df: pd.DataFrame) -> pd.DataFrame:
    """Resumo por volta. A última volta só conta se fechou (houve passagem depois)."""
    g = df.groupby("lap")
    agg: dict[str, tuple[str, str]] = {
        "inicio_s": ("time_s", "first"),
        "fim_s": ("time_s", "last"),
        "distancia_m": ("lap_dist_m", "max"),
        "vel_max_kmh": ("speed_kmh", "max"),
        "vel_media_kmh": ("speed_kmh", "mean"),
    }
    for col, name in [("rpm", "rpm_max"), ("coolant_c", "agua_max_c"), ("oil_c", "oleo_max_c"),
                      ("brake_bar", "freio_max_bar"), ("battery_v", "bateria_min_v")]:
        if col in df.columns:
            agg[name] = (col, "min" if name.endswith("_min_v") else "max")
    tab = g.agg(**agg)

    # tempo de volta = início da próxima volta - início desta
    starts = tab["inicio_s"].to_numpy()
    lap_times = np.r_[np.diff(starts), np.nan]
    # se o log termina logo após a última volta, ela também está completa
    med = np.nanmedian(tab["distancia_m"][tab.index > 0])
    if tab["distancia_m"].iloc[-1] >= 0.97 * med:
        lap_times[-1] = tab["fim_s"].iloc[-1] - starts[-1]
    tab["tempo_volta_s"] = lap_times
    tab["completa"] = tab["tempo_volta_s"].notna() & (tab.index > 0)
    tab = tab[tab.index > 0]
    best = tab.loc[tab["completa"], "tempo_volta_s"].min()
    tab["gap_melhor_s"] = tab["tempo_volta_s"] - best
    return tab.round(2)


def lap_trace(df: pd.DataFrame, lap: int, channel: str, step_m: float = 2.0) -> pd.DataFrame:
    """Canal reamostrado em função da distância na volta (grade regular)."""
    d = df[df["lap"] == lap]
    grid = np.arange(0, d["lap_dist_m"].max(), step_m)
    return pd.DataFrame({
        "lap_dist_m": grid,
        channel: np.interp(grid, d["lap_dist_m"], d[channel]),
    })


def delta_time(df: pd.DataFrame, ref_lap: int, cmp_lap: int, step_m: float = 2.0) -> pd.DataFrame:
    """Delta de tempo acumulado (cmp - ref) ao longo da distância.

    Positivo = volta comparada está atrás da de referência naquele ponto.
    """
    a = df[df["lap"] == ref_lap]
    b = df[df["lap"] == cmp_lap]
    # a distância integrada varia alguns metros entre voltas (ruído/trajetória),
    # então normaliza as duas para a fração da volta antes de comparar
    la, lb = a["lap_dist_m"].max(), b["lap_dist_m"].max()
    frac = np.linspace(0, 1, int(la / step_m) + 1)
    ta = np.interp(frac, a["lap_dist_m"] / la, a["lap_time_s"])
    tb = np.interp(frac, b["lap_dist_m"] / lb, b["lap_time_s"])
    return pd.DataFrame({"lap_dist_m": frac * la, "delta_s": tb - ta})


@dataclass
class AlertRule:
    channel: str
    label: str
    warn: float
    crit: float
    above: bool = True  # True: alerta quando passa de cima; False: quando cai abaixo


DEFAULT_RULES = [
    AlertRule("coolant_c", "Temp. água", warn=98, crit=103),
    AlertRule("oil_c", "Temp. óleo", warn=105, crit=112),
    AlertRule("rpm", "Rotação", warn=12000, crit=12800),
    AlertRule("battery_v", "Bateria", warn=12.6, crit=12.0, above=False),
]


def check_alerts(df: pd.DataFrame, rules: list[AlertRule] = DEFAULT_RULES) -> pd.DataFrame:
    """Alertas por canal e nível: primeira ocorrência, pior valor e voltas afetadas."""
    rows = []
    for r in rules:
        if r.channel not in df.columns:
            continue
        v = df[r.channel]
        for level, lim in (("CRÍTICO", r.crit), ("ATENÇÃO", r.warn)):
            hit = v >= lim if r.above else v <= lim
            if level == "ATENÇÃO":
                crit_hit = v >= r.crit if r.above else v <= r.crit
                hit = hit & ~crit_hit
            if not hit.any():
                continue
            sub = df[hit]
            first = sub.iloc[0]
            worst = sub[r.channel].max() if r.above else sub[r.channel].min()
            rows.append({
                "nivel": level,
                "canal": r.label,
                "primeira_volta": int(first["lap"]),
                "primeiro_tempo_s": round(float(first["time_s"]), 1),
                "pior_valor": round(float(worst), 2),
                "limite": lim,
                "voltas": ", ".join(str(int(v)) for v in sorted(sub["lap"].unique())),
            })
    cols = ["nivel", "canal", "primeira_volta", "primeiro_tempo_s", "pior_valor", "limite", "voltas"]
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["primeiro_tempo_s"]).reset_index(drop=True)
