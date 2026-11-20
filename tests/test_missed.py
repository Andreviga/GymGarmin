"""Treino pulado/perdido e treino livre: falta isolada não gera dívida; crédito pelo conteúdo, não pela data."""
import copy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import analysis, catalog, clock, credit, db, generator, schedule

MONDAY = date(2026, 11, 16)
UL4 = {"training_months": 14, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 60}


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "m.sqlite3"))
    db.init()
    yield


def _setup(profile: dict):
    db.save_profile(profile)
    plan = generator.generate(db.get_profile())
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    return db.get_profile(), saved


def _week(prof, today=MONDAY):
    return schedule.ensure_week(prof, MONDAY, today)


def _shape(entries):
    return {e["id"]: (e["date"], e["status"], [(i["exercise_id"], i.get("sets")) for i in e["items"]]) for e in entries}


_cid = [0]


def _free(items: list[tuple[str, int]], when: date, **extra) -> dict:
    """Treino livre do app: tem client_id e não tem vínculo com a agenda."""
    _cid[0] += 1
    return db.save_session({"date": when.isoformat() + "T18:00", "day_name": "Treino livre", "client_id": f"t-{_cid[0]}",
                            "items": [{"exercise_id": x, "sets": [{"reps": 10, "load_kg": 30} for _ in range(n)]} for x, n in items],
                            **extra})


def _items_of(e: dict, share: float = 1.0) -> list[tuple[str, int]]:
    its = credit.strength_items(e)
    return [(i["exercise_id"], int(i["sets"])) for i in its[:max(1, round(len(its) * share))]]


# ------------------------------------------------------------------ séries de trabalho ---
def test_warmup_sets_do_not_count_as_work():
    it = {"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": w} for w in (19, 40, 54, 68, 75, 82, 82)]}
    assert [s["load_kg"] for s in analysis._hard_sets(it)] == [54, 68, 75, 82, 82]  # ≤ 60% da maior carga antes dela = aquecimento
    marked = {"exercise_id": "Leg_Extensions", "sets": [{"reps": 12, "load_kg": 20, "warmup": False}, {"reps": 10, "load_kg": 80}]}
    assert len(analysis._hard_sets(marked)) == 2  # marcação explícita vale mais que a detecção
    undone = {"exercise_id": "Leg_Extensions", "sets": [{"reps": 10, "load_kg": 80}, {"reps": 10, "load_kg": 80, "done": False}]}
    assert len(analysis._hard_sets(undone)) == 1
    back_off = {"exercise_id": "Leg_Extensions", "sets": [{"reps": 8, "load_kg": 80}, {"reps": 15, "load_kg": 40}]}
    assert len(analysis._hard_sets(back_off)) == 2  # série leve DEPOIS da pesada é trabalho (back-off), não aquecimento


# ------------------------------------------------------------------------ treino livre ---
def test_free_workout_equal_to_todays_plan_closes_it(fresh_db):
    prof, _ = _setup(UL4)
    today_e = _week(prof)[0]
    s = _free(_items_of(today_e), MONDAY)
    c = schedule.credit_session(s, prof, MONDAY)
    assert c["closed"]["entry_id"] == today_e["id"] and c["closed"]["coverage"] >= credit.COVER_CLOSE and not c["question"]
    e = db.get_entry(today_e["id"])
    assert e["status"] == "feito" and e["done_by"]["session_id"] == s["id"] and not e.get("partial")
    assert db.get_session(s["id"])["schedule_id"] == today_e["id"]


def test_arms_on_leg_day_is_extra_and_leg_day_stays_open(fresh_db):
    """Qualquer série solta não "faz" o treino do dia: bíceps e tríceps não fecham um treino de perna."""
    prof, _ = _setup(UL4)
    tue = MONDAY + timedelta(days=1)
    leg = next(e for e in _week(prof, tue) if e["date"] == tue.isoformat())
    assert schedule._is_leg_day(leg)
    s = _free([("Dumbbell_Bicep_Curl", 3), ("Triceps_Pushdown_-_Rope_Attachment", 3)], tue)
    c = schedule.credit_session(s, prof, tue)
    assert c["extra"] and not c["closed"] and c["kept_open"]["entry_id"] == leg["id"]
    assert next(e for e in _week(prof, tue) if e["id"] == leg["id"])["status"] == "planejado"
    assert next(e for e in _week(prof, tue + timedelta(days=1)) if e["id"] == leg["id"])["status"] == "perdido"
    goals = schedule.week_goals(_week(prof, tue), prof, MONDAY, tue)
    assert goals["sessions"]["extra"] == 1 and goals["sets"]["done"] == 6
    # o saldo conta o que foi feito de verdade: bíceps ganhou séries, perna continua na agenda
    bal = {b["key"]: b for b in goals["muscles"]}
    assert bal["biceps"]["done"] >= 3 and bal["quadriceps"]["done"] == 0 and bal["quadriceps"]["scheduled"] > 0


