"""Corrida e outras modalidades: idade, corre-caminha, subidas e limite de 10% por treino."""
from datetime import date, timedelta

from gymgarmin import endurance as en

MONDAY = date(2026, 11, 16)


def _prof(age=None, **run):
    c = {"enabled": True, "env": "rua", "sessions": 4, "current_weekly": 25, **run}
    return {"age": age, "endurance": {"sports": {"corrida": c}, "anchors": {"run_vdot": 45}}}


def _flat(steps):
    out = []
    for s in steps:
        out += [x for _ in range(s["n"]) for x in s["steps"]] if s["kind"] == "repeat" else [s]
    return out


def test_masters_progress_slower_with_lighter_week_every_third():
    young = en.config(_prof(30, goal={"type": "volume", "weekly": 60}))["corrida"]
    old = en.config(_prof(55, goal={"type": "volume", "weekly": 60}))["corrida"]
    vy = [en.week_volume("corrida", young, "intermediario", w, MONDAY + timedelta(weeks=w))["volume"] for w in range(8)]
    vo = [en.week_volume("corrida", old, "intermediario", w, MONDAY + timedelta(weeks=w))["volume"] for w in range(8)]
    assert vo[2] < vo[1] and vo[5] < vo[4]          # leve a cada 3ª
    assert vo[1] <= vy[1] and vo[4] < vy[4]          # sobe mais devagar
    assert en.week_volume("corrida", old, "intermediario", 2, MONDAY)["phase"] == "semana_leve"


def test_base_phase_uses_hills_instead_of_flat_intervals():
    wp = en.week_plan(_prof(30), MONDAY, MONDAY, "intermediario")
    types = [s["endurance"]["type"] for s in wp["sports"]["corrida"]["sessions"]]
    assert "intervalado" not in types and ("subidas" in types or "ritmo" in types or "noruegues" in types)
    hills = en.build_session("corrida", "subidas", 8, {"env": "rua"}, {}, "intermediario")
    assert hills["endurance"]["hard"] and "Subidas" in hills["endurance"]["title"]


def test_beginner_low_volume_gets_run_walk_that_progresses():
    p = _prof(40, current_weekly=4, sessions=3)
    w0 = en.week_plan(p, MONDAY, MONDAY, "iniciante")["sports"]["corrida"]["sessions"]
    w5 = en.week_plan(p, MONDAY + timedelta(weeks=5), MONDAY, "iniciante")["sports"]["corrida"]["sessions"]
    assert all("Corre-caminha" in s["endurance"]["title"] for s in w0)
    run0 = max(x["dur_s"] for x in _flat(w0[0]["endurance"]["steps"]) if x["kind"] == "interval")
    run5 = max(x["dur_s"] for x in _flat(w5[0]["endurance"]["steps"]) if x["kind"] == "interval")
    assert run5 > run0
    # quem já corre 10 km por semana não faz corre-caminha
    w = en.week_plan(_prof(40, current_weekly=15, sessions=3), MONDAY, MONDAY, "iniciante")["sports"]["corrida"]["sessions"]
    assert not any("Corre-caminha" in s["endurance"]["title"] for s in w)


def test_no_session_more_than_10pct_above_longest_recent():
    p = _prof(30, current_weekly=40, goal={"type": "volume", "weekly": 50})
    done = {"corrida": {"avg3": 40, "max3": 42, "weeks": [40, 40, 40], "before": 38, "tracked": True, "longest30": 10}}
    wp = en.week_plan(p, MONDAY + timedelta(weeks=3), MONDAY, "intermediario", done=done)
    sess = wp["sports"]["corrida"]["sessions"]
    assert max((s["endurance"]["dist_m"] or 0) for s in sess) <= 11_000 + 250
    assert any(s["endurance"].get("spike_capped") for s in sess)
    free = en.week_plan(p, MONDAY + timedelta(weeks=3), MONDAY, "intermediario")
    assert max((s["endurance"]["dist_m"] or 0) for s in free["sports"]["corrida"]["sessions"]) > 11_250


def test_sixty_plus_advanced_one_quality_session():
    p = _prof(62, sessions=6, current_weekly=50)
    wp = en.week_plan(p, MONDAY + timedelta(weeks=5), MONDAY, "avancado")
    hard = [s for s in wp["sports"]["corrida"]["sessions"] if s["endurance"]["hard"]]
    assert len(hard) <= 1
