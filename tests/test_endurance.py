from datetime import date, timedelta

import pytest

from gymgarmin import db, endurance as en, generator, schedule, sports

MONDAY = date(2026, 11, 16)
RUN10 = {"enabled": True, "env": "rua", "sessions": 3,
         "goal": {"type": "prova", "distance": 10, "target": "48:00", "date": "2027-03-14"}}


def _flat(steps):
    out = []
    for s in steps:
        out += [x for _ in range(s["n"]) for x in s["steps"]] if s["kind"] == "repeat" else [s]
    return out


# ------------------------------------------------------------------ volume
def test_volume_progression_deload_and_cap():
    c = {"sessions": 4, "goal": {"type": "volume", "weekly": 30}, "current_weekly": 20}
    vols = [en.week_volume("corrida", c, "intermediario", w, MONDAY + timedelta(weeks=w))["volume"] for w in range(12)]
    assert vols[0] == 20
    normal = [v for w, v in enumerate(vols) if w % 4 != 3]
    assert all(b <= a * 1.09 + 1e-9 for a, b in zip(normal, normal[1:]))  # no máximo ~8%/semana fora das semanas leves
    assert vols[3] < vols[2] and vols[7] < vols[6]                            # semana leve a cada 4ª
    assert max(vols) <= 30 + 1e-9                                             # teto na meta
    assert en.week_volume("corrida", c, "intermediario", 11, MONDAY)["phase"] in ("manutencao", "semana_leve")


def test_race_phases():
    race = date(2027, 3, 14)
    ws_race = race - timedelta(days=race.weekday())
    ph = lambda ws: en.week_volume("corrida", RUN10, "intermediario", 10, ws)["phase"]  # noqa: E731
    assert ph(ws_race) == "prova"
    assert ph(ws_race - timedelta(weeks=1)) == "polimento"
    assert ph(ws_race + timedelta(weeks=1)) == "recuperacao"
    assert ph(ws_race - timedelta(weeks=3)) == "pico"


# ----------------------------------------------------------------- sessões
@pytest.mark.parametrize("sport,kinds,vol", [
    ("corrida", ["longo", "leve", "intervalado", "ritmo", "ritmo_prova", "teste", "ativacao", "prova"], 8),
    ("ciclismo", ["longo", "leve", "sweet_spot", "limiar", "vo2", "teste"], 1.5),
    ("natacao", ["leve", "css", "velocidade", "longo", "teste"], 2500),
    ("remo", ["leve", "intervalos_1000", "intervalos_500", "teste"], 8000),
    ("caminhada", ["leve", "longo"], 6), ("trilha", ["longo"], 3),
])
def test_sessions_are_well_formed(sport, kinds, vol):
    a = {"run_vdot": 45, "ftp_w": 240, "css_s100": 105, "row_2k_s": 440, "hrmax": 188}
    for kind in kinds:
        it = en.build_session(sport, kind, vol, RUN10 if sport == "corrida" else {}, a, "intermediario")
        end = it["endurance"]
        assert end["steps"] and end["dur_s"] > 0 and it["exercise_id"]
        for st in _flat(end["steps"]):
            assert st["dur_s"] > 0
            if st["kind"] not in ("rest",):
                assert st.get("target"), (sport, kind, st)
        if kind not in ("teste", "prova"):
            assert end["hard"] == (kind in ("intervalado", "ritmo", "ritmo_prova", "sweet_spot", "limiar", "vo2", "css",
                                            "velocidade", "intervalos_1000", "intervalos_500")), kind


