import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c


def test_full_flow(client):
    assert client.get("/").status_code == 200
    meta = client.get("/api/meta").json()
    assert meta["templates"] and meta["limitations"]

    p = client.put("/api/profile", json={"age": 35, "weight_kg": 80,
                                         "limitations": [{"key": "joelho", "severity": "evitar"}]}).json()
    assert p["limitations"][0]["key"] == "joelho"

    ex = client.get("/api/exercises", params={"q": "salto", "hide_avoid": True}).json()
    assert all(e["limitation"]["status"] != "evitar" for e in ex)

    plan = client.post("/api/plans", json={"template": "ppl"}).json()
    pid = plan["id"]
    assert len(plan["days"]) == 3

    a = client.get(f"/api/plans/{pid}/analysis").json()
    assert {m["muscle"] for m in a["muscles"]} >= {"chest", "lats", "quadriceps"}

    fit = client.get(f"/api/plans/{pid}/days/0/fit")
    assert fit.status_code == 200 and fit.content[8:12] == b".FIT"
    js = client.get(f"/api/plans/{pid}/days/0/connect.json").json()
    assert js["workoutSegments"]

    pre = client.get(f"/api/sessions/prefill/{pid}/0").json()
    items = []
    for it in pre["items"]:
        if it["kind"] == "cardio":
            items.append({"exercise_id": it["exercise_id"], "duration_min": 5, "zone": 2})
        else:
            items.append({"exercise_id": it["exercise_id"], "sets": [{"reps": 10, "load_kg": 20, "rpe": 8}] * 3})
    s = client.post("/api/sessions", json={"date": pre["date"], "plan_id": pid, "day_name": pre["day_name"],
                                           "items": items}).json()
    assert s["id"]
    rep = client.get("/api/report/weekly").json()
    chest = next(m for m in rep["muscles"] if m["muscle"] == "chest")
    assert chest["sets_7d"] > 0

    pre2 = client.get(f"/api/sessions/prefill/{pid}/0").json()
    assert any(it.get("last") for it in pre2["items"])

    csv = client.get(f"/api/plans/{pid}/csv")
    assert "exercicio" in csv.text
    imp = client.post("/api/plans/import-csv", json={"csv": csv.text, "save": True, "name": "Cópia"}).json()
    assert imp["saved"]["name"] == "Cópia"

    st = client.get("/api/garmin/status").json()
    assert st["connected"] is False
    r = client.post(f"/api/plans/{pid}/days/0/push", json={})
    assert r.status_code == 401  # sem login


def test_garmin_override(client):
    r = client.put("/api/exercises/Leg_Extensions/garmin", json={"category": "SQUAT", "exercise": "LEG_PRESS"})
    assert r.status_code == 200
    assert client.get("/api/exercises/Leg_Extensions").json()["garmin"]["confidence"] == "manual"
    bad = client.put("/api/exercises/Leg_Extensions/garmin", json={"category": "X", "exercise": "Y"})
    assert bad.status_code == 400
