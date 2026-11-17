from datetime import date, timedelta

import pytest

from gymgarmin import catalog, db, generator, progression, schedule

MONDAY = date(2026, 11, 16)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "s.sqlite3"))
    db.init()
    yield


def _setup(profile: dict):
    db.save_profile(profile)
    plan = generator.generate(db.get_profile())
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    return db.get_profile(), saved


# -------------------------------------------------------------- gerador ---
@pytest.mark.parametrize("months,goal,days,minutes", [
    (2, "hipertrofia", [0, 2, 4], 45), (12, "hibrido", [0, 1, 2, 3, 4, 5], 60),
    (36, "forca", [0, 1, 3, 4, 5], 75), (80, "hipertrofia", [0, 1, 2, 3, 4, 5], 90),
    (10, "emagrecimento", [1, 3], 50), (18, "condicionamento", [0, 2, 4, 6], 45), (5, "saude", [0, 3], 40),
])
def test_generator_respects_time_and_structure(months, goal, days, minutes):
    p = generator.generate({"training_months": months, "goal": goal, "available_days": days, "session_minutes": minutes,
                            "limitations": [{"key": "joelho", "severity": "evitar"}]})
    assert sorted(d["weekday"] for d in p["days"]) == sorted(days)
    for d in p["days"]:
        assert d["time"]["total"] <= minutes + 0.5, (d["name"], d["time"])
        phases = [i.get("phase") for i in d["items"]]
        if d["kind"] == "forca":
            assert phases[0] == "preparo" and "pos" in phases  # preparo antes, alongamento depois
            assert phases.index("pos") > max(i for i, x in enumerate(phases) if x == "principal")
        for it in d["items"]:
            ex = catalog.get(it["exercise_id"])
            assert ex is not None
            from gymgarmin.limitations import check_exercise
            assert check_exercise(ex, [{"key": "joelho", "severity": "evitar"}])["status"] != "evitar"
    tier = generator.tier_for(months)
    n_strength = sum(1 for d in p["days"] if d["kind"] == "forca")
    assert n_strength <= generator.TIERS[tier]["max_strength_days"]
    assert p["rationale"]


def test_beginner_prefers_machines_advanced_free_weights():
    beg = generator.generate({"training_months": 2, "available_days": [0, 2, 4], "session_minutes": 60})
    adv = generator.generate({"training_months": 48, "available_days": [0, 2, 4], "session_minutes": 75})
    eq = lambda p: [catalog.get(i["exercise_id"])["equipment"] for d in p["days"] for i in d["items"]  # noqa: E731
                    if i.get("role") == "main"]
    assert eq(beg).count("barbell") < eq(adv).count("barbell")


def test_hybrid_no_hiit_before_leg_day():
    p = generator.generate({"training_months": 30, "goal": "hibrido", "available_days": list(range(7)), "session_minutes": 60})
    legs = {d["weekday"] for d in p["days"] if d.get("template") in generator.HEAVY_LEGS}
    for d in p["days"]:
        if "HIIT" in d["name"]:
            assert (d["weekday"] + 1) % 7 not in legs


def test_hypertension_limits_cardio_zone():
    p = generator.generate({"training_months": 20, "goal": "condicionamento", "available_days": [0, 1, 2, 3, 4, 5],
                            "limitations": [{"key": "hipertensao"}]})
    zones = [i.get("zone") or (i.get("intervals") or {}).get("work_zone") for d in p["days"] for i in d["items"]
             if catalog.get(i["exercise_id"])["kind"] == "cardio"]
    assert zones and max(z for z in zones if z) <= 3


# ----------------------------------------------------------- progressão ---
def test_progression_double_progression():
    item = {"exercise_id": "Barbell_Squat", "reps": "6-10"}
    top = {"sets": [{"reps": 10, "load_kg": 100, "rpe": 8}] * 3}
    assert progression.suggest(item, top)["load_kg"] == 105
    low = {"sets": [{"reps": 5, "load_kg": 100, "rpe": 10}] * 3}
    assert progression.suggest(item, low)["load_kg"] == 95
    mid = {"sets": [{"reps": 8, "load_kg": 100, "rpe": 8}] * 3}
    assert progression.suggest(item, mid)["load_kg"] == 100
    assert progression.suggest(item, top, deload=True)["load_kg"] == 90


def test_cycle_deload():
    pos = progression.cycle_position("2026-11-16", MONDAY + timedelta(weeks=4), 5)
    assert pos["week"] == 5 and pos["deload"]
    items = [{"exercise_id": "Barbell_Squat", "sets": 4, "role": "main", "phase": "principal"}]
    assert progression.apply_cycle(items, pos, 1)[0]["sets"] == 2  # deload ~55% das séries
    pos3 = progression.cycle_position("2026-11-16", MONDAY + timedelta(weeks=2), 5)
    assert progression.apply_cycle(items, pos3, 1)[0]["sets"] == 5


