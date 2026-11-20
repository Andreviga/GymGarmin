"""Foco em músculo/aptidão: volume, frequência, ordem, técnica, blocos de aptidão e progresso."""
from datetime import date

from fastapi.testclient import TestClient

from gymgarmin import catalog, focus, generator

TODAY = date(2026, 11, 20)
BASE = {"training_months": 40, "goal": "hipertrofia", "session_minutes": 75, "available_days": [0, 1, 2, 3, 4], "age": 34, "sex": "M",
        "weight_kg": 85}


def _plan(f=None, **extra):
    p = {**BASE, **extra}
    if f:
        p["focus"] = {**f, "start": "2026-11-16"}
    return generator.generate(p, 0, today=TODAY)


def _freq(plan, m):
    return sum(1 for d in plan["days"] if d["kind"] == "forca" and any(
        m in catalog.get(i["exercise_id"])["primaryMuscles"] for i in d["items"] if (i.get("phase") or "principal") == "principal"))


def _within_budget(plan):
    return all(d["time"]["total"] <= d["time"]["budget"] + 1 for d in plan["days"] if d["time"].get("budget"))


def test_muscle_focus_raises_volume_frequency_and_comes_first():
    base, foc = _plan(), _plan({"muscles": ["biceps", "triceps"]})
    for m in ("biceps", "triceps"):
        assert foc["goals_week"]["sets_by_muscle"][m]["planned"] > base["goals_week"]["sets_by_muscle"][m]["planned"]
        assert _freq(foc, m) >= 3 > _freq(base, m) - 1
    # foco forte: o resto vai para manutenção (alvo menor), sem passar do tempo
    assert foc["generator"]["targets"]["quadriceps"] < base["generator"]["targets"]["quadriceps"]
    assert _within_budget(foc)
    # ordem: em cada dia com bíceps, ele aparece entre os 2 primeiros exercícios
    for d in foc["days"]:
        pr = [i for i in d["items"] if (i.get("phase") or "principal") == "principal"]
        pos = [k for k, i in enumerate(pr) if "biceps" in catalog.get(i["exercise_id"])["primaryMuscles"]]
        if pos:
            assert pos[0] <= 2
    notes = " ".join(i.get("notes") or "" for d in foc["days"] for i in d["items"])
    assert "myo-reps" in notes and "foco" in notes
    assert any(r.startswith("Foco: Bíceps + Tríceps") for r in foc["rationale"])


def test_light_focus_keeps_the_rest():
    base, foc = _plan(), _plan({"muscles": ["calves"], "level": "leve"})
    assert foc["generator"]["targets"]["quadriceps"] == base["generator"]["targets"]["quadriceps"]
    assert foc["generator"]["targets"]["calves"] > base["generator"]["targets"]["calves"]


def test_skill_blocks():
    f = _plan({"skills": ["forca"], "lift": "supino"})
    bench = [d for d in f["days"] if any(i["exercise_id"] == "Barbell_Bench_Press_-_Medium_Grip" for i in d["items"])]
    assert len(bench) >= 2
    heavy = next(i for d in bench for i in d["items"] if i["exercise_id"] == "Barbell_Bench_Press_-_Medium_Grip" and i.get("reps") == "3-5")
    assert heavy["rest_s"] >= 180
    p = _plan({"skills": ["potencia"]})
    jumps = [i for d in p["days"] for i in d["items"] if i.get("focus") and i["exercise_id"] in focus.POOLS["potencia_lower"] + focus.POOLS["potencia_upper"]]
    assert jumps and all(i["sets"] == 3 for i in jumps)
    mc = _plan({"skills": ["mobilidade", "core"]})
    for d in [x for x in mc["days"] if x["kind"] == "forca"]:
        assert sum(1 for i in d["items"] if i.get("focus") and i.get("phase") == "preparo") >= 1
        assert any("abdominals" in catalog.get(i["exercise_id"])["primaryMuscles"] for i in d["items"])
    fol = _plan({"skills": ["folego"]}, available_days=[0, 2, 4])
    assert any(i.get("intervals") and i.get("focus") for d in fol["days"] for i in d["items"])
    assert all(_within_budget(x) for x in (f, p, mc, fol))


def test_limitation_blocks_unsafe_jumps():
    p = _plan({"skills": ["potencia"]}, limitations=[{"key": "joelho", "severity": "evitar"}], equipment=["dumbbell", "bands"])
    ids = {i["exercise_id"] for d in p["days"] for i in d["items"]}
    assert not ids & {"Box_Jump_Multiple_Response", "Front_Box_Jump", "Freehand_Jump_Squat"}


def test_api_focus_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.delenv("VERCEL", raising=False)
    from gymgarmin.app import app
    with TestClient(app) as c:
        assert {s["id"] for s in c.get("/api/focus/options").json()["skills"]} >= {"forca", "mobilidade", "folego"}
        c.put("/api/profile", json={**BASE, "focus": {"muscles": ["biceps", "x"], "skills": ["nao-existe"]}})
        f = c.get("/api/profile").json()["focus"]
        assert f["muscles"] == ["biceps"] and f["skills"] == [] and f["start"]
        c.post("/api/generator/apply", json={})
        for day, load in (("2026-11-02", 30), ("2026-11-20", 34)):
            c.post("/api/sessions", json={"date": f"{day}T18:00", "items": [{"exercise_id": "Barbell_Curl", "sets": [{"reps": 10, "load_kg": load}] * 3}]})
        pr = c.get("/api/focus/progress").json()
        assert pr["applied"] and pr["label"] == "Bíceps" and pr["targets"]["biceps"]
        assert any(w["sets"]["biceps"] == 3 for w in pr["weeks"])
        c.put("/api/profile", json={"focus": None})
        assert c.get("/api/focus/progress").json() == {"focus": None}