def test_partial_free_workout_asks_and_the_answer_decides(fresh_db):
    prof, _ = _setup(UL4)
    today_e = _week(prof)[0]
    s = _free(_items_of(today_e, 0.45), MONDAY)
    cov = credit.coverage(s, today_e)
    assert credit.COVER_ASK <= cov < credit.COVER_CLOSE, cov
    c = schedule.credit_session(s, prof, MONDAY)
    assert c["question"]["kind"] == "hoje" and c["question"]["entry_id"] == today_e["id"] and not c["closed"]
    assert db.get_entry(today_e["id"])["status"] == "planejado"
    assert next(e for e in _week(prof) if e["id"] == today_e["id"])["status"] == "planejado"  # a agenda não fecha sozinha
    pend = schedule.free_workout_state(prof, MONDAY)
    assert [p["session_id"] for p in pend] == [s["id"]] and pend[0]["entry_id"] == today_e["id"]
    # reenviar o mesmo treino não muda a decisão em aberto
    again = schedule.credit_session(db.get_session(s["id"]), prof, MONDAY)
    assert again["question"]["entry_id"] == today_e["id"]
    r = schedule.match_session(s["id"], prof, today_e["id"], today=MONDAY)
    e = db.get_entry(today_e["id"])
    assert r["closed"]["partial"] and e["status"] == "feito" and e["partial"] and e["done_by"]["how"] == "decisao"
    assert schedule.free_workout_state(prof, MONDAY) == []
    # mudou de ideia: era um extra → o treino volta para a agenda
    r = schedule.match_session(s["id"], prof, None, today=MONDAY)
    assert r["extra"] and db.get_entry(today_e["id"])["status"] == "planejado" and db.get_session(s["id"])["extra"]


def test_free_workout_of_a_future_day_asks_before_taking_it(fresh_db):
    prof, _ = _setup(UL4)
    week = _week(prof)
    thu = next(e for e in week if e["date"] == (MONDAY + timedelta(days=3)).isoformat())
    s = _free(_items_of(thu), MONDAY)
    c = schedule.credit_session(s, prof, MONDAY)
    # cobre o treino de quinta, mas hoje é segunda: o app pergunta em vez de tirar o treino da quinta sozinho
    if c["question"]:
        assert c["question"]["kind"] == "futuro" and c["question"]["entry_id"] == thu["id"]
        assert db.get_entry(thu["id"])["status"] == "planejado"
    else:  # (planos em que os dois superiores se parecem muito: fecha o de hoje)
        assert c["closed"]["entry_id"] == week[0]["id"]


def test_doing_a_skipped_workout_later_closes_it_and_drops_the_reinforcement(fresh_db):
    prof, _ = _setup({**UL4, "available_days": [0, 2, 4], "session_minutes": 75})
    week = _week(prof)
    before = _shape(week)
    first = week[0]
    opts = schedule.handle_missed(first["id"], prof, MONDAY, dry_run=True)
    strat = "reforcar" if next(o for o in opts["options"] if o["id"] == "reforcar")["available"] else "seguir"
    schedule.handle_missed(first["id"], prof, MONDAY, strategy=strat)
    tue = MONDAY + timedelta(days=1)
    s = _free(_items_of(first), tue)
    c = schedule.credit_session(s, prof, tue)
    assert c["closed"]["entry_id"] == first["id"] and c["closed"]["was_skipped"] and c["closed"]["other_day"]
    after = _shape(_week(prof, tue))
    assert after[first["id"]][1] == "feito"
    for k, v in before.items():
        if k != first["id"]:
            assert after[k][2] == v[2]  # o reforço que o pulo tinha colocado saiu


def test_deleting_or_gutting_the_session_reopens_the_workout(fresh_db):
    prof, _ = _setup(UL4)
    today_e = _week(prof)[0]
    s = _free(_items_of(today_e), MONDAY)
    schedule.credit_session(s, prof, MONDAY)
    assert db.get_entry(today_e["id"])["status"] == "feito"
    schedule.reopen_for_session(db.get_session(s["id"]), MONDAY)
    assert db.get_entry(today_e["id"])["status"] == "planejado" and "done_by" not in db.get_entry(today_e["id"])


