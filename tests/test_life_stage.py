"""Idade e sexo na montagem do treino."""
from gymgarmin import catalog, generator as G, life_stage as L

BASE = {"training_months": 30, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4, 5], "session_minutes": 60}


def _main(pl):
    return [i for d in pl["days"] for i in d["items"] if i.get("phase") == "principal" and i.get("sets")]


def _lo(reps):
    return int(str(reps).split("-")[0])


def test_adult_man_unchanged_by_stage():
    a = G.generate({**BASE, "age": 30, "sex": "M"})
    b = G.generate(BASE)
    assert [i["exercise_id"] for i in _main(a)] == [i["exercise_id"] for i in _main(b)]


def test_teen_no_heavy_sets_no_intensifiers():
    pl = G.generate({**BASE, "age": 15, "sex": "M"})
    for it in _main(pl):
        if it.get("reps"):
            assert _lo(it["reps"]) >= 8
        assert float(it["rpe_target"]) <= 8
        assert "drop set" not in (it.get("notes") or "") and "top set" not in (it.get("notes") or "")


def test_older_adults_fewer_days_balance_power_machines():
    pl = G.generate({**BASE, "age": 75, "sex": "M"})
    strength = [d for d in pl["days"] if d["kind"] == "forca"]
    assert len(strength) <= 3
    for d in strength:
        assert any(i["exercise_id"] == L.BALANCE_DRILL for i in d["items"])
        warm = next(i for i in d["items"] if i.get("ramp") == "elevar")
        assert warm["duration_min"] >= 6
    for it in _main(pl):
        assert float(it["rpe_target"]) <= 7.5
        if it.get("role") == "main":
            assert int(it["sets"]) <= 3 and "potência" in (it.get("notes") or "")
        if it.get("reps"):
            assert _lo(it["reps"]) >= 8
    young = G.generate({**BASE, "age": 30})
    machine = lambda xs: sum(1 for i in xs if catalog.get(i["exercise_id"])["equipment"] in ("machine", "cable"))  # noqa: E731
    assert machine(_main(pl)) / len(_main(pl)) > machine(_main(young)) / len(_main(young))


def test_women_knee_guard_shorter_accessory_rest_and_bone_loading():
    pl = G.generate({**BASE, "age": 30, "sex": "F"})
    man = G.generate({**BASE, "age": 30, "sex": "M"})
    for d in pl["days"]:
        if d.get("template") in G.LOWER_DAYS:
            assert any("abductors" in catalog.get(i["exercise_id"])["primaryMuscles"] for i in d["items"] if i.get("phase") == "principal")
    rest = lambda p: sum(i["rest_s"] for i in _main(p) if i.get("role") == "acc" and not i.get("superset"))  # noqa: E731
    assert rest(pl) < rest(man)
    assert any("ciclo menstrual" in r for r in pl["rationale"])
    older = G.generate({**BASE, "age": 52, "sex": "F"})
    lower = [d for d in older["days"] if d.get("template") in G.LOWER_DAYS]
    assert all(any(i["exercise_id"] == L.BONE_JUMP for i in d["items"]) for d in lower)
    for it in _main(older):
        if catalog.get(it["exercise_id"])["mechanic"] == "compound" and it.get("reps"):
            assert int(str(it["reps"]).split("-")[-1]) <= 10
    knee = G.generate({**BASE, "age": 52, "sex": "F", "limitations": [{"key": "joelho", "severity": "cuidado"}]})
    assert not any(i["exercise_id"] == L.BONE_JUMP for d in knee["days"] for i in d["items"])


def test_stage_without_age_or_sex_is_neutral():
    s = L.stage({})
    assert not s["notes"] and not s["power"] and s["rest_mult_acc"] == 1.0
