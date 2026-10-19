"""Gerador de telemetria sintética de um carro Fórmula SAE em prova de enduro.

O objetivo é ter dados realistas o bastante pra desenvolver e demonstrar o
dashboard sem depender de logs reais da equipe.

Modelo (simplificado, mas fisicamente coerente):
  1. Pista fechada gerada por spline periódica.
  2. Perfil de velocidade "quasi-steady-state": limite por aderência lateral em
     cada ponto (v = sqrt(a_lat / curvatura)) + passadas para frente
     (aceleração) e para trás (frenagem) limitando dv/ds.
  3. Canais derivados: marcha, RPM, acelerador, pressão de freio, forças g,
     temperaturas (água/óleo) e tensão de bateria.
  4. Ruído de sensor e variação de piloto por volta.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.interpolate import splev, splprep

G = 9.81

# Parâmetros do carro (ordem de grandeza de um FSAE monocilíndrico 600cc)
CAR = {
    "a_lat_max": 1.45 * G,    # aderência lateral
    "a_acc_max": 0.75 * G,    # tração
    "a_brk_max": 1.40 * G,    # frenagem
    "v_max": 30.0,            # m/s (~108 km/h)
    "tyre_radius": 0.235,     # m
    "final_drive": 6.0,
    "gears": [2.75, 2.00, 1.67, 1.44, 1.30, 1.21],
    "rpm_shift": 11500,
    "rpm_idle": 3000,
}


def make_track(seed: int = 21, n: int = 4000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Gera uma pista fechada (~1 km) e retorna x, y e distância acumulada."""
    rng = np.random.default_rng(seed)
    k = 20
    ang = np.linspace(0, 2 * np.pi, k, endpoint=False)
    r = 70 + rng.uniform(-38, 38, k)
    px, py = r * np.cos(ang) * 1.6, r * np.sin(ang)
    px, py = np.r_[px, px[0]], np.r_[py, py[0]]
    tck, _ = splprep([px, py], s=0, per=True)
    u = np.linspace(0, 1, n, endpoint=False)
    x, y = splev(u, tck)
    x, y = np.asarray(x), np.asarray(y)
    # coloca a linha de chegada no meio da reta mais longa (menor curvatura média)
    kk = np.abs(curvature(x, y))
    win = n // 25
    smooth = np.convolve(np.r_[kk[-win:], kk, kk[:win]], np.ones(2 * win + 1), "valid")
    shift = int(np.argmin(smooth))
    x, y = np.roll(x, -shift), np.roll(y, -shift)
    seg = np.hypot(np.diff(x, append=x[0]), np.diff(y, append=y[0]))
    s = np.r_[0, np.cumsum(seg)[:-1]]
    return x, y, s


