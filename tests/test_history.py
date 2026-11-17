from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import history

BENCH = "Barbell_Bench_Press_-_Medium_Grip"
TODAY = date(2026, 11, 17)


def _s(days_ago, items, sid=None, source=None):
    d = (TODAY - timedelta(days=days_ago)).isoformat() + "T18:00"
    return {"id": sid, "date": d, "items": items, **({"source": source} if source else {})}


def _bench(*sets):
    return {"exercise_id": BENCH, "sets": [{"load_kg": l, "reps": r} for l, r in sets]}


def test_e1rm_epley():
    assert history.e1rm(100, 1) == 100
    assert history.e1rm(80, 10) == pytest.approx(106.7, abs=0.1)
    assert history.e1rm(0, 10) is None
    assert history.e1rm(50, 0) is None


def test_point_ignores_undone_and_warmup_sets():
    item = {"exercise_id": BENCH, "sets": [
        {"load_kg": 40, "reps": 10, "warmup": True},
        {"load_kg": 80, "reps": 8, "done": True},
        {"load_kg": 90, "reps": 8, "done": False},
        {"load_kg": 80, "reps": 6},  # sem marcação (Garmin) conta
    ]}
    p = history.session_point(_s(0, []), item)
    assert p["sets"] == 2 and p["top_load"] == 80 and p["reps_total"] == 14
    assert p["volume"] == 80 * 14
    assert p["best_set"] == {"load_kg": 80, "reps": 8}
    assert p["metric"] == "carga" and p["value"] == p["e1rm"]


def test_prs_and_progression_status():
    sessions = [_s(56 - 7 * i, [_bench((60 + 2.5 * i, 8), (60 + 2.5 * i, 8))], sid=i) for i in range(8)]
    h = history.exercise_history(sessions, BENCH, today=TODAY)
    pts = h["points"]
    assert len(pts) == 8
    assert pts[0]["prs"] == []  # primeiro treino não é recorde
    assert all("e1rm" in p["prs"] and "top_load" in p["prs"] for p in pts[1:])
    sm = h["summary"]
    assert sm["status"] == "progredindo"
    assert sm["best"]["top_load"] == 77.5
    assert sm["change_total_pct"] > 25
    assert sm["sessions_since_pr"] == 0


def test_stagnation_detected():
    loads = [70, 72.5, 75, 75, 75, 72.5, 75, 75]
    sessions = [_s(56 - 7 * i, [_bench((l, 8), (l, 8))]) for i, l in enumerate(loads)]
    sm = history.exercise_history(sessions, BENCH, today=TODAY)["summary"]
    assert sm["status"] == "estagnado"
    assert sm["sessions_since_pr"] >= 4
    assert "deload" in sm["message"]


def test_drop_detected():
    loads = [80, 80, 77.5, 75, 72.5, 70]
    sessions = [_s(42 - 7 * i, [_bench((l, 6))]) for i, l in enumerate(loads)]
    assert history.exercise_history(sessions, BENCH, today=TODAY)["summary"]["status"] == "caindo"


def test_bodyweight_and_timed_metrics():
    sessions = [
        _s(14, [{"exercise_id": "Pushups", "sets": [{"reps": 15, "load_kg": 0}]},
                {"exercise_id": "Plank", "sets": [{"duration_s": 45}]}]),
        _s(7, [{"exercise_id": "Pushups", "sets": [{"reps": 18, "load_kg": None}]},
               {"exercise_id": "Plank", "sets": [{"duration_s": 60}]}]),
    ]
    push = history.exercise_history(sessions, "Pushups", today=TODAY)
    assert push["summary"]["metric"] == "reps" and push["summary"]["last_value"] == 18
    assert push["points"][1]["prs"] == ["max_reps"]
    plank = history.exercise_history(sessions, "Plank", today=TODAY)
    assert plank["summary"]["metric"] == "tempo" and plank["summary"]["best"]["max_duration_s"] == 60


def test_same_day_sessions_merge():
    sessions = [_s(0, [_bench((80, 5))], sid=1), _s(0, [_bench((85, 3))], sid=2)]
    pts = history.exercise_history(sessions, BENCH, today=TODAY)["points"]
    assert len(pts) == 1
    assert pts[0]["sets"] == 2 and pts[0]["top_load"] == 85


def test_overview_and_recent_prs():
    sessions = [_s(20, [_bench((70, 8))]), _s(10, [_bench((75, 8))]),
                _s(5, [{"exercise_id": "Barbell_Squat", "sets": [{"load_kg": 100, "reps": 5}]}])]
    ov = history.overview(sessions, today=TODAY)
    assert [r["exercise_id"] for r in ov["exercises"]] == ["Barbell_Squat", BENCH]
    assert ov["recent_prs"][0]["exercise_id"] == BENCH
    assert len(ov["exercises"][1]["spark"]) == 2


def test_session_prs_only_for_that_day():
    sessions = [_s(7, [_bench((70, 8))]), _s(0, [_bench((72.5, 8))])]
    prs = history.session_prs(sessions, sessions[-1])
    assert len(prs) == 1 and prs[0]["exercise_id"] == BENCH and "e1rm" in prs[0]["prs"]
    assert history.session_prs(sessions, sessions[0]) == []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c


def test_history_api(client):
    assert client.get("/api/history").json() == {"exercises": [], "recent_prs": []}
    r1 = client.post("/api/sessions", json={"date": "2026-11-01T18:00", "items": [_bench((60, 10))]}).json()
    assert r1["prs"] == []
    r2 = client.post("/api/sessions", json={"date": "2026-11-08T18:00", "items": [_bench((65, 10))]}).json()
    assert r2["prs"] and r2["prs"][0]["best_set"] == {"load_kg": 65.0, "reps": 10}

    ov = client.get("/api/history").json()
    assert ov["exercises"][0]["exercise_id"] == BENCH
    h = client.get(f"/api/history/{BENCH}").json()
    assert h["name"] and len(h["points"]) == 2 and h["summary"]["best"]["top_load"] == 65
    assert client.get("/api/history/Inexistente").json()["summary"] == {"sessions": 0}
