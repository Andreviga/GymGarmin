"""Aparelho ocupado no meio do treino: as trocas têm de fazer sentido com o treino em andamento."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import catalog, clock, db, gyms

LAT = "Machine_Lat_Pulldown"
BENCH = "Barbell_Bench_Press_-_Medium_Grip"
INCL = "Barbell_Incline_Bench_Press_-_Medium_Grip"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "s.sqlite3"))
    clock.freeze(datetime(2026, 11, 24, 7, 0))
    db.init()
    db.save_profile({"gym": {"id": "nation_ct"}, "gym_type": "premium", "weight_kg": 80})
    from gymgarmin.app import app
    with TestClient(app) as c:
        yield c
    clock.freeze(None)


def _opts(c, eid, session=(), done=(), later=(), **kw):
    r = c.post("/api/swap-options", json={"exercise_id": eid, "session": list(session), "done": list(done), "later": list(later), **kw})
    assert r.status_code == 200
    return r.json()


def test_nada_que_ja_esta_no_treino(client):
    sess = ["Seated_Cable_Rows", "Wide-Grip_Lat_Pulldown", "Dumbbell_Bicep_Curl"]
    ids = [x["id"] for x in _opts(client, LAT, sess, done=["Wide-Grip_Lat_Pulldown"])["alternatives"]]
    assert ids and not set(ids) & set(sess)


def test_mesma_estacao_ocupada_nao_aparece(client):
    # puxada no pulley ocupada: nenhuma outra puxada do MESMO pulley serve agora
    ids = [x["id"] for x in _opts(client, "Wide-Grip_Lat_Pulldown")["alternatives"]]
    assert ids
    assert not [i for i in ids if gyms.station_of(i) == "pulley_alto"]
    # o graviton/barra fixa/máquina convergente são outros aparelhos: servem
    assert set(ids) & {LAT, "Pullups", "Assisted_Pull-Up_Machine"}
    # polia é "macia": a academia tem várias, aparece no fim com o aviso
    tri = _opts(client, "Triceps_Pushdown")["alternatives"]
    poly = [x for x in tri if gyms.station_of(x["id"]) == "polia"]
    assert all(x["note"] == "em outra polia livre" for x in poly)
    assert tri[0]["id"] not in {x["id"] for x in poly}


def test_sem_movimento_sem_relacao_nem_nome_obscuro(client):
    alts = _opts(client, BENCH)["alternatives"]
    ids = [x["id"] for x in alts]
    for bad in ("Straight-Arm_Dumbbell_Pullover", "Front_Raise_And_Pullover", "Barbell_Guillotine_Bench_Press", "Bench_Press_with_Chains"):
        assert bad not in ids
    assert all(catalog.get(i)["name"] != catalog.get(i)["name_en"] or i in gyms._curated() for i in ids)
    # mesmo movimento antes de "mesmo músculo, outro movimento" (inclinado/crucifixo)
    fits = [x["fit"] for x in alts]
    assert fits == sorted(fits, key=lambda f: f != "mesmo")
    assert {x["fit"] for x in alts if gyms.motion(x["id"]) == "incline_push"} <= {"outro"}
    lat = [x["id"] for x in _opts(client, "Side_Lateral_Raise")["alternatives"]]
    assert not [i for i in lat if gyms.motion(i) in ("rear_delt", "rotator")]     # deltoide posterior/rotador não é lateral
    leg = [x["id"] for x in _opts(client, "Leg_Press")["alternatives"]]
    assert "Freehand_Jump_Squat" not in leg                                          # salto no lugar de leg press: não


def test_parecido_com_o_que_ja_fez_vai_para_o_fim(client):
    alts = _opts(client, INCL, [BENCH, INCL], done=[BENCH])["alternatives"]
    noted = [k for k, x in enumerate(alts) if x["note"] and "já fez" in x["note"]]
    clean = [k for k, x in enumerate(alts) if not x["note"]]
    assert noted and clean and max(clean) < min(noted)


def test_adiantar_o_proximo_sem_ser_no_mesmo_aparelho(client):
    r = _opts(client, "Wide-Grip_Lat_Pulldown", ["V-Bar_Pulldown", "Seated_Cable_Rows", "Dumbbell_Bicep_Curl"],
              later=["V-Bar_Pulldown", "Seated_Cable_Rows", "Dumbbell_Bicep_Curl"])
    adv = [x["id"] for x in r["advance"]]
    assert "V-Bar_Pulldown" not in adv                   # mesmo pulley: também está ocupado
    assert adv[:2] == ["Seated_Cable_Rows", "Dumbbell_Bicep_Curl"]


def test_academia_do_dia_vale_na_troca(client):
    # treino de hoje em casa (só halteres): as trocas seguem o que tem em casa, não a academia de sempre
    e = db.save_entry({"date": "2026-11-24", "kind": "forca", "status": "planejado", "name": "Superior",
                       "items": [{"exercise_id": BENCH, "sets": 3, "reps": "8"}],
                       "gym_day": {"id": "casa", "name": "Em casa", "home_kit": ["halteres"]}})
    ids = [x["id"] for x in _opts(client, BENCH, schedule_id=e["id"])["alternatives"]]
    assert ids
    assert all(catalog.get(i)["equipment"] in ("dumbbell", "body only", None) for i in ids)
    # sem a academia do dia: aparelhos da academia de sempre aparecem
    ids2 = [x["id"] for x in _opts(client, BENCH)["alternatives"]]
    assert {"Smith_Machine_Bench_Press", "Machine_Bench_Press", "Leverage_Chest_Press"} & set(ids2)


def test_nao_tem_na_academia_tambem_filtra(client):
    r = client.post("/api/gym/missing", json={"exercise_id": BENCH, "apply": False}).json()
    assert "Straight-Arm_Dumbbell_Pullover" not in [o["id"] for o in r["options"]]
