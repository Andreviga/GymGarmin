"""Modelos especiais inspirados em treinos de famosos."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import catalog, clock, templates


def test_modelos_validos_com_fontes():
    f = templates.famous()
    assert len(f["templates"]) >= 10 and "não é endossado" in f["disclaimer"].lower()
    for t in f["templates"]:
        assert t["sources"] and all(u.startswith("https://") for u in t["sources"]), t["id"]
        assert t["about"] and t["cautions"] and t["confidence"] and t["level"]
        for d in t["days"]:
            for it in d["items"]:
                assert catalog.get(it["exercise_id"]), it["exercise_id"]
    assert all(not x["id"].startswith("famoso_") for x in templates.list_templates())   # não poluem a lista comum


def test_build_respeita_limitacoes_e_avisa():
    p = templates.build("famoso_reg_park_5x5", {"limitations": [{"key": "lombar", "severity": "evitar"}]})
    ids = [i["exercise_id"] for d in p["days"] for i in d["items"]]
    assert "Barbell_Deadlift" not in ids and "Inspirado em Reg Park" in p["notes"] and "Trocas" in p["notes"]
    assert "special" not in p


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "f.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c
    clock.freeze(None)


def test_api_usar_na_agenda(client):
    client.put("/api/profile", json={"training_months": 20, "goal": "hipertrofia", "available_days": [0, 2, 4], "age": 30, "sex": "M"})
    lst = client.get("/api/templates/especiais").json()
    assert any(t["id"] == "arnold_golden_six" for t in lst["templates"])
    p = client.post("/api/plans", json={"template": "famoso_arnold_golden_six"}).json()
    assert p["name"].startswith("Arnold Schwarzenegger")
    assert client.post(f"/api/plans/{p['id']}/use").json()["ok"]
    assert client.get("/api/profile").json()["schedule_plan_id"] == p["id"]
    sch = client.get("/api/schedule").json()
    assert any("Golden Six" in (e.get("day_name") or "") for e in sch["entries"])


def test_mesmo_treino_repetido_vira_varios_dias():
    p = templates.build("famoso_arnold_golden_six", {})
    assert len(p["days"]) == 3 and p["days"][0]["name"] == "Golden Six (1/3)"
    bl = templates.build("famoso_bruce_lee", {})
    assert len(bl["days"]) == 3 and bl["days"][2]["name"] == bl["days"][0]["name"]