def test_targets_follow_anchor_or_fall_back_to_hr():
    run = en.build_session("corrida", "intervalado", 8, {}, {"run_vdot": 50}, "intermediario")
    tiro = _flat(run["endurance"]["steps"])[1]
    assert tiro["target"]["type"] == "pace" and 228 <= tiro["target"]["fast_s_km"] <= 240
    bike = en.build_session("ciclismo", "limiar", 1.25, {}, {"hrmax": 185}, "intermediario")
    assert _flat(bike["endurance"]["steps"])[1]["target"]["type"] == "hr"
    gp = en.build_session("corrida", "ritmo_prova", 9, RUN10, {"run_vdot": 42}, "intermediario")
    rp = [s for s in _flat(gp["endurance"]["steps"]) if s["kind"] == "interval"][0]
    assert rp["target"]["fast_s_km"] == 283 and rp["target"]["slow_s_km"] == 293  # 4:48/km ± 5 s


def test_week_plan_polarized_and_test_when_anchor_missing():
    prof = {"endurance": {"sports": {"corrida": {**RUN10, "sessions": 5}, "ciclismo": {"enabled": True, "sessions": 3},
                                     "natacao": {"enabled": True, "sessions": 2}}}}
    wp = en.week_plan(prof, MONDAY, MONDAY, "avancado")
    hard = [x for d in wp["sports"].values() for x in d["sessions"] if x["endurance"]["hard"] and x["endurance"]["type"] != "teste"]
    assert len(hard) <= 3
    tests = [x for d in wp["sports"].values() for x in d["sessions"] if x["endurance"]["type"] == "teste"]
    assert len(tests) == 1  # no máximo um teste por semana
    prof["endurance"]["anchors"] = {"run_vdot": 45, "ftp_w": 240, "css_s100": 105}
    prof["endurance"]["anchors_meta"] = {k: {"date": MONDAY.isoformat()} for k in ("run_vdot", "ftp_w", "css_s100")}
    wp2 = en.week_plan(prof, MONDAY, MONDAY, "avancado")
    assert not [x for d in wp2["sports"].values() for x in d["sessions"] if x["endurance"]["type"] == "teste"]


def test_event_config_and_brick():
    prof = {"endurance": {"event": {"type": "tri_olimpico", "date": "2027-05-02", "target": "2:40:00"},
                          "anchors": {"run_vdot": 45, "ftp_w": 230, "css_s100": 110}}, "weight_kg": 75}
    cfg = en.config(prof)
    assert set(cfg) == {"natacao", "ciclismo", "corrida"}
    build = date(2027, 3, 22)  # semanas de construção
    wp = en.week_plan(prof, build, MONDAY, "intermediario")
    assert wp["bricks"] and wp["bricks"][0]["items"][0]["sport"] == "corrida"
    pred = en.predictions(prof)["event"]
    assert pred["predicted"] and not pred["missing_anchors"] and pred["gap_s"] is not None
    assert 30 < en.bike_speed_kmh(230 * 0.85, 75) < 36


# ----------------------------------------------------------------- dias
def test_allocate_long_on_weekend_quality_apart_strength_off_hard_days():
    slots = [{"key": "corrida:0", "sport": "corrida", "role": "longo", "minutes": 80},
             {"key": "corrida:1", "sport": "corrida", "role": "qualidade", "minutes": 50},
             {"key": "corrida:2", "sport": "corrida", "role": "leve", "minutes": 40},
             {"key": "natacao:0", "sport": "natacao", "role": "longo", "minutes": 50}]
    al = en.allocate(slots, [0, 1, 2, 3, 4, 5, 6], 3)
    assert al["corrida:0"] in (5, 6)
    q, lg = al["corrida:1"], al["corrida:0"]
    assert min((q - lg) % 7, (lg - q) % 7) >= 2
    assert q not in al["_strength"] and lg not in al["_strength"]
    assert len(al["_strength"]) == 3


