"""Treinar com o companheiro: convite + aceite, puxar o treino do outro, juntar com o relógio que só marca tempo, placar."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import auth, clock, db, garmin_sync, partners


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    garmin_sync._clients.clear()
    clock.freeze(datetime(2026, 11, 20, 21, 0))  # sexta à noite
    from gymgarmin.app import app

    a = TestClient(app)
    a.post("/api/auth/setup", json={"email": "andre@x.com", "password": "senha-andre"})
    a.put("/api/profile", json={"name": "Andre", "weight_kg": 82})
    people = []
    for name, mail in (("Nathália Moreira", "nat@x.com"), ("Bia", "bia@x.com")):
        inv = a.post("/api/admin/invites", json={"note": name}).json()
        c = TestClient(app)
        c.post("/api/auth/register", json={"code": inv["code"], "email": mail, "password": "senha-123456", "name": name})
        people.append((c, c.get("/api/auth/me").json()["user"]["id"]))
    aid = a.get("/api/auth/me").json()["user"]["id"]
    yield a, aid, people[0][0], people[0][1], people[1][0], people[1][1]
    clock.freeze(None)
    auth.reset_cache()


def _link(a, n, nid):
    a.post(f"/api/partners/{nid}/invite")
    aid = a.get("/api/auth/me").json()["user"]["id"]
    n.post(f"/api/partners/{aid}/accept")


ANDRE_WORKOUT = {"date": "2026-11-20T12:30", "day_name": "Treino livre", "client_id": "a-1", "duration_min": 65,
                 "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 15, "load_kg": 40}, {"reps": 10, "load_kg": 68}, {"reps": 10, "load_kg": 75}]},
                           {"exercise_id": "Seated_Leg_Curl", "sets": [{"reps": 14, "load_kg": 54}, {"reps": 10, "load_kg": 68}]},
                           {"exercise_id": "Hip_Thrust_Machine", "sets": [{"reps": 10, "load_kg": 80}, {"reps": 10, "load_kg": 110}]},
                           {"exercise_id": "cardio_esteira_corrida", "duration_min": 12, "zone": 2}]}


def test_invite_needs_acceptance_and_shows_only_training(env):
    a, aid, n, nid, b, bid = env
    assert [p["first"] for p in a.get("/api/partners").json()["people"]] == ["Bia", "Nathália"]
    assert a.get(f"/api/partners/{nid}/day").status_code == 403                     # sem parceria não vê nada
    r = a.post(f"/api/partners/{nid}/invite").json()
    assert r["status"] == "convidado" and r["partners"] == []
    assert a.get(f"/api/partners/{nid}/day").status_code == 403                     # convite pendente ainda não abre
    req = n.get("/api/partners").json()["requests"]
    assert [x["first"] for x in req] == ["Andre"]
    assert n.post(f"/api/partners/{bid}/accept").status_code == 400                 # não dá para aceitar convite que não existe
    r = n.post(f"/api/partners/{aid}/accept").json()
    assert [p["id"] for p in r["partners"]] == [aid] and r["requests"] == []
    assert partners.is_partner(aid, nid) and partners.is_partner(nid, aid) and not partners.is_partner(aid, bid)
    assert b.get(f"/api/partners/{aid}/day").status_code == 403 and b.get(f"/api/partners/{aid}/duel").status_code == 403
    # convite cruzado vira parceria na hora
    b.post(f"/api/partners/{aid}/invite")
    assert a.post(f"/api/partners/{bid}/invite").json()["status"] == "parceiro"
    # desfazer vale para os dois lados
    a.delete(f"/api/partners/{bid}")
    assert not partners.is_partner(bid, aid) and b.get(f"/api/partners/{aid}/day").status_code == 403
    # o dia do parceiro traz treino, nunca saúde
    db.kv_set("health:2026-11-20", '{"steps": 9000, "sleep": {"duration_h": 5}}', user_id=aid)
    a.post("/api/sessions", json=ANDRE_WORKOUT)
    day = n.get(f"/api/partners/{aid}/day").json()
    txt = str(day)
    assert day["done"][0]["has_sets"] and day["done"][0]["sets"] == 7 and "steps" not in txt and "sleep" not in txt and "weight" not in txt


def test_pull_partner_workout_into_time_only_watch_session(env):
    """A Nathalia treinou com o relógio que só marca o tempo; o André registrou as séries. Ela puxa o treino dele."""
    a, aid, n, nid, _b, _bid = env
    _link(a, n, nid)
    saved = a.post("/api/sessions", json=ANDRE_WORKOUT).json()
    # o relógio dela entregou "outro exercício": 62 min, 310 kcal
    w = db.save_session({"date": "2026-11-20T12:35:00", "source": "android", "ext_id": "android:z1", "notes": "Treino de força",
                         "duration_min": 62, "garmin_summary": {"duration_min": 62},
                         "items": [{"exercise_id": "atividade_outra", "duration_min": 62, "zone": 1, "avg_hr": 104, "max_hr": 131, "kcal": 310}]}, user_id=nid)
    db.save_session({"date": "2026-11-12T09:00", "client_id": "n-old", "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 25}] * 3}]}, user_id=nid)

    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": saved["id"]}).json()
    assert d["with_partner"] == aid and d["pulled"] and d["day_name"].endswith("(com Andre)") and d["date"].startswith("2026-11-20")
    assert [i["exercise_id"] for i in d["items"]] == ["Leg_Extensions", "Seated_Leg_Curl", "Hip_Thrust_Machine"]   # só a musculação
    ext = d["items"][0]
    assert [s["reps"] for s in ext["sets"]] == [15, 10, 10] and all(s["done"] for s in ext["sets"])
    assert [s["partner_load"] for s in ext["sets"]] == [40, 68, 75]                  # a carga dele aparece só como referência
    assert [s["load_kg"] for s in ext["sets"]] == [25, 25, 25]                      # a última carga que EU usei; nunca a dele nem a meta do próximo treino
    assert all(s["load_kg"] is None for s in d["items"][2]["sets"])                 # sem histórico meu: em branco
    assert d["merge_candidates"] == [{"id": w["id"], "time": "12:35", "minutes": 62, "kcal": 310, "title": "Treino de força", "match": True}]

    payload = {"date": d["date"], "day_name": d["day_name"], "client_id": "n-pull", "with_partner": aid, "merge_into": w["id"],
               "items": [{"exercise_id": i["exercise_id"], "sets": [{"reps": s["reps"], "load_kg": s["load_kg"] or 20} for s in i["sets"]]} for i in d["items"]]}
    r = n.post("/api/sessions", json=payload).json()
    assert r["id"] == w["id"] and r["merged"] and r["ext_id"] == "android:z1" and r["with_partner"] == aid
    assert r["date"].startswith("2026-11-20T12:35") and r["duration_min"] == 62
    assert not any(str(i["exercise_id"]).startswith("atividade_") for i in r["items"])     # os 62 min genéricos saíram: não conta em dobro
    assert r["garmin_summary"]["kcal"] == 310 and r["garmin_summary"]["avg_hr"] == 104
    mine = [s for s in db.list_sessions(nid, since="2026-11-20") if s["date"].startswith("2026-11-20")]
    assert len(mine) == 1
    # puxar de novo avisa que já existe
    again = n.post(f"/api/partners/{aid}/draft", json={"session_id": saved["id"]}).json()
    assert [x["id"] for x in again["already"]] == [w["id"]] and again["merge_candidates"] == []
    # with_partner de quem não é parceiro é ignorado; merge_into de sessão que não é só-tempo também
    x = n.post("/api/sessions", json={"date": "2026-11-19T10:00", "client_id": "n-x", "with_partner": 999, "merge_into": w["id"],
                                      "items": [{"exercise_id": "Leg_Press", "sets": [{"reps": 10, "load_kg": 50}]}]}).json()
    assert "with_partner" not in x and x["id"] != w["id"]


def test_train_together_uses_partner_plan_with_my_loads(env):
    a, aid, n, nid, _b, _bid = env
    _link(a, n, nid)
    e = db.save_entry({"date": "2026-11-20", "kind": "forca", "status": "planejado", "day_name": "Pernas (variação)", "plan_id": None,
                       "items": [{"exercise_id": "Leg_Press", "sets": 4, "reps": "8-12", "rest_s": 120, "load_kg": 180, "phase": "principal"},
                                 {"exercise_id": "Leg_Extensions", "sets": 3, "reps": "10-15", "rest_s": 75, "phase": "principal"},
                                 {"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 30, "phase": "principal"}]}, user_id=aid)
    day = n.get(f"/api/partners/{aid}/day").json()
    assert day["planned"][0]["title"] == "Pernas (variação)" and day["planned"][0]["has_sets"]
    d = n.post(f"/api/partners/{aid}/draft", json={"entry_id": e["id"]}).json()
    assert [i["exercise_id"] for i in d["items"]] == ["Leg_Press", "Leg_Extensions"] and d["with_partner"] == aid and not d.get("pulled")
    press = d["items"][0]
    assert len(press["sets"]) == 4 and not any(s["done"] for s in press["sets"])
    assert all((s["load_kg"] or 0) != 180 for s in press["sets"])                    # a carga planejada dele não vem para mim
    assert n.post(f"/api/partners/{aid}/draft", json={"entry_id": 99999}).status_code == 404
    assert n.post(f"/api/partners/{aid}/draft", json={}).status_code == 400


def test_duel_counts_time_and_calories_and_marks_estimates(env):
    a, aid, n, nid, _b, _bid = env
    _link(a, n, nid)
    a.post("/api/sessions", json=ANDRE_WORKOUT)                                       # sem relógio: 65 min, calorias estimadas
    db.save_session({"date": "2026-11-18T07:00", "source": "garmin", "garmin_activity_id": 1, "duration_min": 40,
                     "garmin_summary": {"kcal": 480, "duration_min": 40},
                     "items": [{"exercise_id": "corrida_rua", "sport": "corrida", "duration_min": 40, "distance_km": 6.2, "kcal": 480}]}, user_id=aid)
    db.save_session({"date": "2026-11-20T12:35", "source": "android", "ext_id": "android:z1", "duration_min": 62,
                     "items": [{"exercise_id": "atividade_outra", "duration_min": 62, "zone": 1, "kcal": 310}]}, user_id=nid)
    db.save_session({"date": "2026-11-17T18:00", "source": "passos", "ext_id": "passos:2026-11-17", "duration_min": 120,
                     "items": [{"exercise_id": "caminhada_rua", "sport": "caminhada", "duration_min": 120, "tss": 30}]}, user_id=nid)
    d = a.get(f"/api/partners/{nid}/duel").json()
    assert d["week_start"] == "2026-11-16" and d["me"]["first"] == "Andre" and d["partner"]["first"] == "Nathália"
    m = {x["key"]: x for x in d["metrics"]}
    assert (m["sessions"]["me"], m["sessions"]["partner"]) == (2, 1)                   # o festival estimado pelos passos não é treino
    assert (m["minutes"]["me"], m["minutes"]["partner"]) == (105, 62)
    assert m["kcal"]["partner"] == 310 and m["kcal"]["me"] > 480 and d["kcal_estimated"] and d["me"]["kcal_estimated"]
    assert d["score"] == {"me": 3, "partner": 0, "lead": "me"} and d["together"] == ["2026-11-20"]
    fri = next(x for x in d["days"] if x["date"] == "2026-11-20")
    assert fri["me"]["minutes"] == 65 and fri["partner"]["kcal"] == 310 and not fri["future"]
    mirror = n.get(f"/api/partners/{aid}/duel").json()
    assert mirror["score"] == {"me": 0, "partner": 3, "lead": "partner"}
    ov = a.get("/api/partners").json()["partners"][0]
    assert ov["duel"]["score"]["lead"] == "me" and ov["today"]["done"][0]["minutes"] == 62


def test_calorie_estimate_only_when_watch_gave_none():
    s_watch = {"items": [{"exercise_id": "Leg_Press", "sets": [{"reps": 10, "load_kg": 50}] * 10}], "duration_min": 60, "garmin_summary": {"kcal": 300}}
    s_app = {"items": [{"exercise_id": "Leg_Press", "sets": [{"reps": 10, "load_kg": 50}] * 10}], "duration_min": 60}
    assert partners.session_kcal(s_watch, 60) == (300.0, False)
    kcal, est = partners.session_kcal(s_app, 60)
    assert est and kcal == round(partners.MET_STRENGTH * 60 * 1.0)
    assert partners.session_minutes({"items": [{"exercise_id": "Leg_Press", "sets": [{}] * 10}]}) == 26.0   # sem duração: ~2,6 min por série


def test_today_card_remembers_yesterdays_workout_until_i_register_it(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    a.post("/api/sessions", json=ANDRE_WORKOUT)
    assert n.get("/api/partners").json()["partners"][0]["recent"] is None          # é de hoje: aparece em "hoje", não em "ontem"
    clock.freeze(datetime(2026, 11, 21, 9, 0))                                     # ela só abre o app no dia seguinte
    p = n.get("/api/partners").json()["partners"][0]
    assert p["today"]["done"] == [] and p["recent"]["days_ago"] == 1 and p["recent"]["date"] == "2026-11-20"
    assert p["recent"]["title"] == "Treino livre"
    draft = n.post(f"/api/partners/{aid}/draft", json={"session_id": p["recent"]["id"]}).json()
    n.post("/api/sessions", json={k: v for k, v in draft.items() if k in ("date", "day_name", "items", "with_partner")})
    assert n.get("/api/partners").json()["partners"][0]["recent"] is None          # registrou: o lembrete some
    assert a.get("/api/partners").json()["partners"][0]["recent"] is None          # e o André não é chamado a puxar o que era dele


def test_pulled_workout_without_watch_keeps_the_time_of_the_workout_done_together(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    sid = a.post("/api/sessions", json=ANDRE_WORKOUT).json()["id"]
    draft = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert draft["duration_min"] == 65 and draft["merge_candidates"] == []        # o relógio dela não entregou nada
    body = {k: v for k, v in draft.items() if k in ("date", "day_name", "items", "with_partner", "duration_min")}
    mine = n.post("/api/sessions", json=body).json()
    assert mine["duration_min"] == 65
    du = n.get(f"/api/partners/{aid}/duel").json()
    by = {m["key"]: m for m in du["metrics"]}
    assert by["minutes"]["me"] == 65 and by["minutes"]["partner"] >= 65           # mesmo treino, mesmo tempo no placar
    # tempo absurdo vindo da tela não é gravado
    bad = n.post("/api/sessions", json={**body, "client_id": "x2", "duration_min": "abc"}).json()
    assert not bad.get("duration_min")


def _ingest(c, kind, start, end, dur, wid=None, **extra):
    tok = c.post("/api/account/ingest-token", json={}).json()["token"]
    w = {"id": wid or f"z-{kind}-{start}", "type": kind, "name": "Treino", "start": f"2026-11-20T{start}:00-03:00",
         "end": f"2026-11-20T{end}:00-03:00", "duration_min": dur, "avg_hr": 104, "max_hr": 131, "kcal": 310, **extra}
    return c.post("/api/ingest", headers={"Authorization": f"Bearer {tok}"}, json={"source": "android", "days": [], "workouts": [w]})


def _pull(a, aid, n, **more):
    sid = a.post("/api/sessions", json=ANDRE_WORKOUT).json()["id"]
    draft = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    body = {k: v for k, v in draft.items() if k in ("date", "day_name", "items", "with_partner", "duration_min")}
    return draft, n.post("/api/sessions", json={**body, "client_id": "n-1", **more}).json()


def test_watch_that_only_times_arrives_after_the_pulled_workout_and_joins_it(env):
    """O caso real: ela registra o treino puxado e só depois o relógio (Amazfit: "outro treino") entrega o tempo."""
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    _, mine = _pull(a, aid, n)
    assert mine["duration_min"] == 65 and not mine.get("merged")
    r = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()
    assert r["imported"][0]["merged"] is True and r["imported"][0]["session_id"] == mine["id"]
    ss = [s for s in n.get("/api/sessions?limit=20").json() if s["date"].startswith("2026-11-20")]
    assert len(ss) == 1 and ss[0]["merged"] and ss[0]["duration_min"] == 62 and ss[0]["garmin_summary"]["kcal"] == 310
    assert [i["exercise_id"] for i in ss[0]["items"]][:2] == ["Leg_Extensions", "Seated_Leg_Curl"] and ss[0]["with_partner"] == aid
    by = {m["key"]: m for m in n.get(f"/api/partners/{aid}/duel").json()["metrics"]}
    assert by["sessions"]["me"] == 1 and by["minutes"]["me"] == 62 and by["kcal"]["me"] == 310      # nada em dobro no placar
    _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62)                                                 # o relógio reenvia a cada sincronização
    assert len([s for s in n.get("/api/sessions?limit=20").json() if s["date"].startswith("2026-11-20")]) == 1
    # outro cardio mais tarde, fora do horário do treino: é outro treino mesmo
    assert _ingest(n, "OTHER_WORKOUT_83", "18:10", "18:40", 30).json()["imported"][0]["merged"] is False
    assert len([s for s in n.get("/api/sessions?limit=20").json() if s["date"].startswith("2026-11-20")]) == 2


def test_same_time_rule_only_for_activities_the_watch_could_not_name(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    _pull(a, aid, n)
    # o relógio disse que era corrida na esteira no mesmo horário: não vira a musculação anotada
    assert _ingest(n, "RUNNING_TREADMILL", "12:40", "13:10", 30, distance_km=4.2).json()["imported"][0]["merged"] is False


def test_logging_the_workout_after_the_watch_joins_too_unless_i_say_it_is_another(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    draft, mine = _pull(a, aid, n, no_merge=True)                      # ela viu a atividade e disse "é outro treino"
    assert [c["id"] for c in draft["merge_candidates"]] == [w] and mine["id"] != w
    assert len(n.get("/api/sessions?limit=20").json()) == 2
    # sem dizer nada (treino anotado por fora do cartão do parceiro): o mesmo horário junta sozinho
    body = {"date": "2026-11-20T12:40", "day_name": "Treino livre", "client_id": "n-2", "duration_min": 55,
            "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 20}, {"reps": 12, "load_kg": 20}]}]}
    n.delete(f"/api/sessions/{mine['id']}")
    again = n.post("/api/sessions", json=body).json()
    assert again["id"] == w and again["merged"] and again["duration_min"] == 62 and again["items"][0]["exercise_id"] == "Leg_Extensions"


# ------------------------------------------------------------------ achados da revisão: puxar e juntar ---
ARMS = {"date": "2026-11-20T19:00", "day_name": "Braços", "client_id": "a-2", "duration_min": 40,
        "items": [{"exercise_id": "Dumbbell_Bicep_Curl", "sets": [{"reps": 12, "load_kg": 12}, {"reps": 10, "load_kg": 14}]}]}


def _day_sessions(c, day="2026-11-20"):
    return sorted((s for s in c.get("/api/sessions?limit=30").json() if s["date"].startswith(day)), key=lambda s: s["date"])


def test_second_strength_workout_of_the_day_never_overwrites_the_one_joined_with_the_watch(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    draft, first = _pull(a, aid, n, merge_into=w)
    assert first["id"] == w and first["merged"] and first["duration_min"] == 62
    sid2 = a.post("/api/sessions", json=ARMS).json()["id"]
    d2 = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid2}).json()
    assert [x["id"] for x in d2["already"]] == [w] and d2["merge_candidates"] == []          # a atividade do relógio já tem dono
    body = {k: v for k, v in d2.items() if k in ("date", "day_name", "items", "with_partner", "duration_min")}
    second = n.post("/api/sessions", json={**body, "client_id": "n-2"}).json()
    ss = _day_sessions(n)
    assert second["id"] != w and len(ss) == 2
    assert [i["exercise_id"] for i in ss[0]["items"]][0] == "Leg_Extensions" and ss[0]["garmin_summary"]["kcal"] == 310   # pernas intacto
    assert ss[1]["items"][0]["exercise_id"] == "Dumbbell_Bicep_Curl" and not ss[1].get("merged") and ss[1]["duration_min"] == 40
    # o mesmo sem parceiro: um treino livre anotado à noite também não toma o lugar do que foi juntado
    free = n.post("/api/sessions", json={"date": "2026-11-20T20:30", "client_id": "n-3", "day_name": "Abdômen",
                                         "items": [{"exercise_id": "Crunches", "sets": [{"reps": 20}, {"reps": 20}]}]}).json()
    assert free["id"] not in (w, second["id"]) and len(_day_sessions(n)) == 3


def test_exercise_done_in_two_blocks_is_pulled_once_with_all_its_sets(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    blocks = {"date": "2026-11-20T12:30", "client_id": "a-9", "day_name": "Pernas", "items": [
        {"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 40}, {"reps": 12, "load_kg": 40}]},
        {"exercise_id": "Seated_Leg_Curl", "sets": [{"reps": 12, "load_kg": 30}, {"reps": 12, "load_kg": 30}]},
        {"exercise_id": "Leg_Extensions", "sets": [{"reps": 8, "load_kg": 50}]},
        {"exercise_id": "Seated_Leg_Curl", "sets": [{"reps": 8, "load_kg": 35}]}]}
    sid = a.post("/api/sessions", json=blocks).json()["id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert [(i["exercise_id"], len(i["sets"])) for i in d["items"]] == [("Leg_Extensions", 3), ("Seated_Leg_Curl", 3)]   # 6 séries, não 12
    assert [s["reps"] for s in d["items"][0]["sets"]] == [12, 12, 8] and [s["partner_load"] for s in d["items"][0]["sets"]] == [40, 40, 50]


def test_only_strength_or_unnamed_watch_activity_can_become_the_pulled_workout(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    yoga = _ingest(n, "YOGA", "07:00", "07:50", 50).json()["imported"][0]["session_id"]
    early = _ingest(n, "STRENGTH_TRAINING", "08:00", "08:45", 45, wid="z-early").json()["imported"][0]["session_id"]
    sid = a.post("/api/sessions", json=ANDRE_WORKOUT).json()["id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert [(c["id"], c["match"]) for c in d["merge_candidates"]] == [(early, False)]   # yoga nunca; musculação de outro horário não vem marcada
    body = {k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner", "duration_min")}
    # tentativa de juntar com a yoga é ignorada; e ela deixou "Não, é outro treino" (a tela manda no_merge)
    r = n.post("/api/sessions", json={**body, "client_id": "n-1", "merge_into": yoga, "no_merge": True})
    assert r.status_code == 200 and r.json()["id"] not in (yoga, early)
    assert len(_day_sessions(n)) == 3 and any(s["id"] == yoga and s["items"][0]["exercise_id"] == "atividade_mobilidade" for s in _day_sessions(n))


def test_deleting_the_joined_workout_keeps_what_the_watch_measured(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    _pull(a, aid, n, merge_into=w)
    r = n.delete(f"/api/sessions/{w}").json()
    assert r["watch_kept"] and r["minutes"] == 62
    left = _day_sessions(n)
    assert len(left) == 1 and not any(i.get("sets") for i in left[0]["items"]) and left[0]["garmin_summary"]["kcal"] == 310
    sid = a.get("/api/sessions?limit=5").json()[0]["id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert [(c["id"], c["match"]) for c in d["merge_candidates"]] == [(w, True)]          # dá para refazer e juntar de novo
    assert n.delete(f"/api/sessions/{w}").json().get("watch_kept") is None and _day_sessions(n) == []   # apagar a do relógio: some de vez
    _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62)
    assert _day_sessions(n) == []                                                         # e não volta na próxima sincronização


def test_cardio_closed_by_the_watch_activity_reopens_when_it_becomes_the_strength_workout(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    e = db.save_entry({"date": "2026-11-20", "kind": "cardio", "status": "planejado", "day_name": "Cardio leve", "plan_id": None,
                       "items": [{"exercise_id": "cardio_eliptico", "duration_min": 30}]}, user_id=nid)
    w = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    n.get("/api/schedule?date=2026-11-20")
    before = db.get_entry(e["id"], user_id=nid)
    assert before["status"] == "feito" and before["done_by"]["session_id"] == w       # a atividade do relógio fechou o cardio "pela data"
    _pull(a, aid, n, merge_into=w)
    n.get("/api/schedule?date=2026-11-20")
    after = db.get_entry(e["id"], user_id=nid)
    assert after["status"] == "planejado" and not after.get("done_by")                 # virou musculação: o cardio não foi feito


# ------------------------------------------------------------------ achados da revisão: placar ---
def test_watch_that_recorded_only_the_treadmill_does_not_shrink_the_whole_workout(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = dict(ANDRE_WORKOUT)
    w.pop("duration_min")
    sid = a.post("/api/sessions", json=w).json()["id"]                       # 7 séries + 12 min de esteira, sem tempo medido
    before = {m["key"]: m["me"] for m in a.get(f"/api/partners/{nid}/duel").json()["metrics"]}
    # o Garmin sincroniza só a corrida na esteira (12 min, 130 kcal) e ela é juntada ao treino
    from gymgarmin import dedupe
    run = {"date": "2026-11-20T13:20:00", "source": "garmin", "garmin_activity_id": 991, "duration_min": 12,
           "garmin_summary": {"duration_min": 12, "kcal": 130, "avg_hr": 150},
           "items": [{"exercise_id": "cardio_esteira_corrida", "sport": "corrida", "duration_min": 12, "kcal": 130, "avg_hr": 150, "zone": 3}]}
    app_s = db.get_session(sid, user_id=aid)
    merged = db.save_session(dedupe.merge(app_s, run), session_id=sid, user_id=aid)
    assert merged["garmin_summary"].get("partial") and not merged.get("duration_min")
    assert partners.watch_covers_only_cardio(merged)
    after = a.get(f"/api/partners/{nid}/duel").json()
    by = {m["key"]: m["me"] for m in after["metrics"]}
    assert by["minutes"] == before["minutes"] and by["minutes"] > 25          # o treino não encolheu para 12 min
    assert by["kcal"] > 130 and after["me"]["kcal_estimated"] and after["me"]["minutes_estimated"]
    # dado antigo, gravado antes desta correção (duração do treino = a da esteira): mesma leitura
    old = {**app_s, "garmin_activity_id": 991, "duration_min": 12, "garmin_summary": {"duration_min": 12, "kcal": 130}}
    assert partners.watch_covers_only_cardio(old) and partners.session_time(old)[0] > 25


def test_workout_done_together_uses_the_measured_time_for_both(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = dict(ANDRE_WORKOUT)
    w.pop("duration_min")
    sid = a.post("/api/sessions", json=w).json()["id"]                       # ele anotou no app: tempo só estimado
    solo = a.get(f"/api/partners/{nid}/duel").json()
    assert solo["me"]["minutes_estimated"] and solo["minutes_estimated"]
    watch = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert d["duration_min"] is None                                          # estimativa por série não vira "tempo do treino"
    body = {k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner")}
    n.post("/api/sessions", json={**body, "client_id": "n-1", "merge_into": watch})
    du = a.get(f"/api/partners/{nid}/duel").json()
    by = {m["key"]: m for m in du["metrics"]}
    assert by["minutes"]["me"] == by["minutes"]["partner"] == 62              # mesmo treino, mesmo tempo (o medido pelo relógio dela)
    assert not du["me"]["minutes_estimated"] and not du["partner"]["minutes_estimated"]
    assert du["me"]["kcal_estimated"] and by["kcal"]["me"] > by["kcal"]["partner"] * 0.9   # 82 kg em 62 min não fica atrás por causa do chute de tempo
    mirror = {m["key"]: m for m in n.get(f"/api/partners/{aid}/duel").json()["metrics"]}
    assert (mirror["minutes"]["me"], mirror["minutes"]["partner"]) == (62, 62) and mirror["kcal"]["partner"] == by["kcal"]["me"]   # os dois veem os mesmos números


def test_owner_without_a_name_is_not_shown_as_eu_and_estimates_do_not_reveal_weight(env):
    a, aid, n, nid, b, bid = env
    a.put("/api/profile", json={"name": "Eu"})
    _link(a, n, nid)
    assert n.get("/api/partners").json()["partners"][0]["first"] == "Andre"   # vem do começo do e-mail (andre@x.com)
    s = {"items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 10, "load_kg": 20}] * 10}], "duration_min": 60}
    k82, k80, k63 = (partners.session_kcal(s, kg)[0] for kg in (82, 80, 63.5))
    assert k82 == k80 and k82 % 10 == 0 and k63 == partners.session_kcal(s, 65)[0]   # faixa de 5 kg, arredondado


def test_today_asks_when_the_watch_time_and_the_logged_workout_do_not_overlap(env):
    """O relógio gravou 62 min ao meio-dia; ela anota o treino às 23h21 (hora em que abriu a tela): pergunta em vez de dobrar."""
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    late = {"date": "2026-11-20T20:50", "day_name": "Treino livre", "client_id": "n-late",
            "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 20}] * 3},
                      {"exercise_id": "Seated_Leg_Curl", "sets": [{"reps": 12, "load_kg": 20}] * 3}]}
    mine = n.post("/api/sessions", json=late).json()
    assert mine["id"] != w and len(_day_sessions(n)) == 2                      # horários não batem: não junta sozinho
    q = n.get("/api/partners").json()["merge_questions"]
    assert len(q) == 1 and (q[0]["watch"]["id"], q[0]["app"]["id"]) == (w, mine["id"]) and q[0]["watch"]["minutes"] == 62 and q[0]["app"]["sets"] == 6
    r = n.post("/api/sessions/merge-answer", json={"watch_id": w, "app_id": mine["id"], "same": True}).json()
    assert r["merged"] == mine["id"] and r["minutes"] == 62 and r["questions"] == []
    left = _day_sessions(n)
    assert len(left) == 1 and left[0]["date"].startswith("2026-11-20T12:35") and left[0]["garmin_summary"]["kcal"] == 310
    assert [i["exercise_id"] for i in left[0]["items"]] == ["Leg_Extensions", "Seated_Leg_Curl"]
    by = {m["key"]: m for m in n.get(f"/api/partners/{aid}/duel").json()["metrics"]}
    assert by["sessions"]["me"] == 1 and by["minutes"]["me"] == 62
    _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62)                           # reenvio do relógio não recria a atividade
    assert len(_day_sessions(n)) == 1
    assert n.post("/api/sessions/merge-answer", json={"watch_id": w, "app_id": mine["id"], "same": True}).status_code == 409


def test_saying_they_are_two_workouts_stops_the_question(env):
    a, aid, n, nid, b, bid = env
    w = _ingest(n, "OTHER_WORKOUT", "07:00", "07:40", 40).json()["imported"][0]["session_id"]
    mine = n.post("/api/sessions", json={"date": "2026-11-20T19:30", "client_id": "n-2", "day_name": "Braços",
                                         "items": [{"exercise_id": "Dumbbell_Bicep_Curl", "sets": [{"reps": 12, "load_kg": 8}] * 3}]}).json()
    assert len(n.get("/api/partners").json()["merge_questions"]) == 1          # aparece mesmo para quem não tem parceiro
    r = n.post("/api/sessions/merge-answer", json={"watch_id": w, "app_id": mine["id"], "same": False}).json()
    assert r["merged"] is None and r["questions"] == [] and len(_day_sessions(n)) == 2
    assert n.get("/api/partners").json()["merge_questions"] == []
    # e sessão de outra pessoa não entra na resposta
    assert a.post("/api/sessions/merge-answer", json={"watch_id": w, "app_id": mine["id"], "same": True}).status_code == 409


# ------------------------------------------------------------------ achados da revisão: telas da parceria ---
def test_unnamed_watch_sets_are_reported_and_my_own_workout_is_not_offered_back(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    garmin = {"date": "2026-11-20T12:30", "source": "garmin", "garmin_activity_id": 77, "duration_min": 60, "day_name": "Força",
              "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 40}] * 3},
                        {"exercise_id": "Garmin_desconhecido", "_name": "Exercício não identificado pelo relógio", "sets": [{"reps": 10, "load_kg": 30}] * 5}]}
    sid = db.save_session(garmin, user_id=aid)["id"]
    day = n.get(f"/api/partners/{aid}/day").json()["done"][0]
    assert day["sets"] == 3 and day["unnamed_sets"] == 5 and day["has_sets"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert d["skipped_sets"] == 5 and [i["exercise_id"] for i in d["items"]] == ["Leg_Extensions"]
    only = db.save_session({**garmin, "garmin_activity_id": 78, "date": "2026-11-19T12:30", "items": garmin["items"][1:]}, user_id=aid)["id"]
    r = n.post(f"/api/partners/{aid}/draft", json={"session_id": only})
    assert r.status_code == 400 and "sem o nome do exercício" in r.json()["detail"]
    # ela salva o treino puxado; do lado dele, o treino dela aparece marcado como vindo do dele e puxar de volta avisa
    n.post("/api/sessions", json={k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner")})
    hers = a.get(f"/api/partners/{nid}/day").json()["done"][0]
    assert hers["with_partner"] == aid
    back = a.post(f"/api/partners/{nid}/draft", json={"session_id": hers["id"]}).json()
    assert [x["id"] for x in back["already"]] == [sid]                                           # ele já tem esse treino no dia


def test_day_window_shows_the_same_shared_time_as_the_scoreboard(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    w = dict(ANDRE_WORKOUT)
    w.pop("duration_min")
    sid = a.post("/api/sessions", json=w).json()["id"]
    card = n.get("/api/partners").json()["partners"][0]["today"]["done"][0]
    assert card["minutes_estimated"] and card["minutes"] < 62                 # antes de ela registrar: estimado, com "~" na tela
    watch = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    n.post("/api/sessions", json={**{k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner")}, "merge_into": watch})
    card = n.get("/api/partners").json()["partners"][0]["today"]["done"][0]
    assert card["minutes"] == 62 and not card["minutes_estimated"]            # o mesmo número do placar, na frase do cartão e na janela
    assert n.get(f"/api/partners/{aid}/day").json()["done"][0]["minutes"] == 62


def test_live_workout_is_not_glued_to_a_watch_strength_session_of_another_time(env):
    a, aid, n, nid, b, bid = env
    morning = _ingest(n, "STRENGTH_TRAINING", "07:00", "07:45", 45, wid="m1").json()["imported"][0]["session_id"]
    live = {"date": "2026-11-20T12:30", "day_name": "Treino livre", "client_id": "n-live", "duration_min": 58,   # feito ao vivo: tempo medido
            "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 20}] * 4}]}
    mine = n.post("/api/sessions", json=live).json()
    assert mine["id"] != morning and len(_day_sessions(n)) == 2               # manhã no relógio × meio-dia no app: dois treinos
    assert len(n.get("/api/partners").json()["merge_questions"]) == 1         # e o Hoje pergunta, para o caso de ser o mesmo
    # anotado depois, sem tempo medido: continua a regra de sempre (o relógio já tinha gravado esse treino)
    n.delete(f"/api/sessions/{mine['id']}")
    later = n.post("/api/sessions", json={k: v for k, v in live.items() if k != "duration_min"} | {"client_id": "n-later"}).json()
    assert later["id"] == morning and later["merged"]


def test_watch_naming_the_same_workout_differently_at_the_same_time_makes_today_ask(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    _, mine = _pull(a, aid, n)                                                # treino puxado: 12:30, 65 min
    r = _ingest(n, "HIIT", "12:35", "13:37", 62).json()["imported"][0]
    assert r["merged"] is False and len(_day_sessions(n)) == 2                # o relógio disse HIIT: não junta sozinho...
    q = n.get("/api/partners").json()["merge_questions"]
    assert len(q) == 1 and (q[0]["watch"]["id"], q[0]["app"]["id"]) == (r["session_id"], mine["id"])   # ...mas pergunta
    ans = n.post("/api/sessions/merge-answer", json={"watch_id": r["session_id"], "app_id": mine["id"], "same": True}).json()
    assert ans["merged"] == mine["id"] and ans["minutes"] == 62
    left = _day_sessions(n)
    assert len(left) == 1 and left[0]["garmin_summary"]["kcal"] == 310 and all(i.get("sets") for i in left[0]["items"])   # sem "HIIT 62 min" sobrando como cardio
    by = {m["key"]: m for m in n.get(f"/api/partners/{aid}/duel").json()["metrics"]}
    assert by["sessions"]["me"] == 1 and by["minutes"]["me"] == 62
    # HIIT de outro horário (aula de manhã) não gera pergunta
    _ingest(n, "HIIT", "07:00", "07:30", 30, wid="aula")
    assert n.get("/api/partners").json()["merge_questions"] == []


def test_people_list_says_who_is_the_owner_and_since_when_each_account_exists(env):
    a, aid, n, nid, b, bid = env
    people = {p["id"]: p for p in n.get("/api/partners").json()["people"]}
    assert people[aid]["owner"] is True and people[bid]["owner"] is False and people[bid]["since"] == "2026-11-20"


def test_workout_typed_with_its_own_time_is_not_glued_to_a_morning_watch_session(env):
    a, aid, n, nid, b, bid = env
    morning = _ingest(n, "STRENGTH_TRAINING", "07:00", "07:30", 30, wid="m1").json()["imported"][0]["session_id"]
    typed = {"date": "2026-11-20T18:00", "day_name": "Treino livre", "client_id": "n-typed", "time_set": True,   # ela trocou a hora para 18:00
             "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 20}] * 3}]}
    mine = n.post("/api/sessions", json=typed).json()
    assert mine["id"] != morning and len(_day_sessions(n)) == 2 and len(n.get("/api/partners").json()["merge_questions"]) == 1
    # a ordem inversa (relógio entrega depois) dá o mesmo: a hora digitada fica guardada no treino
    n.delete(f"/api/sessions/{morning}")
    n.delete(f"/api/sessions/{mine['id']}")
    mine2 = n.post("/api/sessions", json={**typed, "client_id": "n-typed2"}).json()
    r = _ingest(n, "STRENGTH_TRAINING", "07:00", "07:30", 30, wid="m2").json()["imported"][0]
    assert r["merged"] is False and r["session_id"] != mine2["id"] and len(_day_sessions(n)) == 2


# ------------------------------------------------------------------ registro claro, sem cópia, corrigir depois ---
CHEST_PLAN = {"date": "2026-11-20", "kind": "forca", "status": "planejado", "day_name": "Empurrar", "plan_id": None,
              "items": [{"exercise_id": "Dumbbell_Bench_Press", "sets": 5, "reps": "4-6", "phase": "principal"},
                        {"exercise_id": "Barbell_Incline_Bench_Press_-_Medium_Grip", "sets": 4, "reps": "6-10", "phase": "principal"},
                        {"exercise_id": "Triceps_Pushdown", "sets": 2, "reps": "10-15", "phase": "principal"}]}
CHEST_DONE = {"date": "2026-11-20T11:55", "day_name": "Empurrar", "client_id": "a-chest", "duration_min": 61,
              "items": [{"exercise_id": "Dumbbell_Bench_Press", "sets": [{"reps": 6, "load_kg": 80}] * 5},
                        {"exercise_id": "Barbell_Incline_Bench_Press_-_Medium_Grip", "sets": [{"reps": 10, "load_kg": 80}] * 4},
                        {"exercise_id": "Triceps_Pushdown", "sets": [{"reps": 15, "load_kg": 35}] * 2}]}


def test_what_happened_today_train_together_then_pull_again_does_not_duplicate(env):
    """O caso real: ela treinou junto pelo planejado dele e salvou; depois o cartão ainda oferecia puxar e ela puxou de novo."""
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    e = db.save_entry(dict(CHEST_PLAN), user_id=aid)
    live = n.post(f"/api/partners/{aid}/draft", json={"entry_id": e["id"]}).json()
    assert live["already"] == []
    mine = {"date": "2026-11-20T11:56", "day_name": live["day_name"], "client_id": "n-live", "with_partner": aid, "duration_min": 70,
            "items": [{"exercise_id": "Dumbbell_Bench_Press", "sets": [{"reps": 12, "load_kg": 12}] * 4},
                      {"exercise_id": "Barbell_Incline_Bench_Press_-_Medium_Grip", "sets": [{"reps": 12, "load_kg": 12.5}] * 4},
                      {"exercise_id": "Triceps_Pushdown", "sets": [{"reps": 15, "load_kg": 13.5}, {"reps": 8, "load_kg": 16}]}]}
    saved = n.post("/api/sessions", json=mine).json()
    assert saved["summary"]["partner_name"] == "Andre" and saved["summary"]["registered"]["sets"] == 10   # a tela mostra o que ficou salvo
    sid = a.post("/api/sessions", json=CHEST_DONE).json()["id"]               # ele salva o dele depois
    card = n.get("/api/partners").json()["partners"][0]["today"]
    assert [r["id"] for r in card["mine"]] == [saved["id"]] and card["mine"][0]["exercises"] == 3   # ✓ Você registrou
    assert card["done"][0]["mine_ids"] == [saved["id"]]                       # e o treino dele aparece como já registrado por ela
    day = n.get(f"/api/partners/{aid}/day").json()
    assert [r["id"] for r in day["registered"]] == [saved["id"]] and day["done"][0]["mine_ids"] == [saved["id"]]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    assert [x["id"] for x in d["already"]] == [saved["id"]]                   # puxar de novo avisa o que já está salvo
    body = {k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner", "duration_min")}
    r = n.post("/api/sessions", json={**body, "client_id": "n-again"})
    assert r.status_code == 409 and "já registrou esse treino" in r.json()["detail"]   # e o servidor não cria a cópia
    assert len(_day_sessions(n)) == 1
    # "refazer a partir do treino dele" substitui o mesmo registro
    re = n.put(f"/api/sessions/{saved['id']}", json={**body, "edit": True, "with_partner": aid}).json()
    assert re["id"] == saved["id"] and len(_day_sessions(n)) == 1 and re["with_partner"] == aid
    assert [len(i["sets"]) for i in re["items"]] == [5, 4, 2]
    # do lado dele: vocês treinaram juntos (o dele está ligado ao dela)
    his = a.get("/api/partners").json()["partners"][0]["today"]
    assert [r["id"] for r in his["mine"]] == [sid] and his["done"][0]["mine_ids"] == [sid]


def test_duplicate_already_saved_is_shown_so_it_can_be_removed(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    sid = a.post("/api/sessions", json=CHEST_DONE).json()["id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    body = {k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner")}
    first = n.post("/api/sessions", json={**body, "client_id": "n1"}).json()
    second = n.post("/api/sessions", json={**body, "client_id": "n2", "allow_repeat": True}).json()   # como ficou em produção hoje
    mine = n.get("/api/partners").json()["partners"][0]["today"]["mine"]
    assert sorted(r["id"] for r in mine) == sorted([first["id"], second["id"]])   # o cartão mostra os dois para apagar um
    n.delete(f"/api/sessions/{second['id']}")
    assert [r["id"] for r in n.get("/api/partners").json()["partners"][0]["today"]["mine"]] == [first["id"]]


def test_saved_workout_can_be_seen_and_corrected_without_losing_anything(env):
    a, aid, n, nid, b, bid = env
    _link(a, n, nid)
    sid = a.post("/api/sessions", json=ANDRE_WORKOUT).json()["id"]
    watch = _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62).json()["imported"][0]["session_id"]
    d = n.post(f"/api/partners/{aid}/draft", json={"session_id": sid}).json()
    body = {k: v for k, v in d.items() if k in ("date", "day_name", "items", "with_partner")}
    saved = n.post("/api/sessions", json={**body, "merge_into": watch}).json()   # salvou sem as cargas
    assert saved["summary"]["registered"]["no_load"] == 7
    sm = n.get(f"/api/sessions/{saved['id']}/summary").json()
    assert sm["summary"]["partner_name"] == "Andre" and [i["name"] for i in sm["items"]][0] == "Cadeira extensora"
    ed = n.get(f"/api/sessions/{saved['id']}/edit").json()
    assert ed["edit_id"] == saved["id"] and [len(i["sets"]) for i in ed["items"]] == [3, 2, 2] and all(x["done"] for i in ed["items"] for x in i["sets"])
    for i in ed["items"]:
        for x in i["sets"]:
            x["load_kg"] = 20
    payload = {"date": ed["date"], "day_name": ed["day_name"], "schedule_id": None, "plan_id": None, "client_id": "novo", "edit": True,
               "items": [{"exercise_id": i["exercise_id"], "sets": [{k: x[k] for k in ("reps", "load_kg", "rpe", "duration_s")} for x in i["sets"]]} for i in ed["items"]]}
    fixed = n.put(f"/api/sessions/{saved['id']}", json=payload).json()
    assert fixed["id"] == saved["id"] and fixed["summary"]["registered"]["no_load"] == 0
    assert fixed["ext_id"] and fixed["merged"] and fixed["with_partner"] == aid and fixed["duration_min"] == 62   # relógio, parceiro e tempo continuam
    assert fixed["garmin_summary"]["kcal"] == 310 and len(_day_sessions(n)) == 1
    _ingest(n, "OTHER_WORKOUT", "12:35", "13:37", 62)                           # relógio reenvia: continua um só
    assert len(_day_sessions(n)) == 1


def test_correcting_keeps_cardio_and_the_agenda_link(env):
    a, aid, n, nid, b, bid = env
    s = n.post("/api/sessions", json={"date": "2026-11-20T18:00", "client_id": "c1", "day_name": "Treino livre",
                                       "items": [{"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": None}] * 3},
                                                 {"exercise_id": "cardio_eliptico", "duration_min": 15, "zone": 2}]}).json()
    db.save_session({**db.get_session(s["id"], user_id=nid), "schedule_id": 999}, session_id=s["id"], user_id=nid)
    ed = n.get(f"/api/sessions/{s['id']}/edit").json()
    assert [i["exercise_id"] for i in ed["items"]] == ["Leg_Extensions"] and ed["kept_items"]
    fixed = n.put(f"/api/sessions/{s['id']}", json={"edit": True, "date": ed["date"], "schedule_id": None, "items": [
        {"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 25}] * 3}]}).json()
    assert [i["exercise_id"] for i in fixed["items"]] == ["Leg_Extensions", "cardio_eliptico"] and fixed["schedule_id"] == 999
