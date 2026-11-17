"""Progressão de carga sugerida — regressões das simulações (scripts/simulate_suggestions.py)."""
from datetime import date

from gymgarmin import progression as pg

TODAY = date(2026, 11, 17)


def _perf(d, load, reps, rpe=None, deload=False):
    sets = [{"load_kg": load, "reps": r, **({"rpe": rpe} if rpe else {})} for r in reps]
    return {"date": d, "sets": sets, **({"deload": True} if deload else {})}


def sug(eid, rng, perfs, deload=False):
    return pg.suggest({"exercise_id": eid, "reps": rng}, pg.pick_last(perfs), deload=deload, today=TODAY)


BENCH = "Barbell_Bench_Press_-_Medium_Grip"


def test_after_deload_load_returns_to_where_it_was():
    perfs = [_perf("2026-11-16", 72.0, [8, 8, 8], 6, deload=True), _perf("2026-11-12", 80.0, [10, 10, 9], 8)]
    s = sug(BENCH, "6-10", perfs)
    assert s["load_kg"] >= 80  # antes: 72 + 2,5 = 74,5 (cada deload puxava 10% para baixo)


def test_back_from_break_reduces():
    perfs = [_perf("2026-10-10", 80.0, [10, 10, 10], 8)]
    s = sug(BENCH, "6-10", perfs)
    assert s["load_kg"] < 80 and "semanas" in s["note"]


def test_machine_without_small_step_jumps_by_stack():
    eid = "Triceps_Pushdown"
    perfs = [_perf("2026-11-16", 40.0, [15, 15, 15], 8), _perf("2026-11-13", 40.0, [15, 15, 14], 8)]
    s = sug(eid, "10-15", perfs)
    assert s["load_kg"] == 45  # bateu o topo 2× com 40: o +2,5 não existe na pilha → +5


def test_way_below_range_without_rpe_reduces():
    s = sug(BENCH, "8-12", [_perf("2026-11-16", 80.0, [5, 5, 4])])
    assert s["load_kg"] < 80  # antes: sem RPE ficava 'mantenha' para sempre


def test_too_light_jumps_more_than_one_increment():
    s = sug(BENCH, "8-12", [_perf("2026-11-16", 50.0, [12, 12, 12], 6)])
    assert 55 <= s["load_kg"] <= 57.5  # RPE 6: pula até ~15%, não só +2,5


def test_light_dumbbell_holds_instead_of_ping_pong():
    eid = "Side_Lateral_Raise"
    perfs = [_perf("2026-11-16", 10.0, [8, 8, 7], 9), _perf("2026-11-13", 10.0, [8, 7, 7], 9.5),
             _perf("2026-11-10", 8.0, [15, 15, 15], 6)]
    s = sug(eid, "10-15", perfs)
    assert s["load_kg"] == 10  # o halter de 8 kg ficaria leve demais: reconstrua as reps no de 10


def test_low_rep_barbell_uses_small_plates():
    s = sug("Barbell_Squat", "3-5", [_perf("2026-11-16", 100.0, [5, 5, 5], 8)])
    assert s["load_kg"] == 102.5
