"""Treino que aprende com o histórico, prontidão pelo normal da pessoa, nota de esforço e calor."""
import json
import math
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import adaptive as AD, clock, readiness, weather

TODAY = date(2026, 11, 16)


def _sess(d, eid, load, reps=8, srpe=None, mins=60):
    s = {"date": d.isoformat() + "T08:00", "items": [{"exercise_id": eid, "sets": [{"reps": reps, "load_kg": load}] * 3}],
         "duration_min": mins}
    if srpe is not None:
        s["srpe"] = srpe
    return s


def _history(eid, loads, start_days_ago=40, every=4):
    return [_sess(TODAY - timedelta(days=start_days_ago - k * every), eid, l) for k, l in enumerate(loads)]


PLAN = {"id": 1, "days": [{"items": [
    {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "role": "main", "sets": 4, "phase": "principal"},
    {"exercise_id": "Incline_Dumbbell_Press", "role": "sec", "sets": 3, "phase": "principal"},
    {"exercise_id": "Barbell_Squat", "role": "main", "sets": 4, "phase": "principal"},
    {"exercise_id": "Leg_Extensions", "role": "acc", "sets": 3, "phase": "principal"},
]}]}


def test_trend_classifies_progress_stall_and_fall():
    up = AD.trend(AD.series(_history("X", [60, 62, 64, 66, 68, 70]), "X", TODAY))
    flat = AD.trend(AD.series(_history("X", [60, 60, 60.5, 60, 60, 60.5]), "X", TODAY))
    down = AD.trend(AD.series(_history("X", [70, 70, 68, 66, 65, 65]), "X", TODAY))
    assert up["status"] == "progredindo" and flat["status"] == "parado" and down["status"] == "caindo"
    assert AD.trend(AD.series(_history("X", [60, 61]), "X", TODAY)) is None   # pouco dado: não conclui


def test_stalled_muscle_with_good_recovery_gets_one_more_set():
    sessions = _history("Barbell_Bench_Press_-_Medium_Grip", [60, 60, 60, 60.5, 60, 60]) + \
        _history("Barbell_Squat", [80, 83, 86, 89, 92, 95])
    res = AD.proposals(PLAN, sessions, TODAY, {"sleep_avg7": 7.5, "hrv_band": "normal"})
    up = next(p for p in res["items"] if p["kind"] == "volume_up")
    assert "chest" in up["muscles"] and "quadriceps" not in up["muscles"]
    plan = json.loads(json.dumps(PLAN))
    changed = AD.apply_volume(plan, ["chest"], 1)
    assert changed and plan["days"][0]["items"][1]["sets"] == 4   # o secundário de peito ganhou 1 série


def test_two_signals_trigger_a_lighter_week_and_poor_recovery_blocks_volume_up():
    sessions = _history("Barbell_Bench_Press_-_Medium_Grip", [70, 70, 68, 66, 65, 65]) + \
        _history("Barbell_Squat", [100, 100, 97, 95, 94, 94])
    res = AD.proposals(PLAN, sessions, TODAY, {"sleep_avg7": 5.4})
    kinds = [p["kind"] for p in res["items"]]
    assert kinds[0] == "deload" and "volume_up" not in kinds
    assert any("sono" in w for w in res["items"][0]["why"]) and any("força caindo" in w for w in res["items"][0]["why"])
    # um sinal só (sono) não basta
    flat = _history("Barbell_Bench_Press_-_Medium_Grip", [60, 60, 60, 60, 60, 60])
    assert not any(p["kind"] == "deload" for p in AD.proposals(PLAN, flat, TODAY, {"sleep_avg7": 5.4})["items"])
    # adiado: não pergunta de novo
    st = {"snooze": {"deload": (TODAY + timedelta(days=3)).isoformat()}}
    assert not any(p["kind"] == "deload" for p in AD.proposals(PLAN, sessions, TODAY, {"sleep_avg7": 5.4}, st)["items"])


def test_srpe_week_load_and_monotony():
    ss = [_sess(TODAY - timedelta(days=k), "X", 50, srpe=s, mins=m) for k, s, m in ((0, 9, 60), (2, 9, 50), (4, 8, 70))]
    w = AD.srpe_week(ss, TODAY)
    assert w["avg"] == pytest.approx(8.7, abs=0.05) and w["load"] == 9 * 60 + 9 * 50 + 8 * 70 and w["monotony"] is not None
    assert "esforço médio" in AD.recovery_signals({}, w)[0]


def _hdays(week_ms, base_ms, sleep_h=7.5):
    out = {}
    for i in range(0, 67):
        d = (TODAY - timedelta(days=i)).isoformat()
        out[d] = {"hrv": {"last_night": week_ms if i < 7 else base_ms + (i % 5) - 2}, "sleep": {"duration_h": sleep_h}}
    return out


