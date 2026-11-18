"""Prova híbrida (formato HYROX): previsão, parciais, semanas (comprometida, simulados), estações e envio ao relógio."""
from datetime import date, timedelta

from gymgarmin import catalog, endurance, garmin_export, generator, hybrid_race, schedule

PROF = {"age": 34, "sex": "M", "weight_kg": 90, "training_months": 36, "goal": "hibrido", "session_minutes": 75,
        "available_days": [0, 1, 2, 3, 4, 5], "same_session": True,
        "endurance": {"anchors": {"run_vdot": 44}, "sports": {"corrida": {"enabled": True, "env": "rua", "sessions": 3}},
                      "event": {"type": "hibrida", "date": "2027-03-14", "target": "1:30:00", "division": "open"}}}


def test_station_exercises_exist_and_standards_by_division():
    for s in hybrid_race.STATIONS:
        assert catalog.get(s[2]), s[2]
    open_m = {s["id"]: s for s in hybrid_race.stations_for("open", "M")}
    pro_f = {s["id"]: s for s in hybrid_race.stations_for("pro", "F")}
    assert open_m["sled_push"]["load"] == "152 kg" and pro_f["sled_push"]["load"] == "152 kg"
    assert "75" in hybrid_race.stations_for("open", "F")[-1]["standard"]
    assert any(e["id"] == "hibrida" for e in endurance.options()["events"])


def test_prediction_and_splits_add_up():
    p = endurance.event_prediction(PROF, PROF["endurance"]["event"])
    assert p["predicted"] and len(p["stations"]) == 8 and p["gap_s"] is not None
    sp_ = hybrid_race.splits(90 * 60)
    to_s = lambda t: sum(int(x) * 60 ** i for i, x in enumerate(reversed(t.split(":"))))  # noqa: E731
    total = to_s(sp_["run_total"]) + to_s(sp_["stations_total"]) + to_s(sp_["roxzone"])
    assert abs(total - 90 * 60) < 90  # corrida + estações + roxzone ≈ alvo
    assert sp_["run_km"] in ("5:37/km", "5:38/km")  # 45 min de corrida / 8 km


def _plan():
    plan = generator.generate(PROF, 0, recent={"corrida": 15.0}, today=date(2026, 11, 16))
    plan["id"] = 1
    plan["generator"]["cycle_start"] = "2026-11-16"
    return plan


def test_weeks_have_compromised_runs_simulations_and_stations():
    plan = _plan()
    titles, station_weeks = {}, 0
    ws = date(2026, 11, 16)
    while ws < date(2027, 3, 15):
        wp = schedule._endurance_week(plan, PROF, ws)
        titles[ws] = [s["endurance"]["title"] for s in wp["sports"]["corrida"]["sessions"]]
        es = schedule.build_week(plan, PROF, ws)
        station_weeks += any(i.get("station") for e in es for i in e["items"])
        ws += timedelta(days=7)
    flat = [t for v in titles.values() for t in v]
    assert any("Corrida comprometida" in t for t in flat)
    assert titles[date(2027, 1, 25)][0].startswith("Meio simulado")      # 6 semanas antes
    assert titles[date(2027, 2, 15)][0].startswith("Simulado completo")   # 3 semanas antes
    # semana de simulado: o resto da corrida é leve
    sim = schedule._endurance_week(plan, PROF, date(2027, 2, 15))["sports"]["corrida"]["sessions"]
    assert sum(1 for s in sim if s["endurance"]["hard"]) == 1
    assert station_weeks >= 8


def test_compromised_session_exports_to_watch():
    from gymgarmin import sports as sp
    sess = hybrid_race.compromised_session(PROF, PROF["endurance"]["event"], sp.anchors_of(PROF), 4, False, "Teste")
    day = {"name": "Corrida comprometida", "items": [sess]}
    assert garmin_export.to_fit(day, "Plano")[:14]
    payload = garmin_export.to_connect_json(day, "Plano")
    assert payload["workoutSegments"]