def test_thin_linked_session_keeps_the_workout_open(fresh_db):
    """Abriu o treino pela agenda e fez só um exercício (< 25%): o treino não vira "feito" sozinho."""
    prof, _ = _setup(UL4)
    today_e = _week(prof)[0]
    one = _items_of(today_e)[-1:]
    s = _free([(one[0][0], 1)], MONDAY, schedule_id=today_e["id"])
    c = schedule.credit_session(s, prof, MONDAY)
    assert c["kept_open"]["entry_id"] == today_e["id"] and not c["closed"]
    assert next(e for e in _week(prof) if e["id"] == today_e["id"])["status"] == "planejado"
    # a maior parte feita pela agenda fecha normalmente
    s2 = _free(_items_of(today_e), MONDAY, schedule_id=today_e["id"])
    assert schedule.credit_session(s2, prof, MONDAY)["closed"]["entry_id"] == today_e["id"]


def test_one_session_with_strength_and_run_closes_both(fresh_db, monkeypatch):
    monkeypatch.setattr(db, "save_entry", lambda e: e)
    lift = {"id": 1, "date": "2026-11-18", "kind": "forca", "status": "planejado", "day_name": "Puxar",
            "items": [{"exercise_id": "Pullups", "sets": 4}]}
    run = {"id": 2, "date": "2026-11-18", "kind": "resistencia", "sport": "corrida", "status": "planejado", "day_name": "Corrida",
           "items": [{"sport": "corrida", "endurance": {"dur_s": 2400}}]}
    walk = {"id": 3, "date": "2026-11-18", "kind": "cardio", "status": "planejado", "day_name": "Cardio", "items": []}
    sess = [{"id": 9, "date": "2026-11-18T07:00", "items": [{"exercise_id": "Pullups", "sets": [{"reps": 8, "load_kg": 0}] * 4},
                                                            {"sport": "corrida", "duration_min": 35}]}]
    out = {e["id"]: e["status"] for e in schedule.sync_status([lift, run, walk], sess, date(2026, 11, 18))}
    assert out == {1: "feito", 2: "feito", 3: "planejado"}  # força + corrida sim; a mesma corrida não vale por dois cardios


# ------------------------------------------------------------------------ pular / reforço ---
def test_dry_run_writes_nothing_and_recommends_one_option(fresh_db):
    prof, _ = _setup(UL4)
    week = _week(prof)
    before = _shape(week)
    r = schedule.handle_missed(week[0]["id"], prof, MONDAY, dry_run=True)
    assert _shape(_week(prof)) == before
    assert [o["id"] for o in r["options"]] == ["seguir", "reforcar", "mover"]
    assert sum(1 for o in r["options"] if o["recommended"]) == 1 and r["recommended"] and r["why"]
    assert r["muscles"] and all(k in r["muscles"][0] for k in ("pt", "projected", "ref"))


def test_reinforcement_guards(fresh_db):
    """Nunca cria exercício, nunca sai dos músculos do treino pulado, +1 por exercício, ≤ 4 por treino, nada no principal pesado."""
    for prof_in in (UL4, {"training_months": 30, "goal": "forca", "available_days": [0, 2, 4], "session_minutes": 75},
                    {"training_months": 40, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4], "session_minutes": 75}):
        for e in db.list_schedule("2026-11-16", "2026-11-22"):
            db.delete_entry(e["id"])
        prof, _ = _setup(prof_in)
        week = _week(prof)
        for target in [e for e in week if e["kind"] == "forca"][:2]:
            before = {e["id"]: copy.deepcopy(e) for e in _week(prof)}
            ctx = schedule._skip_ctx(target, prof, MONDAY)
            ops, work = schedule._reinforce_ops(target, ctx, asked=True)
            for o in ops:
                src, dst = before[o["entry_id"]], work[o["entry_id"]]
                a, b = src["items"][o["index"]], dst["items"][o["index"]]
                assert a["exercise_id"] == b["exercise_id"] == o["exercise_id"]
                assert int(b["sets"]) - int(a["sets"]) == 1 and int(b["sets"]) <= schedule.COMP_EXERCISE_MAX_SETS
                assert not a.get("superset") and not a.get("duration_s")
                assert not (a.get("role") == "main" and generator._reps_bounds(a.get("reps"))[1] <= 6)
                # só reforça músculo que o treino pulado treinava de verdade, em exercício que treina ESSE músculo
                assert o["muscle"] in catalog.get(a["exercise_id"])["primaryMuscles"]
                assert credit.entry_direct(target, base=True).get(o["muscle"], 0) >= 2
                assert credit.entry_direct(src).get(o["muscle"], 0) >= schedule.NEIGHBOR_DIRECT  # o treino já treinava o músculo
            per = {}
            for o in ops:
                per[o["entry_id"]] = per.get(o["entry_id"], 0) + 1
            assert all(n <= schedule.COMP_PER_SESSION for n in per.values())
            assert len(ops) == len({(o["entry_id"], o["index"]) for o in ops})  # no máximo +1 por exercício


