"""Sincronização automática com o Garmin, com um cliente falso (sem rede)."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import db, garmin_sync

STRENGTH_ID, RUN_ID, BIKE_ID = 9001, 9002, 9003


def _acts():
    return [
        {"activityId": STRENGTH_ID, "activityName": "Treino A", "activityType": {"typeKey": "strength_training"},
         "startTimeLocal": "2026-11-16 19:00:00", "duration": 3000},
        {"activityId": RUN_ID, "activityName": "Corrida na rua", "activityType": {"typeKey": "running"},
         "startTimeLocal": "2026-11-15 07:00:00", "duration": 1800},
        {"activityId": BIKE_ID, "activityName": "Bike indoor", "activityType": {"typeKey": "indoor_cycling"},
         "startTimeLocal": "2026-11-14 18:00:00", "duration": 1200, "averageHR": 130},
    ]


class FakeClient:
    def __init__(self):
        self.tokens = None

    def dumps(self):
        return json.dumps(self.tokens)


class FakeGarmin:
    mfa = False
    rate_limit_on = None
    calls: list = []

    def __init__(self, email=None, password=None, return_on_mfa=False, **_):
        self.email, self.password = email, password
        self.client = FakeClient()
        self.display_name = "andre"

    def login(self, tokenstore=None):
        FakeGarmin.calls.append(("login", bool(tokenstore), self.email))
        if tokenstore:
            data = json.loads(tokenstore)
            if data.get("di_refresh_token") == "revogado":
                raise RuntimeError("401 refresh recusado")
            # simula renovação do token durante o login
            self.client.tokens = {**data, "di_token": data["di_token"] + "+"}
            return None, None
        if not self.email:
            raise RuntimeError("Username and password are required")
        if FakeGarmin.mfa:
            return "needs_mfa", {"cookies": "sso"}
        self.client.tokens = {"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}
        return None, None

    def resume_login(self, state, code):
        assert state == {"cookies": "sso"} and code == "123456"
        self.client.tokens = {"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}
        return None, None

    def get_activities_by_date(self, start, end):
        return _acts()

    def get_activity(self, aid):
        if FakeGarmin.rate_limit_on == int(aid):
            raise RuntimeError("429 Too Many Requests")
        a = next(x for x in _acts() if x["activityId"] == int(aid))
        return {"activityId": a["activityId"], "activityName": a["activityName"], "activityTypeDTO": a["activityType"],
                "summaryDTO": {"startTimeLocal": a["startTimeLocal"], "duration": a["duration"],
                               "averageHR": a.get("averageHR")}}

    def get_activity_exercise_sets(self, aid):
        return {"exerciseSets": [
            {"setType": "ACTIVE", "repetitionCount": 8, "weight": 70000, "duration": 40,
             "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS", "probability": 99}]},
            {"setType": "REST"},
            {"setType": "ACTIVE", "repetitionCount": 8, "weight": 72500, "duration": 40,
             "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS", "probability": 99}]},
        ]}


@pytest.fixture(autouse=True)
def fake_garmin(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("GARMIN_TOKEN_KEY", "chave-de-teste")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setattr(garmin_sync, "_lib", lambda: FakeGarmin)
    monkeypatch.setattr(garmin_sync, "_client", None)
    garmin_sync._pending_mfa.clear()
    FakeGarmin.mfa, FakeGarmin.rate_limit_on, FakeGarmin.calls = False, None, []
    yield
    garmin_sync._pending_mfa.clear()


def _forget_memory(monkeypatch):
    """Simula outra instância do servidor (sem nada em memória)."""
    monkeypatch.setattr(garmin_sync, "_client", None)
    garmin_sync._pending_mfa.clear()


def test_tokens_are_encrypted_in_db(monkeypatch):
    garmin_sync.login("eu@x.com", "senha")
    raw = db.kv_get(garmin_sync.TOKENS_KEY)
    assert raw and "di_refresh_token" not in raw and "senha" not in raw
    assert json.loads(garmin_sync._get_secret(garmin_sync.TOKENS_KEY))["di_refresh_token"] == "r"
    # com outra chave não decifra → trata como desconectado
    monkeypatch.setenv("GARMIN_TOKEN_KEY", "outra")
    assert garmin_sync._get_secret(garmin_sync.TOKENS_KEY) is None


def test_vercel_without_key_refuses(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("GARMIN_TOKEN_KEY")
    st = garmin_sync.status()
    assert st["storage_ok"] is False and "GARMIN_TOKEN_KEY" in st["storage_error"]


def test_reconnect_from_db_on_new_instance_and_saves_refreshed_token(monkeypatch):
    garmin_sync.login("eu@x.com", "senha")
    _forget_memory(monkeypatch)
    assert garmin_sync.connect_with_tokens()
    assert ("login", True, None) in FakeGarmin.calls  # entrou só com token, sem e-mail/senha
    assert json.loads(garmin_sync._get_secret(garmin_sync.TOKENS_KEY))["di_token"] == "t+"


def test_mfa_finished_on_another_instance(monkeypatch):
    FakeGarmin.mfa = True
    assert garmin_sync.login("eu@x.com", "senha") == {"mfa_required": True}
    assert "sso" not in (db.kv_get(garmin_sync.MFA_KEY) or "")  # estado do MFA também cifrado
    _forget_memory(monkeypatch)
    assert garmin_sync.status()["mfa_pending"]
    assert garmin_sync.submit_mfa(" 123456 ")["connected"]
    assert db.kv_get(garmin_sync.MFA_KEY) is None
    assert garmin_sync.status()["has_tokens"]


def test_expired_mfa_state_is_rejected(monkeypatch):
    FakeGarmin.mfa = True
    garmin_sync.login("eu@x.com", "senha")
    garmin_sync._put_secret(garmin_sync.MFA_KEY, {"state": {"cookies": "sso"},
                                                  "started": (datetime.now() - timedelta(minutes=30)).isoformat()})
    _forget_memory(monkeypatch)
    with pytest.raises(RuntimeError, match="expirou"):
        garmin_sync.submit_mfa("123456")


def test_revoked_token_marks_reconnect_without_password_login(monkeypatch):
    garmin_sync._put_secret(garmin_sync.TOKENS_KEY, {"di_token": "t", "di_refresh_token": "revogado"})
    garmin_sync._set_sync_state(display_name="andre")
    r = garmin_sync.sync(db.save_session, set(), trigger="cron")
    assert r["ok"] is False and r["needs_login"]
    st = garmin_sync.status()
    assert st["needs_reconnect"] and not st["has_tokens"]
    assert all(not c[2] for c in FakeGarmin.calls)  # nunca tentou e-mail/senha


def test_sync_imports_gym_activities_once():
    garmin_sync.login("eu@x.com", "senha")
    r = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), trigger="manual")
    assert r["ok"] and r["skipped"] == 1  # corrida na rua fica de fora
    assert sorted(i["name"] for i in r["imported"]) == ["Bike indoor", "Treino A"]
    sessions = db.list_sessions()
    strength = next(s for s in sessions if s.get("garmin_activity_id") == STRENGTH_ID)
    assert [x["load_kg"] for x in strength["items"][0]["sets"]] == [70.0, 72.5]

    again = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), trigger="cron")
    assert again["ok"] and again["imported"] == []
    assert len(db.list_sessions()) == 2
    st = garmin_sync.status()["sync"]
    assert st["last_ok"] and st["trigger"] == "cron"


def test_rate_limit_is_reported_and_retried_next_time():
    garmin_sync.login("eu@x.com", "senha")
    FakeGarmin.rate_limit_on = STRENGTH_ID
    r = garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert r["ok"] is False and "limitou" in r["error"]
    FakeGarmin.rate_limit_on = None
    r2 = garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert r2["ok"] and any(i["name"] == "Treino A" for i in r2["imported"])


def test_due_logic():
    assert garmin_sync.due()
    garmin_sync._set_sync_state(last_run=datetime.now().isoformat())
    assert not garmin_sync.due()
    assert garmin_sync.due(now=datetime.now() + timedelta(hours=7))
    garmin_sync._set_sync_state(needs_reconnect=True)
    assert not garmin_sync.due(now=datetime.now() + timedelta(days=2))


@pytest.fixture()
def client():
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c


def test_api_sync_auto_and_cron(client, monkeypatch):
    # sem login: auto não roda, cron responde sem erro
    assert client.post("/api/garmin/sync?auto=true").json()["ran"] is False
    monkeypatch.setenv("CRON_SECRET", "s3")
    assert client.get("/api/cron/garmin-sync").status_code == 401
    assert client.get("/api/cron/garmin-sync", headers={"Authorization": "Bearer errado"}).status_code == 401
    assert client.get("/api/cron/garmin-sync", headers={"Authorization": "Bearer s3"}).json()["ran"] is False
    assert client.get("/api/garmin/status").json()["sync"]["last_cron"]

    assert client.post("/api/garmin/login", json={"email": "eu@x.com", "password": "senha"}).json()["connected"]
    r = client.get("/api/cron/garmin-sync", headers={"Authorization": "Bearer s3"}).json()
    assert r["ran"] and len(r["imported"]) == 2
    # logo depois, o automático ao abrir o app não roda de novo
    assert client.post("/api/garmin/sync?auto=true").json()["ran"] is False
    assert client.post("/api/garmin/sync").json()["ran"] is True
    st = client.get("/api/garmin/status").json()
    assert st["connected"] and st["sync"]["last_ok"]
    # o histórico de cargas já enxerga o supino importado
    hist = client.get("/api/history").json()
    assert any(e["summary"]["best"]["top_load"] == 72.5 for e in hist["exercises"])
    # backup não leva os tokens
    assert "kv" not in client.get("/api/backup").json()
