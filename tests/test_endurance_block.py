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