def test_deload_week_never_reinforces(fresh_db):
    prof, _ = _setup(UL4)
    week = _week(prof)
    first = week[0]
    first["cycle"] = {**(first.get("cycle") or {}), "deload": True}
    db.save_entry(first)
    r = schedule.handle_missed(first["id"], prof, MONDAY, dry_run=True)
    ref = next(o for o in r["options"] if o["id"] == "reforcar")
    assert not ref["available"] and r["recommended"] == "seguir" and "leve" in r["why"].lower()
    with pytest.raises(ValueError):
        schedule.handle_missed(first["id"], prof, MONDAY, strategy="reforcar")


def test_two_skips_undo_independently(fresh_db):
    prof, _ = _setup({**UL4, "available_days": [0, 1, 2, 3, 4], "training_months": 40, "session_minutes": 75})
    week = _week(prof)
    before = _shape(week)
    a, b = [e for e in week if e["kind"] == "forca"][:2]

    def do(e, day):
        o = schedule.handle_missed(e["id"], prof, day, dry_run=True)
        ok = next(x for x in o["options"] if x["id"] == "reforcar")["available"]
        schedule.handle_missed(e["id"], prof, day, strategy="reforcar" if ok else "seguir")

    do(a, MONDAY)
    do(b, MONDAY + timedelta(days=1))
    schedule.undo(a["id"])  # desfazer o primeiro não apaga o ajuste do segundo
    mid = db.get_entry(b["id"])
    assert mid["status"] == "pulado" and mid.get("undo")
    schedule.undo(b["id"])
    after = _shape(_week(prof, MONDAY))
    assert {k: v[2] for k, v in after.items()} == {k: v[2] for k, v in before.items()}
    assert all(v[1] == "planejado" for v in after.values())


def test_move_validations_and_all_warnings(fresh_db):
    prof, _ = _setup(UL4)
    week = _week(prof)
    first = week[0]
    with pytest.raises(ValueError):
        schedule.move(first["id"], (MONDAY + timedelta(days=8)).isoformat(), MONDAY)  # outra semana
    with pytest.raises(ValueError):
        schedule.move(week[1]["id"], MONDAY.isoformat(), MONDAY + timedelta(days=1))  # dia que já passou
    r = schedule.move(first["id"], (MONDAY + timedelta(days=2)).isoformat(), MONDAY)  # quarta: colado no superior de quinta
    assert r["entry"]["date"] == "2026-11-18" and r["warning"] and "colado" in r["warning"]
    schedule.handle_missed(week[1]["id"], prof, MONDAY + timedelta(days=1))
    with pytest.raises(ValueError):
        schedule.move(week[1]["id"], "2026-11-21", MONDAY + timedelta(days=1))  # treino pulado não se move (desfaça antes)


# ------------------------------------------------------------- ajuste da regra antiga ---
def _legacy_week(prof):
    """Semana como a regra antiga deixava: supino transplantado no dia de perna + séries extras no outro superior."""
    week = _week(prof)
    upper_a, leg, upper_b = week[0], week[1], week[2]
    snapshot = [copy.deepcopy(e) for e in week if e["id"] != upper_a["id"]]
    idx = max(i for i, it in enumerate(leg["items"]) if (it.get("phase") or "principal") == "principal") + 1
    leg["items"].insert(idx, {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "sets": 2, "reps": "8-12", "phase": "principal",
                              "added_from": "Superior A", "notes": "do treino Superior A"})
    leg["changes"] = ["+ Supino reto 2×8-12 (do treino Superior A)"]
    it = credit.strength_items(upper_b)[0]
    it["sets"], it["added"] = int(it["sets"]) + 2, 2
    upper_b["changes"] = [f"+2 séries em {catalog.get(it['exercise_id'])['name']} (compensa Superior A)"]
    for e in (leg, upper_b):
        db.save_entry(e)
    upper_a["status"] = "pulado"
    upper_a["undo"] = {"snapshot": snapshot, "created": []}
    upper_a["skip_result"] = {"strategy": "redistribuir", "changes": ["…"], "at": "2026-11-16T09:00"}
    db.save_entry(upper_a)
    return upper_a, leg, upper_b, {e["id"]: e for e in snapshot}