def test_generate_hybrid_with_endurance():
    prof = {"training_months": 24, "goal": "hibrido", "session_minutes": 60, "available_days": list(range(7)),
            "endurance": {"sports": {"corrida": RUN10, "natacao": {"enabled": True, "sessions": 2}},
                          "anchors": {"run_vdot": 42, "css_s100": 115}}}
    plan = generator.generate(prof, today=MONDAY)
    kinds = [d["kind"] for d in plan["days"]]
    assert kinds.count("forca") == 3 and kinds.count("resistencia") == 5
    assert plan["endurance_plan"]["start"] == MONDAY.isoformat()
    for d in plan["days"]:
        if d["kind"] == "forca":
            assert d["time"]["total"] <= 60.5
            # perna pesada nunca na véspera do longão/forte de corrida
            if d["template"] in generator.HEAVY_LEGS:
                nxt = [x for x in plan["days"] if x["weekday"] == (d["weekday"] + 1) % 7 and x["kind"] == "resistencia"
                       and x["endurance_slot"]["sport"] == "corrida" and x["endurance_slot"]["role"] != "leve"]
                assert not nxt
    assert plan["generator"]["targets"]["quadriceps"] < generator.weekly_targets("avancado", "hibrido")["quadriceps"]
    assert any("polarizada" in r for r in plan["rationale"])


# ---------------------------------------------------------------- agenda
@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "e.sqlite3"))
    db.init()
    yield


def _setup(profile):
    db.save_profile(profile)
    plan = generator.generate(db.get_profile(), today=MONDAY)
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    return db.get_profile(), saved


def test_schedule_materializes_weeks_and_race(fresh_db):
    prof, plan = _setup({"training_months": 18, "goal": "hibrido", "session_minutes": 60, "available_days": list(range(7)),
                         "endurance": {"sports": {"corrida": {**RUN10, "goal": {**RUN10["goal"], "date": "2026-12-20"}}},
                                       "anchors": {"run_vdot": 42}, "anchors_meta": {"run_vdot": {"date": "2026-11-10"}}}})
    w0 = schedule.build_week(plan, prof, MONDAY)
    w1 = schedule.build_week(plan, prof, MONDAY + timedelta(weeks=1))
    km = lambda w: sum(i["endurance"].get("dist_m") or 0 for e in w for i in e["items"] if i.get("endurance")) / 1000  # noqa: E731
    assert km(w1) > km(w0)
    race_ws = MONDAY + timedelta(weeks=4)  # 14/12 → prova em 20/12
    wr = schedule.build_week(plan, prof, race_ws)
    race = [e for e in wr if e.get("race")]
    assert race and race[0]["date"] == "2026-12-20" and "PROVA" in race[0]["day_name"]
    assert len([e for e in wr if e["date"] == "2026-12-20"]) == 1


def test_doubles_share_date_and_sync_by_kind(fresh_db):
    prof, plan = _setup({"training_months": 18, "goal": "hibrido", "session_minutes": 60, "available_days": [0, 2, 4, 5, 6],
                         "endurance": {"sports": {"corrida": {**RUN10, "sessions": 3}, "natacao": {"enabled": True, "sessions": 2}},
                                       "anchors": {"run_vdot": 42, "css_s100": 115},
                                       "anchors_meta": {"run_vdot": {"date": "2026-11-10"}, "css_s100": {"date": "2026-11-10"}}}})
    entries = [db.save_entry(e) for e in schedule.build_week(plan, prof, MONDAY)]
    by_date = {}
    for e in entries:
        by_date.setdefault(e["date"], []).append(e)
    double = next(v for v in by_date.values() if len(v) == 2)
    day = double[0]["date"]
    strength = next(e for e in double if e["kind"] == "forca")
    other = next(e for e in double if e["kind"] == "resistencia")
    db.save_session({"date": day + "T07:00", "items": [{"exercise_id": strength["items"][-1]["exercise_id"],
                                                         "sets": [{"reps": 10, "load_kg": 20}]}]})
    st = {e["id"]: e["status"] for e in schedule.sync_status(entries, schedule._sessions_in_week(MONDAY), MONDAY)}
    assert st[strength["id"]] == "feito" and st[other["id"]] == "planejado"


