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


# ------------------------------------------------------------------ achados da revisão: entrega do relógio ---
def _token(c):
    c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
    return {"Authorization": "Bearer " + c.post("/api/account/ingest-token", json={}).json()["token"]}


def test_readiness_is_not_invented_from_steps_alone_and_follows_sleep_hours(c):
    h = _token(c)
    c.post("/api/ingest", headers=h, json={"source": "android", "days": [{"date": "2026-11-16", "steps": 2056}, {"date": "2026-11-17", "steps": 7053}], "workouts": []})
    rd = c.get("/api/readiness").json()
    assert rd.get("score") is None and rd["level"] == "sem_dados"            # só passos: sem nota (antes: "70 · Alta")
    from gymgarmin import readiness
    assert readiness.score({}, form=5.0)["level"] == "sem_dados"             # carga de treino sozinha também não vira nota
    bad = readiness.score({"sleep_h": 3.2, "sleep_need_h": 7}, form=0)
    good = readiness.score({"sleep_h": 8.5, "sleep_need_h": 7}, form=0)
    assert bad["score"] < 40 and bad["action"] in ("trocar", "descanso", "moderar") and good["score"] >= 75 and good["action"] == "seguir"
    assert "sono de 3h12" in " ".join(bad["reasons"])
    c.post("/api/ingest", headers=h, json={"source": "android", "days": [{"date": "2026-11-17", "sleep_h": 3.2, "sleep_start": "03:10", "sleep_end": "06:22"}], "workouts": []})
    rd = c.get("/api/readiness").json()
    assert rd["score"] is not None and rd["score"] < 45                       # noite de 3h12 derruba a nota


def test_many_workouts_in_one_delivery_keep_the_newest_and_say_what_was_left(c):
    h = _token(c)
    ws = []
    for i in range(170):  # 170 caminhadas em 29 dias, da mais antiga para a mais nova (como o app manda)
        d = 19 + i // 6 if i < 72 else 1 + (i - 72) // 6
        mth = "10" if i < 72 else "11"
        ws.append({"id": f"w{i}", "type": "WALKING", "name": "WALKING", "start": f"2026-{mth}-{d:02d}T{6 + i % 6:02d}:00:00-03:00", "duration_min": 20})
    r = c.post("/api/ingest", headers=h, json={"source": "android", "days": [], "workouts": ws}).json()
    assert len(r["imported"]) == ingest.MAX_WORKOUTS and r["left_out"] == 170 - ingest.MAX_WORKOUTS
    days = sorted(x["date"] for x in r["imported"])
    assert days[-1] == "2026-11-17" and days[0] > "2026-10-19"               # entraram os mais novos; os mais antigos ficaram para o próximo envio
    r2 = c.post("/api/ingest", headers=h, json={"source": "android", "days": [], "workouts": ws}).json()
    assert len(r2["imported"]) == 170 - ingest.MAX_WORKOUTS and r2["left_out"] == 0   # reenviar traz o resto: o conhecido não ocupa vaga
    assert c.get("/api/account/ingest").json()["last"]["left_out"] == 0


def test_watch_workout_titles_are_shown_in_portuguese_not_as_codes(c):
    h = _token(c)
    c.post("/api/ingest", headers=h, json={"source": "android", "days": [], "workouts": [
        {"id": "a", "type": "OTHER_WORKOUT", "name": "OTHER_WORKOUT", "start": "2026-11-16T07:05:00-03:00", "duration_min": 62},
        {"id": "b", "type": "WALKING", "name": "", "start": "2026-11-16T18:00:00-03:00", "duration_min": 25, "distance_km": 2.1},
        {"id": "c", "type": "STRENGTH_TRAINING", "name": "STRENGTH_TRAINING", "start": "2026-11-15T07:00:00-03:00", "duration_min": 40},
        {"id": "d", "type": "YOGA", "name": "Yoga da manhã", "start": "2026-11-14T07:00:00-03:00", "duration_min": 30},
        {"id": "e", "type": "HIIT", "name": "HIIT", "start": "2026-11-13T07:00:00-03:00", "duration_min": 30}]})
    titles = {s["ext_id"].split(":")[1]: s["notes"] for s in c.get("/api/sessions?limit=20").json()}
    assert titles["a"] == "Atividade do relógio" and titles["c"] == "Musculação (relógio)" and titles["d"] == "Yoga da manhã"
    assert not any(t.isupper() and "_" in t for t in titles.values()) and titles["b"] and titles["e"] == "HIIT"
    hiit = next(s for s in c.get("/api/sessions?limit=20").json() if s["ext_id"].endswith(":e"))
    assert hiit["items"][0]["exercise_id"] == "atividade_hiit"                # aula de HIIT não entra como musculação


def test_iphone_minutes_seconds_duration_is_not_read_as_hours():
    assert ingest.minutes("12:30") == 12.5 and round(ingest.minutes("45:10"), 2) == 45.17
    assert ingest.minutes("1:02:15") == 62.25 and ingest.minutes("0:45:00") == 45 and ingest.minutes("1:30") == 90
    s = ingest.workout_session({"type": "Caminhada", "start": "2026-11-16T07:00:00-03:00", "end": "2026-11-16T07:12:30-03:00", "duration": "3:30"}, "apple", 186)
    assert s["duration_min"] == 12.5                                          # início e fim desmentem a duração mal lida (3h30)


def test_short_warm_up_recorded_by_the_watch_does_not_become_the_logged_workout(c):
    h = _token(c)
    body = {"date": "2026-11-16T18:00", "day_name": "Treino livre", "client_id": "x1", "duration_min": 65,
            "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 20}] * 4}]}
    mine = c.post("/api/sessions", json=body).json()
    warm = {"id": "warm", "type": "OTHER_WORKOUT", "start": "2026-11-16T18:02:00-03:00", "duration_min": 15, "kcal": 70}
    main = {"id": "main", "type": "OTHER_WORKOUT", "start": "2026-11-16T18:20:00-03:00", "duration_min": 50, "kcal": 290}
    r = c.post("/api/ingest", headers=h, json={"source": "android", "days": [], "workouts": [warm, main]}).json()
    by = {x["session_id"]: x for x in r["imported"]}
    assert by[mine["id"]]["merged"] is True and len(r["imported"]) == 2
    s = db.get_session(mine["id"])
    assert s["duration_min"] == 50 and s["garmin_summary"]["kcal"] == 290 and s["ext_id"].endswith(":main")   # juntou com o treino, não com o aquecimento
    # aquecimento chegando sozinho antes: não vira o treino; quando o treino de verdade chega, junta
    mine2 = c.post("/api/sessions", json={**body, "date": "2026-11-15T18:00", "client_id": "x2"}).json()
    w2 = {**warm, "id": "warm2", "start": "2026-11-15T18:02:00-03:00"}
    assert c.post("/api/ingest", headers=h, json={"source": "android", "days": [], "workouts": [w2]}).json()["imported"][0]["merged"] is False
    m2 = {**main, "id": "main2", "start": "2026-11-15T18:20:00-03:00"}
    assert c.post("/api/ingest", headers=h, json={"source": "android", "days": [], "workouts": [m2]}).json()["imported"][0]["session_id"] == mine2["id"]


def test_android_app_download_uses_the_hax_name_and_the_old_link_still_works(c):
    old = c.get("/static/gymgarmin-sync.apk", follow_redirects=False)
    assert old.status_code == 301 and old.headers["location"] == "/static/hax-sync.apk"
    new = c.get("/static/hax-sync.apk")
    assert new.status_code == 200 and new.content[:2] == b"PK" and len(new.content) > 100_000
