"""Passos do relógio: treino × dia a dia, dia fora do costume e a resposta "o que foi"."""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, garmin_sync, load, schedule, steps

TODAY = date(2026, 11, 16)  # segunda


def _days(values: dict[int, int], today: date = TODAY, sleep_start: dict[int, str] | None = None) -> dict:
    """{dias atrás: passos} → registros de saúde."""
    out = {}
    for ago, n in values.items():
        d = (today - timedelta(days=ago)).isoformat()
        out[d] = {"date": d, "steps": n, "step_goal": 8000}
        if sleep_start and ago in sleep_start:
            out[d]["sleep"] = {"start": sleep_start[ago], "duration_h": 5.0}
    return out


def _run(ago: int, minutes: float = 40, steps_n: int | None = None, today: date = TODAY, sid: int = 1) -> dict:
    it = {"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": minutes, "distance_km": 6.0}
    if steps_n:
        it["steps"] = steps_n
    return {"id": sid, "date": f"{(today - timedelta(days=ago)).isoformat()}T07:00", "source": "garmin", "items": [it]}


def test_run_steps_leave_the_daily_count():
    days = _days({1: 11000, 2: 4500})
    rows = steps.split(days, [_run(1, 40, 6400)])
    by = {r["date"]: r for r in rows}
    d1, d2 = (TODAY - timedelta(days=1)).isoformat(), (TODAY - timedelta(days=2)).isoformat()
    assert by[d1]["workout"] == 6400 and by[d1]["daily"] == 4600 and by[d1]["parts"] == {"corrida": 6400} and not by[d1]["estimated"]
    assert by[d2]["workout"] == 0 and by[d2]["daily"] == 4500


def test_run_without_measured_steps_is_estimated_and_never_passes_the_day_total():
    days = _days({1: 11000, 2: 5000})
    rows = {r["date"]: r for r in steps.split(days, [_run(1, 40), _run(2, 60, sid=2)])}
    d1, d2 = (TODAY - timedelta(days=1)).isoformat(), (TODAY - timedelta(days=2)).isoformat()
    assert rows[d1]["workout"] == 40 * steps.RUN_SPM and rows[d1]["estimated"]
    assert rows[d2]["workout"] == 5000 and rows[d2]["daily"] == 0 and rows[d2]["parts"]["corrida"] == 5000  # estimativa maior que o dia


def test_strength_and_bike_do_not_count_as_steps():
    sess = {"id": 3, "date": f"{(TODAY - timedelta(days=1)).isoformat()}T07:00",
            "items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 8, "load_kg": 60}]},
                      {"exercise_id": "ciclismo_estrada", "sport": "ciclismo", "duration_min": 60}]}
    assert steps.split(_days({1: 6000}), [sess])[0]["workout"] == 0


def _routine(extra: dict[int, int] | None = None) -> tuple[dict, list]:
    """4 semanas no padrão do André: ~4,4 mil no dia a dia; corrida a cada 3 dias soma ~7 mil."""
    vals, sessions = {}, []
    for ago in range(0, 30):
        base = [4400, 3900, 5200, 2600, 4700, 4100, 5400][ago % 7]
        if ago % 3 == 1:
            vals[ago] = base + 7000
            sessions.append(_run(ago, 42, 7000, sid=100 + ago))
        else:
            vals[ago] = base
    vals.update(extra or {})
    return vals, sessions


def test_run_days_never_ask_but_a_big_day_without_workout_does():
    vals, sessions = _routine()
    s = steps.summary(_days(vals), sessions, {}, TODAY)
    assert s["pending"] == [] and 3900 <= s["baseline"] <= 4700  # corrida explica os dias de 11–12 mil
    assert s["avg7"]["workout"] > 0 and s["avg7"]["daily"] < 6000

    vals, sessions = _routine({2: 19400})  # sábado sem treino registrado: 19 mil passos
    s = steps.summary(_days(vals, sleep_start={1: "03:20"}), sessions, {}, TODAY)
    assert [p["date"] for p in s["pending"]] == [(TODAY - timedelta(days=2)).isoformat()]
    p = s["pending"][0]
    assert p["hint"] == "festival" and p["extra"] == p["daily"] - p["baseline"] and p["daily"] == 19400
    assert s["threshold"] == max(2 * s["baseline"], s["baseline"] + 5000, 9000)


def test_today_and_old_days_are_not_asked_and_little_data_never_asks():
    vals, sessions = _routine({0: 21000, 20: 22000})
    assert steps.summary(_days(vals), sessions, {}, TODAY)["pending"] == []  # hoje ainda em andamento; 20 dias atrás ninguém lembra
    few = _days({1: 20000, 2: 4000, 3: 4100})
    assert steps.summary(few, [], {}, TODAY)["pending"] == []  # menos de uma semana de dados: sem "costume" ainda


