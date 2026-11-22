"""Área social: compartilhar o treino, quem vê, reações, comentários, apagar e ocultar."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, partners, social

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "s.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))
    db.init()
    with db.conn() as c:
        for uid, name in ((2, "Nathália"), (3, "Carlos")):
            c.execute("INSERT INTO users (id, name, profile, created_at) VALUES (?,?,?,?)", (uid, name, "{}", db.now()))
    db.save_profile({"name": "André", "weight_kg": 80}, 1)
    monkeypatch.setattr(partners, "_names", lambda: {1: "André", 2: "Nathália", 3: "Carlos"})
    s = db.save_session({"date": "2026-11-16T08:00", "day_name": "Superior A", "items": [
        {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "sets": [{"reps": 8, "load_kg": 80, "done": True}] * 3},
        {"exercise_id": "cardio_esteira_caminhada", "duration_min": 10, "zone": 2}]}, user_id=1)
    yield s
    clock.freeze(None)


def test_quem_ve_e_interacoes(env):
    sid = env["id"]
    p = social.create(1, sid, "Peito feito 💪", "parceiros", PNG, show_loads=True)
    w = p["workout"]
    assert w["title"] == "Superior A" and w["sets"] == 3 and p["photo_url"]
    assert w["items"][0]["best"] == "8 × 80 kg"
    # parceiros: Nathália só vê depois de virar parceira; Carlos nunca
    assert not social.feed(2) and not social.feed(3)
    partners.invite(1, 2)
    partners.accept(2, 1)
    assert [x["id"] for x in social.feed(2)] == [p["id"]] and not social.feed(3)
    with pytest.raises(LookupError):
        social.react(3, p["id"], "💪")
    r = social.react(2, p["id"], "🔥")
    assert r["reacts"]["🔥"] == 1 and r["my_react"] == "🔥"
    assert social.react(2, p["id"], None)["reacts"]["🔥"] == 0
    c = social.comment(2, p["id"], "Boa!")
    assert c["comments"][0]["author"] == "Nathália"
    with pytest.raises(ValueError):
        social.comment(2, p["id"], "   ")
    # dono do post apaga comentário de outro; terceiro não
    social.delete_comment(1, c["comments"][0]["id"])
    assert not social.get(1, p["id"])["comments"]
    # sem cargas: não aparecem
    p2 = social.create(1, sid, "", "todos")
    assert "best" not in p2["workout"]["items"][0] and [x["id"] for x in social.feed(3)] == [p2["id"]]
    social.hide(3, p2["id"])
    assert not social.feed(3)
    with pytest.raises(LookupError):
        social.delete(2, p2["id"])
    social.delete(1, p2["id"])
    assert len(social.feed(1)) == 1
    eu = social.create(1, sid, "", "eu")
    assert eu["id"] in [x["id"] for x in social.feed(1)] and eu["id"] not in [x["id"] for x in social.feed(2)]
    with pytest.raises(LookupError):
        social.create(2, sid)            # o treino é de outra pessoa


def test_api(env):
    from gymgarmin.app import app
    with TestClient(app) as c:
        r = c.post("/api/social", json={"session_id": env["id"], "caption": "x", "visibility": "todos"})
        assert r.status_code == 200
        pid = r.json()["id"]
        f = c.get("/api/social").json()
        assert f["posts"][0]["id"] == pid and env["id"] in f["shared"]
        assert c.post(f"/api/social/{pid}/react", json={"kind": "💪"}).json()["reacts"]["💪"] == 1
        assert c.post(f"/api/social/{pid}/comments", json={"text": "boa"}).json()["comments"]
        assert c.post("/api/social", json={"session_id": 99999}).status_code == 404
        assert c.delete(f"/api/social/{pid}").json()["ok"]
        assert c.get(f"/api/social/{pid}").status_code == 404
