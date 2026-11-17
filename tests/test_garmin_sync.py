"""Sincronização automática com o Garmin, com um cliente falso (sem rede)."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, garmin_sync

STRENGTH_ID, RUN_ID, BIKE_ID = 9001, 9002, 9003


EXTRA: list = []


def _acts():
    return EXTRA + [
        {"activityId": STRENGTH_ID, "activityName": "Treino A", "activityType": {"typeKey": "strength_training"},
         "startTimeLocal": "2026-11-16 19:00:00", "duration": 3000},
        {"activityId": RUN_ID, "activityName": "Corrida na rua", "activityType": {"typeKey": "running"},
         "startTimeLocal": "2026-11-15 07:00:00", "duration": 1800, "distance": 6000, "averageHR": 150,
         "trainingStressScore": 48.0},
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
        return [a for a in _acts() if start <= a["startTimeLocal"][:10] <= end]

    def get_activity(self, aid):
        FakeGarmin.calls.append(("detail", aid, None))
        if FakeGarmin.rate_limit_on == int(aid):
            raise RuntimeError("429 Too Many Requests")
        a = next(x for x in _acts() if x["activityId"] == int(aid))
        return {"activityId": a["activityId"], "activityName": a["activityName"], "activityTypeDTO": a["activityType"],
                "summaryDTO": {"startTimeLocal": a["startTimeLocal"], "duration": a["duration"],
                               "averageHR": a.get("averageHR"), "distance": a.get("distance"),
                               "trainingStressScore": a.get("trainingStressScore")}}

    def get_activity_exercise_sets(self, aid):
        if int(aid) != STRENGTH_ID:
            return {"exerciseSets": []}
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
    EXTRA.clear()
    yield
    clock.freeze(None)
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
                                                  "started": (clock.now() - timedelta(minutes=30)).isoformat()})
    _forget_memory(monkeypatch)
    with pytest.raises(RuntimeError, match="expirou"):
        garmin_sync.submit_mfa("123456")


def test_revoked_token_marks_reconnect_without_password_login(monkeypatch):
    garmin_sync._put_secret(garmin_sync.TOKENS_KEY, {"di_token": "t", "di_refresh_token": "revogado"})
    garmin_sync._set_sync_state(display_name="andre")
    # 1ª recusa: pode ser o Garmin fora do ar (o garminconnect não distingue) → mantém os tokens
    r = garmin_sync.sync(db.save_session, set(), trigger="cron")
    assert r["ok"] is False and r.get("temporary")
    assert garmin_sync.status()["has_tokens"] and not garmin_sync.status()["needs_reconnect"]
    # recusa de novo horas depois → aí sim pede para entrar de novo
    clock.freeze(clock.now() + timedelta(hours=7))
    try:
        r = garmin_sync.sync(db.save_session, set(), trigger="cron")
    finally:
        clock.freeze(None)
    assert r["ok"] is False and r["needs_login"]
    st = garmin_sync.status()
    assert st["needs_reconnect"] and not st["has_tokens"]
    assert all(not c[2] for c in FakeGarmin.calls)  # nunca tentou e-mail/senha


def test_sync_imports_gym_activities_once():
    garmin_sync.login("eu@x.com", "senha")
    r = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), trigger="manual")
    assert r["ok"] and r["skipped"] == 0
    assert sorted(i["name"] for i in r["imported"]) == ["Bike indoor", "Corrida na rua", "Treino A"]
    sessions = db.list_sessions()
    strength = next(s for s in sessions if s.get("garmin_activity_id") == STRENGTH_ID)
    assert [x["load_kg"] for x in strength["items"][0]["sets"]] == [70.0, 72.5]

    run = next(s for s in sessions if s.get("garmin_activity_id") == RUN_ID)["items"][0]
    assert run["sport"] == "corrida" and run["exercise_id"] == "corrida_rua"
    assert run["distance_km"] == 6.0 and run["pace_s_km"] == 300 and run["tss"] == 48.0
    bike = next(s for s in sessions if s.get("garmin_activity_id") == BIKE_ID)["items"][0]
    assert bike["sport"] == "ciclismo" and bike["exercise_id"] == "ciclismo_rolo"

    again = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), trigger="cron")
    assert again["ok"] and again["imported"] == []
    assert len(db.list_sessions()) == 3
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
    garmin_sync._set_sync_state(last_run=clock.now().isoformat())
    assert not garmin_sync.due()
    assert garmin_sync.due(now=clock.now() + timedelta(hours=7))
    garmin_sync._set_sync_state(needs_reconnect=True)
    assert not garmin_sync.due(now=clock.now() + timedelta(days=2))


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
    assert r["ran"] and len(r["imported"]) == 3
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


# ------------------------------------------------- regressões das simulações ---
def test_garmin_down_does_not_drop_login(monkeypatch):
    """Falha passageira (Garmin fora do ar) não apaga os tokens nem pede para reconectar."""
    garmin_sync.login("eu@x.com", "senha")
    garmin_sync._set_sync_state(display_name="andre")
    _forget_memory(monkeypatch)

    def down(self, tokenstore=None):
        raise ConnectionError("503 Service Unavailable")

    monkeypatch.setattr(FakeGarmin, "login", down)
    r = garmin_sync.sync(db.save_session, set(), trigger="cron")
    assert r["ok"] is False and r["temporary"]
    st = garmin_sync.status()
    assert st["has_tokens"] and not st["needs_reconnect"]


def test_token_renewed_mid_run_is_saved_even_if_sync_fails(monkeypatch):
    garmin_sync.login("eu@x.com", "senha")
    orig = FakeGarmin.get_activity

    def renew_then_429(self, aid):
        self.client.tokens = {**self.client.tokens, "di_refresh_token": "novo"}
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(FakeGarmin, "get_activity", renew_then_429)
    assert garmin_sync.sync(db.save_session, set())["ok"] is False
    assert json.loads(garmin_sync._get_secret(garmin_sync.TOKENS_KEY))["di_refresh_token"] == "novo"
    monkeypatch.setattr(FakeGarmin, "get_activity", orig)


def test_late_watch_sync_still_imported_and_unsupported_not_fetched():
    """Relógio que passou dias sem sincronizar: a atividade antiga aparece depois e ainda entra.
    Yoga/tênis nem têm o detalhe baixado, e não são buscados de novo a cada execução."""
    clock.freeze(datetime(2026, 11, 20, 9, 0))
    garmin_sync.login("eu@x.com", "senha")
    assert garmin_sync.sync(db.save_session, db.garmin_imported_ids())["ok"]
    clock.freeze(datetime(2026, 11, 21, 9, 0))
    EXTRA.append({"activityId": 9100, "activityName": "Pedal de domingo", "activityType": {"typeKey": "road_biking"},
                  "startTimeLocal": "2026-11-15 08:00:00", "duration": 5400, "distance": 50000})
    EXTRA.append({"activityId": 9101, "activityName": "Yoga", "activityType": {"typeKey": "yoga"},
                  "startTimeLocal": "2026-11-20 07:00:00", "duration": 3600})
    FakeGarmin.calls = []
    r = garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert [i["name"] for i in r["imported"]] == ["Pedal de domingo"] and r["skipped"] == 1
    assert not any(c[0] == "detail" and str(c[1]) == "9101" for c in FakeGarmin.calls)
    FakeGarmin.calls = []
    garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert not any(c[0] == "detail" for c in FakeGarmin.calls)


def test_strength_without_detected_sets_counts():
    EXTRA.append({"activityId": 9200, "activityName": "Academia", "activityType": {"typeKey": "strength_training"},
                  "startTimeLocal": "2026-11-16 18:00:00", "duration": 3300, "averageHR": 118})
    garmin_sync.login("eu@x.com", "senha")
    garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    s = next(x for x in db.list_sessions() if x.get("garmin_activity_id") == 9200)
    assert s["items"][0]["strength_min"] == 55.0
    from gymgarmin import load

    assert load.session_stress(s, {})["by_sport"]["musculacao"] > 20


def test_workout_logged_in_app_and_on_watch_counts_once(client):
    """Registrou no app e o relógio também gravou: vira uma sessão só, com as medidas do relógio."""
    app_s = client.post("/api/sessions", json={"date": "2026-11-16T20:05", "day_name": "Superior A", "items": [
        {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "sets": [{"reps": 8, "load_kg": 70, "rpe": 8}] * 3}]}).json()
    garmin_sync.login("eu@x.com", "senha")
    from gymgarmin.app import _run_sync

    r = _run_sync("manual")
    assert any(i["merged"] for i in r["imported"] if i["name"] == "Treino A")
    strength = [s for s in db.list_sessions() if any(i.get("sets") for i in s["items"])]
    assert len(strength) == 1 and strength[0]["id"] == app_s["id"]
    assert strength[0]["garmin_activity_id"] == STRENGTH_ID and strength[0]["garmin_summary"]["duration_min"] == 50.0
    assert [x["load_kg"] for x in strength[0]["items"][0]["sets"]] == [70, 70, 70]  # vale o que foi digitado
    # ordem inversa: corrida já veio do relógio e o atleta registra depois no app
    client.post("/api/sessions", json={"date": "2026-11-15T09:00", "items": [
        {"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 30, "feeling": 3}]})
    runs = [s for s in db.list_sessions() if any(i.get("sport") == "corrida" for i in s["items"])]
    assert len(runs) == 1 and runs[0]["items"][0]["distance_km"] == 6.0


def test_session_resent_after_lost_signal_is_saved_once(client):
    body = {"date": "2026-11-16T19:00", "client_id": "abc-123", "items": [
        {"exercise_id": "Barbell_Squat", "sets": [{"reps": 5, "load_kg": 100}]}]}
    a = client.post("/api/sessions", json=body).json()
    b = client.post("/api/sessions", json=body).json()
    assert a["id"] == b["id"] and len(db.list_sessions()) == 1


def test_plan_comes_with_exercise_names(client):
    client.put("/api/profile", json={"training_months": 12, "available_days": [0, 2, 4], "session_minutes": 60})
    plan = client.post("/api/generator/apply", json={}).json()
    p = client.get(f"/api/plans/{plan['id']}").json()
    assert all(it.get("_ex", {}).get("name") for d in p["days"] for it in d["items"])


def test_category_only_detection_maps_to_common_exercise():
    """A detecção automática do relógio costuma trazer só a categoria: vira o exercício mais comum dela."""
    rev = garmin_sync._reverse_map()
    assert rev[("BENCH_PRESS", None)] == "Barbell_Bench_Press_-_Medium_Grip"
    assert rev[("LATERAL_RAISE", None)] == "Side_Lateral_Raise"
    assert rev[("TRICEPS_EXTENSION", None)] == "Triceps_Pushdown"
    assert rev[("DEADLIFT", "BARBELL_DEADLIFT")] == "Barbell_Deadlift"  # não o stiff
    # exercício do plano do usuário ganha a categoria
    assert garmin_sync._reverse_map({"Close-Grip_Barbell_Bench_Press"})[("BENCH_PRESS", None)] == "Close-Grip_Barbell_Bench_Press"
    s = garmin_sync.activity_to_session({"activityId": 1, "startTimeLocal": "2026-11-13 05:00:00"}, {"exerciseSets": [
        {"setType": "ACTIVE", "repetitionCount": 10, "weight": 40000, "exercises": [{"category": "BENCH_PRESS", "name": None}]},
        {"setType": "ACTIVE", "repetitionCount": 9, "weight": 0, "exercises": [{"category": "UNKNOWN", "name": None}]},
    ]})
    bench, unknown = s["items"]
    assert bench["exercise_id"] == "Barbell_Bench_Press_-_Medium_Grip" and bench["guess"]
    assert unknown["exercise_id"] == garmin_sync.UNKNOWN_ID and unknown["_name"] == garmin_sync.UNKNOWN_NAME


def test_remap_fixes_old_imports_once_and_keeps_user_choice():
    old = [{"id": 1, "source": "garmin", "items": [
        {"exercise_id": "Alternating_Floor_Press", "sets": [{"reps": 8, "load_kg": 50}], "garmin": {"category": "BENCH_PRESS", "name": None}},
        {"exercise_id": "Incline_Dumbbell_Press", "sets": [{"reps": 8, "load_kg": 20}], "garmin": {"category": "BENCH_PRESS", "name": None}},
        {"exercise_id": "Stiff-Legged_Barbell_Deadlift", "sets": [{"reps": 8, "load_kg": 80}], "garmin": {"category": "DEADLIFT", "name": "BARBELL_DEADLIFT"}},
    ]}, {"id": 2, "source": "app", "items": [{"exercise_id": "Alternating_Floor_Press", "sets": [{"reps": 8, "load_kg": 50}]}]}]
    changed = garmin_sync.remap_imported(old)
    assert [s["id"] for s in changed] == [1]
    a, b, c = old[0]["items"]
    assert a["exercise_id"] == "Barbell_Bench_Press_-_Medium_Grip" and a["guess"]
    assert b["exercise_id"] == "Incline_Dumbbell_Press"  # o usuário trocou: fica
    assert c["exercise_id"] == "Barbell_Deadlift"
    assert old[1]["items"][0]["exercise_id"] == "Alternating_Floor_Press"  # registrado no app: não mexe
    assert garmin_sync.remap_imported(old) == []


def test_sync_stops_at_deadline_and_saves_state(monkeypatch):
    """Na Vercel a função morre aos 30 s sem gravar nada: com prazo, importa o que der e grava o estado."""
    import time as _t

    garmin_sync.login("eu@x.com", "senha")
    r = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), until=_t.monotonic() - 1)
    assert r["ok"] and len(r["imported"]) == 1 and r["pending"] == 2  # pelo menos uma por vez, para avançar
    assert garmin_sync.status()["sync"]["last_ok"]
    r2 = garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert len(r2["imported"]) == 2 and r2["pending"] == 0


def test_queue_left_by_limit_runs_again_on_open(monkeypatch):
    """Primeira sincronização com mais que o limite por vez: ao abrir o app de novo, continua já
    (não espera as 6 h do intervalo normal)."""
    monkeypatch.setattr(garmin_sync, "MAX_PER_RUN", 2)
    garmin_sync.login("eu@x.com", "senha")
    r = garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert r["pending"] == 1 and garmin_sync.sync_state()["pending"] == 1
    assert not garmin_sync.due()  # logo em seguida, não (evita rajada)
    assert garmin_sync.due(now=clock.now() + timedelta(minutes=2))
    garmin_sync.sync(db.save_session, db.garmin_imported_ids())
    assert garmin_sync.sync_state()["pending"] == 0
    assert not garmin_sync.due(now=clock.now() + timedelta(minutes=2))
