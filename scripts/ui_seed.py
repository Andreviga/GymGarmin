"""Cria bancos de exemplo (personas) para a simulação das telas no celular.

Uso:  python scripts/ui_seed.py <persona> <arquivo.sqlite3>
Personas: novo · musculacao · hibrido · extremo
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import timedelta
from pathlib import Path

persona, path = sys.argv[1], sys.argv[2]
os.environ["GYMGARMIN_DB"] = path
os.environ["GYMGARMIN_HOME"] = str(Path(path).parent)
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
Path(path).unlink(missing_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from gymgarmin import clock, db, garmin_sync, schedule  # noqa: E402
from gymgarmin.app import app  # noqa: E402

rng = random.Random(7)
today = clock.today()


def must(r):
    assert r.status_code < 400, (r.status_code, r.text[:300])
    return r.json()


def history(c, weeks: int, endurance: bool, n_per_week: int = 4):
    plan = must(c.get("/api/plans"))[0]
    days = [d for d in plan["days"] if d.get("kind") == "forca"] or plan["days"]
    start = today - timedelta(weeks=weeks)
    k = 0
    for w in range(weeks):
        for j in range(n_per_week):
            d = start + timedelta(weeks=w, days=j * 2)
            if d >= today:
                break
            day = days[k % len(days)]
            k += 1
            items = []
            for it in day["items"]:
                if (it.get("phase") or "principal") != "principal" or not it.get("sets"):
                    continue
                base = 20 + (hash(it["exercise_id"]) % 60)
                load = round(base * (1 + 0.015 * w), 1)
                items.append({"exercise_id": it["exercise_id"],
                              "sets": [{"reps": rng.randint(7, 11), "load_kg": load, "rpe": rng.choice([7, 8, 8.5, 9]), "done": True}
                                       for _ in range(int(it["sets"]))]})
            c.post("/api/sessions", json={"date": f"{d.isoformat()}T18:30", "plan_id": plan["id"], "day_name": day["name"],
                                          "items": items, "feeling": rng.choice([3, 4, 5])})
            if endurance:
                c.post("/api/sessions", json={"date": f"{d.isoformat()}T07:00", "day_name": "Corrida", "source": "garmin",
                                              "garmin_activity_id": 5_000_000 + k, "items": [
                                                  {"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 40 + w,
                                                   "distance_km": round(7 + w * 0.3, 2), "avg_hr": 148, "tss": 45}]})


with TestClient(app) as c:
    if persona == "novo":
        pass
    elif persona == "musculacao":
        must(c.put("/api/profile", json={"name": "Ana", "age": 31, "sex": "F", "weight_kg": 62, "training_months": 14,
                                         "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 60}))
        must(c.post("/api/generator/apply", json={}))
        history(c, 8, False)
        sch = must(c.get("/api/schedule"))
        past = [e for e in sch["entries"] if e["date"] < today.isoformat()]
        if past:
            c.post(f"/api/schedule/{past[0]['id']}/skip", json={})
    elif persona == "hibrido":
        must(c.put("/api/profile", json={"name": "André", "age": 38, "sex": "M", "weight_kg": 80, "training_months": 40,
                                         "goal": "hibrido", "available_days": [0, 1, 2, 3, 4, 5, 6], "session_minutes": 75,
                                         "limitations": [{"key": "joelho", "severity": "cuidado"}]}))
        must(c.put("/api/endurance", json={"sports": {
            "corrida": {"enabled": True, "env": "rua", "sessions": 3, "goal": {"type": "prova", "distance": 21.1, "target": "1:45:00",
                                                                              "date": (today + timedelta(weeks=9)).isoformat()}},
            "ciclismo": {"enabled": True, "env": "estrada", "sessions": 2, "goal": {"type": "volume", "weekly": 120}},
            "natacao": {"enabled": True, "env": "piscina", "sessions": 1}}, "readiness_auto": True}))
        c.post("/api/anchors/test", json={"sport": "corrida", "data": {"distance_km": 5, "time": "22:30"}})
        c.post("/api/anchors/test", json={"sport": "ciclismo", "data": {"ftp": 245}})
        c.post("/api/anchors/test", json={"sport": "natacao", "data": {"t400": "7:10", "t200": "3:20"}})
        must(c.post("/api/generator/apply", json={}))
        history(c, 8, True, 3)
        db.kv_set(f"readiness:{today.isoformat()}", json.dumps({"date": today.isoformat(), "garmin_readiness": 33,
                                                                  "hrv_status": "UNBALANCED", "sleep_score": 52,
                                                                  "body_battery_max": 41, "fetched_at": f"{today}T07:10"}))
        db.kv_set("garmin_metrics", json.dumps({"date": today.isoformat(), "ftp": 252, "lthr": 171, "vo2max": 52.1,
                                                "race_predictions": {"time5K": 1330, "time10K": 2790}}))
        garmin_sync._set_sync_state(display_name="andre", last_run=f"{today}T07:10:00", last_ok=True,
                                    last_ok_at=f"{today}T07:10:00", trigger="auto",
                                    last_imported=[{"name": "Corrida matinal", "date": today.isoformat(), "type": "running"}])
        c.get("/api/schedule")
        c.post("/api/readiness/apply", json={})
    elif persona == "extremo":
        must(c.put("/api/profile", json={"name": "Maria Eduarda de Albuquerque Figueiredo Nascimento", "age": 61, "sex": "F",
                                         "weight_kg": 95, "training_months": 2, "goal": "emagrecimento",
                                         "available_days": [0, 1, 2, 3, 4, 5], "session_minutes": 90,
                                         "limitations": [{"key": "joelho", "severity": "evitar"}, {"key": "ombro", "severity": "cuidado"},
                                                         {"key": "lombar", "severity": "cuidado"}]}))
        must(c.put("/api/endurance", json={"sports": {s: {"enabled": True, "sessions": 1} for s in
                                                      ("corrida", "ciclismo", "natacao", "remo", "caminhada", "trilha")},
                                           "event": {"type": "tri_olimpico", "date": (today + timedelta(weeks=14)).isoformat(),
                                                     "target": "3:10:00"}}))
        must(c.post("/api/generator/apply", json={}))
        plans = must(c.get("/api/plans"))
        p = plans[0]
        p["name"] = "Planilha de recondicionamento pós-lesão com foco em membros inferiores e core — versão 3 (revisada)"
        c.put(f"/api/plans/{p['id']}", json=p)
        history(c, 20, True, 5)
        garmin_sync._set_sync_state(display_name="maria", needs_reconnect=True, last_run=f"{today}T06:00:00", last_ok=False,
                                    last_error="Login do Garmin expirou: entre de novo (GarminConnectAuthenticationError).")
        sch = must(c.get("/api/schedule"))
        for e in sch["entries"][:2]:
            if e["date"] < today.isoformat():
                c.post(f"/api/schedule/{e['id']}/skip", json={})
    else:
        raise SystemExit(f"persona desconhecida: {persona}")
print("ok", persona)
