"""Gerador de resistência ao longo do bloco — regressões de scripts/simulate_endurance_block.py."""
from datetime import date, timedelta

from gymgarmin import endurance as en, sports as sp

START = date(2026, 11, 16)


def _prof(goal_dist=21.1, weeks=16, current=None):
    c = {"enabled": True, "env": "rua", "sessions": 4,
         "goal": {"type": "prova", "distance": goal_dist, "date": (START + timedelta(weeks=weeks, days=6)).isoformat()}}
    if current:
        c["current_weekly"] = current
    return {"endurance": {"sports": {"corrida": c}, "anchors": {"run_vdot": 45}}}


def _runs(km_per_week, weeks, end):
    """3 corridas por semana nas `weeks` semanas (de segunda a domingo) que terminam antes de `end`."""
    out = []
    for w in range(weeks):
        monday = end - timedelta(weeks=w + 1)
        for k in (0, 2, 4):
            d = monday + timedelta(days=k)
            out.append({"date": d.isoformat() + "T07:00", "items": [{"exercise_id": "corrida_rua", "sport": "corrida",
                                                                      "distance_km": km_per_week / 3, "duration_min": 30}]})
    return out


def test_back_from_two_sick_weeks_restarts_lower_not_higher():
    prof = _prof(current=30)
    ws = START + timedelta(weeks=8)
    planned = en.week_plan(prof, ws, START, "intermediario")["sports"]["corrida"]["volume"]
    # treinava ~30 km/semana, depois 2 semanas sem correr (doença)
    sessions = [s for s in _runs(30, 6, ws - timedelta(weeks=2))]
    done = en.done_volumes(sessions, prof, ws)
    d = en.week_plan(prof, ws, START, "intermediario", done=done)["sports"]["corrida"]
    assert d["adjusted"] and d["volume"] <= 30 * 0.7 < planned
    assert "ajustado" in d["sessions"][0]["endurance"]["note"]


def test_normal_misses_do_not_throttle_progression():
    prof = _prof(current=30)
    ws = START + timedelta(weeks=6)
    planned = en.week_plan(prof, ws, START, "intermediario")["sports"]["corrida"]["volume"]
    sessions = _runs(planned * 0.85, 4, ws)  # cumpre ~85% do plano
    done = en.done_volumes(sessions, prof, ws)
    assert en.week_plan(prof, ws, START, "intermediario", done=done)["sports"]["corrida"]["volume"] == planned


def test_plan_never_cuts_below_current_volume_for_short_race():
    prof = _prof(goal_dist=5, weeks=12, current=50)
    vols = [en.week_plan(prof, START + timedelta(weeks=w), START, "avancado")["sports"]["corrida"] for w in range(0, 9)]
    build = [v["volume"] for v in vols if v["phase"] not in ("semana_leve",)]
    assert min(build) >= 40  # antes: meta de 5 km cortava 50 km/semana para ~25 (o longo de 5 km tem teto)


def test_goal_prediction_is_more_conservative_for_fast_runners():
    race = (START + timedelta(weeks=12)).isoformat()
    fast = {"endurance": {"sports": {"corrida": {"enabled": True, "goal": {"type": "prova", "distance": 10, "date": race,
                                                                          "target": sp.fmt_duration(sp.predict_time(58.5, 10))}}},
                          "anchors": {"run_vdot": 56}}}
    g = en.predictions(fast)["sports"]["corrida"]["goal"]
    assert g["status"] == "ambicioso"  # +2,5 VDOT em 12 semanas para quem já tem 56 é muito (antes: "possível")


def test_same_session_fits_strength_and_run_in_daily_minutes():
    """90 min por dia, musculação + corrida no mesmo treino: cada dia cabe nos 90, mais dias de força,
    corrida leve depois de treino de superior e nunca perna em dois dias seguidos."""
    from gymgarmin import generator

    base = {"age": 34, "weight_kg": 98, "training_months": 18, "goal": "hipertrofia", "session_minutes": 90,
            "available_days": [0, 1, 2, 3, 4, 5],
            "endurance": {"anchors": {"run_vdot": 33}, "sports": {"corrida": {"enabled": True, "env": "esteira", "sessions": 4}}}}
    sep = generator.generate({**base, "same_session": False}, 0)
    comb = generator.generate({**base, "same_session": True}, 0)
    per_day = {}
    for d in comb["days"]:
        per_day[d["weekday"]] = per_day.get(d["weekday"], 0) + d["time"]["total"]
    assert max(per_day.values()) <= 92
    n_str = lambda p: sum(1 for d in p["days"] if d["kind"] == "forca")  # noqa: E731
    assert n_str(comb) > n_str(sep)
    runs_after = [d for d in comb["days"] if d.get("same_session")]
    assert len(runs_after) >= 2 and all("mesmo treino" in d["items"][0]["notes"] for d in runs_after)
    legs = sorted(d["weekday"] for d in comb["days"] if d["kind"] == "forca" and d["template"] in generator.HEAVY_LEGS)
    assert not any(b - a == 1 for a, b in zip(legs, legs[1:]))
    assert any("mesmo treino" in r for r in comb["rationale"])


def test_same_session_week_stays_within_daily_minutes_as_runs_grow(tmp_path, monkeypatch):
    """A agenda recalcula a corrida a cada semana (progressão): a musculação do dia encolhe junto."""
    from datetime import date

    from gymgarmin import generator, schedule

    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    prof = {"age": 34, "weight_kg": 98, "training_months": 72, "goal": "hipertrofia", "session_minutes": 90,
            "available_days": [0, 1, 2, 3, 4, 5, 6], "same_session": True,
            "endurance": {"sports": {"corrida": {"enabled": True, "env": "esteira", "sessions": 4, "long_day": 6}}}}
    plan = generator.generate(prof, 0, recent={"corrida": 16.0}, today=date(2026, 11, 16))
    plan["id"] = 1
    plan["generator"]["cycle_start"] = "2026-11-16"
    assert plan["generator"]["same_session"]
    for ws in (date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 30)):
        tot = {}
        for e in schedule.build_week(plan, prof, ws):
            tot[e["date"]] = tot.get(e["date"], 0) + generator.time_breakdown(e["items"])["total"]
        assert max(tot.values()) <= 92, (ws, tot)
    # corrida que não cabe nem com a força no mínimo: passa do tempo (com aviso), mas a força fica >= 40 min
    long_prof = {**prof, "endurance": {"sports": {"corrida": {**prof["endurance"]["sports"]["corrida"], "current_weekly": 45}}}}
    plan2 = generator.generate(long_prof, 0, recent={"corrida": 45.0}, today=date(2026, 11, 16))
    plan2["id"] = 2
    plan2["generator"]["cycle_start"] = "2026-11-16"
    for e in schedule.build_week(plan2, long_prof, date(2026, 11, 23)):
        if e["kind"] == "forca":
            assert generator.time_breakdown(e["items"])["total"] >= generator.STRENGTH_MIN_COMBINED - 3
