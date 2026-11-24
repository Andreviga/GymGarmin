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
        from datetime import timedelta
        from gymgarmin import clock
        t0 = clock.today()
        win = {(t0 + timedelta(days=k)).isoformat() for k in range(7)}
        second = c.post("/api/schedule/push-week", json={"only_outdated": True}).json()
        ents = c.get("/api/schedule").json()["entries"] + c.get("/api/schedule", params={"date": (t0 + timedelta(days=7)).isoformat()}).json()["entries"]
        pend = [e for e in ents if e["status"] == "planejado" and e["date"] in win]
        assert pend and len(second) == len(pend)
        # sem duplicados e sem treino da planilha antiga (nem na semana seguinte): só o novo no relógio
        assert sorted(on_watch) == sorted(e["garmin_workout_id"] for e in pend)
    auth.reset_cache()


def test_mudou_no_celular_o_relogio_atualiza_sozinho(tmp_path, monkeypatch):
    """Troca na planilha, academia do dia ou regerar: o autopush reenvia só o que mudou e apaga a versão antiga."""
    from datetime import datetime, timedelta

    from gymgarmin import clock, db
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    clock.freeze(datetime(2026, 11, 24, 7, 0))
    on_watch: dict[int, dict] = {}
    counter = iter(range(1000, 9999))

    def push(payload, date):
        wid = next(counter)
        on_watch[wid] = {"date": date, "payload": payload}
        return {"workout_id": wid, "scheduled": date, "name": payload["workoutName"]}

    def delete(wid):
        if wid not in on_watch:
            raise RuntimeError("404 not found")
        on_watch.pop(wid)

    monkeypatch.setattr(garmin_sync, "push_workout", push)
    monkeypatch.setattr(garmin_sync, "delete_workout", delete)
    real_status = garmin_sync.status
    monkeypatch.setattr(garmin_sync, "status", lambda: {**real_status(), "has_tokens": True, "connected": True})
    from gymgarmin.app import app
    try:
        with TestClient(app) as c:
            c.post("/api/generator/apply", json={"profile": PROF})
            # quem nunca enviou treino ao relógio: nada é enviado sozinho
            assert c.post("/api/garmin/autopush").json()["pushed"] == []
            assert not on_watch
            c.post("/api/schedule/push-week", json={"only_outdated": False})
            n = len(on_watch)
            assert n == 7
            assert c.post("/api/garmin/autopush").json()["pushed"] == []       # nada mudou
            # troca "usar sempre" no meio do treino → as entradas da agenda com esse exercício mudam
            today = clock.today().isoformat()
            ent = next(e for e in db.list_schedule(today, (clock.today() + timedelta(days=6)).isoformat())
                       if any((i.get("phase") or "principal") == "principal" and not i.get("endurance") for i in e.get("items", [])))
            src = next(i["exercise_id"] for i in ent["items"] if (i.get("phase") or "principal") == "principal" and not i.get("endurance"))
            old_wid = ent["garmin_workout_id"]
            alts = c.get(f"/api/exercises/{src}/alternatives").json()
            r = c.post(f"/api/plans/{ent['plan_id']}/swap", json={"from": src, "to": alts[0]["id"]}).json()
            assert r["changed"] >= 1
            ap = c.post("/api/garmin/autopush").json()
            assert ap["pushed"] and not ap.get("error")
            assert old_wid not in on_watch                       # versão antiga apagada do relógio
            assert len(on_watch) == n                            # sem duplicar
            assert all(not e.get("garmin_outdated") for e in db.list_schedule(today, (clock.today() + timedelta(days=6)).isoformat())
                       if e["status"] == "planejado")
            # regerar: tudo volta ao relógio na versão nova, inclusive o dia da semana seguinte
            c.post("/api/generator/apply", json={"profile": PROF, "seed": 3})
            ap = c.post("/api/garmin/autopush").json()
            pend = [e for e in db.list_schedule(today, (clock.today() + timedelta(days=6)).isoformat()) if e["status"] == "planejado"]
            assert sorted(on_watch) == sorted(e["garmin_workout_id"] for e in pend)
            # desligado: não mexe
            c.put("/api/garmin/autopush", json={"on": False})
            c.post(f"/api/schedule/{pend[0]['id']}/gym", json={"id": "casa", "home_kit": ["halteres"]})
            assert c.post("/api/garmin/autopush").json()["ran"] is False
    finally:
        clock.freeze(None)
        auth.reset_cache()
