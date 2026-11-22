"""Cargas: dois lados (peso total, metade de cada lado) e equivalência de carga entre exercícios/aparelhos."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import catalog, clock, generator as G, loads, progression


def test_dois_lados():
    for e in ("Dumbbell_Bench_Press", "One-Arm_Dumbbell_Row", "Hammer_Curls", "Leverage_Iso_Row", "Single-Leg_Leg_Press",
              "Cable_Seated_Lateral_Raise", "Split_Squat_with_Dumbbells", "Dumbbell_Shrug"):
        assert loads.sides(e), e
    for e in ("Goblet_Squat", "Barbell_Squat", "Machine_Bench_Press", "Butterfly", "Standing_Dumbbell_Triceps_Extension",
              "Pushups", "Single_Leg_Glute_Bridge"):
        assert not loads.sides(e), e
    assert loads.per_side("Dumbbell_Bench_Press", 40) == 20
    assert loads.per_side("Barbell_Squat", 100) is None


def test_arredonda_no_salto_que_existe():
    assert loads.round_load("Dumbbell_Bench_Press", 15) in (14, 16)       # 7/8 por lado: halter de 1 em 1 kg
    assert loads.round_load("Dumbbell_Bench_Press", 41) == 40            # 20 por lado: de 2 em 2 kg
    assert loads.round_load("Barbell_Squat", 101) == 100
    assert loads.round_load("Machine_Bench_Press", 52) == 50


def test_progressao_de_halter_sobe_no_salto_real():
    it = {"exercise_id": "Dumbbell_Bench_Press", "reps": "8-12"}
    last = {"date": "2026-11-10", "sets": [{"reps": 12, "load_kg": 40, "rpe": 8}] * 3}
    sug = progression.suggest(it, progression.pick_last([last]), today=datetime(2026, 11, 16).date())
    assert sug["load_kg"] == 44     # 22 kg por lado (de 2 em 2), não 41


def test_equivalencia_estima_pelo_exercicio_parecido():
    known = {"Barbell_Bench_Press_-_Medium_Grip": {"e1rm": 100, "date": "2026-11-10"}}
    est = loads.estimate("Dumbbell_Bench_Press", known, 10, 2.5)
    assert est and est["estimated"] and est["from"] == "Barbell_Bench_Press_-_Medium_Grip"
    # supino com halteres (total) costuma ser ~75–90% do supino com barra; com 10 reps e 2–3 no tanque
    assert 45 <= est["load_kg"] <= 70, est
    assert loads.estimate("Dumbbell_Bench_Press", {}, 10) is None
    # família diferente não serve de base
    assert loads.estimate("Dumbbell_Bench_Press", {"Barbell_Squat": {"e1rm": 140}}, 10) is None
    for eid, r in loads.ratios()["exercises"].items():
        assert catalog.get(eid), eid
        assert 0 < float(r["ratio"]) < 5, eid


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "l.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c
    clock.freeze(None)


def test_api_item_novo_vem_estimado(client):
    client.put("/api/profile", json={"training_months": 30, "goal": "hipertrofia", "available_days": [0, 2, 4], "sex": "M",
                                     "age": 35, "weight_kg": 80})
    client.post("/api/sessions", json={"date": "2026-11-12T08:00", "client_id": "a",
                                       "items": [{"exercise_id": "Barbell_Bench_Press_-_Medium_Grip",
                                                  "sets": [{"reps": 8, "load_kg": 80, "rpe": 8}] * 3}]})
    it = client.post("/api/sessions/item", json={"exercise_id": "Dumbbell_Bench_Press", "reps": "8-12", "sets": 3}).json()
    assert it["sides"] is True and it["suggestion"]["estimated"]
    assert it["sets"][0]["load_kg"] == it["suggestion"]["load_kg"] and it["suggestion"]["load_kg"] > 0
    assert "1ª série é teste" in it["suggestion"]["note"]
    plain = client.post("/api/sessions/item", json={"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "reps": "8-12", "sets": 3}).json()
    assert plain["sides"] is False and not plain["suggestion"].get("estimated")
