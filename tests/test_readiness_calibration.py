from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import calibration, endurance as en, readiness


class FakeApi:
    def get_training_readiness(self, d):
        return [{"score": 34, "level": "LOW", "feedbackShort": "RECOVERY"}]

    def get_hrv_data(self, d):
        return {"hrvSummary": {"status": "UNBALANCED", "lastNightAvg": 41, "weeklyAvg": 48}}

    def get_sleep_data(self, d):
        return {"dailySleepDTO": {"sleepScores": {"overall": {"value": 58}}, "sleepTimeSeconds": 21600}}

    def get_body_battery(self, a, b):
        return [{"bodyBatteryValuesArray": [[1, 20], [2, 45], [3, 38]]}]

    def get_rhr_day(self, d):
        raise RuntimeError("sem dado")

    def get_race_predictions(self):
        return {"time5K": 1380}

    def get_cycling_ftp(self):
        return [{"functionalThresholdPower": 251}]

    def get_lactate_threshold(self, latest=True):
        return {"speed_and_heart_rate": {"heartRate": 168}}

    def get_max_metrics(self, d):
        return [{"generic": {"vo2MaxPreciseValue": 51.3}}]


def test_fetch_is_defensive_and_scores():
    data = readiness.fetch(FakeApi(), date(2026, 11, 17))
    assert data["garmin_readiness"] == 34 and data["hrv_status"] == "UNBALANCED"
    assert data["sleep_score"] == 58 and data["body_battery_max"] == 45 and "rhr" not in data
    sc = readiness.score(data, form=-5)
    assert sc["action"] == "trocar" and sc["score"] == 34
    # sem Training Readiness: combina sono, HRV e Body Battery
    sc2 = readiness.score({"sleep_score": 85, "hrv_status": "BALANCED", "body_battery_max": 90})
    assert sc2["action"] == "seguir"
    assert readiness.score({"sleep_score": 80}, form=-35)["score"] == 65
    assert readiness.score({})["action"] == "seguir"


def test_adjust_entry_swaps_hard_for_easy_and_undo():
    a = {"run_vdot": 45}
    hard = en.build_session("corrida", "intervalado", 9, {}, a, "intermediario")
    entry = {"id": 1, "status": "planejado", "day_name": "Corrida: Intervalado", "items": [hard], "garmin_workout_id": 9}
    sw = readiness.adjust_entry(entry, "trocar", a)
    assert not sw["items"][0]["endurance"]["hard"] and sw["garmin_outdated"]
    assert "era Intervalado" in sw["items"][0]["endurance"]["note"]
    assert readiness.undo_entry(sw)["items"][0]["endurance"]["hard"]
    mod = readiness.adjust_entry(entry, "moderar", a)
    rep = next(s for s in mod["items"][0]["endurance"]["steps"] if s["kind"] == "repeat")
    orig = next(s for s in hard["endurance"]["steps"] if s["kind"] == "repeat")
    assert rep["n"] < orig["n"] or rep["n"] == 2
    assert readiness.adjust_entry(entry, "seguir", a) is None
    strength = {"id": 2, "status": "planejado", "items": [{"exercise_id": "Barbell_Squat", "sets": 4, "role": "main", "phase": "principal"}]}
    assert readiness.adjust_entry(strength, "trocar")["items"][0]["sets"] == 2


def test_calibration_from_tests_and_suggestions():
    assert calibration.from_test("corrida", {"distance_km": 5, "time": "20:00"})[0] == "run_vdot"
    assert calibration.from_test("ciclismo", {"power_20min": 263}) == ("ftp_w", 250)
    assert calibration.from_test("natacao", {"t400": "6:40", "t200": "3:05"}) == ("css_s100", pytest.approx(107.5, abs=0.1))
    with pytest.raises(ValueError):
        calibration.from_test("natacao", {"t400": "3:00", "t200": "3:05"})
    today = date(2026, 11, 17)
    sessions = [{"date": (today - timedelta(days=5)).isoformat(), "items": [
        {"exercise_id": "corrida_rua", "sport": "corrida", "distance_km": 10, "duration_min": 46}]}]
    sug = calibration.suggestions(sessions, {"run_vdot": 40}, {"ftp": 251, "lthr": 168}, today)
    keys = {s["key"] for s in sug}
    assert keys == {"run_vdot", "ftp_w", "lthr"}
    gm = calibration.garmin_metrics(FakeApi(), today)
    assert gm["ftp"] == 251 and gm["lthr"] == 168 and gm["vo2max"] == 51.3 and gm["race_predictions"]["time5K"] == 1380


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "r.sqlite3"))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c


def test_endurance_api_flow(client, monkeypatch):
    opts = client.get("/api/endurance/options").json()
    assert {s["id"] for s in opts["sports"]} >= {"corrida", "ciclismo", "natacao", "remo", "caminhada", "trilha"}
    assert any(e["id"] == "tri_olimpico" for e in opts["events"])
    client.put("/api/profile", json={"age": 36, "weight_kg": 78, "training_months": 18, "goal": "hibrido",
                                     "available_days": [0, 1, 2, 3, 4, 5, 6], "session_minutes": 60})
    client.put("/api/endurance", json={"sports": {"corrida": {"enabled": True, "env": "rua", "sessions": 3,
                                                              "goal": {"type": "prova", "distance": 10, "target": "48:00",
                                                                       "date": "2027-03-14"}}}, "readiness_auto": True})
    r = client.post("/api/anchors/test", json={"sport": "corrida", "data": {"distance_km": 5, "time": "23:00"}}).json()
    assert r["key"] == "run_vdot" and r["value"] > 40
    ov = client.get("/api/endurance/overview").json()
    assert ov["active"] == ["corrida"] and ov["zones"]["corrida"][1]["type"] == "pace"
    assert ov["predictions"]["sports"]["corrida"]["goal"]["status"] in ("no_alvo", "possivel", "ambicioso")
    plan = client.post("/api/generator/apply", json={}).json()
    assert any(d["kind"] == "resistencia" for d in plan["days"]) and plan.get("endurance_plan")
    sch = client.get("/api/schedule").json()
    assert "endurance" in sch["goals"]
    # prontidão: grava dado do relógio e aplica no dia
    from gymgarmin import db

    db.kv_set(f"readiness:{date.today().isoformat()}", '{"garmin_readiness": 30}')
    rv = client.get("/api/readiness").json()
    assert rv["action"] == "trocar"
    res = client.post("/api/readiness/apply", json={}).json()
    assert res["action"] == "trocar"
    assert client.get("/api/load").json()["series"]
