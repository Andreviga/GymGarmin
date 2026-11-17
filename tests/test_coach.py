"""Área do coach: consentimento, agir como atleta, triagem com alertas, áreas de tensão e check-in."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import auth, clock, coach, db, garmin_sync


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    garmin_sync._clients.clear()
    clock.freeze(datetime(2026, 11, 18, 8, 0))
    from gymgarmin.app import app

    coach_c = TestClient(app)
    coach_c.post("/api/auth/setup", json={"email": "coach@x.com", "password": "senha-coach"})
    coach_c.put("/api/profile", json={"name": "André"})
    inv = coach_c.post("/api/admin/invites", json={"note": "Nat"}).json()
    ath = TestClient(app)
    ath.post("/api/auth/register", json={"code": inv["code"], "email": "nat@x.com", "password": "senha-nat", "name": "Nat"})
    aid = ath.get("/api/auth/me").json()["user"]["id"]
    yield coach_c, ath, aid
    clock.freeze(None)
    auth.reset_cache()


def test_consent_required_and_revocable(env):
    c, a, aid = env
    assert c.get("/api/auth/me").json()["user"]["is_coach"]  # o admin é coach
    assert c.get("/api/coach/athletes").json()["athletes"] == []
    assert c.get("/api/profile", headers={"X-GG-Athlete": str(aid)}).status_code == 403  # sem autorização
    mine = a.get("/api/coach/mine").json()
    assert [x["name"] for x in mine["coaches"]] == ["André"]
    a.post(f"/api/coach/mine/{mine['coaches'][0]['id']}")
    assert [x["name"] for x in c.get("/api/coach/athletes").json()["athletes"]] == ["Nat"]
    assert c.get("/api/profile", headers={"X-GG-Athlete": str(aid)}).json()["name"] == "Nat"
    # coach nunca mexe em login, código pessoal ou Garmin do atleta
    assert c.post("/api/account/ingest-token", headers={"X-GG-Athlete": str(aid)}).json() is not None
    assert db.get_account(aid)["ingest_token"] is None  # o pedido rodou como o próprio coach, não como a atleta
    a.delete(f"/api/coach/mine/{mine['coaches'][0]['id']}")
    assert c.get("/api/profile", headers={"X-GG-Athlete": str(aid)}).status_code == 403
    # atleta comum não abre a área do coach
    assert a.get("/api/coach/athletes").status_code == 403


def test_triage_flags_from_checkin_sleep_and_missed(env):
    c, a, aid = env
    a.post("/api/coach/mine/1")
    with db.as_user(aid):
        for i in range(10):
            d = (date(2026, 11, 18) - timedelta(days=i)).isoformat()
            db.kv_set(f"health:{d}", '{"date": "%s", "sleep": {"duration_h": %s}, "rhr": %d}' % (d, 5.4 if i < 7 else 7.5, 60 if i < 3 else 50))
            if i:
                db.kv_set(f"checkin:{d}", '{"date": "%s", "fatigue": 2, "soreness": 2, "stress": 2, "mood": 2, "sleep_q": 2, "pain": {}}' % d)
    r = a.post("/api/checkin", json={"fatigue": 5, "soreness": 4, "stress": 3, "mood": 3, "sleep_q": 4, "pain": {"joelho": 7}}).json()
    assert r["suggestions"][0]["region"] == "joelho" and r["suggestions"][0]["severity"] == "cuidado"
    ath = c.get("/api/coach/athletes").json()["athletes"][0]
    codes = {f["code"]: f["level"] for f in ath["flags"]}
    assert codes["sono"] == "agir" and codes["bem_estar"] == "agir" and codes["dor_joelho"] == "agir"
    assert codes.get("fc_repouso") == "agir"  # +10 bpm nos últimos 3 dias
    assert ath["priority"] >= 9 and ath["suggestions"][0]["region"] == "joelho"
    # silenciar um alerta
    c.post(f"/api/coach/athletes/{aid}/mute", json={"code": "sono"})
    assert "sono" not in {f["code"] for f in c.get("/api/coach/athletes").json()["athletes"][0]["flags"]}
    # atleta deixa de compartilhar saúde: some sono/check-in, fica o treino
    a.post("/api/coach/mine/1/share", json={"health": False})
    ath2 = c.get("/api/coach/athletes").json()["athletes"][0]
    assert not {"bem_estar", "dor_joelho", "fc_repouso"} & {f["code"] for f in ath2["flags"]}
    assert c.get("/api/health", headers={"X-GG-Athlete": str(aid)}).status_code == 403


def test_tension_area_swaps_exercises_and_athlete_sees_changes(env):
    c, a, aid = env
    a.post("/api/coach/mine/1")
    a.post("/api/generator/apply", json={"profile": {"training_months": 24, "goal": "hipertrofia", "session_minutes": 60,
                                                     "available_days": [0, 2, 4]}})
    plan = a.get("/api/plans").json()[0]
    before = [i["exercise_id"] for d in plan["days"] for i in d["items"] if i.get("phase") == "principal"]
    r = c.post(f"/api/coach/athletes/{aid}/tension", json={"region": "lombar", "severity": "evitar"}).json()
    assert {"key": "lombar", "severity": "evitar"} in r["limitations"]
    after = [i["exercise_id"] for d in a.get(f"/api/plans/{plan['id']}").json()["days"] for i in d["items"] if i.get("phase") == "principal"]
    from gymgarmin import catalog, limitations

    bad = [e for e in after if limitations.check_exercise(catalog.get(e), r["limitations"])["status"] == "evitar"]
    assert not bad and (r["changes"] or before == after)
    inbox = a.get("/api/coach/inbox").json()
    assert any("perfil" in x["text"] for x in inbox["changes"])
    c.post(f"/api/coach/athletes/{aid}/note", json={"text": "Bora! Hoje só técnica."})
    inbox = a.get("/api/coach/inbox").json()
    assert inbox["unread"] == 1 and inbox["notes"][0]["text"] == "Bora! Hoje só técnica."
    a.post("/api/coach/inbox/read")
    assert a.get("/api/coach/inbox").json()["unread"] == 0


def test_quick_adjust_and_settings(env):
    c, a, aid = env
    a.post("/api/coach/mine/1")
    a.post("/api/generator/apply", json={"profile": {"training_months": 24, "goal": "hipertrofia", "session_minutes": 60,
                                                     "available_days": [0, 1, 2, 3, 4, 5, 6]}})
    r = c.post(f"/api/coach/athletes/{aid}/adjust", json={"scope": "semana", "action": "moderar"}).json()
    assert r["changed"]
    s = c.put("/api/coach/settings", json={"sleep_warn": 7.5, "xx": 1}).json()["settings"]
    assert s["sleep_warn"] == 7.5 and "xx" not in s


def test_strain_and_wellness_z_against_own_baseline():
    # semanas normais: alterna leve/forte; a última: pesada todo dia (monótona)
    series = [{"tss": x} for _ in range(4) for x in (80, 0, 60, 20, 80, 0, 100)] + [{"tss": x} for x in (100, 100, 100, 100, 100, 100, 90)]
    n = coach.load_numbers(series)
    assert n["load_change"] > 25 and n["strain_high"] is True
    cks = [{"fatigue": 5, "soreness": 5, "stress": 4, "mood": 4, "sleep_q": 4}] + [
        {"fatigue": 2, "soreness": 2, "stress": 2, "mood": 2, "sleep_q": 2 + (i % 2)} for i in range(10)]
    assert coach.wellness_z(cks) >= 2