def test_legacy_compensation_is_detected_and_removed(fresh_db):
    prof, _ = _setup(UL4)
    upper_a, leg, upper_b, orig = _legacy_week(prof)
    entries = _week(prof)
    leg_now = next(e for e in entries if e["id"] == leg["id"])
    # o crédito compara com o plano ORIGINAL: o supino transplantado não entra na cobertura do treino de perna
    assert credit.entry_load(leg_now, base=True) == credit.entry_load(orig[leg["id"]])
    found = schedule.legacy_compensation(entries, MONDAY)
    assert {d["entry_id"] for d in found["days"]} == {leg["id"], upper_b["id"]}
    assert any("veio do treino Superior A" in ln for d in found["days"] for ln in d["lines"])
    r = schedule.remove_compensation(MONDAY, MONDAY)
    assert r["removed_sets"] == 4
    after = {e["id"]: e for e in _week(prof)}
    for k in (leg["id"], upper_b["id"]):
        assert [(i["exercise_id"], i.get("sets")) for i in after[k]["items"]] == [(i["exercise_id"], i.get("sets")) for i in orig[k]["items"]]
        assert not any("compensa" in c or "do treino" in c for c in after[k]["changes"][:-1])
    assert schedule.legacy_compensation(list(after.values()), MONDAY) is None
    schedule.undo(upper_a["id"])  # desfazer depois da limpeza não traz o reforço de volta
    final = {e["id"]: e for e in _week(prof)}
    assert final[upper_a["id"]]["status"] == "planejado"
    assert not any(i.get("added_from") or i.get("added") for e in final.values() for i in e["items"])


def test_legacy_keep_hides_the_card_and_legacy_undo_refuses_after_newer_adjustment(fresh_db):
    prof, _ = _setup(UL4)
    upper_a, leg, upper_b, _ = _legacy_week(prof)
    clock.freeze(clock.now().replace(year=2026, month=11, day=17, hour=10))
    try:
        schedule.handle_missed(leg["id"], prof, MONDAY + timedelta(days=1))
        with pytest.raises(ValueError) as ex:
            schedule.undo(upper_a["id"])  # o retrato antigo apagaria o ajuste mais novo
        assert "Desfaça primeiro" in str(ex.value)
        schedule.undo(leg["id"])
        schedule.remove_compensation(MONDAY, MONDAY + timedelta(days=1), keep=True)
        assert schedule.legacy_compensation(_week(prof, MONDAY + timedelta(days=1)), MONDAY + timedelta(days=1)) is None
        kept = db.get_entry(leg["id"])
        assert any(i.get("added_from") for i in kept["items"])  # "manter como está" não mexe no treino
        schedule.undo(upper_a["id"])
        assert db.get_entry(upper_a["id"])["status"] == "planejado"
    finally:
        clock.unfreeze() if hasattr(clock, "unfreeze") else clock.freeze(None)


# ------------------------------------------------------------------------------- API ---
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "a.sqlite3"))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c