def test_normal_day_answer_raises_the_bar_and_event_answer_does_not():
    vals, sessions = _routine({2: 19400, 9: 18000, 16: 17500, 23: 18500})
    d = lambda ago: (TODAY - timedelta(days=ago)).isoformat()  # noqa: E731
    asked = steps.summary(_days(vals), sessions, {}, TODAY)
    assert {p["date"] for p in asked["pending"]} == {d(2), d(9)}  # só os dos últimos 10 dias, no máximo 2
    as_event = {d(k): {"tag": "festival"} for k in (9, 16, 23)}
    as_normal = {d(k): {"tag": "nada"} for k in (9, 16, 23)}
    b_event = steps.baseline(steps.split(_days(vals), sessions), tags=as_event)
    b_normal = steps.baseline(steps.split(_days(vals), sessions), tags=as_normal)
    assert b_normal >= b_event  # "dia normal" entra no costume; festival fica fora


def test_event_session_is_a_light_walk_with_its_own_load():
    s = steps.event_session("2026-11-14", 15000, "festival")
    it = s["items"][0]
    assert s["source"] == steps.SOURCE and it["sport"] == "caminhada" and it["steps"] == 15000 and it["estimated"]
    assert s["duration_min"] == round(15000 / steps.EVENT_SPM) and it["zone"] == 1
    tss = load.session_stress(s, {})["tss"]
    assert tss == it["tss"] and 25 <= tss <= 60  # entre uma corrida leve curta e um treino médio, nunca um treino pesado
    assert steps.event_session("2026-11-14", 800, "festival") is None and steps.event_session("2026-11-14", 9000, "nada") is None
    walk = steps.event_session("2026-11-14", 9000, "caminhada")
    assert walk["items"][0]["zone"] == 2 and walk["duration_min"] == round(9000 / steps.WALK_SPM)
    assert steps.event_session("2026-11-14", 90000, "passeio")["duration_min"] == steps.MAX_EVENT_MIN


def test_event_session_does_not_close_the_cardio_of_the_day(monkeypatch):
    monkeypatch.setattr(db, "save_entry", lambda e: e)
    day = "2026-11-14"
    entry = {"id": 7, "date": day, "kind": "cardio", "status": "planejado", "day_name": "Cardio",
             "items": [{"exercise_id": "cardio_eliptico", "duration_min": 20}]}
    fest = {**steps.event_session(day, 12000, "festival"), "id": 50}
    walk = {**steps.event_session(day, 12000, "caminhada"), "id": 51}
    assert schedule.sync_status([dict(entry)], [fest], TODAY)[0]["status"] == "perdido"
    assert schedule.sync_status([dict(entry)], [walk], TODAY)[0]["status"] == "feito"


def test_garmin_activity_brings_steps_and_cadence():
    act = {"activityType": {"typeKey": "running"}, "duration": 2400, "distance": 6000, "averageHR": 150,
           "steps": 6480.0, "averageRunCadence": 162.4}
    it = garmin_sync.endurance_item(act, 190)
    assert it["steps"] == 6480 and it["cadence"] == 162
    assert steps.item_steps(it) == 6480
    ride = garmin_sync.endurance_item({"activityType": {"typeKey": "cycling"}, "duration": 3600}, 190)
    assert "steps" not in ride


# ------------------------------------------------------------------------------- API ---
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "a.sqlite3"))
    from gymgarmin.app import app

    clock.freeze(datetime(2026, 11, 16, 12, 0))
    try:
        with TestClient(app) as c:
            yield c
    finally:
        clock.freeze(None)


def _seed(extra: dict[int, int]) -> None:
    vals, sessions = _routine(extra)
    for d, rec in _days(vals, sleep_start={1: "02:40"}).items():
        db.kv_set(f"health:{d}", json.dumps(rec))
    for s in sessions:
        db.save_session({k: v for k, v in s.items() if k != "id"})


