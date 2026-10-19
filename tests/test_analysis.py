import io

import numpy as np
import pandas as pd
import pytest

from telemetry.analysis import (
    check_alerts,
    delta_time,
    detect_laps,
    lap_table,
    load_log,
    standardize_columns,
)
from telemetry.simulate import simulate_session


@pytest.fixture(scope="module")
def session():
    df = simulate_session(n_laps=5, hz=20, seed=1)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return detect_laps(load_log(buf))


def test_detects_all_laps(session):
    tab = lap_table(session)
    assert tab["completa"].sum() == 5


def test_lap_distance_is_consistent(session):
    tab = lap_table(session)
    dist = tab.loc[tab["completa"], "distancia_m"]
    assert dist.std() / dist.mean() < 0.01  # mesma pista, < 1% de variação


def test_delta_same_lap_is_zero(session):
    d = delta_time(session, 2, 2)
    assert np.allclose(d["delta_s"], 0)


def test_delta_end_matches_lap_time_difference(session):
    tab = lap_table(session)
    d = delta_time(session, 2, 3)
    expected = tab.loc[3, "tempo_volta_s"] - tab.loc[2, "tempo_volta_s"]
    assert d["delta_s"].iloc[-1] == pytest.approx(expected, abs=0.15)


def test_aliases_are_standardized():
    raw = pd.DataFrame({"Time": [0, 1], "Vehicle Speed": [10, 20], "ECT": [80, 81]})
    out, mapping = standardize_columns(raw)
    assert {"time_s", "speed_kmh", "coolant_c"} <= set(out.columns)
    assert mapping["ECT"] == "coolant_c"


def test_missing_required_column_raises():
    with pytest.raises(ValueError, match="obrigatórias"):
        load_log(io.StringIO("rpm\n1000\n"))


def test_overheat_alert_fires():
    df = detect_laps(load_log(io.StringIO(
        simulate_session(n_laps=12, hz=10, seed=42).to_csv(index=False)
    )))
    alerts = check_alerts(df)
    assert (alerts["canal"] == "Temp. água").any()
    assert (alerts["nivel"] == "CRÍTICO").any()
