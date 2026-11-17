from datetime import date, timedelta

import pytest

from gymgarmin import load, sports


def test_vdot_matches_daniels_tables():
    # Daniels: 5 km em 19:57 ≈ VDOT 50; 10 km em 41:21 ≈ 50
    assert sports.vdot(5, 19 * 60 + 57) == pytest.approx(50, abs=0.5)
    assert sports.vdot(10, 41 * 60 + 21) == pytest.approx(50, abs=0.5)
    p = sports.run_paces(50)
    # T ≈ 4:15/km, I ≈ 3:55/km, E entre ~5:05 e ~5:40
    assert 245 <= sum(p[4]) / 2 <= 262
    assert 228 <= sum(p[5]) / 2 <= 242
    assert 300 <= p[2][0] <= 315 and 330 <= p[2][1] <= 345
    # previsão inversa
    assert sports.predict_time(50, 10) == pytest.approx(41 * 60 + 21, abs=20)


def test_parse_and_format():
    assert sports.parse_duration("25:30") == 1530
    assert sports.parse_duration("1:45:00") == 6300
    assert sports.parse_duration("50 min") == 3000
    assert sports.fmt_duration(6300) == "1:45:00"
    assert sports.fmt_pace(305) == "5:05/km"


def test_other_anchors_and_targets():
    assert sports.ftp_from_20min(263) == 250
    assert sports.css_from_tests(400, 185) == pytest.approx(107.5, abs=0.1)  # 1:47,5/100 m
    a = {"run_vdot": 50, "ftp_w": 250, "css_s100": 100, "row_2k_s": 420, "hrmax": 190}
    assert sports.target_for("ciclismo", 4, a) == {"type": "power", "low_w": 228, "high_w": 262, "label": "228–262 W"}
    sw = sports.target_for("natacao", 4, a)
    assert sw["type"] == "swim_pace" and sw["fast_s_100"] == 98 and sw["slow_s_100"] == 102
    row = sports.target_for("remo", 2, a)
    assert row["type"] == "split" and row["fast_s_500"] == 117  # 105 + 12
    walk = sports.target_for("caminhada", 2, a)
    assert walk["type"] == "hr" and walk["low_bpm"] == 114
    # sem âncora do esporte nem FC → zona
    assert sports.target_for("ciclismo", 3, {})["type"] == "hr_zone"


def test_endurance_stress_methods():
    a = {"run_vdot": 50, "ftp_w": 250, "css_s100": 100, "hrmax": 190}
    # 1 h no limiar de corrida ≈ 100
    thr_pace = sports.pace_at(50, 0.88)
    run = {"exercise_id": "corrida_rua", "duration_min": 60, "distance_km": round(3600 / thr_pace, 2)}
    assert load.endurance_stress(run, a)["tss"] == pytest.approx(100, abs=3)
    bike = {"exercise_id": "ciclismo_estrada", "duration_min": 120, "np": 175}
    st = load.endurance_stress(bike, a)
    assert st["method"] == "potencia" and st["tss"] == pytest.approx(98, abs=1)
    garmin = {"exercise_id": "ciclismo_estrada", "duration_min": 60, "tss": 77}
    assert load.endurance_stress(garmin, a)["tss"] == 77
    hr = {"exercise_id": "trilha_hiking", "duration_min": 180, "avg_hr": 128}
    assert load.endurance_stress(hr, a)["method"] == "fc"
    zone = {"exercise_id": "cardio_eliptico", "duration_min": 30, "zone": 2}
    assert load.endurance_stress(zone, {})["method"] == "zona"


def test_strength_stress_scale():
    items = [{"exercise_id": "x", "sets": [{"reps": 8, "load_kg": 80, "rpe": 8}] * 20}]
    assert 30 <= load.strength_stress(items, 60) <= 50