def test_readiness_uses_personal_baseline():
    low = readiness.baseline(_hdays(40, 60), TODAY)
    ok = readiness.baseline(_hdays(60, 60), TODAY)
    assert low["hrv_band"] == "baixa" and ok["hrv_band"] == "normal" and low["sleep_avg7"] == 7.5
    s1 = readiness.score({"sleep_score": 80, "body_battery_max": 80, **ok})["score"]
    s2 = readiness.score({"sleep_score": 80, "body_battery_max": 80, **low, "sleep_avg7": 5.5})["score"]
    assert s2 <= s1 - 15
    assert readiness.baseline({}, TODAY) == {}   # sem dados: não inventa


def test_weather_bands_and_best_hour():
    assert weather.wbgt(25, 50) < weather.wbgt(30, 70)
    assert weather.band(33)["level"] == "perigo" and weather.band(20)["level"] == "leve"
    hours = [{"hour": h, "temp": 22 + max(0, h - 6) * 1.2, "rh": 70, "wbgt": weather.wbgt(22 + max(0, h - 6) * 1.2, 70)} for h in range(0, 24)]
    adv = weather.advice({"city": "Belo Horizonte", "hours": hours}, "meio_dia")
    assert adv["best"]["hour"] <= 7 and adv["at"]["hour"] == 12 and adv["show"]


def test_forecast_uses_cache_and_survives_no_network():
    kv = {}
    calls = []

    def fetch(url, params, timeout=6):
        calls.append(url)
        if "geocoding" in url:
            return {"results": [{"latitude": -19.9, "longitude": -43.9, "name": "Belo Horizonte", "timezone": "America/Sao_Paulo"}]}
        return {"hourly": {"time": [f"2026-11-16T{h:02d}:00" for h in range(24)], "temperature_2m": [20 + h * 0.5 for h in range(24)],
                           "relative_humidity_2m": [70] * 24}}
    now = datetime(2026, 11, 16, 7, 0)
    f1 = weather.forecast("Belo Horizonte", TODAY, kv.get, kv.__setitem__, now, fetch)
    f2 = weather.forecast("Belo Horizonte", TODAY, kv.get, kv.__setitem__, now + timedelta(hours=1), fetch)
    assert f1["hours"] and f2 == f1 and len(calls) == 2

    def broken(*a, **k):
        raise OSError("sem rede")
    assert weather.forecast("Recife", TODAY, {}.get, {}.__setitem__, now, broken) is None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "a.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))
    from gymgarmin.app import app

    with TestClient(app) as c:
        c.put("/api/profile", json={"training_months": 24, "goal": "hipertrofia", "available_days": [0, 2, 4], "age": 35,
                                    "weight_kg": 80, "session_minutes": 60})
        yield c
    clock.freeze(None)


def test_api_srpe_adaptive_and_weather(client, monkeypatch):
    s = client.post("/api/sessions", json={"date": "2026-11-16T08:00", "client_id": "e1",
                                           "items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 8, "load_kg": 80}]}]}).json()
    assert client.put(f"/api/sessions/{s['id']}", json={"srpe": 11}).status_code == 400
    r = client.put(f"/api/sessions/{s['id']}", json={"srpe": 7}).json()
    assert r["srpe"] == 7 and r["items"][0]["sets"][0]["load_kg"] == 80
    client.post("/api/generator/apply", json={})
    assert "items" in client.get("/api/adaptive").json()
    assert client.post("/api/adaptive/apply", json={"kind": "deload"}).status_code == 400   # sem sinais: não aplica
    assert client.get("/api/weather/today").json() == {"advice": None}                     # sem cidade: nada

    def fake(url, params, timeout=6):
        if "geocoding" in url:
            return {"results": [{"latitude": -3.7, "longitude": -38.5, "name": "Fortaleza"}]}
        return {"hourly": {"time": [f"2026-11-16T{h:02d}:00" for h in range(24)], "temperature_2m": [27 + (h > 9) * 5 for h in range(24)],
                           "relative_humidity_2m": [75] * 24}}
    monkeypatch.setattr(weather, "_get", fake)
    monkeypatch.setattr(weather.forecast, "__defaults__", (fake,))
    client.put("/api/profile", json={"personal": {"city": "Fortaleza", "run_time": "meio_dia"}})
    adv = client.get("/api/weather/today").json()["advice"]
    assert adv and adv["city"] == "Fortaleza" and adv["level"] in ("alto", "perigo") and adv["best"]["hour"] <= 9
