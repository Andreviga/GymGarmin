"""Treino gravado no relógio (Garmin ou qualquer outro) conferido exercício por exercício."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, ingest, watch_labels as wl, watch_review as wr


def _sets(txt: str) -> list[dict]:
    return [{"reps": int(a), "load_kg": float(b), "duration_s": 25} for a, b in (p.split("x") for p in txt.split())]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "w.sqlite3"))
    from gymgarmin.app import app

    clock.freeze(datetime(2026, 11, 16, 12, 0))  # segunda
    try:
        with TestClient(app) as c:
            c.put("/api/profile", json={"training_months": 18, "goal": "hipertrofia", "available_days": [0, 2, 4],
                                        "session_minutes": 60, "age": 35, "weight_kg": 80})
            yield c
    finally:
        clock.freeze(None)


def _garmin(items, day="2026-11-16T07:00", aid=555):
    return db.save_session({"date": day, "source": "garmin", "garmin_activity_id": aid, "notes": "Força",
                            "duration_min": 52, "garmin_summary": {"duration_min": 52, "avg_hr": 118, "kcal": 380}, "items": items})


def test_garmin_free_workout_reviewed_one_by_one(client):
    s = _garmin([
        {"exercise_id": "Dumbbell_Bicep_Curl", "sets": _sets("10x12 10x12 8x14"), "garmin": {"category": "CURL", "name": None}, "guess": True},
        {"exercise_id": wl.UNKNOWN_ID, "_name": wl.UNKNOWN_NAME, "sets": _sets("12x60 10x70 8x80 15x30 12x35 12x35"),
         "garmin": {"category": "UNKNOWN", "name": None}},
        {"exercise_id": "Leg_Extensions", "sets": _sets("12x40 12x45")},
    ])
    box = client.get("/api/watch/inbox").json()["items"]
    assert [x["id"] for x in box] == [s["id"]] and box[0]["guesses"] == 1 and box[0]["unknown_sets"] == 6

    d = client.get(f"/api/sessions/{s['id']}/edit").json()
    assert d["watch_review"] and d["watch"]["source"] == "Garmin"
    picks = [i for i in d["items"] if i.get("need_pick")]
    assert len(picks) == 2 and [len(p["sets"]) for p in picks] == [3, 3]   # a carga recomeçou: dois exercícios
    guess = next(i for i in d["items"] if i.get("watch_guess"))
    assert guess["watch_guess"] == "Rosca"

    # a pessoa: rosca era martelo, 1º bloco foi supino, o 2º fica sem nome, tira a extensora, corrige uma carga
    items = []
    for it in d["items"]:
        if it.get("exercise_id") == "Leg_Extensions":
            continue
        out = {"exercise_id": it["exercise_id"], "orig_id": it.get("orig_id"),
               "sets": [{k: x.get(k) for k in ("reps", "load_kg", "rpe", "duration_s")} for x in it["sets"]]}
        if it.get("watch_guess"):
            out["exercise_id"] = "Hammer_Curls"
        if it is picks[0]:
            out["exercise_id"] = "Barbell_Bench_Press_-_Medium_Grip"
            out["sets"][0]["load_kg"] = 62
        items.append(out)
    saved = client.put(f"/api/sessions/{s['id']}", json={"edit": True, "watch_review": True, "items": items}).json()
    got = [(i["exercise_id"], len(i["sets"])) for i in saved["items"]]
    assert got == [("Hammer_Curls", 3), ("Barbell_Bench_Press_-_Medium_Grip", 3), (wl.UNKNOWN_ID, 3)]
    assert saved["items"][1]["sets"][0]["load_kg"] == 62 and saved["items"][1]["labeled"] == "bloco"
    assert "guess" not in saved["items"][0] and saved["items"][0]["labeled"] == "tela"
    assert saved["reviewed"] and saved["garmin_activity_id"] == 555 and saved["duration_min"] == 52
    assert client.get("/api/watch/inbox").json()["items"] == []
    # o app aprendeu: a próxima "rosca" do relógio já chega como rosca martelo
    prefs = wl.category_prefs(wl.load_prefs(db.kv_get(wl.PREFS_KEY)))
    assert prefs["CURL"] == "Hammer_Curls"


def test_confirming_a_guess_keeps_it_and_stops_asking(client):
    s = _garmin([{"exercise_id": "Barbell_Squat", "sets": _sets("10x60 8x70"), "garmin": {"category": "SQUAT", "name": None}, "guess": True}])
    d = client.get(f"/api/sessions/{s['id']}/edit").json()
    it = d["items"][0]
    saved = client.put(f"/api/sessions/{s['id']}", json={"edit": True, "watch_review": True, "items": [
        {"exercise_id": it["exercise_id"], "orig_id": it["orig_id"], "confirm_guess": True, "sets": it["sets"]}]}).json()
    assert saved["items"][0]["exercise_id"] == "Barbell_Squat" and "guess" not in saved["items"][0]


def test_ok_button_marks_reviewed(client):
    s = _garmin([{"exercise_id": "Barbell_Squat", "sets": _sets("10x60 8x70")}])
    assert client.get("/api/watch/inbox").json()["items"]
    client.post(f"/api/watch/inbox/{s['id']}/ok")
    assert client.get("/api/watch/inbox").json()["items"] == []


def test_time_only_watch_gets_exercises_and_closes_the_plan(client):
    client.post("/api/generator/apply", json={})
    w = ingest.workout_session({"id": "hc-1", "type": "STRENGTH_TRAINING", "start": "2026-11-16T07:00:00-03:00",
                                "end": "2026-11-16T07:50:00-03:00", "duration_min": 50, "avg_hr": 121, "kcal": 340}, "android", 185)
    s = db.save_session(w)
    box = client.get("/api/watch/inbox").json()["items"]
    assert box and box[0]["time_only"] and box[0]["source"].startswith("relógio")
    d = client.get(f"/api/sessions/{s['id']}/edit").json()
    assert d["items"] == [] and d["planned"]["items"]
    items = [{"exercise_id": p["exercise_id"], "sets": [{"reps": 10, "load_kg": 40}] * 3} for p in d["planned"]["items"]]
    saved = client.put(f"/api/sessions/{s['id']}", json={"edit": True, "watch_review": True, "items": items}).json()
    assert all(i.get("sets") for i in saved["items"])                      # o item genérico de 50 min saiu
    assert saved["garmin_summary"]["avg_hr"] == 121 and saved["duration_min"] == 50
    sch = client.get("/api/schedule").json()
    today = next(e for e in sch["entries"] if e["date"] == "2026-11-16" and e["kind"] == "forca")
    assert today["status"] == "feito"


def test_health_connect_segments_become_sets():
    segs = [{"type": "BENCH_PRESS", "reps": 10, "start": "2026-11-16T07:01:00", "end": "2026-11-16T07:01:40"},
            {"type": "REST", "reps": 0, "start": "2026-11-16T07:01:40", "end": "2026-11-16T07:03:00"},
            {"type": "BENCH_PRESS", "reps": 8, "start": "2026-11-16T07:03:00", "end": "2026-11-16T07:03:35"},
            {"type": "ARM_CURL", "reps": 12, "start": "2026-11-16T07:06:00", "end": "2026-11-16T07:06:40"},
            {"type": "RUNNING", "reps": 0, "start": "2026-11-16T07:08:00", "end": "2026-11-16T07:20:00"},
            {"type": "WEIGHTLIFTING", "reps": 9, "start": "2026-11-16T07:22:00", "end": "2026-11-16T07:22:40"}]
    items = wr.segment_items(segs, {"CURL": "Hammer_Curls"})
    assert [(i["exercise_id"], len(i["sets"])) for i in items] == [
        ("Barbell_Bench_Press_-_Medium_Grip", 2), ("Hammer_Curls", 1), (wl.UNKNOWN_ID, 1)]
    assert items[0]["guess"] and items[1]["labeled"] == "categoria" and items[0]["sets"][0]["load_kg"] is None
    w = ingest.workout_session({"id": "hc-2", "type": "STRENGTH_TRAINING", "start": "2026-11-16T07:00:00-03:00",
                                "end": "2026-11-16T07:30:00-03:00", "avg_hr": 120, "segments": segs}, "android", 185)
    assert [i["exercise_id"] for i in w["items"]][0] == "Barbell_Bench_Press_-_Medium_Grip"
    assert w["garmin_summary"]["avg_hr"] == 120 and w["duration_min"] == 30


def test_merged_app_workout_and_runs_are_not_in_the_inbox(client):
    db.save_session({"date": "2026-11-16T07:00", "source": "garmin", "garmin_activity_id": 9, "merged": True,
                     "items": [{"exercise_id": "Barbell_Squat", "sets": _sets("10x60")}]})
    db.save_session({"date": "2026-11-16T08:00", "source": "garmin", "garmin_activity_id": 10,
                     "items": [{"exercise_id": "corrida", "duration_min": 30, "sport": "corrida"}]})
    assert client.get("/api/watch/inbox").json()["items"] == []
