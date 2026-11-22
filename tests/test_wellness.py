"""Bem-estar (yoga, Pilates, mobilidade, alongamento, respiração) e treino em casa."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import catalog, clock, generator as G, wellness


def test_sessoes_validas():
    d = wellness.data()
    ids = [s["id"] for s in d["sessions"]]
    assert len(ids) == len(set(ids)) >= 15
    for s in d["sessions"]:
        assert s["cat"] in d["cats"] and s["steps"] and s["sources"] and s["why"] and s["cautions"], s["id"]
        assert all(g in d["goals"] for g in s["goal"]), s["id"]
        assert 1 <= s["minutes"] <= 40, (s["id"], s["minutes"])
        for st in s["steps"] + [x["alt"] for x in s["steps"] if x.get("alt")]:
            assert st["t"] > 0 and st["n"] and st["how"], (s["id"], st)
            assert not st.get("ex") or catalog.get(st["ex"]), st.get("ex")
            if st.get("breath"):
                assert len(st["breath"]) == 4 and sum(st["breath"]) >= 6
    # antes de correr: nada de alongamento parado longo (≥60 s tira força)
    pre = wellness.session("aquecimento_corrida")
    assert not any(st["t"] >= 60 and not st.get("tap") and "Trote" not in st["n"] and "Acelera" not in st["n"] for st in pre["steps"])


def test_osteoporose_troca_flexao_de_coluna():
    p = {"personal": {"conditions": ["osteoporose"]}}
    s = wellness.session("pilates_iniciante", p)
    names = [st["n"] for st in s["steps"]]
    assert "Meio rolamento para trás" not in names and "Perdigueiro" in names
    assert s.get("personal_note")
    assert "Meio rolamento para trás" in [st["n"] for st in wellness.session("pilates_iniciante", {})["steps"]]
    via_lim = wellness.session("amplitude", {"limitations": [{"key": "osso"}]})
    assert "Terra romeno lento" not in [st["n"] for st in via_lim["steps"]]


def test_sugestoes_do_dia():
    ids = lambda r: [x["id"] for x in r]  # noqa: E731
    assert ids(wellness.suggest({}, hour=7, run_planned=True))[0] == "aquecimento_corrida"
    assert ids(wellness.suggest({}, hour=18, run_done=True))[0] == "pos_corrida"
    assert "yoga_sono" in ids(wellness.suggest({}, hour=22))
    st = wellness.suggest({"personal": {"stress": "muito", "job": "sentado"}, "limitations": [{"key": "joelho"}]}, hour=10)
    assert {"resp_suspiro", "joelho"} <= set(ids(st)) and len(st) <= 3
    assert wellness.suggest({}, hour=10)  # sempre há algo


def test_casa_sem_aparelho_e_com_elastico():
    for kit in ([], ["elastico"], ["halteres", "banco", "barra"]):
        p = {"training_months": 14, "goal": "hipertrofia", "available_days": [0, 2, 4], "session_minutes": 45, "age": 35,
             "sex": "F", "weight_kg": 65, "gym": {"id": "casa"}, "home_kit": kit}
        pl = G.generate(p, 0)
        for d in pl["days"]:
            main = [catalog.get(i["exercise_id"]) for i in d["items"] if (i.get("phase") or "principal") == "principal"]
            assert not any(e.get("equipment") in ("machine", "cable", "barbell") for e in main), (kit, d["name"])
            assert not any(e["id"].startswith("cardio_") and e["id"] != "cardio_corda" for e in main), (kit, d["name"])
            pats = {G_pat for G_pat in (i.get("pattern") for i in d["items"]) if G_pat}
            assert pats & {"squat", "single_leg", "hinge", "leg_curl"}, (kit, d["name"])   # perna sempre
            assert d["time"]["total"] >= 0.75 * 45
        assert "Treino em casa" in pl["rationale"][0]
    if_bar = G.generate({**p, "home_kit": []}, 0)
    assert "Pullups" not in {i["exercise_id"] for d in if_bar["days"] for i in d["items"]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "w.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 21, 0))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c
    clock.freeze(None)


def test_api_bem_estar_e_casa(client):
    client.put("/api/profile", json={"training_months": 14, "goal": "hipertrofia", "available_days": [1, 3, 5], "sex": "F",
                                     "age": 40, "weight_kg": 60, "personal": {"conditions": ["osteoporose"]}})
    d = client.get("/api/wellness").json()
    assert d["sessions"] and d["suggestions"][0]["id"] == "yoga_sono"     # 21h, dia sem treino
    assert client.get("/api/wellness?light=1").json()["suggestions"]
    s = client.get("/api/wellness/pilates_iniciante").json()
    assert s["personal_note"] and client.get("/api/wellness/nao_existe").status_code == 404
    r = client.post("/api/sessions", json={"date": "2026-11-16T21:00", "client_id": "ws1", "day_name": s["title"],
                                           "items": [{"exercise_id": "atividade_mobilidade", "kind": "cardio", "duration_min": 12, "zone": 1, "done": True}]})
    assert r.status_code == 200
    prof = client.put("/api/gym", json={"id": "casa", "home_kit": ["elastico", "xyz", "corda"]}).json()
    assert prof["gym"]["id"] == "casa" and prof["home_kit"] == ["elastico", "corda"] and prof["gym_type"] == "basica"
