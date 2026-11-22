"""Academia da pessoa: aparelhos prováveis, 'não tem aqui' e o substituto mais parecido."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import catalog, clock, generator as G, gyms

BASE = {"training_months": 30, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 75, "age": 35,
        "sex": "M", "weight_kg": 80, "height_cm": 178}


def _ids(pl):
    return {i["exercise_id"] for d in pl["days"] for i in d["items"]}


def test_lista_de_sp_e_kits_validos():
    d = gyms.data()
    assert {g["id"] for g in d["gyms"]} >= {"ironberg", "les_cinq", "six", "reebok_club", "bodytech"}
    for g in d["gyms"] + d["generic"]:
        assert g["kit"] in d["kits"]
    for k in d["kits"].values():
        assert k["gym_type"] in G.GYM_TYPES
    bad = [(k, v) for k, l in gyms.CLOSEST.items() for v in [k] + l if not catalog.get(v)]
    assert not bad


def test_kit_decide_os_aparelhos():
    iron, boutique, basica = {"gym": {"id": "ironberg"}}, {"gym": {"id": "les_cinq"}}, {"gym_type": "basica"}
    assert gyms.kit_allows(gyms.kit_of(iron), "Belt_Squat")            # rara: musculação de alto nível tem
    assert not gyms.kit_allows(gyms.kit_of(boutique), "Belt_Squat")    # boutique: não
    assert not gyms.kit_allows(gyms.kit_of(boutique), "Pendulum_Squat")
    assert not gyms.kit_allows(gyms.kit_of(basica), "Pendulum_Squat")  # média: básica não
    assert gyms.kit_allows(gyms.kit_of(basica), "Barbell_Squat")       # peso livre: toda academia
    assert gyms.status({**boutique, "gym_has": ["Belt_Squat"]}, "Belt_Squat") == "tem"
    assert gyms.status({**iron, "gym_missing": ["Belt_Squat"]}, "Belt_Squat") == "nao_tem"
    # sem academia escolhida: premium continua liberando tudo (como antes)
    assert gyms.kit_allows(gyms.kit_of({"gym_type": "premium"}), "Belt_Squat")


def test_substituto_mais_parecido():
    pool = list(G.PATTERNS["squat"]) + list(G.PREMIUM_EXTRA["squat"])
    iron = gyms.substitutes("Pendulum_Squat", {"gym": {"id": "ironberg"}}, pool)
    assert iron[0] == "VSquat_Machine"
    boutique = gyms.substitutes("Pendulum_Squat", {"gym": {"id": "les_cinq"}}, pool)
    assert boutique[0] == "Hack_Squat" and "VSquat_Machine" not in boutique
    bench = gyms.substitutes("Barbell_Bench_Press_-_Medium_Grip", {}, list(G.PATTERNS["h_push"]))
    assert bench[0] == "Dumbbell_Bench_Press"


def test_gerador_respeita_academia_e_o_que_falta():
    boutique = G.generate({**BASE, "gym": {"id": "les_cinq"}, "gym_type": "premium"}, 0)
    rare = {m for m, x in gyms.machines().items() if x.get("availability_br") == "rara"}
    assert not (_ids(boutique) & rare), _ids(boutique) & rare
    assert "Pendulum_Squat" not in _ids(boutique)
    assert any("Les Cinq" in r for r in boutique["rationale"])
    iron = G.generate({**BASE, "gym": {"id": "ironberg"}, "gym_type": "premium"}, 0)
    some = sorted(_ids(iron) & set(gyms.machines()))
    assert some
    miss = G.generate({**BASE, "gym": {"id": "ironberg"}, "gym_type": "premium", "gym_missing": some}, 0)
    assert not (_ids(miss) & set(some))
    # o que a pessoa confirmou que tem entra mesmo numa academia completa (e o que não tem sai)
    pk = G._Picker(G.TIERS["avancado"], [], None, 0, "completa", {"gym_has": ["DyRow_Machine"], "gym_missing": ["Seated_Cable_Rows"]})
    ids = [c[0] for c in pk.candidates("h_pull")]
    assert "DyRow_Machine" in ids and "Seated_Cable_Rows" not in ids


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "g.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c
    clock.freeze(None)


def test_api_escolher_academia_e_nao_tem(client):
    client.put("/api/profile", json=BASE)
    lst = client.get("/api/gyms").json()
    assert lst["current"] is None and any(g["id"] == "ironberg" for g in lst["gyms"])
    prof = client.put("/api/gym", json={"id": "ironberg"}).json()
    assert prof["gym"]["name"] == "Ironberg" and prof["gym_type"] == "premium"
    assert client.put("/api/gym", json={"id": "nao_existe"}).status_code == 400
    client.post("/api/generator/apply", json={})
    pid = client.get("/api/profile").json()["schedule_plan_id"]
    plan = client.get(f"/api/plans/{pid}").json()
    eid = next(i["exercise_id"] for d in plan["days"] for i in d["items"] if i["exercise_id"] in gyms.machines())
    # só consultar não marca nada
    opt = client.post("/api/gym/missing", json={"exercise_id": eid, "apply": False}).json()
    assert opt["options"] and not client.get("/api/profile").json().get("gym_missing")
    alt = opt["options"][1]["id"]
    r = client.post("/api/gym/missing", json={"exercise_id": eid, "to": alt}).json()
    assert r["to"] == alt and r["changed"] >= 1
    plan = client.get(f"/api/plans/{pid}").json()
    assert eid not in {i["exercise_id"] for d in plan["days"] for i in d["items"]}
    assert client.get("/api/profile").json()["gym_missing"] == [eid]
    assert eid not in [x["id"] for x in client.get(f"/api/exercises/{alt}/alternatives").json()]
    assert client.get("/api/gyms").json()["missing"][0]["id"] == eid
    # gerar de novo: o que não tem não volta
    client.post("/api/generator/apply", json={})
    pid2 = client.get("/api/profile").json()["schedule_plan_id"]
    assert eid not in _ids(client.get(f"/api/plans/{pid2}").json())
    client.post("/api/gym/missing", json={"exercise_id": eid, "undo": True})
    assert not client.get("/api/profile").json()["gym_missing"]
    client.post("/api/gym/has", json={"exercise_id": "Belt_Squat"})
    assert client.get("/api/profile").json()["gym_has"] == ["Belt_Squat"]


def test_api_academia_do_dia(client):
    client.put("/api/profile", json={**BASE, "available_days": [0, 1, 2, 3, 4, 5, 6]})
    client.put("/api/gym", json={"id": "ironberg"})
    client.post("/api/generator/apply", json={})
    sch = client.get("/api/schedule").json()
    e = next(x for x in sch["entries"] if x["kind"] == "forca" and x["status"] == "planejado")
    orig = [i["exercise_id"] for i in e["items"]]
    # boutique: nada raro fica
    r = client.post(f"/api/schedule/{e['id']}/gym", json={"id": "les_cinq"}).json()
    rare = {m for m, x in gyms.machines().items() if x.get("availability_br") == "rara"}
    assert r["gym_day"]["name"] == "Les Cinq Gym" and not ({i["exercise_id"] for i in r["items"]} & rare)
    # em casa só com elástico: sem máquina/barra
    r = client.post(f"/api/schedule/{e['id']}/gym", json={"id": "casa", "home_kit": ["elastico"]}).json()
    eqs = {catalog.get(i["exercise_id"]).get("equipment") for i in r["items"] if (i.get("phase") or "principal") == "principal"}
    assert not eqs & {"machine", "cable", "barbell"} and r["gym_changes"]
    pre = client.get(f"/api/schedule/{e['id']}/prefill").json()
    assert [i["exercise_id"] for i in pre["items"]] == [i["exercise_id"] for i in r["items"]]
    # desfazer volta ao original
    back = client.post(f"/api/schedule/{e['id']}/gym", json={"id": None}).json()
    assert [i["exercise_id"] for i in back["items"]] == orig and "gym_day" not in back
    assert client.post(f"/api/schedule/{e['id']}/gym", json={"id": "xyz"}).status_code == 400
    # de academia básica para uma de musculação: oferece o aparelho moderno nos secundários
    client.put("/api/gym", json={"id": "outra_basica"})
    client.post("/api/generator/apply", json={})
    e2 = next(x for x in client.get("/api/schedule").json()["entries"] if x["kind"] == "forca" and x["status"] == "planejado")
    up = client.post(f"/api/schedule/{e2['id']}/gym", json={"id": "ironberg"}).json()
    assert all(i.get("role") != "main" for i in up["items"] if i.get("gym_from"))