# --------------------------------------------------------------- agenda ---
def test_skip_moves_to_free_day(fresh_db):
    prof, plan = _setup({"training_months": 14, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4, 5],
                         "session_minutes": 60})
    week = schedule.ensure_week(prof, MONDAY, MONDAY)
    kinds = {e["date"]: e["kind"] for e in week}
    assert list(kinds.values()).count("forca") == 4 and "cardio" in kinds.values()
    first = next(e for e in week if e["kind"] == "forca")
    res = schedule.handle_missed(first["id"], prof, MONDAY)
    assert res["strategy"] == "mover", res
    after = schedule.ensure_week(prof, MONDAY, MONDAY)
    assert any(e.get("moved_from") == first["date"] for e in after)
    schedule.undo(first["id"])
    restored = schedule.ensure_week(prof, MONDAY, MONDAY)
    assert {e["date"]: e["status"] for e in restored} == {e["date"]: e["status"] for e in week}


def test_skip_redistributes_when_no_free_day(fresh_db):
    prof, plan = _setup({"training_months": 14, "goal": "hipertrofia", "available_days": [0, 2, 4],
                         "session_minutes": 75})
    week = schedule.ensure_week(prof, MONDAY, MONDAY)
    before = {e["id"]: sum(i.get("sets") or 0 for i in e["items"]) for e in week}
    first = week[0]
    res = schedule.handle_missed(first["id"], prof, MONDAY)
    assert res["strategy"] == "redistribuir"
    after = {e["id"]: e for e in schedule.ensure_week(prof, MONDAY, MONDAY)}
    added = sum(sum(i.get("sets") or 0 for i in after[k]["items"]) - before[k] for k in before if k != first["id"])
    assert added > 0 and res["changes"]
    for k, e in after.items():
        if k != first["id"]:
            assert generator.time_breakdown(e["items"])["total"] <= 75 * 1.15 + 0.5
    assert after[first["id"]]["status"] == "pulado"


