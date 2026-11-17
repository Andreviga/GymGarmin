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


# ------------------------------------------------- regressões da simulação de cargas ---
def test_typo_is_flagged_not_a_permanent_record():
    loads = [60, 62.5, 62.5, 625, 65, 65, 67.5, 67.5]  # 625 = 62,5 digitado errado
    sessions = [_s(56 - 7 * i, [_bench((l, 8), (l, 8))]) for i, l in enumerate(loads)]
    h = history.exercise_history(sessions, BENCH, today=TODAY)
    bad = [p for p in h["points"] if p["top_load"] == 625][0]
    assert bad["suspect"] and not bad["prs"]
    assert h["summary"]["best"]["top_load"] == 67.5 and h["summary"]["status"] == "progredindo"
    assert any(p["prs"] for p in h["points"][-4:])  # os recordes de verdade voltam a aparecer


def test_rpe_makes_deload_not_look_like_a_drop():
    sets = lambda l, r, rpe: {"exercise_id": BENCH, "sets": [{"load_kg": l, "reps": r, "rpe": rpe}] * 3}  # noqa: E731
    sessions = [_s(35, [sets(80, 5, 8)]), _s(28, [sets(82.5, 5, 8)]), _s(21, [sets(85, 5, 8)]), _s(14, [sets(87.5, 5, 8)]),
                _s(7, [sets(90, 5, 8.5)]), _s(1, [sets(75, 5, 6)])]  # última = deload leve (RPE 6)
    sm = history.exercise_history(sessions, BENCH, today=TODAY)["summary"]
    assert sm["status"] != "caindo"
    assert history.e1rm(75, 5, 6) > history.e1rm(75, 5)  # RPE 6 = 4 reps no tanque


def test_switching_rep_ranges_is_not_a_fake_record():
    # mesma força: 5×100 kg (e1RM 116,7) e depois 10×85 kg (Epley daria 113,3 → 10 reps "rendem" diferente)
    sessions = [_s(56 - 7 * i, [_bench((100, 5), (100, 5))]) for i in range(4)]
    sessions += [_s(28 - 7 * i, [_bench((90, 10), (90, 10))]) for i in range(4)]  # e1RM 120 só por trocar de faixa
    h = history.exercise_history(sessions, BENCH, today=TODAY)
    first_10 = [p for p in h["points"] if p["best_set"]["reps"] == 10][0]
    assert "e1rm" not in first_10["prs"]  # trocar 5 por 10 reps não é ficar mais forte
    assert h["summary"]["status"] in ("estavel", "estagnado")


def test_rep_record_and_return_from_break():
    sessions = [_s(120 - 7 * i, [_bench((80 + 2.5 * i, 5))]) for i in range(6)]          # pico 92,5
    sessions += [_s(42 - 7 * i, [_bench((80 + 2.5 * i, 5))]) for i in range(6)]          # voltando de pausa
    sessions.append(_s(3, [_bench((65, 10))]))                                            # primeira vez com 10 reps
    sessions.append(_s(0, [_bench((70, 10))]))                                            # 70×10: recorde para 10 reps
    h = history.exercise_history(sessions, BENCH, today=TODAY)
    assert "rep_load" in h["points"][-1]["prs"] and h["points"][-1]["rep_pr"]["reps"] == 10
    sm = history.exercise_history(sessions[:-2], BENCH, today=TODAY)["summary"]
    assert sm["status"] == "progredindo"  # subindo de novo, mesmo abaixo do recorde antigo


def test_pullups_bodyweight_then_weighted_same_curve():
    pu = lambda reps, kg: {"exercise_id": "Pullups", "sets": [{"reps": reps, "load_kg": kg}] * 3}  # noqa: E731
    sessions = [_s(70 - 7 * i, [pu(8 + i, 0)]) for i in range(6)] + [_s(28 - 7 * i, [pu(8, 5 + 2.5 * i)]) for i in range(4)]
    h = history.exercise_history(sessions, "Pullups", today=TODAY, bodyweight=80)
    sm = h["summary"]
    assert sm["metric"] == "carga" and sm["bodyweight"] == 80 and sm["status"] == "progredindo"
    assert abs(sm["change_total_pct"]) < 60  # antes: reps × kg misturados davam variações absurdas