def test_api_skip_options_free_workout_credit_and_match(client):
    client.put("/api/profile", json={"training_months": 14, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4, 5, 6],
                                     "session_minutes": 60})
    client.post("/api/generator/apply", json={})
    sch = client.get("/api/schedule").json()
    assert sch["goals"]["muscles"] and sch["pending"] == [] and sch["legacy"] is None
    today = sch["today"]
    todays = [e for e in sch["entries"] if e["date"] == today and e["kind"] == "forca" and e["status"] == "planejado"]
    later = [e for e in sch["entries"] if e["date"] > today and e["kind"] == "forca" and e["status"] == "planejado"]
    if later:  # opções com prévia, depois a escolha
        e = later[-1]
        pre = client.post(f"/api/schedule/{e['id']}/skip", json={"dry_run": True}).json()
        assert pre["dry_run"] and pre["options"][0]["id"] == "seguir"
        assert client.get("/api/schedule").json()["entries"] == sch["entries"]
        r = client.post(f"/api/schedule/{e['id']}/skip", json={"strategy": "seguir"}).json()
        assert r["strategy"] == "seguir" and r["changes"]
        assert client.post(f"/api/schedule/{e['id']}/skip", json={}).status_code == 400  # já ajustado
        assert client.post(f"/api/schedule/{e['id']}/undo").status_code == 200
    if todays:
        e = todays[0]
        its = [i for i in e["items"] if (i.get("phase") or "principal") == "principal" and i.get("sets") and not i.get("duration_min")]
        half = its[:max(1, round(len(its) * 0.45))]
        body = {"date": today + "T18:00", "day_name": "Treino livre", "client_id": "api-free-1",
                "items": [{"exercise_id": i["exercise_id"], "sets": [{"reps": 10, "load_kg": 20}] * int(i["sets"])} for i in half]}
        saved = client.post("/api/sessions", json=body).json()
        cr = saved["credit"]
        assert cr["work_sets"] > 0 and cr["balance"]
        if cr["question"]:
            pend = client.get("/api/schedule").json()["pending"]
            assert pend and pend[0]["session_id"] == saved["id"]
            m = client.post(f"/api/sessions/{saved['id']}/match", json={"schedule_id": e["id"]}).json()
            assert m["closed"]["entry_id"] == e["id"]
            now = {x["id"]: x for x in client.get("/api/schedule").json()["entries"]}
            assert now[e["id"]]["status"] == "feito" and now[e["id"]]["partial"]
            assert client.delete(f"/api/sessions/{saved['id']}").status_code == 200
            assert {x["id"]: x for x in client.get("/api/schedule").json()["entries"]}[e["id"]]["status"] == "planejado"
    assert client.post("/api/schedule/legacy", json={"keep": True}).status_code == 200


# ------------------------------------------------------------------------- gerador ---
def test_generator_gives_the_same_plan_in_every_process():
    """A prévia e o treino aplicado têm de ser o mesmo plano (antes a divisão variava de um processo para outro)."""
    import subprocess
    import sys
    code = ("import hashlib,json;from datetime import date;from gymgarmin import generator;"
            "p=generator.generate({'training_months':72,'goal':'hipertrofia','available_days':[0,1,2,3,4,5,6],'session_minutes':90,"
            "'same_session':True,'endurance':{'sports':{'corrida':{'enabled':True,'sessions':4,'long_day':6}}}},0,today=date(2026,11,16));"
            "print(hashlib.sha1(json.dumps([[d['name'],d['weekday'],[(i['exercise_id'],i.get('sets')) for i in d['items']]] for d in p['days']]).encode()).hexdigest())")
    seen = set()
    for seed in ("1", "7", "42"):
        env = {**__import__("os").environ, "PYTHONHASHSEED": seed}
        seen.add(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout.strip())
    assert len(seen) == 1, seen


def test_complements_stay_in_the_region_of_the_day():
    """Complemento de superior não entra em dia de perna (o "supino no treino de perna"), nem o contrário."""
    for prof in ({"training_months": 14, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 60},
                 {"training_months": 72, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4, 5], "session_minutes": 90},
                 {"training_months": 40, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4], "session_minutes": 75},
                 {"training_months": 8, "goal": "emagrecimento", "available_days": [0, 1, 2, 3, 4, 5], "session_minutes": 50}):
        plan = generator.generate(prof)
        for d in plan["days"]:
            if d["kind"] != "forca":
                continue
            reg = generator._day_region(d)
            for it in d["items"]:
                if it.get("complement"):
                    prim = set(catalog.get(it["exercise_id"])["primaryMuscles"])
                    assert not (reg == "inferior" and prim & generator._UPPER_M), (d["name"], it["exercise_id"])
                    assert not (reg == "superior" and prim & generator._LOWER_M), (d["name"], it["exercise_id"])


def test_api_exercises_ranked_for_training_a_muscle(client):
    from gymgarmin import my_gym
    for muscle in ("chest", "triceps", "calves", "quadriceps"):
        r = client.get("/api/exercises", params={"muscle": muscle, "kind": "strength", "train": "true", "limit": 8}).json()
        assert len(r) >= 5
        for x in r[:5]:
            ex = catalog.get(x["id"])
            assert ex["primaryMuscles"][0] == muscle and x["id"] not in my_gym.station_ids() and ex.get("category") != "stretching", (muscle, x["id"])