def test_api_ask_answer_and_undo(client):
    _seed({2: 19400})
    day = (TODAY - timedelta(days=2)).isoformat()
    s = client.get("/api/steps").json()
    assert [p["date"] for p in s["pending"]] == [day] and s["has_data"]
    before = load.pmc(db.list_sessions(limit=500), {}, today=TODAY, days=14)["atl"] if hasattr(load, "pmc") else None

    r = client.post("/api/steps/tag", json={"date": day, "tag": "festival"}).json()
    assert r["pending"] == [] and r["tag"]["tag"] == "festival" and r["session_id"]
    ev = db.get_session(r["session_id"])
    assert ev["source"] == "passos" and ev["date"].startswith(day) and ev["items"][0]["steps"] == r["tag"]["extra"]
    row = next(x for x in r["series"] if x["date"] == day)
    assert row["event"] and row["parts"]["evento"] == r["tag"]["extra"] and abs(row["daily"] - s["pending"][0]["baseline"]) <= 1
    assert r["recent_event"]["tag"] == "festival" and r["recent_event"]["days_ago"] == 2
    after = load.pmc(db.list_sessions(limit=500), {}, today=TODAY, days=14)["atl"]
    assert before is None or after > before  # o festival entrou na carga recente

    # o painel de saúde fala do dia a dia separado dos treinos
    h = client.get("/api/health").json()
    area = next(a for a in h["areas"] if a["title"] == "Atividade diária")
    assert "dia a dia" in area["text"] and h["steps"]["tags"][day]["label"]

    # trocar a resposta para "dia normal": a caminhada estimada sai e o dia deixa de ser perguntado
    r = client.post("/api/steps/tag", json={"date": day, "tag": "nada"}).json()
    assert r["session_id"] is None and db.get_session(ev["id"]) is None and r["pending"] == []
    assert not any(s_.get("source") == "passos" for s_ in db.list_sessions(limit=500))

    # desfazer: volta a perguntar
    r = client.post("/api/steps/tag", json={"date": day, "tag": None}).json()
    assert [p["date"] for p in r["pending"]] == [day] and day not in r["tags"]


def test_api_tag_validation(client):
    _seed({2: 19400})
    assert client.post("/api/steps/tag", json={"date": "ontem", "tag": "festival"}).status_code == 400
    assert client.post("/api/steps/tag", json={"date": (TODAY - timedelta(days=2)).isoformat(), "tag": "balada"}).status_code == 400
    assert client.post("/api/steps/tag", json={"date": (TODAY + timedelta(days=1)).isoformat(), "tag": "festival"}).status_code == 400
    assert client.post("/api/steps/tag", json={"date": (TODAY - timedelta(days=60)).isoformat(), "tag": "festival"}).status_code == 400
    # responder duas vezes não cria duas caminhadas
    day = (TODAY - timedelta(days=2)).isoformat()
    client.post("/api/steps/tag", json={"date": day, "tag": "festival"})
    client.post("/api/steps/tag", json={"date": day, "tag": "passeio"})
    assert sum(1 for s in db.list_sessions(limit=500) if s.get("source") == "passos") == 1


def test_fill_steps_of_runs_imported_before(client):
    from gymgarmin import app as app_mod

    s = db.save_session({"date": "2026-11-10T07:00", "source": "garmin", "garmin_activity_id": 9001,
                         "items": [{"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 40}]})
    assert app_mod._fill_activity_steps({"9001": {"steps": 6600, "cadence": 165.2}, "777": {"steps": 10}}) == 1
    it = db.get_session(s["id"])["items"][0]
    assert it["steps"] == 6600 and it["cadence"] == 165
    assert app_mod._fill_activity_steps({"9001": {"steps": 7000}}) == 0  # já tinha: não mexe


def test_generic_activity_does_not_stand_in_for_a_planned_run(monkeypatch):
    """Jogo de futebol no dia da corrida planejada: entra na carga, mas a corrida não vira 'feita' por troca de modalidade."""
    monkeypatch.setattr(db, "save_entry", lambda e: e)
    day = "2026-11-14"
    run = {"id": 7, "date": day, "kind": "resistencia", "sport": "corrida", "status": "planejado", "day_name": "Corrida leve",
           "items": [{"exercise_id": "corrida_rua", "sport": "corrida", "endurance": {"dur_s": 2400}}]}
    game = {"id": 60, "date": f"{day}T20:00", "source": "garmin", "garmin_activity_id": 1,
            "items": [{"exercise_id": "atividade_coletivo", "duration_min": 70, "zone": 4, "avg_hr": 152}]}
    bike = {"id": 61, "date": f"{day}T07:00", "source": "garmin", "garmin_activity_id": 2,
            "items": [{"exercise_id": "ciclismo_estrada", "sport": "ciclismo", "duration_min": 60}]}
    assert schedule.sync_status([dict(run)], [game], TODAY)[0]["status"] == "perdido"
    assert schedule.sync_status([dict(run)], [bike], TODAY)[0]["status"] == "feito"  # outra modalidade de resistência continua valendo
    cardio = {"id": 8, "date": day, "kind": "cardio", "status": "planejado", "day_name": "Cardio", "items": [{"exercise_id": "cardio_eliptico", "duration_min": 20}]}
    assert schedule.sync_status([dict(cardio)], [game], TODAY)[0]["status"] == "feito"  # cardio livre: o jogo conta
