"""Contas: primeira conta fica com os dados existentes, convites, isolamento entre usuários."""
import pytest
from fastapi.testclient import TestClient

from gymgarmin import auth, db, garmin_sync


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    garmin_sync._clients.clear()
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c
    auth.reset_cache()


def _new(app_client):
    from gymgarmin.app import app

    return TestClient(app)


def test_owner_setup_keeps_existing_data_and_then_login_is_required(app_client):
    c = app_client
    # antes de existir conta: funciona como sempre (usuário 1)
    c.put("/api/profile", json={"name": "André", "age": 34})
    c.post("/api/sessions", json={"date": "2026-11-16T07:00", "items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 5, "load_kg": 100}]}]})
    assert c.get("/api/auth/me").json() == {"logged_in": False, "setup_needed": True}
    r = c.post("/api/auth/setup", json={"email": "Dono@x.com", "password": "senha-forte"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] and r.json()["user"]["name"] == "André"
    assert len(c.get("/api/sessions").json()) == 1  # os dados de antes são do dono
    # segunda tentativa de "setup" não rouba o app
    assert c.post("/api/auth/setup", json={"email": "x@y.com", "password": "12345678"}).status_code == 409
    # sem cookie: 401
    anon = _new(c)
    assert anon.get("/api/sessions").status_code == 401
    assert anon.get("/api/auth/me").json()["logged_in"] is False
    # login errado / certo
    assert anon.post("/api/auth/login", json={"email": "dono@x.com", "password": "errada!!"}).status_code == 401
    assert anon.post("/api/auth/login", json={"email": "DONO@x.com", "password": "senha-forte"}).status_code == 200
    assert len(anon.get("/api/sessions").json()) == 1


def test_invited_user_has_separate_data_and_garmin(app_client):
    c = app_client
    c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
    c.post("/api/sessions", json={"date": "2026-11-16T07:00", "items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 5, "load_kg": 100}]}]})
    db.kv_set("garmin_tokens", "cifrado-do-dono", user_id=1)
    inv = c.post("/api/admin/invites", json={"note": "Maria"}).json()
    assert len(c.get("/api/admin/users").json()["invites"]) == 1
    m = _new(c)
    assert m.get(f"/api/auth/invite/{inv['code']}").json() == {"valid": True, "note": "Maria"}
    r = m.post("/api/auth/register", json={"code": inv["code"], "email": "maria@x.com", "password": "outra-senha", "name": "Maria"})
    assert r.status_code == 200 and not r.json()["user"]["is_admin"]
    assert m.get("/api/sessions").json() == []                   # não vê os treinos do dono
    assert m.get("/api/profile").json()["name"] == "Maria"
    assert m.get("/api/garmin/status").json()["has_tokens"] is False  # nem o Garmin dele
    m.put("/api/profile", json={"age": 29})
    assert c.get("/api/profile").json().get("age") != 29
    # convite usado não serve de novo; membro não administra
    assert _new(c).post("/api/auth/register", json={"code": inv["code"], "email": "z@x.com", "password": "12345678"}).status_code == 400
    assert m.post("/api/admin/invites", json={}).status_code == 403
    assert m.get("/api/backup").status_code == 403
    # a sessão da Maria não acessa o treino do dono pelo id
    sid = c.get("/api/sessions").json()[0]["id"]
    assert m.get(f"/api/sessions/{sid}").status_code == 404


def test_password_change_invalidates_old_sessions(app_client):
    c = app_client
    c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
    other = _new(c)
    other.post("/api/auth/login", json={"email": "dono@x.com", "password": "senha-forte"})
    assert c.post("/api/account/password", json={"current": "senha-forte", "new": "nova-senha-1"}).status_code == 200
    assert c.get("/api/profile").status_code == 200        # quem trocou continua dentro
    assert other.get("/api/profile").status_code == 401    # o outro aparelho sai
    assert c.post("/api/auth/register", json={}).status_code == 400
    assert c.post("/api/account/password", json={"current": "x", "new": "nova-senha-2"}).status_code == 400


def test_short_password_rejected(app_client):
    r = app_client.post("/api/auth/setup", json={"email": "dono@x.com", "password": "123"})
    assert r.status_code == 400 and "8" in r.json()["detail"]