def test_missed_long_run_moves_or_compensates(fresh_db):
    prof, plan = _setup({"training_months": 18, "goal": "hibrido", "session_minutes": 60, "available_days": [0, 1, 2, 3, 4, 5, 6],
                         "endurance": {"sports": {"corrida": {**RUN10, "sessions": 3}},
                                       "anchors": {"run_vdot": 42}, "anchors_meta": {"run_vdot": {"date": "2026-11-10"}}}})
    for e in schedule.build_week(plan, prof, MONDAY):
        db.save_entry(e)
    entries = db.list_schedule(MONDAY.isoformat(), (MONDAY + timedelta(days=6)).isoformat())
    long_run = next(e for e in entries if e.get("sport") == "corrida" and schedule._end_item(e)["endurance"]["role"] == "longo")
    r = schedule.handle_missed(long_run["id"], prof, today=MONDAY)
    assert r["strategy"] in ("mover", "redistribuir")
    easy = next(e for e in entries if e.get("sport") == "corrida" and schedule._end_item(e)["endurance"]["role"] == "leve")
    r2 = schedule.handle_missed(easy["id"], prof, today=MONDAY)
    assert r2["strategy"] == "dispensar"
    goals = schedule.week_goals(db.list_schedule(MONDAY.isoformat(), (MONDAY + timedelta(days=6)).isoformat()), prof, MONDAY)
    assert goals["endurance"]["corrida"]["unit"] == "km" and goals["endurance"]["corrida"]["planned"] > 0


def test_predict_and_anchor_math_consistency():
    vd = sports.vdot(10, 48 * 60)
    assert sports.predict_time(vd, 10) == pytest.approx(48 * 60, abs=5)


def test_endurance_fit_and_connect_roundtrip():
    from garmin_fit_sdk import Decoder, Stream

    from gymgarmin import garmin_export as ge

    a = {"run_vdot": 45, "ftp_w": 240, "css_s100": 105, "row_2k_s": 440, "hrmax": 188}
    cases = [("corrida", "intervalado", 8, {}, "running", "speed"), ("ciclismo", "limiar", 1.25, {}, "cycling", "power"),
             ("natacao", "css", 2500, {"env": "piscina", "pool_m": 25}, "swimming", "swim_stroke"),
             ("remo", "intervalos_500", 8000, {}, "rowing", "speed"), ("trilha", "longo", 2, {}, "hiking", "heart_rate")]
    for sport, kind, vol, c, fit_sport, tgt in cases:
        it = en.build_session(sport, kind, vol, c, a, "intermediario")
        day = {"name": f"{sport} {kind}", "items": [it]}
        msgs, errs = Decoder(Stream.from_byte_array(bytearray(ge.to_fit(day, "T")))).read()
        assert not errs
        assert msgs["workout_mesgs"][0]["sport"] == fit_sport
        steps = msgs["workout_step_mesgs"]
        main = steps[1] if len(steps) > 1 else steps[0]
        assert main["target_type"] == tgt, (sport, main)
        if sport == "natacao":
            assert msgs["workout_mesgs"][0]["pool_length"] == 25.0
        if sport == "corrida":
            assert main["duration_distance"] == 1000.0 and 3.7 < main["custom_target_speed_low"] < main["custom_target_speed_high"] < 4.2
        if sport == "ciclismo":
            assert main["custom_target_power_low"] == 1228  # 228 W (+1000)
        cj = ge.to_connect_json(day, "T")
        assert cj["workoutSegments"][0]["workoutSteps"]
    brick = ge.to_connect_json({"name": "Brick", "items": [en.build_session("ciclismo", "longo", 2, {}, a, "intermediario"),
                                                            en.build_session("corrida", "leve", 5, {}, a, "intermediario")]})
    assert brick["sportType"]["sportTypeKey"] == "multi_sport" and len(brick["workoutSegments"]) == 2