def curvature(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Curvatura com sinal (positiva = curva à esquerda)."""
    dx, dy = np.gradient(x), np.gradient(y)
    ddx, ddy = np.gradient(dx), np.gradient(dy)
    return (dx * ddy - dy * ddx) / np.power(dx**2 + dy**2, 1.5)


def speed_profile(s: np.ndarray, k: np.ndarray, grip: float = 1.0) -> np.ndarray:
    """Velocidade máxima em cada ponto com círculo de atrito.

    A aceleração longitudinal disponível diminui quando o pneu já está usando
    aderência lateral: a_long = a_max * sqrt(1 - (a_lat / a_lat_max)^2).
    """
    a_lat_max = CAR["a_lat_max"] * grip
    v = np.minimum(np.sqrt(a_lat_max / np.maximum(k, 1e-6)), CAR["v_max"])
    ds = np.diff(s, append=s[-1] + (s[1] - s[0]))

    def avail(vel: float, kk: float, a_max: float) -> float:
        use = min((vel**2 * kk) / a_lat_max, 1.0)
        return a_max * np.sqrt(max(1.0 - use**2, 0.05))

    n = len(v)
    for _ in range(2):  # duas voltas para fechar o laço periódico
        for i in range(n):  # aceleração
            p = i - 1
            a = avail(v[p], k[p], CAR["a_acc_max"] * grip)
            v[i] = min(v[i], np.sqrt(v[p] ** 2 + 2 * a * ds[p]))
        for i in range(n - 1, -1, -1):  # frenagem
            nx = (i + 1) % n
            a = avail(v[nx], k[nx], CAR["a_brk_max"] * grip)
            v[i] = min(v[i], np.sqrt(v[nx] ** 2 + 2 * a * ds[i]))
    return v


def smooth_noise(n: int, rng: np.random.Generator, scale: float, width: int) -> np.ndarray:
    """Ruído suave (variação de piloto ao longo da volta)."""
    raw = rng.normal(0, 1, n + width)
    ker = np.hanning(width)
    out = np.convolve(raw, ker / ker.sum(), mode="same")[:n]
    return out / (out.std() + 1e-9) * scale


def lag(x: np.ndarray, hz: int, tau: float) -> np.ndarray:
    """Filtro de 1ª ordem (pedal/atuador não muda instantaneamente)."""
    a = 1 / (1 + tau * hz)
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = y[i - 1] + a * (x[i] - y[i - 1])
    return y


def gear_and_rpm(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wheel_rpm = v / (2 * np.pi * CAR["tyre_radius"]) * 60
    gear = np.ones_like(v, dtype=int)
    rpm = wheel_rpm * CAR["final_drive"] * CAR["gears"][0]
    for g_idx, ratio in enumerate(CAR["gears"][1:], start=2):
        r = wheel_rpm * CAR["final_drive"] * ratio
        mask = rpm > CAR["rpm_shift"]
        gear[mask] = g_idx
        rpm = np.where(mask, r, rpm)
    rpm = np.clip(rpm, CAR["rpm_idle"], 12500)
    return gear, rpm


def simulate_session(
    n_laps: int = 12,
    hz: int = 50,
    seed: int = 42,
    overheat_from_lap: int | None = 9,
) -> pd.DataFrame:
    """Gera uma sessão completa de enduro amostrada a `hz` Hz."""
    rng = np.random.default_rng(seed)
    x, y, s = make_track()
    track_len = s[-1] + (s[1] - s[0])
    k_signed = curvature(x, y)
    k = np.abs(k_signed)

    frames = []
    t0 = 0.0
    coolant, oil = 75.0, 80.0
    t_amb = 30.0
    for lap in range(1, n_laps + 1):
        # piloto melhora nas primeiras voltas e pneu degrada no fim
        grip = 0.90 + 0.06 * (1 - np.exp(-lap / 2.5)) - 0.004 * max(0, lap - 7)
        grip *= rng.normal(1.0, 0.008)
        v_track = speed_profile(s, k, grip)
        # piloto não é perfeito: perde/ganha um pouco em cada trecho
        v_track = v_track * (1 - np.abs(smooth_noise(len(s), rng, 0.018, 160)))

        # integra no tempo: dt = ds / v
        ds = np.diff(s, append=track_len)
        dt = ds / np.maximum(v_track, 1.0)
        t_track = np.r_[0, np.cumsum(dt)[:-1]]
        lap_time = t_track[-1] + dt[-1]

        t = np.arange(0, lap_time, 1 / hz)
        dist = np.interp(t, t_track, s)
        v = np.interp(t, t_track, v_track)
        px = np.interp(dist, s, x)
        py = np.interp(dist, s, y)
        kk = np.interp(dist, s, k_signed)

        a_long = np.gradient(v, t)
        a_lat = v**2 * kk
        gear, rpm = gear_and_rpm(v)

        throttle = np.clip(a_long / (CAR["a_acc_max"] * grip) * 100, 0, 100)
        throttle = np.where(a_long > -1.0, np.maximum(throttle, 22), 0)  # gás de manutenção em curva
        brake = np.clip(-a_long / CAR["a_brk_max"] * 65, 0, 70)  # bar
        throttle = np.clip(lag(throttle, hz, 0.08), 0, 100)
        brake = lag(brake, hz, 0.06)

        # térmica: aquece com rpm/carga, resfria com velocidade
        # falha simulada (ex.: radiador obstruído) a partir de certa volta
        extra = 0.12 if (overheat_from_lap and lap >= overheat_from_lap) else 0.0
        cool = np.empty_like(t)
        oil_t = np.empty_like(t)
        for i in range(len(t)):
            load = 0.35 + 0.65 * (rpm[i] / 12000) * (throttle[i] / 100)
            airflow = 0.5 + v[i] / 30
            coolant += (0.50 * load - 0.0045 * (coolant - t_amb) * airflow + extra) / hz
            oil += (0.50 * load - 0.0040 * (oil - t_amb) * airflow + extra * 0.7) / hz
            cool[i], oil_t[i] = coolant, oil

        batt = 13.6 - 0.15 * (rpm < 3500) + rng.normal(0, 0.03, len(t))

        frames.append(
            pd.DataFrame(
                {
                    "time_s": t0 + t,
                    "pos_x_m": px + rng.normal(0, 0.4, len(t)),
                    "pos_y_m": py + rng.normal(0, 0.4, len(t)),
                    "speed_kmh": v * 3.6 + rng.normal(0, 0.4, len(t)),
                    "rpm": rpm + rng.normal(0, 40, len(t)),
                    "gear": gear,
                    "throttle_pct": np.clip(throttle + rng.normal(0, 1.0, len(t)), 0, 100),
                    "brake_bar": np.clip(brake + rng.normal(0, 0.3, len(t)), 0, None),
                    "g_long": a_long / G + rng.normal(0, 0.03, len(t)),
                    "g_lat": a_lat / G + rng.normal(0, 0.03, len(t)),
                    "coolant_c": cool + rng.normal(0, 0.15, len(t)),
                    "oil_c": oil_t + rng.normal(0, 0.15, len(t)),
                    "battery_v": batt,
                }
            )
        )
        t0 += lap_time

    df = pd.concat(frames, ignore_index=True)
    return df.round(3)


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Gera um log sintético de enduro FSAE.")
    ap.add_argument("-o", "--out", default="data/sample_endurance.csv")
    ap.add_argument("--laps", type=int, default=12)
    ap.add_argument("--hz", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = simulate_session(n_laps=args.laps, hz=args.hz, seed=args.seed)
    data.to_csv(out, index=False)
    print(f"{len(data):,} amostras, {data.time_s.iloc[-1]:.1f} s -> {out}")
