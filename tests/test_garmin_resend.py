"""Regerar o treino e reenviar ao Garmin: os treinos antigos são apagados do relógio (sem duplicar)."""
from fastapi.testclient import TestClient

from gymgarmin import auth, garmin_sync

PROF = {"training_months": 72, "goal": "hipertrofia", "session_minutes": 90, "available_days": [0, 1, 2, 3, 4, 5, 6],
        "age": 34, "sex": "M", "weight_kg": 98}


def test_regenerate_then_resend_cleans_old_workouts(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    on_watch: dict[int, str] = {}
    counter = iter(range(1000, 9999))

    def push(payload, date):
        wid = next(counter)
        on_watch[wid] = date
        return {"workout_id": wid, "scheduled": date, "name": payload["workoutName"]}

    def delete(wid):
        if wid not in on_watch:
            raise RuntimeError("404 not found")
        on_watch.pop(wid)

    monkeypatch.setattr(garmin_sync, "push_workout", push)
    monkeypatch.setattr(garmin_sync, "delete_workout", delete)
    from gymgarmin.app import app
    with TestClient(app) as c:
        c.post("/api/generator/apply", json={"profile": PROF})
        first = c.post("/api/schedule/push-week", json={"only_outdated": False}).json()
        n = len(first)
        assert n and len(on_watch) == n
        # nada mudou: o cartão de reenviar não tem o que enviar
        assert c.post("/api/schedule/push-week", json={"only_outdated": True}).json() == []
        # regerar remonta a semana: os do relógio viram órfãos e são apagados no próximo envio
        c.post("/api/generator/apply", json={"profile": PROF, "seed": 1})
        pend = [e for e in c.get("/api/schedule").json()["entries"] if e["status"] == "planejado"]
        assert pend and all(not e.get("garmin_workout_id") for e in pend)
        second = c.post("/api/schedule/push-week", json={"only_outdated": True}).json()
        assert len(second) == len(pend)
        assert len(on_watch) == len(pend)  # sem duplicados: só a semana nova no relógio
    auth.reset_cache()