# ---------------------------------------------------- regressões das simulações
def test_long_is_biggest_and_volume_matches():
    a = {"run_vdot": 45, "ftp_w": 220}
    for sport, vols in (("corrida", [8, 15, 25, 40, 60]), ("ciclismo", [1.5, 3, 5, 9]), ("remo", [10000, 26000])):
        for v in vols:
            c = {"sessions": 3, "goal": {"type": "volume", "weekly": v}, "current_weekly": v}
            prof = {"endurance": {"sports": {sport: {"enabled": True, **c}}, "anchors": a}}
            d = en.week_plan(prof, MONDAY, MONDAY, "intermediario")["sports"][sport]
            vol = lambda x: en._vol(x)  # noqa: E731
            longs = [x for x in d["sessions"] if x["endurance"]["role"] == "longo"]
            others = [x for x in d["sessions"] if x["endurance"]["role"] != "longo"]
            if longs and others:
                assert vol(longs[0]) >= max(vol(o) for o in others) * 0.95, (sport, v, [(x["endurance"]["title"], vol(x)) for x in d["sessions"]])
            built = sum(vol(x) for x in d["sessions"])
            assert 0.75 <= built / d["volume"] <= 1.3, (sport, v, d["volume"], built)


def test_low_volume_uses_fewer_sessions_and_single_session_is_continuous():
    prof = {"endurance": {"sports": {"natacao": {"enabled": True, "sessions": 5, "current_weekly": 2500}}}}
    d = en.week_plan(prof, MONDAY + timedelta(weeks=1), MONDAY, "intermediario")["sports"]["natacao"]
    assert len(d["sessions"]) < 5 and all(en._vol(x) >= 1000 for x in d["sessions"])
    one = {"endurance": {"sports": {"ciclismo": {"enabled": True, "sessions": 1}}}}
    d1 = en.week_plan(one, MONDAY, MONDAY, "intermediario")["sports"]["ciclismo"]
    assert len(d1["sessions"]) == 1 and d1["sessions"][0]["endurance"]["type"] == "longo"  # nem teste nem tiro


def test_taper_and_deload_still_reduce_when_capped():
    c = {"sessions": 2, "current_weekly": 30, "goal": {"type": "prova", "distance": 21.1, "date": "2026-12-30"}}
    prof = {"endurance": {"sports": {"corrida": {"enabled": True, **c}}, "anchors": {"run_vdot": 40}}}
    vols = {w: en.week_plan(prof, MONDAY + timedelta(weeks=w), MONDAY, "intermediario")["sports"]["corrida"] for w in range(7)}
    assert vols[3]["phase"] == "semana_leve" and vols[3]["volume"] < vols[2]["volume"]
    taper = [v for v in vols.values() if v["phase"] == "polimento"]
    assert taper and all(t["volume"] < vols[2]["volume"] * 0.9 for t in taper)


def test_never_more_than_two_sessions_per_day_and_warns():
    prof = {"training_months": 12, "goal": "hibrido", "session_minutes": 60, "available_days": [1, 2, 4],
            "endurance": {"sports": {"caminhada": {"enabled": True, "sessions": 3}, "natacao": {"enabled": True, "sessions": 5}}}}
    plan = generator.generate(prof, today=MONDAY)
    from collections import Counter

    assert max(Counter(d["weekday"] for d in plan["days"]).values()) <= 2
    assert plan["endurance_plan"]["sessions"] and any("máx. 2 treinos por dia" in r for r in plan["rationale"])


def test_impossible_target_is_ignored_with_warning():
    c = {"enabled": True, "sessions": 3, "goal": {"type": "prova", "distance": 10, "target": "8:00", "date": "2027-01-10"}}
    assert en._goal_pace("corrida", c) is None and en.goal_warning("corrida", c)
    plan = generator.generate({"training_months": 12, "goal": "hibrido", "available_days": list(range(7)),
                               "endurance": {"sports": {"corrida": c}}}, today=MONDAY)
    assert any("fora do possível" in r for r in plan["rationale"])


def test_short_sessions_scale_with_athlete_speed():
    fast = {"run_vdot": 65}
    assert en.min_session("corrida", fast) > 4  # 20 min no ritmo leve de quem tem VDOT 65
    assert en.session_max("natacao", {"css_s100": 150}) < 6000  # natação lenta: limitada a ~110 min
