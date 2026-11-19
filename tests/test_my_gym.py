"""Minha academia: foto e regulagem do aparelho, troca por exercício equivalente e área do desenvolvedor."""
import base64

import pytest
from fastapi.testclient import TestClient

from gymgarmin import auth, garmin_sync, my_gym

# JPEG mínimo válido o bastante para o teste (o servidor só confere tipo, base64 e tamanho)
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 2000 + b"\xff\xd9").decode()


@pytest.fixture()
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    garmin_sync._clients.clear()
    my_gym._CACHE["custom"] = None
    from gymgarmin.app import app

    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
        yield c
    auth.reset_cache()
    my_gym._CACHE["custom"] = None


def _member(owner):
    from gymgarmin.app import app

    inv = owner.post("/api/admin/invites", json={"note": "Maria"}).json()
    m = TestClient(app)
    m.post("/api/auth/register", json={"code": inv["code"], "email": "maria@x.com", "password": "outra-senha", "name": "Maria"})
    return m


def test_my_machine_photo_settings_and_prefill(owner):
    r = owner.post("/api/my-gym/machines", json={"image": JPEG, "exercise_id": "Leg_Press", "name": "Leg press 45° da Smart",
                                                "settings": "encosto 3, pés no meio", "suggest": False})
    assert r.status_code == 200, r.text
    mine = owner.get("/api/my-gym/machines").json()
    assert len(mine) == 1 and mine[0]["settings"] == "encosto 3, pés no meio" and mine[0]["photo_url"]
    img = owner.get(mine[0]["photo_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert owner.get("/api/exercises/Leg_Press").json()["my_machine"]["name"] == "Leg press 45° da Smart"
    item = owner.post("/api/sessions/item", json={"exercise_id": "Leg_Press", "sets": 3, "reps": "8-12", "rest_s": 90}).json()
    assert item["my_machine"]["settings"] == "encosto 3, pés no meio" and len(item["sets"]) == 3
    owner.patch(f"/api/my-gym/machines/{mine[0]['id']}", json={"settings": "encosto 4"})
    assert owner.get("/api/my-gym/machines").json()[0]["settings"] == "encosto 4"
    # foto de outra pessoa não aparece para quem não é dono nem admin
    m = _member(owner)
    assert m.get(mine[0]["photo_url"]).status_code == 404
    assert owner.delete(f"/api/my-gym/machines/{mine[0]['id']}").status_code == 200
    assert owner.get("/api/my-gym/machines").json() == []
    assert owner.get(mine[0]["photo_url"]).status_code == 404  # foto apagada junto


def test_bad_photo_rejected(owner):
    r = owner.post("/api/my-gym/machines", json={"image": "data:text/html;base64,PGgxPg==", "exercise_id": "Leg_Press"})
    assert r.status_code == 400


def test_alternatives_same_movement_with_my_machines_first(owner):
    alts = [a["id"] for a in owner.get("/api/exercises/Leg_Press/alternatives").json()]
    assert "Hack_Squat" in alts and "Leg_Press" not in alts
    assert not any(a in alts for a in ("Barbell_Curl", "Lying_Leg_Curls"))
    owner.post("/api/my-gym/machines", json={"exercise_id": "Smith_Machine_Squat", "name": "Smith da academia"})
    alts = owner.get("/api/exercises/Leg_Press/alternatives").json()
    assert alts[0]["id"] == "Smith_Machine_Squat" and alts[0]["my_machine"]


def test_swap_in_plan_and_upcoming_agenda(owner):
    plan = owner.post("/api/plans", json={"name": "P", "days": [{"name": "A", "items": [
        {"exercise_id": "Leg_Press", "sets": 3, "reps": "10", "rest_s": 90}]}]}).json()
    r = owner.post(f"/api/plans/{plan['id']}/swap", json={"from": "Leg_Press", "to": "Hack_Squat"}).json()
    assert r["changed"] == 1
    assert owner.get(f"/api/plans/{plan['id']}").json()["days"][0]["items"][0]["exercise_id"] == "Hack_Squat"


def test_dev_queue_approve_into_gallery_and_member_blocked(owner):
    m = _member(owner)
    r = m.post("/api/my-gym/machines", json={"image": JPEG, "exercise_id": "Leg_Press", "name": "Leg horizontal",
                                            "settings": "banco 2", "suggest": True})
    assert r.status_code == 200
    assert m.get("/api/dev/queue").status_code == 403
    q = owner.get("/api/dev/queue").json()
    assert len(q["pending"]) == 1 and q["pending"][0]["user_name"] == "Maria"
    photo = q["pending"][0]["photo_url"]
    assert owner.get(photo).status_code == 200            # admin vê a foto da fila
    qid = q["pending"][0]["qid"]
    a = owner.post(f"/api/dev/queue/{qid}/approve", json={"name": "Leg press horizontal", "category": "pernas",
                                                          "exercise_ids": ["Leg_Press"], "what": "Empurre a plataforma."})
    assert a.status_code == 200, a.text
    mid = a.json()["machine_id"]
    d = owner.get(f"/api/machines/{mid}").json()
    assert d["photos"][0]["url"] == photo and d["community"]
    assert m.get(photo).status_code == 200                # aprovada: pública para todos
    assert any(x["id"] == mid for x in m.get("/api/machines").json()["items"])
    assert owner.get("/api/dev/queue").json()["pending"] == []
    assert owner.delete(f"/api/dev/machines/{mid}").status_code == 200
    my_gym._CACHE["custom"] = None
    assert owner.get(f"/api/machines/{mid}").status_code == 404


def test_dev_merge_photo_into_existing_machine(owner):
    from gymgarmin import gallery

    base = gallery._file_load()[0]["machine_id"]
    r = owner.post("/api/dev/machines", json={"image": JPEG, "name": "x", "exercise_ids": [], "merge_into": base})
    assert r.status_code == 200, r.text
    my_gym._CACHE["custom"] = None
    d = owner.get(f"/api/machines/{base}").json()
    assert d["photos"][0]["url"].startswith("/api/photos/")


def test_swap_survives_regenerating_the_plan(owner):
    prof = {"training_months": 36, "goal": "hipertrofia", "session_minutes": 75, "available_days": [0, 2, 4], "age": 34, "sex": "M"}
    plan = owner.post("/api/generator/apply", json={"profile": prof}).json()
    ids = [i["exercise_id"] for d in plan["days"] for i in d["items"] if i.get("phase") == "principal"]
    src = ids[0]
    alt = owner.get(f"/api/exercises/{src}/alternatives").json()
    dst = next(a["id"] for a in alt if a["id"] not in ids)
    assert owner.post(f"/api/plans/{plan['id']}/swap", json={"from": src, "to": dst}).json()["changed"] >= 1
    new = owner.post("/api/generator/apply", json={"profile": prof}).json()
    new_ids = [i["exercise_id"] for d in new["days"] for i in d["items"]]
    assert src not in new_ids and dst in new_ids
