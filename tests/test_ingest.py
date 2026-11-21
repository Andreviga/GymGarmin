"""Apple Saúde (Atalhos) e Health Connect (Android) → GymGarmin pelo token pessoal."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import auth, clock, db, garmin_sync, ingest


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    garmin_sync._clients.clear()
    clock.freeze(datetime(2026, 11, 17, 9, 0))
    from gymgarmin.app import app

    with TestClient(app) as cl:
        yield cl
    clock.freeze(None)
    auth.reset_cache()


def test_numbers_as_shortcuts_send_them():
    assert ingest.num("6,4") == 6.4 and ingest.num("48 bpm") == 48 and ingest.num("9.312", integer=True) == 9312 and ingest.num("5.1") == 5.1
    assert ingest.num("1.234,5") == 1234.5 and ingest.num("") is None
    assert ingest.minutes("45 min") == 45 and ingest.minutes("1 h 5 min") == 65 and ingest.minutes("0:45:00") == 45
    assert ingest.minutes("2700 s") == 45 and ingest.minutes(32) == 32
    assert ingest.garmin_type("Corrida") == "running" and ingest.garmin_type("HKWorkoutActivityTypeTraditionalStrengthTraining") == "strength_training"
    assert ingest.garmin_type("RUNNING_TREADMILL") == "treadmill_running" and ingest.garmin_type("BIKING_STATIONARY") == "indoor_cycling"
    assert ingest.garmin_type("Elíptico") == "elliptical" and ingest.garmin_type("Ioga") == "ioga"
    assert ingest.garmin_type("HKWorkoutActivityTypeSoccer") == "soccer" and ingest.garmin_type("EXERCISE_TYPE_TENNIS") == "tennis"
    assert ingest.garmin_type("Meditação") is None and ingest.garmin_type("") is None  # não é treino


def test_iphone_shortcut_payload_creates_sessions_health_and_readiness(c):
    c.put("/api/profile", json={"age": 34})
    c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
    assert c.post("/api/ingest", json={}, headers={"Authorization": "Bearer errado"}).status_code == 401
    tok = c.post("/api/account/ingest-token").json()["token"]
    anon = TestClient(c.app)  # o Atalhos não tem o cookie do app, só o token
    payload = {"source": "apple", "date": "2026-11-17", "sleep_h": "6,5", "deep_min": "50", "rem_min": "85",
               "light_min": "255", "awake_min": "18", "sleep_start": "2026-11-16T22:50:00-03:00", "rhr": "47 bpm",
               "hrv": "52", "hrv_kind": "sdnn", "steps": "9.312",
               "workouts": [{"id": "w1", "type": "Corrida", "start": "2026-11-16T06:05:00-03:00", "duration_min": "32 min",
                             "distance_km": "5,1", "kcal": "380", "avg_hr": "141", "max_hr": "168"},
                            {"id": "w2", "type": "Treino de Força Tradicional", "start": "2026-11-16T07:00:00-03:00",
                             "duration_min": "55", "avg_hr": "102"},
                            {"id": "w3", "type": "Ioga", "start": "2026-11-15T07:00:00-03:00", "duration_min": "30"}]}
    r = anon.post("/api/ingest", json=payload, headers={"Authorization": f"Bearer {tok}"}).json()
    assert r["days"] == ["2026-11-17"] and len(r["imported"]) == 3 and r["skipped"] == 0
    sessions = c.get("/api/sessions").json()
    yoga = next(s for s in sessions if s["items"][0]["exercise_id"] == "atividade_mobilidade")  # antes era descartada
    assert yoga["items"][0]["light"] and yoga["items"][0]["tss"] == 7.5 and yoga["items"][0]["duration_min"] == 30
    run = next(s for s in sessions if s["items"][0].get("sport") == "corrida")
    assert run["source"] == "apple" and run["items"][0]["distance_km"] == 5.1 and run["items"][0]["pace_s_km"] == 376
    assert any(s["items"][0].get("strength_min") == 55 for s in sessions)
    # reenviar não duplica
    again = anon.post("/api/ingest?token=" + tok, json=payload).json()
    assert again["imported"] == [] and len(c.get("/api/sessions").json()) == 3
    h = c.get("/api/health").json()
    assert h["has_data"] and h["last_night"]["duration_h"] == 6.5 and h["last_night"]["rem_pct"] == 21.8
    rd = c.get("/api/readiness").json()
    assert rd and rd.get("score") is not None  # prontidão sem Garmin, pelo sono e FC de repouso
    st = c.get("/api/account/ingest").json()
    assert st["has_token"] and st["last"]["days"] == ["2026-11-17"] and st["last"]["workouts"] == 0  # o último envio (repetido)


def test_android_health_connect_payload_and_merge_with_app_log(c):
    c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
    tok = c.post("/api/account/ingest-token").json()["token"]
    # registrou a musculação no app e o relógio também mandou: vira uma sessão só
    c.post("/api/sessions", json={"date": "2026-11-16T19:10", "items": [
        {"exercise_id": "Barbell_Squat", "sets": [{"reps": 5, "load_kg": 100}] * 3}]})
    payload = {"source": "android", "days": [{"date": "2026-11-16", "sleep_h": 7.2, "rhr": 52, "steps": 11000}],
               "workouts": [{"id": "hc-1", "type": "STRENGTH_TRAINING", "start": "2026-11-16T22:05:00Z",
                             "end": "2026-11-16T23:00:00Z", "avg_hr": 110, "kcal": 300}]}
    r = TestClient(c.app).post("/api/ingest", json=payload, headers={"X-GymGarmin-Token": tok}).json()
    assert r["imported"][0]["merged"]
    ss = c.get("/api/sessions").json()
    assert len(ss) == 1 and ss[0]["items"][0]["sets"] and ss[0]["garmin_summary"]["avg_hr"] == 110


def test_token_belongs_to_one_user_only(c):
    c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
    inv = c.post("/api/admin/invites", json={}).json()
    m = TestClient(c.app)
    m.post("/api/auth/register", json={"code": inv["code"], "email": "m@x.com", "password": "senha-maria"})
    tok = m.post("/api/account/ingest-token").json()["token"]
    TestClient(c.app).post("/api/ingest", json={"workouts": [{"id": "a", "type": "Corrida", "start": "2026-11-16T06:00:00",
                                                              "duration_min": 30, "distance_km": 5}]},
                           headers={"Authorization": f"Bearer {tok}"})
    assert len(m.get("/api/sessions").json()) == 1 and c.get("/api/sessions").json() == []
    # trocar o código invalida o antigo
    m.post("/api/account/ingest-token")
    assert TestClient(c.app).post("/api/ingest", json={}, headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_android_sends_every_workout_type_and_says_what_it_found(c):
    """Relógio que grava a musculação como "outro exercício" (Amazfit/Zepp): o treino entra com o tempo em vez de sumir."""
    c.put("/api/profile", json={"age": 34})
    c.post("/api/auth/setup", json={"email": "dona@x.com", "password": "senha-forte"})
    tok = c.post("/api/account/ingest-token").json()["token"]
    anon = TestClient(c.app)
    payload = {"source": "android", "days": [{"date": "2026-11-19", "steps": 7053}],
               "workouts": [{"id": "z1", "type": "OTHER_WORKOUT", "name": "Treino de força", "start": "2026-11-20T18:05:00-03:00",
                             "end": "2026-11-20T19:10:00-03:00", "duration_min": 65.0, "avg_hr": 104, "max_hr": 131, "kcal": 310},
                            {"id": "z2", "type": "OTHER_WORKOUT_83", "start": "2026-11-19T07:00:00-03:00", "duration_min": 20.0},
                            {"id": "z3", "type": "GUIDED_BREATHING", "start": "2026-11-19T22:00:00-03:00", "duration_min": 10.0}],
               "diag": {"app": "1.1", "window_days": 30, "missing": ["READ_SLEEP"], "health_days": 1, "sleep_days": 0, "steps_days": 1,
                        "exercise_total": 3, "exercise_types": {"OTHER_WORKOUT": 1, "OTHER_WORKOUT_83": 1, "GUIDED_BREATHING": 1},
                        "exercise_apps": ["com.huami.watch.hmwatchmanager"], "junk": {"x": 1}}}
    r = anon.post("/api/ingest", json=payload, headers={"Authorization": f"Bearer {tok}"}).json()
    assert len(r["imported"]) == 2 and r["skipped"] == 1                       # respiração guiada não é treino
    s = next(x for x in c.get("/api/sessions").json() if x.get("ext_id") == "android:z1")
    it = s["items"][0]
    assert it["exercise_id"] == "atividade_outra" and it["duration_min"] == 65 and it["kcal"] == 310 and it["avg_hr"] == 104
    last = c.get("/api/account/ingest").json()["last"]
    assert last["sent"] == 3 and last["diag"]["exercise_total"] == 3 and last["diag"]["missing"] == ["READ_SLEEP"]
    assert "junk" not in last["diag"] and "diag" not in last["sample"]


def test_phone_workout_time_stays_in_the_persons_timezone():
    """O celular manda a hora com fuso (-03:00); o servidor roda em UTC. Treino das 21h30 é das 21h30 do mesmo dia."""
    assert ingest.parse_dt("2026-11-20T21:30:00-03:00").isoformat() == "2026-11-20T21:30:00"
    assert ingest.parse_dt("2026-11-21T00:30:00Z").isoformat() == "2026-11-20T21:30:00"
    assert ingest.parse_dt("2026-11-20T12:35:00").isoformat() == "2026-11-20T12:35:00"   # sem fuso: já é hora local
    s = ingest.workout_session({"id": "z", "type": "OTHER_WORKOUT", "start": "2026-11-20T21:30:00-03:00", "duration_min": 40}, "android", 190)
    assert s["date"].startswith("2026-11-20T21:30")