def test_pmc_builds_fitness_and_flags_overreach():
    today = date(2026, 11, 17)
    prof = {"endurance": {"anchors": {"run_vdot": 50}}, "age": 35}
    sessions = []
    thr_pace = sports.pace_at(50, 0.88)
    for i in range(60):
        d = today - timedelta(days=60 - i)
        sessions.append({"date": d.isoformat() + "T07:00", "items": [
            {"exercise_id": "corrida_rua", "duration_min": 60, "distance_km": round(0.8 * 3600 / thr_pace, 2)}]})
    r = load.pmc(sessions, prof, today=today)
    assert r["ctl"] > 40 and abs(r["form"]) < 15 and r["status"] in ("equilibrado", "produtivo")
    # semana de choque
    for i in range(1, 7):
        d = today - timedelta(days=7 - i)
        sessions.append({"date": d.isoformat() + "T18:00", "items": [
            {"exercise_id": "ciclismo_estrada", "duration_min": 180, "avg_hr": 160}]})
    r2 = load.pmc(sessions, prof, today=today)
    assert r2["form"] < -30 and r2["status"] == "risco"
    assert r2["week"]["by_sport"]["ciclismo"]["minutes"] == 1080


def test_session_stress_combines_sports():
    a = {"hrmax": 190}
    s = {"items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 5, "load_kg": 100}] * 10},
                   {"exercise_id": "natacao_piscina", "duration_min": 30, "distance_km": 1.5, "avg_hr": 140}]}
    st = load.session_stress(s, a)
    assert set(st["by_sport"]) == {"musculacao", "natacao"} and st["km"]["natacao"] == 1.5


def test_short_history_does_not_raise_false_overtraining_alarm():
    """Conectou o relógio há 2 semanas e já treinava: o condicionamento não começa do zero."""
    from datetime import date, timedelta

    from gymgarmin import load

    today = date(2026, 11, 17)
    S = [{"date": (today - timedelta(days=i)).isoformat() + "T07:00", "items": [
        {"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 45, "zone": 2}]}
        for i in range(14) if i % 7 not in (2, 5)]
    r = load.pmc(S, {"level": "intermediario"}, today=today, days=21)
    assert r["seeded"] and r["status"] != "risco" and "estimado" in r["message"]
    # iniciante de verdade começando agora: sem estimativa (a fadiga alta é real)
    assert not load.pmc(S, {"level": "iniciante"}, today=today, days=21)["seeded"]
    # com 6+ semanas de dados não precisa estimar
    S2 = S + [{**s, "date": (date.fromisoformat(s["date"][:10]) - timedelta(days=42)).isoformat() + "T07:00"} for s in S]
    assert not load.pmc(S2, {"level": "intermediario"}, today=today, days=21)["seeded"]


def test_seed_does_not_depend_on_window_size():
    from datetime import date, timedelta

    from gymgarmin import load

    today = date(2026, 11, 17)
    S = [{"date": (today - timedelta(days=i)).isoformat() + "T07:00", "items": [
        {"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 45, "zone": 2}]} for i in range(14) if i % 3]
    a, b = load.pmc(S, {}, today=today, days=21), load.pmc(S, {}, today=today, days=90)
    assert a["ctl"] == b["ctl"] and a["status"] == b["status"]


def test_easy_run_does_not_beat_garmin_race_prediction_for_vdot():
    from datetime import date

    from gymgarmin import calibration

    S = [{"date": "2026-11-15T12:00", "items": [{"sport": "corrida", "exercise_id": "cardio_esteira_corrida",
                                                 "distance_km": 5.8, "duration_min": 40}]}]
    sug = calibration.suggestions(S, {}, {"race_predictions": {"time5K": 1703}}, today=date(2026, 11, 17))
    v = next(x for x in sug if x["key"] == "run_vdot")
    assert v["value"] > 32 and "previsão" in v["source"]
    # sem previsão do Garmin, usa o melhor treino
    v2 = next(x for x in calibration.suggestions(S, {}, {}, today=date(2026, 11, 17)) if x["key"] == "run_vdot")
    assert "melhor corrida" in v2["source"]