def test_missed_detection_and_done(fresh_db):
    prof, plan = _setup({"training_months": 14, "available_days": [0, 2, 4], "session_minutes": 60})
    week = schedule.ensure_week(prof, MONDAY, MONDAY)
    db.save_session({"date": week[1]["date"] + "T18:00", "schedule_id": week[1]["id"],
                     "items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 8, "load_kg": 60}]}]})
    later = schedule.ensure_week(prof, MONDAY, MONDAY + timedelta(days=3))
    st = {e["id"]: e["status"] for e in later}
    assert st[week[0]["id"]] == "perdido" and st[week[1]["id"]] == "feito"


# ------------------------------------------------- regressões das simulações de musculação
def test_double_progression_moves_at_rpe9_and_near_top():
    item = {"exercise_id": "Barbell_Squat", "reps": "6-10"}
    top9 = {"sets": [{"reps": 10, "load_kg": 100, "rpe": 9}] * 3}
    assert progression.suggest(item, top9)["load_kg"] == 105
    near = {"sets": [{"reps": 10, "load_kg": 100, "rpe": 8}, {"reps": 10, "load_kg": 100, "rpe": 9}, {"reps": 9, "load_kg": 100, "rpe": 9.5}]}
    assert progression.suggest(item, near)["load_kg"] > 100  # faltou 1 rep no total: sobe
    top95 = {"sets": [{"reps": 10, "load_kg": 100, "rpe": 9.5}] * 3}
    assert 100 < progression.suggest(item, top95)["load_kg"] < 105  # meio incremento
    top10 = {"sets": [{"reps": 10, "load_kg": 100, "rpe": 10}] * 3}
    assert progression.suggest(item, top10)["load_kg"] == 100
    light = {"exercise_id": "Side_Lateral_Raise", "reps": "10-15"}
    note = progression.suggest(light, {"sets": [{"reps": 15, "load_kg": 6, "rpe": 8}] * 3})["note"]
    assert "salto" in note


def test_cycle_ramp_respects_time_and_mrv_and_deload_halves():
    items = [{"exercise_id": "Barbell_Squat", "sets": 4, "reps": "6-10", "rest_s": 150, "role": "main", "phase": "principal"},
             {"exercise_id": "Romanian_Deadlift", "sets": 3, "reps": "8-12", "rest_s": 120, "role": "sec", "phase": "principal"}]
    w3 = {"week": 3, "length": 4, "cycle": 1, "deload": False}
    budget = generator.time_breakdown(items)["total"] + 1
    out = progression.apply_cycle(items, w3, 1, budget_min=budget)
    assert generator.time_breakdown(out)["total"] <= budget
    out2 = progression.apply_cycle(items, w3, 1, allowance={"quadriceps": 0.5})
    assert out2[0]["sets"] == 4  # sem espaço no MRV do quadríceps
    dl = progression.apply_cycle(items, {"week": 4, "length": 4, "cycle": 1, "deload": True}, 1)
    assert sum(i["sets"] for i in dl) <= 0.6 * sum(i["sets"] for i in items)


def test_supersets_paired_timed_and_exported():
    from garmin_fit_sdk import Decoder, Stream

    from gymgarmin import garmin_export as ge

    p = generator.generate({"training_months": 18, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 60})
    pairs = [(d, k) for d in p["days"] for k, it in enumerate(d["items"]) if it.get("superset") == "A"]
    assert pairs
    d, k = pairs[0]
    a, b = d["items"][k], d["items"][k + 1]
    assert b.get("superset") == "B" and a["rest_s"] == generator.SUPERSET_SWITCH_S
    sep = generator.item_time({**a, "superset": None, "rest_s": b["rest_s"]})["total"]
    assert generator.item_time(a)["total"] < sep  # bi-set economiza o descanso do 1º
    msgs, errs = Decoder(Stream.from_byte_array(bytearray(ge.to_fit(d, "T")))).read()
    assert not errs
    cj = ge.to_connect_json(d, "T")
    assert any(s.get("type") == "RepeatGroupDTO" and len(s["workoutSteps"]) == 3 for s in cj["workoutSegments"][0]["workoutSteps"])
    for day in p["days"]:  # nenhum bi-set órfão
        for j, it in enumerate(day["items"]):
            if it.get("superset") == "A":
                assert day["items"][j + 1].get("superset") == "B"


def test_three_day_split_trains_most_muscles_twice():
    p = generator.generate({"training_months": 40, "goal": "hipertrofia", "available_days": [0, 2, 4], "session_minutes": 60})
    assert [d["template"] for d in p["days"]] != ["PUSH", "PULL", "LEGS"]
    p2 = generator.generate({"training_months": 40, "goal": "hipertrofia", "available_days": [0, 1, 2], "session_minutes": 60})
    assert sorted(d["template"] for d in p2["days"]) == ["LEGS", "PULL", "PUSH"]  # dias seguidos: PPL evita colar


def test_deadlift_counts_for_posterior_chain():
    assert set(catalog.get("Barbell_Deadlift")["primaryMuscles"]) >= {"hamstrings", "glutes"}


def test_pull_not_far_above_push():
    for days in ([1, 4], [0, 1, 3, 4], [5, 6]):
        p = generator.generate({"training_months": 9, "goal": "hipertrofia", "available_days": days, "session_minutes": 60})
        assert generator._push_pull(generator._weekly_volume(p["days"])) <= 1.65


# ------------------------------------------------------- agenda e reajustes ---
def _per_day(entries):
    from collections import Counter

    return Counter(e["date"] for e in entries if e["status"] in ("planejado", "feito"))


def test_missed_adjustments_never_stack_three_sessions(fresh_db):
    """Simulação: faltas seguidas num plano misto nunca deixam um dia com 3+ treinos."""
    prof, _ = _setup({"training_months": 18, "goal": "hibrido", "available_days": [0, 1, 3, 4, 6],
                      "session_minutes": 45, "age": 34,
                      "endurance": {"sports": {"corrida": {"enabled": True, "sessions": 3},
                                               "ciclismo": {"enabled": True, "sessions": 3}},
                                    "anchors": {"run_vdot": 45, "ftp_w": 220},
                                    "anchors_meta": {k: {"date": MONDAY.isoformat()} for k in ("run_vdot", "ftp_w")}}})
    week = schedule.ensure_week(prof, MONDAY, MONDAY)
    assert max(_per_day(week).values()) <= 2
    for day in range(6):
        today = MONDAY + timedelta(days=day)
        for e in [x for x in schedule.ensure_week(prof, MONDAY, today) if x["date"] == today.isoformat()
                  and x["status"] == "planejado"]:
            schedule.handle_missed(e["id"], prof, today)
            assert max(_per_day(schedule.ensure_week(prof, MONDAY, today)).values(), default=0) <= 2, e["day_name"]


def test_manual_move_warns_when_day_gets_crowded(fresh_db):
    prof, _ = _setup({"training_months": 18, "goal": "hibrido", "available_days": [0, 1, 2, 3, 4, 5],
                      "session_minutes": 60,
                      "endurance": {"sports": {"corrida": {"enabled": True, "sessions": 3},
                                               "ciclismo": {"enabled": True, "sessions": 3}},
                                    "anchors": {"run_vdot": 45, "ftp_w": 220}}})
    week = schedule.ensure_week(prof, MONDAY, MONDAY)
    per = _per_day(week)
    full = next((d for d, n in per.items() if n == 2), None)
    assert full, per
    src = next(e for e in week if e["date"] != full and e["status"] == "planejado")
    res = schedule.move(src["id"], full)
    assert res["warning"] and "3 treinos" in res["warning"]
