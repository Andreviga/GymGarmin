"""'Atualizar os próximos treinos': o app mostra o que mudaria e refaz só o que ainda não foi feito."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db

PROF = {"training_months": 30, "goal": "hipertrofia", "session_minutes": 75, "available_days": [0, 1, 2, 3, 4, 5, 6],
        "age": 34, "sex": "M", "weight_kg": 80, "height_cm": 180,
        "endurance": {"sports": {"corrida": {"enabled": True, "env": "rua", "sessions": 3}}}}


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "r.sqlite3"))
    clock.freeze(datetime(2026, 11, 24, 7, 0))
    from gymgarmin.app import app
    with TestClient(app) as cl:
        yield cl
    clock.freeze(None)


def test_semana_em_dia_nao_avisa_e_desatualizada_mostra_o_que_muda(c):
    c.post("/api/generator/apply", json={"profile": PROF})
    sch = c.get("/api/schedule").json()
    assert sch["rebuild"] == []                      # recém-montada: nada a atualizar
    # treino da semana trocado à mão no banco (como uma regra antiga): o aviso aparece
    e = next(x for x in sch["entries"] if x["status"] == "planejado" and x["date"] > sch["today"])
    raw = db.get_entry(e["id"])
    raw["day_name"] = "Corrida: Corre-caminha 5 × (1.5 min correndo + 2 min caminhando)"
    raw["items"] = raw["items"][:1]
    db.save_entry(raw)
    prev = c.get("/api/schedule").json()["rebuild"]
    assert [p["date"] for p in prev] == [e["date"]] and prev[0]["after"] == e["day_name"]
    # atualizar: volta ao da planilha, e os feitos ficam
    feito = next(x for x in sch["entries"] if x["date"] == sch["today"])
    raw2 = db.get_entry(feito["id"]); raw2["status"] = "feito"; db.save_entry(raw2)
    r = c.post("/api/schedule/rebuild").json()
    assert r["ok"] and r["changes"]
    after = c.get("/api/schedule").json()
    assert after["rebuild"] == []
    assert any(x["id"] == feito["id"] and x["status"] == "feito" for x in after["entries"])
